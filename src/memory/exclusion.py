"""Secrets and approvals exclusion filter.

One rule set, two entry points. `check_ingest` runs before anything is
written to memory, the vault, or the ledger. `check_recall` runs before
anything read from those stores enters model context. They share rules
today so a later divergence is a deliberate edit, not a drift.

Secrets are keys, tokens, and logins. They are found two ways: by pattern,
and by literal value for secrets the harness itself has resolved (the
provider registers its key here so any text that contains it is caught).

Approvals are the grant and the approved draft. They are structural, not
textual: a record whose kind is in `APPROVAL_KINDS` is refused outright.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("openai-style-key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("bearer-token", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{16,}=*")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("private-key-block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "credential-assignment",
        re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)\s*[:=]\s*\S{6,}"),
    ),
]

APPROVAL_KINDS = frozenset({"approval_grant", "approved_draft"})

# Keys whose values are approval content. A record carrying one is refused
# even if its kind looks harmless.
APPROVAL_FIELDS = frozenset({"grant", "approved_draft", "draft_body"})

REDACTED = "[redacted:{reason}]"


class ExclusionError(ValueError):
    pass


@dataclass
class Verdict:
    ok: bool
    reasons: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.ok


class ExclusionFilter:
    def __init__(self) -> None:
        self._values: set[str] = set()

    # -- literal values ---------------------------------------------------

    def register_secret_value(self, value: str) -> None:
        if value and len(value) >= 6:
            self._values.add(value)

    def clear_values(self) -> None:
        self._values.clear()

    # -- text -------------------------------------------------------------

    def find(self, text: str) -> list[str]:
        if not text:
            return []
        reasons: list[str] = []
        for value in self._values:
            if value in text:
                reasons.append("registered-secret")
                break
        for name, pat in SECRET_PATTERNS:
            if pat.search(text):
                reasons.append(name)
        return reasons

    def check_text(self, text: str) -> Verdict:
        reasons = self.find(text)
        return Verdict(not reasons, reasons)

    def redact(self, text: str) -> tuple[str, list[str]]:
        """Replace every match with a marker. Returns (text, reasons)."""
        if not text:
            return text, []
        reasons: list[str] = []
        for value in self._values:
            if value in text:
                text = text.replace(value, REDACTED.format(reason="registered-secret"))
                reasons.append("registered-secret")
        for name, pat in SECRET_PATTERNS:
            text, n = pat.subn(REDACTED.format(reason=name), text)
            if n:
                reasons.append(name)
        return text, reasons

    def redact_obj(self, obj: Any) -> tuple[Any, list[str]]:
        """Redact every string inside a nested dict/list structure."""
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            out: dict[str, Any] = {}
            reasons: list[str] = []
            for k, v in obj.items():
                out[k], r = self.redact_obj(v)
                reasons.extend(r)
            return out, reasons
        if isinstance(obj, (list, tuple)):
            items = [self.redact_obj(v) for v in obj]
            return [i[0] for i in items], [r for i in items for r in i[1]]
        return obj, []

    # -- records ----------------------------------------------------------

    def check_record(self, record: dict[str, Any]) -> Verdict:
        reasons: list[str] = []
        kind = record.get("kind")
        if kind in APPROVAL_KINDS:
            reasons.append(f"approval-kind:{kind}")
        for key in APPROVAL_FIELDS & set(_walk_keys(record)):
            reasons.append(f"approval-field:{key}")
        for text in _walk_strings(record):
            reasons.extend(self.find(text))
        return Verdict(not reasons, sorted(set(reasons)))

    def check_ingest(self, record: dict[str, Any]) -> Verdict:
        return self.check_record(record)

    def check_recall(self, record: dict[str, Any]) -> Verdict:
        return self.check_record(record)

    def require(self, record: dict[str, Any], where: str = "ingest") -> None:
        v = self.check_ingest(record) if where == "ingest" else self.check_recall(record)
        if not v:
            raise ExclusionError(f"blocked at {where}: {', '.join(v.reasons)}")


def _walk_keys(obj: Any):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_keys(v)


def _walk_strings(obj: Any):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _walk_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _walk_strings(v)


# The shared instance. Providers register resolved key values here; the
# ledger, memory, vault ingest, and recorder all check against it.
exclusion = ExclusionFilter()

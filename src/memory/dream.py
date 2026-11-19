"""What a dream reads from a conversation: the user's own messages, and only those.

Nothing but `Exchange.user` is ever returned here: not the reply, the addendum, comments, attachments,
the summary or the note."""

from __future__ import annotations

import json
import threading
import unicodedata
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..vault_settings import settings_dir

if TYPE_CHECKING:  # the harness imports this package, so the import is for types only
    from ..harness.conversations import Conversation

MESSAGE_MAX = 4000
BATCH_MAX = 12000
QUIET_S = 1800
FORM_PREFIX = "Form answers ("
IDLE_MIN, IDLE_MAX, IDLE_DEFAULT = 1, 120, 5
RETRY_WAITS = (600.0, 3600.0)  # seconds to wait after the 1st and 2nd failed attempt
MAX_ATTEMPTS = 3
FACTS_MAX = 8
FACT_MAX = 300


def _replace_file(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(path)


def _idle_ok(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and IDLE_MIN <= v <= IDLE_MAX


@dataclass
class DreamSettings:
    """Dreaming's switch and how long the app must be idle first; `vault-settings/dream.json`, no secret."""
    enabled: bool = False
    idle_minutes: int = IDLE_DEFAULT

    @staticmethod
    def _path(vault_root: Path) -> Path:
        return settings_dir(vault_root) / "dream.json"

    @classmethod
    def load(cls, vault_root: Path) -> DreamSettings:
        try:
            d = json.loads(cls._path(vault_root).read_text(encoding="utf-8"))
            return cls.checked(d["enabled"], d["idle_minutes"])
        except (OSError, ValueError, KeyError, TypeError):
            return cls()

    @classmethod
    def checked(cls, enabled: Any, idle_minutes: Any) -> DreamSettings:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be true or false")
        if not _idle_ok(idle_minutes):
            raise ValueError(f"idle_minutes must be a whole number from {IDLE_MIN} to {IDLE_MAX}")
        return cls(enabled, idle_minutes)

    def save(self, vault_root: Path) -> None:
        _replace_file(self._path(vault_root), asdict(self))


@dataclass
class Mark:
    """Progress in one conversation. `seen` is its exchange count when last read or last failed."""
    read: int = 0
    attempts: int = 0
    not_before: float = 0.0
    seen: int = 0


class DreamMarks:
    """Per-conversation progress, kept in `<vault>-ledger/dreams.json` (tmp-then-replace on every change)."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()
        self._marks: dict[str, Mark] = {}
        self.learned = 0
        self.last_dream_at = ""
        self._load()

    def _load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        try:
            d = json.loads(text)
            marks = {k: Mark(**v) for k, v in d["conversations"].items()}
            learned, at = int(d["learned"]), str(d["last_dream_at"])
        except (ValueError, KeyError, TypeError, AttributeError):
            self.path.replace(self.path.with_name(self.path.name + ".bad"))  # keep the damaged file recoverable
            return
        self._marks, self.learned, self.last_dream_at = marks, learned, at

    def _save(self) -> None:  # caller holds the lock
        _replace_file(self.path, {"conversations": {k: asdict(m) for k, m in self._marks.items()},
                                  "learned": self.learned, "last_dream_at": self.last_dream_at})

    def get(self, conv_id: str) -> Mark:
        with self._lock:
            return Mark(**asdict(self._marks.get(conv_id, Mark())))

    def set_read(self, conv_id: str, n: int) -> None:
        with self._lock:
            self._marks[conv_id] = Mark(read=n, seen=n)
            self._save()

    def fail(self, conv_id: str, now: float, seen: int) -> None:
        with self._lock:
            m = self._marks.setdefault(conv_id, Mark())
            m.attempts += 1
            m.seen = seen
            m.not_before = now + RETRY_WAITS[m.attempts - 1] if m.attempts < MAX_ATTEMPTS else 0.0
            self._save()

    def skipped(self, conv_id: str) -> bool:
        return self.get(conv_id).attempts >= MAX_ATTEMPTS

    def grown(self, conv_id: str) -> None:
        """The conversation grew past `seen`: try it again. `read` and `seen` stay."""
        with self._lock:
            m = self._marks.setdefault(conv_id, Mark())
            m.attempts, m.not_before = 0, 0.0
            self._save()

    def add_learned(self, n: int, at: str) -> None:
        with self._lock:
            self.learned += n
            self.last_dream_at = at
            self._save()


def unread_marked(conv: Conversation, read: int, clean: Callable[[str], bool]) -> tuple[list[tuple[str, int]], int]:
    """The messages to read past the mark `read`, each with the mark that holds once it has been read
    (its exchange index + 1), and the new mark: the index of the first pending exchange, else the end.
    Skipped but counted as read: a routed or set-aside exchange, a form's answers, a repeat of an earlier
    message (those before `read` count), an empty one, and one `clean` refuses."""
    kept: list[tuple[str, int]] = []
    exchanges = conv.exchanges
    seen = {e.user.strip() for e in exchanges[:read]}
    mark = read
    for i in range(read, len(exchanges)):
        e = exchanges[i]
        if e.outcome == "pending":
            return kept, i
        text = e.user.strip()
        repeat = text in seen
        seen.add(text)
        mark = i + 1
        if (e.outcome == "routed" or e.set_aside or e.answers_to or text.startswith(FORM_PREFIX)
                or repeat or not text or not clean(text)):
            continue
        kept.append((text[:MESSAGE_MAX], mark))
    return kept, mark


def unread(conv: Conversation, read: int, clean: Callable[[str], bool]) -> tuple[list[str], int]:
    """`unread_marked` without the per-message marks: the texts and the new mark."""
    kept, mark = unread_marked(conv, read, clean)
    return [text for text, _ in kept], mark


def batches(messages: list[str], limit: int = BATCH_MAX) -> list[list[str]]:
    """Split messages, in order, into groups of at most `limit` characters (a lone longer one stands alone)."""
    out: list[list[str]] = []
    size = 0
    for m in messages:
        if out and size + len(m) <= limit:
            out[-1].append(m)
            size += len(m)
        else:
            out.append([m])
            size = len(m)
    return out


@dataclass(frozen=True)
class DreamFact:
    text: str
    store: str


def _plain(text: str) -> bool:
    """No control character at all (newline, tab, DEL, U+2028 and U+2029 included)."""
    return not any(unicodedata.category(ch) in ("Cc", "Zl", "Zp") for ch in text)


def clean_facts(raw: Any, stores: set[str], known: set[str]) -> list[DreamFact]:
    """The model's answer as facts: plain, one to FACT_MAX characters, not known or repeated, at most FACTS_MAX.
    A store that is not offered becomes `personal`. A wrong overall shape is a ValueError."""
    if not isinstance(raw, dict) or not isinstance(raw.get("facts"), list):
        raise ValueError("the answer is not {\"facts\": [...]}")
    seen = {k.casefold() for k in known}
    out: list[DreamFact] = []
    for item in raw["facts"]:
        if isinstance(item, str):
            text, store = item, "personal"
        elif isinstance(item, dict) and isinstance(item.get("text"), str):
            text, store = item["text"], item.get("store")
        else:
            continue
        text = text.strip()
        if not text or len(text) > FACT_MAX or not _plain(text) or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        out.append(DreamFact(text, store if isinstance(store, str) and store in stores else "personal"))
        if len(out) == FACTS_MAX:
            break
    return out

"""The sync log: one line per ingested file with its md5, where it
landed in the vault, and the memory fact that points at it. A rescan
compares md5s against this log to find what changed."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VAULT_SCHEMA = 1  # bumped when the layout or the log shape changes; decided at import, see ingest.vault_path_for


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class SyncEntry:
    source: str  # absolute path of the original file
    md5: str
    vault_path: str  # relative to the vault root
    fact_id: str
    chars: int = 0
    at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SyncLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._entries: dict[str, SyncEntry] = {}
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for d in data.get("entries", []):
                self._entries[d["source"]] = SyncEntry(**d)

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"schema": VAULT_SCHEMA, "entries": [e.to_dict() for e in self._entries.values()]}, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, source: str) -> SyncEntry | None:
        return self._entries.get(source)

    def put(self, entry: SyncEntry) -> None:
        self._entries[entry.source] = entry
        self._save()

    def remove(self, source: str) -> bool:
        if self._entries.pop(source, None) is None:
            return False
        self._save()
        return True

    def entries(self) -> list[SyncEntry]:
        return list(self._entries.values())

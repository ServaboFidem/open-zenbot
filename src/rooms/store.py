"""Room persistence: one JSON file per room, written atomically. Disk I/O
only; the record shape belongs to `Room.to_record` / `Room.from_record`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .room import Room

__all__ = ["RoomStore"]


class RoomStore:
    def __init__(self, dir: Path) -> None:
        self.dir = Path(dir)

    def _path(self, room_id: str) -> Path:
        return self.dir / f"{room_id}.json"

    def save(self, room: "Room") -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self._path(room.id)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(room.to_record(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def load_all(self, ledger: Any) -> list["Room"]:
        from .room import Room

        rooms: list[Room] = []
        if not self.dir.is_dir():
            return rooms
        for f in sorted(self.dir.glob("*.json")):
            try:
                rooms.append(Room.from_record(json.loads(f.read_text(encoding="utf-8")), ledger))
            except Exception as e:  # a bad file must not take the other rooms down
                ledger.append("error", "", "", where="room_store", file=f.name, error=f"{type(e).__name__}: {e}")
        return rooms

    def delete(self, room_id: str) -> None:
        self._path(room_id).unlink(missing_ok=True)

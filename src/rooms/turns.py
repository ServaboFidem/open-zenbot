"""Turn taking. One active speaker per room, each turn bounded by a
deadline, every turn carrying a nonce and the nonce of the turn before
it (the parent chain). A delivery must quote the open nonce. A turn
past its deadline expires and frees the floor; a turn whose bot failed
is released. Every event goes to the ledger with the room id in the
session_id slot."""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .room import Post, Room


class RoomError(ValueError):
    pass


class TurnState(StrEnum):
    open = "open"
    delivered = "delivered"
    expired = "expired"
    released = "released"


@dataclass
class Turn:
    nonce: int
    speaker: str
    started_at: float
    deadline: float
    parent_nonce: int | None
    state: TurnState = TurnState.open
    task_session: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "nonce": self.nonce, "speaker": self.speaker, "started_at": self.started_at, "deadline": self.deadline,
            "parent_nonce": self.parent_nonce, "state": str(self.state), "task_session": self.task_session,
        }


class TurnTaker:
    def __init__(self, ledger: Any, deadline_s: float) -> None:
        self.ledger = ledger
        self.deadline_s = deadline_s

    def _log(self, room: "Room", event: str, turn: Turn, **extra: Any) -> None:
        self.ledger.append("room_turn", turn.speaker, room.id, event=event, nonce=turn.nonce, speaker=turn.speaker,
                           parent_nonce=turn.parent_nonce, task_session=turn.task_session, **extra)

    def expire_overdue(self, room: "Room") -> Turn | None:
        t = room.open_turn
        if t is not None and time.monotonic() > t.deadline:
            t.state = TurnState.expired
            room.open_turn, room.active_speaker = None, None
            self._log(room, "expired", t)
            return t
        return None

    def request(self, room: "Room", bot_id: str) -> Turn:
        self.expire_overdue(room)
        if bot_id not in room.members:
            raise RoomError(f"{bot_id!r} is not a member of room {room.id}")
        if room.open_turn is not None:
            raise RoomError(f"the floor is held by {room.open_turn.speaker!r} (nonce {room.open_turn.nonce})")
        now = time.monotonic()
        turn = Turn(nonce=room.last_nonce + 1, speaker=bot_id, started_at=now, deadline=now + self.deadline_s,
                    parent_nonce=room.last_nonce or None)
        room.last_nonce = turn.nonce
        room.turns.append(turn)
        room.open_turn, room.active_speaker = turn, bot_id
        self._log(room, "requested", turn, deadline_s=self.deadline_s)
        return turn

    def deliver(self, room: "Room", nonce: int, text: str, parent_post: str | None = None,
                conversation: str = "") -> "Post":
        self.expire_overdue(room)
        t = room.open_turn
        if t is None or t.nonce != nonce:
            raise RoomError(f"no open turn with nonce {nonce}")
        post = room.add_post(author=t.speaker, text=text, nonce=nonce, parent=parent_post,  # may refuse; turn stays open
                             conversation=conversation)
        t.state = TurnState.delivered
        room.open_turn, room.active_speaker = None, None
        self._log(room, "delivered", t, post_id=post.id)
        return post

    def release(self, room: "Room", nonce: int, reason: str) -> None:
        t = room.open_turn
        if t is None or t.nonce != nonce:
            return
        t.state = TurnState.released
        room.open_turn, room.active_speaker = None, None
        self._log(room, "released", t, reason=reason)

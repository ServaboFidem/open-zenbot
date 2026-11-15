"""Pause on provider loss.

Offline means pause. The session keeps its state. The pause and the
resume are both written to the ledger. Resume happens when the user acts
or when a user-triggered retry finds the provider answering. Never on a
timer alone.

The local branch is a documented no-op on day one: `local_available`
returns False and the harness never switches. When local inference lands
it registers a provider and this function consults the registry.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from src.memory.ledger import Kind, Ledger
from src.providers.interface import Provider, ProviderError

if TYPE_CHECKING:
    from .loop import Session


def local_available() -> bool:
    """Day one: no local inference. Kept so the fallback rule reads as
    written in the spec and so the slot is visible."""
    return False


class PauseController:
    def __init__(self, ledger: Ledger, provider: Provider) -> None:
        self.ledger = ledger
        self.provider = provider
        self._events: dict[str, asyncio.Event] = {}
        self._reasons: dict[str, str] = {}

    def is_paused(self, session_id: str) -> bool:
        ev = self._events.get(session_id)
        return ev is not None and not ev.is_set()

    async def hold(self, session: "Session", error: ProviderError) -> bool:
        """Pause on provider loss until resumed. Returns True to retry the
        step, False if the session was stopped while paused."""
        if local_available():  # pragma: no cover - day-one no-op
            pass
        return await self.hold_reason(session, f"{type(error).__name__}: {error}")

    async def hold_reason(self, session: "Session", reason: str) -> bool:
        """Pause for any reason (provider loss, supervisor halt) until the
        user resumes. Same ledger entries, same stop semantics."""
        from .loop import SessionState  # local import to avoid a cycle

        if session.stop_requested:  # a stop that landed before the pause existed must not be lost
            session.state = SessionState.stopped
            return False
        session.state = SessionState.paused
        session.pause_reason = reason
        ev = asyncio.Event()
        self._events[session.id] = ev
        self.ledger.append(Kind.pause, session.bot_id, session.id, reason=session.pause_reason, provider=self.provider.kind)
        try:
            await ev.wait()
        except asyncio.CancelledError:
            session.pause_reason = None
            raise
        finally:
            self._events.pop(session.id, None)
        reason = self._reasons.pop(session.id, "user")
        if session.stop_requested:
            return False
        session.state = SessionState.running
        session.pause_reason = None
        self.ledger.append(Kind.resume, session.bot_id, session.id, reason=reason, provider=self.provider.kind)
        return True

    def resume(self, session_id: str, reason: str = "user") -> bool:
        ev = self._events.get(session_id)
        if ev is None or ev.is_set():
            return False
        self._reasons[session_id] = reason
        ev.set()
        return True

    async def retry(self, session_id: str) -> bool:
        """User-triggered probe. Resumes only if the provider answers."""
        if not self.is_paused(session_id):
            return False
        if await self.provider.probe():
            return self.resume(session_id, reason="provider")
        return False

    def release_for_stop(self, session_id: str) -> None:
        ev = self._events.get(session_id)
        if ev is not None and not ev.is_set():
            self._reasons[session_id] = "stop"
            ev.set()

"""Silent helpers.

A main bot starts a quiet helper. The helper has three channels:
`control` (start, stop), `data` (work, by reference or short text),
and `result` (what it found, back to the main bot). It runs each piece
of work as a task-loop session on its own bot and never posts to a
room: it holds no room and no room holds it. Start, each result, and
stop go to the ledger."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.harness.bot import Bot
    from src.harness.loop import TaskLoop


@dataclass
class HelperResult:
    text: str
    state: str
    session_id: str
    error: str | None = None


class SilentHelper:
    kind = "silent-helper"

    def __init__(self, bot: "Bot", loop: "TaskLoop", ledger: Any) -> None:
        self.bot = bot
        self.loop = loop
        self.ledger = ledger
        self.control: asyncio.Queue[str] = asyncio.Queue()
        self.data: asyncio.Queue[str] = asyncio.Queue()
        self.result: asyncio.Queue[HelperResult] = asyncio.Queue()
        self.running = False

    async def run(self) -> None:
        while (cmd := await self.control.get()) != "start":
            if cmd == "stop":
                return
        self.running = True
        self.ledger.append("helper", self.bot.id, "", event="started", helper=self.kind)
        try:
            while True:
                ctl = asyncio.ensure_future(self.control.get())
                dat = asyncio.ensure_future(self.data.get())
                done, _ = await asyncio.wait({ctl, dat}, return_when=asyncio.FIRST_COMPLETED)
                if dat in done:  # work first: a control item that landed in the same tick must not drop it
                    work = dat.result()
                    session = await self.loop.run(self.bot, work)
                    self.ledger.append("helper", self.bot.id, session.id, event="result", state=str(session.state), chars=len(session.result or ""))
                    await self.result.put(HelperResult(session.result or "", str(session.state), session.id, session.error))
                else:
                    dat.cancel()
                if ctl in done:
                    if ctl.result() == "stop":
                        return
                else:
                    ctl.cancel()
        finally:
            self.running = False
            self.ledger.append("helper", self.bot.id, "", event="stopped", helper=self.kind)

"""The orchestrator: when the user messages ZenBot, one scoring call decides
whether the message continues an existing topic room, opens a new room with
the best-fit specialists, goes to a studio bot's own conversation (a design
bot works one to one: its preview pane, question forms and comments are not
in a room), or is answered by ZenBot directly.

It never imports the harness; the harness wires it in."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Sequence

from src.decider.interface import BandConfig, DecisionRefused, DecisionRequest, NoulQuestion, Outcome, decide_and_log, select_band
from src.providers.interface import ProviderError

from .room import Post, Room, RoomService, refusal

if TYPE_CHECKING:
    from src.harness.bot import Bot

FILES_HEADER = "Files from our chat:"


def _unseen(room: Room, files: list[str]) -> list[str]:
    """The file lines the room's current conversation has not been given yet."""
    posts = [p.text for p in room.current_posts()]
    return [f for f in files if not any(f in text for text in posts)]


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass
class OrchestratorConfig:
    continue_threshold: float = field(default_factory=lambda: _env_float("ZEN_ROUTE_CONTINUE", 0.6))
    band_width: float = 0.1
    k: int = 2
    low_confidence: float = field(default_factory=lambda: _env_float("ZEN_ROUTE_SPECIALIST_MIN", 0.5))
    max_candidate_rooms: int = 10


@dataclass
class RouteOutcome:
    outcome: str  # "room" | "studio" | "task"
    room_id: str | None = None
    created: bool = False
    scores: dict[str, float] = field(default_factory=dict)
    fallback: str | None = None
    bot_id: str | None = None  # "studio": the bot whose own conversation takes the message

    def to_dict(self) -> dict[str, Any]:
        return {"outcome": self.outcome, "room_id": self.room_id, "created": self.created,
                "scores": self.scores, "fallback": self.fallback, "bot_id": self.bot_id}


class Orchestrator:
    def __init__(self, scorer: Any, rooms: RoomService, bots: "dict[str, Bot]", ledger: Any,
                 topic_writer: Callable[[str], Awaitable[tuple[str, str]]], lead_id: str,
                 config: OrchestratorConfig | None = None) -> None:
        self.scorer = scorer
        self.rooms = rooms
        self.bots = bots
        self.ledger = ledger
        self.topic_writer = topic_writer
        self.lead_id = lead_id
        self.config = config or OrchestratorConfig()

    def _done(self, out: RouteOutcome) -> RouteOutcome:
        self.ledger.append("room", self.lead_id, out.room_id or "", event="routed", outcome=out.outcome,
                           created=out.created, fallback=out.fallback, **({"bot": out.bot_id} if out.bot_id else {}))
        return out

    def _candidates(self, new_topic: bool) -> list[Room]:
        if new_topic:
            return []
        mine = [r for r in self.rooms.list() if r.lead == self.lead_id and not r.archived]
        mine.sort(key=lambda r: r.last_active, reverse=True)
        return mine[: self.config.max_candidate_rooms]

    async def route(self, text: str, *, context: str = "", files: Sequence[str] = (), new_topic: bool = False) -> RouteOutcome:
        """`files` are `Attached:` lines naming the files of the lead's recent chat; the room the message goes to
        is given every one it does not have yet, so its bots can work with them."""
        cfg = self.config
        candidates = self._candidates(new_topic)
        specialists = [b for bid, b in self.bots.items()
                       if bid != self.lead_id and not getattr(b, "closed", False)  # a closed bot never joins a room
                       and not getattr(b, "narrates", False)]  # nor does the Narrator: it takes no task
        if self.scorer is None:
            return self._done(RouteOutcome("task", fallback="no scorer"))
        if not candidates and not specialists:
            return self._done(RouteOutcome("task"))
        refused = refusal(text)  # the room would refuse it; check before any room is made or posted to
        if refused:
            return self._done(RouteOutcome("task", fallback="refused in room: " + refused))
        if context and refusal(context):
            context = ""  # never quoted into a room, never shown to the scorer
        files = [f for f in files if not refusal(f)]  # the same rule for each file line; the others still go

        questions: dict[str, NoulQuestion] = {}
        for r in candidates:
            questions[f"room:{r.id}"] = NoulQuestion(
                f"Is this message about the same subject as the room '{r.name}', so it belongs there as a follow-up? The room is about: {r.topic or r.name}")
        for b in specialists:
            questions[f"bot:{b.id}"] = NoulQuestion(
                f"Is {b.name} ({b.role}) clearly a better fit for this message than a generalist assistant?")
        state = {"message": text, "recent": context}
        if files:
            state["files"] = "\n".join(files)
        request = DecisionRequest(state=state, questions=questions, purpose="orchestration")
        try:
            decision = await decide_and_log(self.scorer, request, self.ledger, self.lead_id, "")
            scores = {qid: decision.noul(qid).noul for qid in questions}
        except (ProviderError, DecisionRefused) as e:  # a refused question is no answer either
            err = f"{type(e).__name__}: {e}"
            self.ledger.append("error", self.lead_id, "", where="orchestrator", error=err, action="answer directly")
            return self._done(RouteOutcome("task", fallback=err))

        room_scores = {r.id: scores[f"room:{r.id}"] for r in candidates}
        if room_scores:
            best = max(room_scores, key=lambda k: room_scores[k])
            if room_scores[best] >= cfg.continue_threshold:
                new = _unseen(self.rooms.get(best), files)
                if new:
                    await self.rooms.post(best, self.lead_id, FILES_HEADER + "\n" + "\n".join(new), schedule=False)
                await self.rooms.post(best, "user", text)
                return self._done(RouteOutcome("room", best, created=False, scores=scores))

        band = select_band({b.id: scores[f"bot:{b.id}"] for b in specialists},
                           BandConfig(cfg.band_width, cfg.k, cfg.low_confidence))
        if band.outcome == Outcome.act and band.winners:
            winners = band.winners
            if getattr(self.bots[winners[0]], "studio", False):
                # the best fit designs in its studio, which a room does not have: no room, the caller hands over
                return self._done(RouteOutcome("studio", scores=scores, bot_id=winners[0]))
            name, topic = await self.topic_writer(text)
            room = self.rooms.create(name, [self.lead_id, *winners], lead=self.lead_id, topic=topic)
            intro = [f"From our chat just now:\n\n{context}"] if context else []
            if files:
                intro.append(FILES_HEADER + "\n" + "\n".join(files))
            if intro:
                await self.rooms.post(room.id, self.lead_id, "\n\n".join(intro), schedule=False)
            await self.rooms.post(room.id, "user", text, schedule=False)
            await self.rooms.post(room.id, self.lead_id, "Over to you, " + " ".join(f"@{w}" for w in winners) + ".")
            return self._done(RouteOutcome("room", room.id, created=True, scores=scores))
        return self._done(RouteOutcome("task", scores=scores))

    async def refresh_topic(self, room: Room, post: Post) -> None:
        try:
            mine = [p.text.strip()[:300] for p in room.current_posts() if p.author == "user"][-10:]
            asks = "\n".join(f"- {t}" for t in mine) or "(none yet)"
            text = (
                "Describe the overall subject of this room in one sentence, not the latest sub-request. "
                "Keep what the current topic already covers.\n\n"
                f"Room name: {room.name}\nCurrent topic: {room.topic}\n"
                f"What the user has asked in the room, oldest first:\n{asks}"
            )
            _, topic = await self.topic_writer(text)
            room.topic = topic
            self.rooms._persist(room)
        except Exception as e:  # never raise into the room
            self.ledger.append("error", self.lead_id, room.id, where="refresh_topic", error=f"{type(e).__name__}: {e}")

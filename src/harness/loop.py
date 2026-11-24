"""The task loop.

One bot, one task, one session. Build context from role plus selected
skills plus recalled facts, call the provider, run tool calls, repeat
until the model answers without a tool call. Approval-gated calls wait
for the user. Provider errors pause the session. Every turn, tool call,
and approval event goes to the ledger.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from src.context.compaction import context_chars, is_summary
from src.context.protected import ProtectedBlock
from src.memory.ledger import Kind, Ledger
from src.providers import tracing
from src.providers.interface import CANT_SEE, Message, Provider, ProviderError
from src.skills.loader import Skill

from .approvals import ApprovalState, ApprovalStore, needs_approval
from .bot import Bot
from .pause import PauseController
from .stubs import InjectAllSelector, NoRecall, Recall, Selection, SkillSelector
from .telemetry import span
from .tools import ToolRunner, current_call
from .turn_notes import NOTE_HEADING

if TYPE_CHECKING:
    from collections.abc import Callable

    from src.context.compaction import Compactor
    from src.decider.supervisor import LoopSupervisor

    from .write_guard import ShellWriteCheck


class SessionState(StrEnum):
    running = "running"
    waiting_approval = "waiting_approval"
    paused = "paused"
    done = "done"
    stopped = "stopped"
    failed = "failed"


@dataclass
class Session:
    bot_id: str
    task: str
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    state: SessionState = SessionState.running
    messages: list[Message] = field(default_factory=list)
    result: str | None = None
    error: str | None = None
    pause_reason: str | None = None
    steps: int = 0
    injected: list[str] = field(default_factory=list)
    route: dict[str, Any] = field(default_factory=dict)
    approvals: list[str] = field(default_factory=list)
    stop_requested: bool = False
    summary: str = ""  # folded compaction summary, carried forward
    protected: dict[str, Any] = field(default_factory=dict)  # the pinned block, as dict
    compactions: int = 0
    nudges: int = 0
    halts: int = 0
    scope_pending: dict[str, Any] | None = None  # a scope change waiting for the user
    context: str = ""  # this task's recalled facts and skills; kept out of compaction, not serialised
    conversation_id: str = ""  # the conversation this session belongs to; empty outside conversations
    conversation_owner: str = ""  # the conversation's owner bot id; empty outside conversations
    room_id: str = ""  # the room this session is a turn of; empty outside rooms
    recall_query: str = ""  # what recall searches; empty means the task (a room turn's task is the whole thread)
    pinned_skills: list[str] = field(default_factory=list)  # skill slugs a routine forces; the selector is skipped
    design: str = ""  # the conversation's design system block, appended to the context after the note
    note: str = ""  # the bot's turn note from its last turn in this conversation: read into the context and the route
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def to_dict(self, redact=None) -> dict[str, Any]:
        """`redact` is a function over a nested object, e.g.
        `exclusion.redact_obj`. It is applied to the whole dict so tool
        call arguments and tool outputs are covered, not just content."""
        d = {
            "id": self.id,
            "bot_id": self.bot_id,
            "task": self.task,
            "state": str(self.state),
            "result": self.result,
            "error": self.error,
            "pause_reason": self.pause_reason,
            "steps": self.steps,
            "injected": self.injected,
            "route": self.route,
            "approvals": self.approvals,
            "summary": self.summary,
            "protected": self.protected,
            "compactions": self.compactions,
            "nudges": self.nudges,
            "halts": self.halts,
            "scope_pending": self.scope_pending,
            "room_id": self.room_id,
            "conversation_id": self.conversation_id,
            "conversation_owner": self.conversation_owner,
            "created_at": self.created_at,
            "messages": [m.to_dict() for m in self.messages],
        }
        if redact:
            d, _ = redact(d)
        return d


class TaskLoop:
    def __init__(
        self,
        provider: Provider,
        ledger: Ledger,
        approvals: ApprovalStore,
        tools: ToolRunner,
        pause: PauseController | None = None,
        selector: SkillSelector | None = None,
        recall: Recall | None = None,
        max_steps: int = 50,
        compactor: "Compactor | None" = None,
        supervisor: "LoopSupervisor | None" = None,
        model_for: "Callable[[Bot], tuple[Provider, str | None]] | None" = None,
        write_check: "ShellWriteCheck | None" = None,
        delegate_check: "Callable[[dict, bool, str], tuple[str, str, str] | None] | None" = None,
    ) -> None:
        self.provider = provider
        self.model_for = model_for  # a bot's own provider and model (Edit bot); None or (provider, None): the chat model
        self.ledger = ledger
        self.approvals = approvals
        self.tools = tools
        self.pause = pause or PauseController(ledger, provider)
        self.selector = selector or InjectAllSelector()
        self.recall = recall or NoRecall()
        self.max_steps = max_steps
        self.compactor = compactor
        self.supervisor = supervisor
        self.write_check = write_check  # a shell command that may write outside the allowed folders waits for the user
        # (rule, title, detail) when a hand-off to Claude Code or Codex waits for the user; its second argument is
        # true when a skill rule already asks, so the approval still shows the hand-off; its third is the session, so
        # a routine (nobody to approve) gets no approval at all and the tool refuses it
        self.delegate_check = delegate_check

    async def run(
        self,
        bot: Bot,
        task: str,
        session: Session | None = None,
        *,
        history: list[Message] | None = None,
        protected: dict[str, Any] | None = None,
        turn_extra: str = "",
        images: list[str] | None = None,
        files: int = 0,
    ) -> Session:
        session = session or Session(bot_id=bot.id, task=task)
        # one Langfuse trace per run, grouped by conversation (or room) so the session view replays it
        with tracing.attributes(session_id=session.conversation_id or session.room_id or session.id, tags=[bot.id],
                                trace_name="run-task", metadata={"harness_session": session.id, "room": session.room_id}):
            with tracing.observe("run-task", as_type="agent", input=task, metadata={"bot": bot.id}) as run_obs:
                await self._run(bot, task, session, history, protected, turn_extra, images, files)
                run_obs.update(output=session.result or session.error or str(session.state),
                               level="ERROR" if session.state == SessionState.failed else None,
                               status_message=session.error or None,
                               metadata={"bot": bot.id, "state": str(session.state), "steps": session.steps,
                                         "skills": session.injected, "route": session.route.get("outcome")})
        return session

    async def _run(self, bot: Bot, task: str, session: Session, history: list[Message] | None,
                   protected: dict[str, Any] | None, turn_extra: str = "", images: list[str] | None = None,
                   files: int = 0) -> None:
        if session.pinned_skills:
            by_slug = {s.slug: s for s in bot.skills}
            missing = next((slug for slug in session.pinned_skills if slug not in by_slug), None)
            if missing is not None:
                session.state = SessionState.failed
                session.error = f"Routine skill '{missing}' is not attached to {bot.id}."
                self.ledger.append(Kind.error, bot.id, session.id, error=session.error)
                return
            pinned = [by_slug[slug] for slug in session.pinned_skills]
            # no router runs, so name the servers here: the bot's own, plus any whose tools a pinned skill lists
            groups = list(dict.fromkeys([*bot.mcps, *(g for s in pinned for name in s.access
                                                      if (g := self.tools.group_of(name)))]))
            selection = Selection(pinned, {"skills": [s.slug for s in pinned], "mcps": groups, "outcome": "act",
                                           "scorer": None, "pinned": True})
        else:
            with span("skills.select", selector=getattr(self.selector, "kind", "?")), \
                    tracing.observe("select-skills", input=task, metadata={"selector": getattr(self.selector, "kind", "?")}) as sel:
                # the router reads the turn note too, so a terse follow-up ("do the rest") routes to what the work needs
                route_task = f"{task}\n\n{NOTE_HEADING}\n{session.note}" if session.note else task
                selection = await self.selector.select(bot, route_task, session.id)
                sel.update(output=[s.slug for s in selection.skills], metadata={"outcome": selection.detail.get("outcome")})
        skills = selection.skills
        with span("memory.recall", recall=getattr(self.recall, "kind", "?")), \
                tracing.observe("recall-memory", as_type="retriever", input=session.recall_query or task,
                                metadata={"recall": getattr(self.recall, "kind", "?")}) as rec:
            facts = await self.recall.recall(bot, session.recall_query or task, session.id)
            rec.update(output=facts)
        session.injected = [s.slug for s in skills]
        session.route = selection.detail
        context = bot.context_text(skills, facts)
        if session.note:  # in the context message, not the system prompt, and so kept out of compaction too
            context = "\n\n".join(p for p in (context, f"{NOTE_HEADING}\n{session.note}") if p)
        if session.design:  # likewise in the context message: authoritative, and never compacted away
            context = "\n\n".join(p for p in (context, session.design) if p)
        session.context = context
        session.messages = [
            Message("system", bot.base_prompt()),
            *(history or []),
            # the per-message context is its own user message: a mid-conversation system message could be
            # merged into the top-level system prompt (breaking the cached prefix) or rejected by a chat template
            *([Message("user", context)] if context else []),
            Message("user", f"{task}\n\n{turn_extra}" if turn_extra else task, images=list(images or [])),
        ]
        # a scope check's "reject, just refine" carries the earlier block forward; otherwise the task is the block
        session.protected = ProtectedBlock(**protected).to_dict() if protected else ProtectedBlock(task=task).to_dict()
        self.ledger.append(
            Kind.session, bot.id, session.id, event="start", task=task,
            injected=session.injected, selector=getattr(self.selector, "kind", "?"), recall=getattr(self.recall, "kind", "?"),
            route_outcome=selection.detail.get("outcome"), route_cached=selection.detail.get("cached"), route_fallback=selection.detail.get("fallback"),
            images=len(images or []), files=files, images_sent=bool(images),
        )
        try:
            await self._drive(bot, session, skills)
        except asyncio.CancelledError:
            session.state = SessionState.stopped
            self.ledger.append(Kind.session, bot.id, session.id, event="stopped", by="cancel")
            raise
        except Exception as e:  # noqa: BLE001
            session.state = SessionState.failed
            session.error = f"{type(e).__name__}: {e}"
            self.ledger.append(Kind.error, bot.id, session.id, error=session.error)
        else:
            self.ledger.append(Kind.session, bot.id, session.id, event=str(session.state), steps=session.steps)

    async def _drive(self, bot: Bot, session: Session, skills: list[Skill]) -> None:
        groups, builtins = offered_groups(bot, session.route)
        specs = self.tools.specs(groups, room=bool(session.room_id), caller=(session.bot_id, session.id), builtins=builtins)
        while session.steps < self.max_steps:
            if session.stop_requested:
                session.state = SessionState.stopped
                return
            if self.compactor:
                with span("context.compact"):
                    await self._compact(bot, session)
                if session.stop_requested:
                    session.state = SessionState.stopped
                    return
            try:
                with span("llm.complete", step=session.steps + 1, messages=len(session.messages)) as s:
                    provider, model = self.model_for(bot) if self.model_for else (self.provider, None)
                    completion = await provider.complete(session.messages, tools=specs, model=model, session_id=session.conversation_id or None)
                    s.set_data("model", completion.model)
                    s.set_data("prompt_tokens", completion.usage.prompt_tokens)
                    s.set_data("completion_tokens", completion.usage.completion_tokens)
                    s.set_data("cached_tokens", completion.usage.cached_tokens)
            except ProviderError as e:
                if not await self.pause.hold(session, e):
                    session.state = SessionState.stopped
                    return
                continue  # retry the same step
            if completion.images_dropped:  # refused with pictures: later steps must not send them again
                session.messages = [
                    replace(m, images=[], content=f"{m.content}\n\n{CANT_SEE}" if m.content else CANT_SEE)
                    if m.images else m for m in session.messages]
                self.ledger.append(Kind.upload, bot.id, session.id, event="images_refused")
            session.steps += 1
            msg = completion.message
            session.messages.append(msg)
            self.ledger.append(
                Kind.turn, bot.id, session.id, step=session.steps, role="assistant", content=msg.content,
                tool_calls=[c.name for c in msg.tool_calls], finish=completion.finish_reason, model=completion.model,
                provider=completion.kind, prompt_tokens=completion.usage.prompt_tokens,
                completion_tokens=completion.usage.completion_tokens, cached_tokens=completion.usage.cached_tokens,
                cost=completion.usage.cost,
            )
            if not msg.tool_calls:
                session.result = msg.content
                session.state = SessionState.done
                return
            for call in msg.tool_calls:
                if session.stop_requested:
                    session.state = SessionState.stopped
                    return
                content = await self._run_call(bot, session, skills, call)
                session.messages.append(Message("tool", content, tool_call_id=call.id, name=call.name))
            if self.supervisor:
                with span("loop.supervise", step=session.steps):
                    await self.supervisor.after_step(bot, session)
                if session.stop_requested:
                    session.state = SessionState.stopped
                    return
        session.state = SessionState.failed
        session.error = f"max steps ({self.max_steps}) reached"

    async def _compact(self, bot: Bot, session: Session) -> None:
        block = ProtectedBlock(**session.protected) if session.protected else ProtectedBlock(task=session.task)
        # the context message is not summarized: take it out for the compactor, put it back after the summary
        ctx = Message("user", session.context) if session.context else None
        messages = [m for m in session.messages if not (ctx and m.role == "user" and m.content == session.context)]
        size = context_chars(messages)
        budget = getattr(getattr(self.compactor, "config", None), "budget_chars", None)
        if budget is not None and size <= budget:  # under budget: nothing to do, nothing to trace
            return
        with tracing.observe("compact-context", input={"messages": len(messages), "chars": size},
                             metadata={"budget_chars": budget}) as obs, \
                tracing.llm_step("summarize-context"):
            res = await self.compactor.maybe_compact(messages, block, bot_id=bot.id, session_id=session.id, summary=session.summary)
            obs.update(output={"compacted": res.compacted, "messages": len(res.messages), "chars": context_chars(res.messages)})
        if res.compacted:
            out = res.messages
            if ctx:
                at = next((i for i, m in enumerate(out) if is_summary(m)), len(out) - 1) + 1
                out = out[:at] + [ctx] + out[at:]
            session.messages, session.summary = out, res.summary
            session.compactions += 1

    async def _run_call(self, bot: Bot, session: Session, skills: list[Skill], call) -> str:
        if bot.closed:  # the wall holds at the call, not only at the offer: a model can name a tool it was never shown
            group = self.tools.group_of(call.name)
            if group is None or group not in bot.mcps:
                self.ledger.append(Kind.tool_call, bot.id, session.id, tool=call.name, arguments={}, ok=False,
                                   error=f"{call.name} is not available to this bot", output="")
                return f"error: {call.name} is not available to this bot"
        rule = needs_approval(skills, call)
        detail = f"Matched rule {rule!r}. Approve to run, reject to tell the bot it may not."
        rejected = "The user rejected this action. Do not retry it. Explain what you would have done and continue if possible."
        why: dict[str, Any] = {}
        if not rule and call.name == "shell" and self.write_check is not None:
            from .write_guard import REJECTED

            with span("shell.write_check"):
                verdict = await self.write_check.check(bot.id, session.id, call.arguments.get("command"))
            if verdict.ask:
                rule, detail = verdict.rule, verdict.detail
                rejected = verdict.rejected or REJECTED.format(cwd=self.write_check.cwd)
                why = {"guard": self.write_check.kind, "verdict": verdict.choice, "p_inside": verdict.p_inside,
                       "reads": verdict.reads}
        title = f"{call.name}: {_summary(call.arguments)}"
        if call.name == "delegate" and self.delegate_check is not None:
            from .delegation import REJECTED as DELEGATE_REJECTED

            # a skill rule that matched keeps its name, but the user still sees what is handed off and to whom
            if asked := self.delegate_check(call.arguments, bool(rule), session.id):
                rule, title, detail = rule or asked[0], asked[1], asked[2]
                rejected = DELEGATE_REJECTED
            else:  # nothing to approve, a skill rule included: it runs unasked, or the tool refuses it (a routine)
                rule = None
        if rule:
            req = self.approvals.create(
                bot.id, session.id,
                title=title,
                detail=detail,
                rule=rule, tool=call.name,
            )
            session.approvals.append(req.id)
            session.state = SessionState.waiting_approval
            self.ledger.append(Kind.approval_request, bot.id, session.id, approval_id=req.id, tool=call.name, rule=rule,
                               title=req.title, **why)
            with span("approval.wait", tool=call.name):  # the user's time, not the harness's
                decision = await self.approvals.wait(req.id)
            self.ledger.append(Kind.approval_decision, bot.id, session.id, approval_id=req.id, decision=str(decision.state))
            session.state = SessionState.running
            if decision.state != ApprovalState.approved:
                return rejected
        # logged before the call runs, so a watcher sees a slow search or crawl while it is happening
        self.ledger.append(Kind.tool_start, bot.id, session.id, tool=call.name, arguments=call.arguments, step=session.steps)
        token = current_call.set((bot.id, session.id))
        try:
            with span("tool.run", call.name) as s, \
                    tracing.observe(call.name, as_type="tool", input=call.arguments, metadata={"tool_call_id": call.id}) as obs:
                result = await self.tools.run(call)
                s.set_data("ok", result.ok)
                obs.update(output=result.output if result.ok else result.error,
                           level=None if result.ok else "ERROR", status_message=None if result.ok else result.error)
        finally:
            current_call.reset(token)
        self.ledger.append(
            Kind.tool_call, bot.id, session.id, tool=call.name, arguments=call.arguments,
            ok=result.ok, error=result.error, output=result.output[:4000],
        )
        return result.as_content()


def offered_groups(bot: Bot, route: dict[str, Any] | None) -> tuple[set[str] | None, bool]:
    """(groups, builtins) for `ToolRunner.specs`. A closed bot gets its own `mcps` and no built-in tool, whatever the
    route says (a router winner or a pinned skill's tools never widen it); any other bot gets the route's groups."""
    if bot.closed:
        return set(bot.mcps), False
    return allowed_tool_groups(route), True


def allowed_tool_groups(route: dict[str, Any] | None) -> set[str] | None:
    """Which MCP servers' tools the model is offered: the mcp band's winners
    when the router scored one (none when it deferred); for a routine (pinned
    skills, no router) the servers named in its route; every tool when no
    router ran, so an unbound or scorer-less harness still has its MCPs."""
    route = route or {}
    if route.get("pinned"):
        return set(route.get("mcps") or [])
    bands = route.get("bands") or {}
    if "mcp" not in bands:
        return None
    return set(route.get("mcps") or [])


def _summary(args: dict[str, Any]) -> str:
    for v in args.values():
        if isinstance(v, str):
            return v[:120]
    return str(args)[:120]

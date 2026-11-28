"""Conversations: an owner's running thread of tasks. Each task is one
exchange (the user's text and the final reply or outcome, never tool traffic);
the history a new task starts from is rebuilt from the exchanges. Standalone:
imports nothing from `src.rooms`."""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..context.compaction import fit_fields, notify
from ..context.fit import ContextOverrun, Overrun
from ..context.render import render_transcript
from ..memory.exclusion import exclusion
from ..providers.interface import Message
from .chat_turn import attached

__all__ = [
    "SUMMARY_PREFIX",
    "Exchange",
    "history_chars",
    "Conversation",
    "ConversationStore",
    "Conversations",
    "reply_text",
    "title_of",
]

SUMMARY_PREFIX = "Earlier in this conversation (summary):\n"
TITLE_MAX = 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


EDIT_ACK = "(Noted. I will read the design file again before I change it.)"


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def title_of(text: str) -> str:
    lines = text.strip().splitlines()
    first = " ".join(lines[0].split()) if lines else ""
    return first if len(first) <= TITLE_MAX else first[: TITLE_MAX - 1] + "…"


@dataclass
class Exchange:
    user: str
    session_id: str = ""
    reply: str = ""
    outcome: str = "pending"  # pending|done|failed|stopped|interrupted|routed|edit (a version the user made in the design pane: user is empty, reply says what changed)
    at: str = field(default_factory=_now)
    attachments: list[dict[str, str]] = field(default_factory=list)  # {id, title, path}: files the bot attached; path never leaves the harness
    comments: list[dict[str, str]] = field(default_factory=list)  # the user's design comments, as the chat shows them
    addendum: str = ""  # the comment block the bot read after the user's text; the GUI never gets it
    bot: str = ""  # the bot that answered, when it is not the conversation's owner
    set_aside: bool = False  # a helper's change the user sent back to the owner
    answers_to: str = ""  # the session id of the reply whose question form this message answers
    files: list[dict[str, str]] = field(default_factory=list)  # {id, name, kind, path}: files the user sent with the message; path (vault-relative) never leaves the harness
    context_overrun: bool = False  # the summary model's context was too small while this exchange ran: the chat says so, once


def reply_text(ex: Exchange) -> str:
    text = _reply_text(ex)
    if ex.attachments:  # the bot knows, next turn, which files the user already has
        text += "\n(Attached: " + "; ".join(f"{a['title']} ({Path(a['path']).name})" for a in ex.attachments) + ")"
    if ex.bot and ex.outcome == "done" and ex.attachments:
        # a helper changed the design: the owner reads which bot did, and whether the user sent it back
        text = f"({ex.bot} changed the design) {text}"
        if ex.set_aside:
            text += " (These changes were set aside.)"
    return text


def user_text(ex: Exchange) -> str:
    """The user side of an exchange as later turns read it: the text, the comment block, then one line per file
    the user sent (its name and vault path, never its content)."""
    text = f"{ex.user}\n\n{ex.addendum}" if ex.addendum else ex.user
    if ex.files:
        lines = "\n".join(attached(f.get("name", ""), f.get("path", "")) for f in ex.files)
        text = f"{text}\n\n{lines}" if text else lines
    return text


def edit_note(ex: Exchange) -> str:
    """The user side of an `edit` exchange: what the user changed in the app, as a note to the model."""
    return f"(The user changed the design in the app, not through you: {ex.reply})"


def _reply_text(ex: Exchange) -> str:
    if ex.outcome == "edit":
        return EDIT_ACK
    if ex.outcome in ("done", "routed"):
        return ex.reply
    if ex.outcome == "failed":
        return f"(This task failed: {ex.reply})"
    if ex.outcome == "stopped":
        return "(Stopped before finishing.)"
    if ex.outcome == "interrupted":
        return "(Interrupted: the app closed before this finished.)"
    return "(Still in progress.)"


@dataclass
class Conversation:
    owner: str
    kind: str = "bot"
    id: str = field(default_factory=_new_id)
    title: str = ""
    exchanges: list[Exchange] = field(default_factory=list)
    summary: str = ""
    folded: int = 0  # how many leading exchanges the summary already covers
    design_system: str = ""  # slug of the design system the user picked for this conversation; empty for none
    design_system_source: str = ""  # where the picked system came from ("shipped" or "yours"); empty on older records
    design_folder: str = ""  # a studio bot's conversation: the slug of its own `<vault>/designs/<slug>/`, picked by the harness
    overrun_noticed: bool = False  # an exchange already carries the context notice: no second one
    note: str = ""  # the owner bot's turn note: what its last task found, made and left (src/harness/turn_notes.py)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def to_record(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_record(cls, d: dict[str, Any]) -> "Conversation":
        d = dict(d)
        d["exchanges"] = [Exchange(**e) for e in d.get("exchanges", [])]
        return cls(**d)

    def brief(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "exchanges": len(self.exchanges),
        }

    def history(self, exclude: str = "") -> list[Message]:
        msgs: list[Message] = []
        if self.summary:
            msgs.append(Message("system", SUMMARY_PREFIX + self.summary))
        for ex in self.exchanges[self.folded :]:
            if exclude and ex.session_id == exclude:
                continue
            if ex.outcome == "edit":
                msgs.append(Message("user", edit_note(ex)))
            else:
                msgs.append(Message("user", user_text(ex)))
            msgs.append(Message("assistant", reply_text(ex)))
        return msgs


def history_chars(messages: list[Message]) -> int:
    return sum(len(m.content) for m in messages)


class ConversationStore:
    def __init__(self, dir: Path) -> None:
        self.dir = Path(dir)

    def _path(self, conv_id: str) -> Path:
        return self.dir / f"{conv_id}.json"

    def _write(self, path: Path, data: Any) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def save(self, c: Conversation) -> None:
        # the file passes the exclusion filter's redaction; the object in memory keeps the real text for this run
        record, _ = exclusion.redact_obj(c.to_record())
        self._write(self._path(c.id), record)

    def load_all(self, ledger: Any) -> list[Conversation]:
        out: list[Conversation] = []
        if not self.dir.is_dir():
            return out
        for f in sorted(self.dir.glob("*.json")):
            if f.name == "current.json":
                continue
            try:
                c = Conversation.from_record(json.loads(f.read_text(encoding="utf-8")))
            except Exception as e:  # a bad file must not take the others down
                ledger.append(
                    "error", "", "", where="conversation_store", file=f.name, error=f"{type(e).__name__}: {e}"
                )
                continue
            changed = False
            for ex in c.exchanges:
                if ex.outcome == "pending":
                    ex.outcome = "interrupted"
                    changed = True
            if changed:
                self.save(c)
            out.append(c)
        return out

    def _current_map(self) -> dict[str, str]:
        try:
            d = json.loads((self.dir / "current.json").read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:
            return {}

    def current_for(self, owner: str) -> str | None:
        return self._current_map().get(owner)

    def set_current(self, owner: str, conv_id: str) -> None:
        m = self._current_map()
        m[owner] = conv_id
        self._write(self.dir / "current.json", m)


class Conversations:
    def __init__(self, store: ConversationStore, ledger: Any) -> None:
        self.store = store
        self.ledger = ledger
        self._by_id: dict[str, Conversation] = {}

    def load(self) -> None:
        self._by_id = {c.id: c for c in self.store.load_all(self.ledger)}

    def get(self, conv_id: str) -> Conversation | None:
        return self._by_id.get(conv_id)

    def all(self) -> list[Conversation]:
        """Every conversation, as a list of its own: a caller that awaits while it walks them is not upset by one
        started meanwhile."""
        return list(self._by_id.values())

    def for_owner(self, owner: str) -> list[Conversation]:
        # newest first; ties (same second) fall to the later-created one: reversed insertion order, sort is stable
        mine = [c for c in reversed(self._by_id.values()) if c.owner == owner]
        return sorted(mine, key=lambda c: (c.updated_at, c.created_at), reverse=True)

    def recent(self, owners: set[str], limit: int) -> list[Conversation]:
        """The newest `limit` one-to-one conversations with any of `owners` that have at least one exchange,
        ordered as `for_owner` orders one bot's."""
        mine = [c for c in reversed(self._by_id.values()) if c.kind == "bot" and c.owner in owners and c.exchanges]
        return sorted(mine, key=lambda c: (c.updated_at, c.created_at), reverse=True)[:limit]

    def current(self, owner: str) -> Conversation | None:
        cid = self.store.current_for(owner)
        c = self._by_id.get(cid) if cid else None
        return c if c and c.owner == owner else None

    def start_new(self, owner: str) -> Conversation:
        c = Conversation(owner=owner)
        self._by_id[c.id] = c
        self.store.save(c)
        self.store.set_current(owner, c.id)
        return c

    def select(self, owner: str, conv_id: str) -> Conversation:
        c = self._by_id.get(conv_id)
        if c is None or c.owner != owner:
            raise KeyError(conv_id)
        self.store.set_current(owner, c.id)
        return c

    def _touch(self, c: Conversation) -> None:
        c.updated_at = _now()
        self.store.save(c)

    def begin(
        self, owner: str, user_text: str, session_id: str = "", conv_id: str | None = None,
        comments: list[dict[str, str]] | None = None, addendum: str = "", bot: str = "",
        answers_to: str = "", files: list[dict[str, str]] | None = None,
    ) -> tuple[Conversation, Exchange]:
        c = self.get(conv_id) if conv_id else self.current(owner)
        if conv_id and (c is None or c.owner != owner):
            raise KeyError(conv_id)
        if c is None:
            c = self.start_new(owner)
        if not c.title:
            c.title = title_of(user_text) if user_text.strip() or not files else title_of(files[0].get("name", ""))
        ex = Exchange(user=user_text, session_id=session_id, comments=list(comments or []), addendum=addendum,
                      bot=bot, answers_to=answers_to, files=[dict(f) for f in files or []])
        c.exchanges.append(ex)
        self._touch(c)
        return c, ex

    def finish(self, conv_id: str, session_id: str, outcome: str, reply: str,
               attachments: list[dict[str, str]] | None = None) -> None:
        c = self._by_id.get(conv_id)
        if c is None or not session_id:  # an empty id would match an exchange recorded without a session
            return
        for ex in reversed(c.exchanges):
            if ex.session_id == session_id:
                ex.outcome, ex.reply = outcome, reply
                if attachments:
                    ex.attachments = list(attachments)
                self._touch(c)
                return

    def set_aside(self, conv_id: str, session_id: str) -> None:
        """Mark a helper's exchange as sent back to the owner, so the owner's history says so."""
        c = self._by_id.get(conv_id)
        if c is None or not session_id:
            return
        for ex in c.exchanges:
            if ex.session_id == session_id:
                ex.set_aside = True
                self._touch(c)
                return

    def mark_overrun(self, conv_id: str, session_id: str) -> bool:
        """Flag the exchange of this session as the one that met a summary model with too small a context.
        Once a conversation, and only a one-to-one one. Says whether it marked."""
        c = self._by_id.get(conv_id)
        if c is None or c.kind != "bot" or c.overrun_noticed or not session_id:
            return False
        for ex in c.exchanges:
            if ex.session_id == session_id:
                ex.context_overrun = True
                c.overrun_noticed = True
                self.store.save(c)
                return True
        return False

    def set_note(self, conv_id: str, note: str) -> None:
        """Keep the owner bot's turn note. Saved without touching `updated_at`: a note is not a new message."""
        c = self._by_id.get(conv_id)
        if c is not None:
            c.note = note
            self.store.save(c)

    def set_design_folder(self, conv_id: str, slug: str) -> None:
        """Keep the conversation's design folder. Saved without touching `updated_at`, like the note."""
        c = self._by_id.get(conv_id)
        if c is not None:
            c.design_folder = slug
            self.store.save(c)

    def set_design_system(self, conv_id: str, slug: str, source: str = "") -> None:
        """Keep the picked design system's slug and source (`shipped` or `yours`; both empty to clear). Saved
        without touching `updated_at`: a pick is not a new message."""
        c = self._by_id.get(conv_id)
        if c is not None:
            c.design_system, c.design_system_source = slug, source if slug else ""
            self.store.save(c)

    def attachment(self, conv_id: str, attachment_id: str) -> dict[str, str] | None:
        c = self._by_id.get(conv_id)
        if c is None:
            return None
        return next((a for ex in c.exchanges for a in ex.attachments if a["id"] == attachment_id), None)

    def record(self, owner: str, user_text: str, outcome: str, reply: str) -> Exchange:
        c, ex = self.begin(owner, user_text)
        ex.outcome, ex.reply = outcome, reply
        self._touch(c)
        return ex

    def record_edit(self, conv_id: str, reply: str, attachment: dict[str, str]) -> Exchange:
        """Add a finished exchange for a version the user made directly in the design pane."""
        c = self._by_id.get(conv_id)
        if c is None:
            raise KeyError(conv_id)
        ex = Exchange(user="", session_id="edit-" + _new_id(), reply=reply, outcome="edit", attachments=[dict(attachment)])
        c.exchanges.append(ex)
        self._touch(c)
        return ex

    async def fold_if_needed(
        self,
        conv: Conversation,
        generator: Any,
        *,
        budget_chars: int,
        keep: int,
        on_overrun: Callable[[Overrun], None] | None = None,
        on_success: Callable[[], None] | None = None,
    ) -> bool:
        """When the unfolded exchanges pass `budget_chars`, fold the oldest of
        them into the summary until what stays unfolded is at or under half
        the budget. The last `keep` exchanges stay unfolded only while they
        fit under that half: a preference, not a floor. Folding to half
        leaves room to grow, so a fold (which changes the prompt's prefix
        and costs a cache miss) comes rarely and in a large step, never one
        exchange per message. A pending exchange and everything after it
        is never folded, nor is the newest exchange. One compact and fold
        call per fold, each request sized to the summary model's context.
        The exchanges stay in the record for display; only the history
        drops them. On a generator error the history is kept as it was and
        the failure goes to the ledger; a context too small is also told to
        `on_overrun`, and a fold that worked to `on_success`."""
        sizes = [len(edit_note(ex) if ex.outcome == "edit" else user_text(ex)) + len(reply_text(ex)) for ex in conv.exchanges]
        unfolded = sum(sizes[conv.folded :])
        if unfolded <= budget_chars:
            return False
        stop = len(conv.exchanges) - 1  # the newest stays
        for i in range(conv.folded, stop):
            if conv.exchanges[i].outcome == "pending":
                stop = i
                break
        target = budget_chars // 2
        end = max(conv.folded, min(len(conv.exchanges) - max(0, keep), stop))
        left = unfolded - sum(sizes[conv.folded : end])
        while left > target and end < stop:
            left -= sizes[end]
            end += 1
        if end <= conv.folded:
            return False
        before = history_chars(conv.history())
        old = conv.exchanges[conv.folded : end]
        msgs = [m for ex in old for m in (Message("user", edit_note(ex) if ex.outcome == "edit" else user_text(ex)), Message("assistant", reply_text(ex)))]
        try:
            fitted = await generator.compact_and_fold(render_transcript(msgs), conv.summary, goal=conv.title or None)
        except ContextOverrun as e:  # the summary model's context is too small: kept, and said so
            o = e.overrun
            self.ledger.append(
                "conversation_fold", conv.owner, "", conversation_id=conv.id, ok=False,
                error=f"{type(e).__name__}: {e}", folded=conv.folded, chars_before=before,
                overrun=True, n_ctx=o.n_ctx, n_prompt_tokens=o.n_prompt_tokens,
            )
            notify(on_overrun, o)
            return False
        except Exception as e:  # noqa: BLE001 - a failed fold must not fail the task
            self.ledger.append(
                "conversation_fold", conv.owner, "", conversation_id=conv.id, ok=False,
                error=f"{type(e).__name__}: {e}", folded=conv.folded, chars_before=before,
            )
            return False
        conv.summary, conv.folded = fitted.summary, end
        self.store.save(conv)  # not a touch: folding is not activity, the conversation's order stays
        after = history_chars(conv.history())
        self.ledger.append(
            "conversation_fold", conv.owner, "", conversation_id=conv.id, ok=True,
            folded=end, step=len(old), chars_before=before, chars_after=after, **fit_fields(fitted),
        )
        notify(on_success)
        return True

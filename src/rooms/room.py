"""Rooms.

A room holds two to six bots and carries text only. Tools run in each
bot's own task loop, never in the room. A post is checked by the
exclusion filter before it lands: secrets, approvals, and drafts are
refused. An at-mention forces that bot to take a turn. A reply with no
mention steers the thread back to the author of the replied post. A
bot's turn is a task-loop session whose result is delivered as the
bot's post, bounded by the turn deadline. Every post and turn is in
the ledger with the room id in the session_id slot.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable
from uuid import uuid4

from src.harness.conversations import title_of
from src.harness.turn_notes import TurnNotes, for_another_bot, outcome_of
from src.memory.exclusion import exclusion

from .store import RoomStore
from .turns import RoomError, Turn, TurnTaker

if TYPE_CHECKING:
    from src.harness.allowed_folders import Folders
    from src.harness.bot import Bot
    from src.harness.loop import Session, TaskLoop
    from src.harness.tools import ToolRunner

__all__ = ["Attachment", "Post", "Room", "RoomConfig", "RoomConversation", "RoomError", "RoomService"]

MENTION = re.compile(r"@([A-Za-z0-9_-]+)")
PASS = "PASS"  # a lead review reply equal to this (any case, trailing punctuation allowed) posts nothing
_PASS_REPLY = re.compile(re.escape(PASS) + r"[\s.!,;:]*", re.IGNORECASE)


def is_pass(text: str) -> bool:
    return _PASS_REPLY.fullmatch(text.strip()) is not None


def refusal(text: str, kind: str = "room_post") -> str:
    """Why a room would refuse this text at ingest, or "" when it would take it."""
    verdict = exclusion.check_ingest({"kind": kind, "text": text})
    return "" if verdict else ", ".join(verdict.reasons)


@dataclass
class RoomConfig:
    min_members: int = field(default_factory=lambda: int(os.environ.get("ZEN_ROOM_MIN", "2")))
    max_members: int = field(default_factory=lambda: int(os.environ.get("ZEN_ROOM_MAX", "6")))
    turn_deadline_s: float = field(default_factory=lambda: float(os.environ.get("ZEN_TURN_DEADLINE_S", "600")))
    thread_posts: int = field(default_factory=lambda: int(os.environ.get("ZEN_ROOM_THREAD_POSTS", "20")))
    max_hops: int = field(default_factory=lambda: int(os.environ.get("ZEN_ROOM_MAX_HOPS", "4")))  # bot-to-bot mention chain length
    max_invites: int = field(default_factory=lambda: int(os.environ.get("ZEN_ROOM_MAX_INVITES", "2")))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def workspace_file(cwd: Path, path: str, folders: "Folders | None" = None) -> Path | str:
    """`path` (relative to `cwd`, or absolute) resolved, when it is a regular file inside `cwd` or, given
    `folders`, inside one a bot may read; never inside the harness's own settings, ledger or music folders.
    Otherwise the refusal to hand back to the bot."""
    base = Path(cwd).resolve()
    target = Path(path).expanduser()
    target = (target if target.is_absolute() else base / target).resolve()  # follows symlinks and `..`
    private = [Path(p).resolve() for p in (folders.private if folders else [])]
    if any(target == p or target.is_relative_to(p) for p in private):
        return "Refused: that file is in the harness's own folders, which bots may not reach."
    if not target.is_relative_to(base) and not (folders and folders.readable(target)):
        return ("Refused: that file is outside the folders you may use. Save it in your working folder "
                "or the vault, then attach it.")
    if not target.exists():
        return f"There is no file at {path!r}."
    if not target.is_file():
        return f"Refused: {path!r} is not a file."
    return target


@dataclass
class Attachment:
    id: str
    title: str
    path: str  # on disk; never in API output

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "name": Path(self.path).name}


@dataclass
class Post:
    room_id: str
    nonce: int
    author: str
    text: str
    mentions: list[str] = field(default_factory=list)
    reply_to: str | None = None
    parent: str | None = None  # the post this one answers (a turn's trigger)
    id: str = field(default_factory=lambda: uuid4().hex[:10])
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))
    hop: int = field(default=0, repr=False)  # how many bot-to-bot handoffs led here; 0 for a person
    attachments: list[Attachment] = field(default_factory=list)
    root: str = ""  # id of the user post that started the chain; a user post's root is its own id
    recovery: bool = False
    conversation: str = ""  # id of the room conversation this post belongs to

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "room_id": self.room_id, "nonce": self.nonce, "author": self.author, "text": self.text,
            "mentions": list(self.mentions), "reply_to": self.reply_to, "parent": self.parent, "at": self.at,
            "attachments": [a.to_dict() for a in self.attachments],
        }

    def to_record(self) -> dict[str, Any]:
        d = self.to_dict()
        d["attachments"] = [{"id": a.id, "title": a.title, "path": a.path} for a in self.attachments]
        d.update(hop=self.hop, root=self.root, recovery=self.recovery, conversation=self.conversation)
        return d

    @classmethod
    def from_record(cls, d: dict[str, Any]) -> "Post":
        return cls(
            room_id=d["room_id"], nonce=d["nonce"], author=d["author"], text=d["text"], mentions=list(d.get("mentions", [])),
            reply_to=d.get("reply_to"), parent=d.get("parent"), id=d["id"], at=d["at"], hop=d.get("hop", 0),
            attachments=[Attachment(a["id"], a["title"], a["path"]) for a in d.get("attachments", [])],
            root=d.get("root", ""), recovery=d.get("recovery", False), conversation=d.get("conversation", ""),
        )


@dataclass
class RoomConversation:
    """One conversation in a room: its posts carry this id. The topic the
    orchestrator matches messages against belongs to the conversation."""
    id: str = field(default_factory=lambda: uuid4().hex[:12])
    title: str = ""
    topic: str = ""
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    notes: dict[str, str] = field(default_factory=dict)  # bot id -> its turn note in this conversation; private to that bot

    def to_record(self) -> dict[str, Any]:
        d: dict[str, Any] = {"id": self.id, "title": self.title, "topic": self.topic, "created_at": self.created_at,
                             "updated_at": self.updated_at}
        if self.notes:
            d["notes"] = dict(self.notes)
        return d

    @classmethod
    def from_record(cls, d: dict[str, Any]) -> "RoomConversation":
        return cls(id=d["id"], title=d.get("title", ""), topic=d.get("topic", ""), created_at=d.get("created_at", _now()),
                   updated_at=d.get("updated_at", _now()), notes=dict(d.get("notes") or {}))


def _thread_line(post: Post) -> str:
    """One post as the thread shows it. Attached files are named by their
    title and file name only; the directory never reaches a prompt."""
    line = f"[{post.author}] {post.text}"
    if post.attachments:
        names = "; ".join(f"{a.title} ({Path(a.path).name})" for a in post.attachments)
        line += f" [attached: {names}]"
    return line


@dataclass
class Room:
    name: str
    members: list[str]
    id: str = field(default_factory=lambda: uuid4().hex[:10])
    posts: list[Post] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    open_turn: Turn | None = None
    active_speaker: str | None = None
    running_sessions: dict[str, str] = field(default_factory=dict)  # bot id -> session id
    waiters: list[str] = field(default_factory=list)  # bots queued for the floor, FIFO
    last_nonce: int = 0
    ledger: Any = None
    lead: str = ""
    archived: bool = False
    created_at: str = field(default_factory=_now)
    last_active: str = field(default_factory=_now)
    conversations: list[RoomConversation] = field(default_factory=list)
    conversation_id: str = ""  # the current conversation: new posts land in it, the thread shows only it
    migrated: bool = field(default=False, repr=False, compare=False)  # loaded from a pre-conversations record; not saved

    def __post_init__(self) -> None:
        if not self.conversations:
            self.conversations.append(RoomConversation())
        if self.conversation_by_id(self.conversation_id) is None:
            self.conversation_id = self.conversations[-1].id

    def conversation_by_id(self, conv_id: str) -> RoomConversation | None:
        return next((c for c in self.conversations if c.id == conv_id), None)

    @property
    def conversation(self) -> RoomConversation:
        conv = self.conversation_by_id(self.conversation_id)
        assert conv is not None  # __post_init__ and select_conversation keep the current id valid
        return conv

    @property
    def topic(self) -> str:
        """The current conversation's topic."""
        return self.conversation.topic

    @topic.setter
    def topic(self, value: str) -> None:
        self.conversation.topic = value

    def conversation_posts(self, conv_id: str) -> list[Post]:
        return [p for p in self.posts if p.conversation == conv_id]

    def current_posts(self) -> list[Post]:
        return self.conversation_posts(self.conversation_id)

    def new_conversation(self) -> RoomConversation:
        """Start a fresh conversation, with no posts and no topic, and make it current."""
        conv = RoomConversation()
        self.conversations.append(conv)
        self.conversation_id = conv.id
        return conv

    def select_conversation(self, conv_id: str) -> RoomConversation:
        """Make an earlier conversation current again: its posts show and new posts continue it."""
        conv = self.conversation_by_id(conv_id)
        if conv is None:
            raise RoomError(f"no conversation {conv_id!r} in this room")
        self.conversation_id = conv.id
        return conv

    def post_by_id(self, post_id: str) -> Post | None:
        return next((p for p in self.posts if p.id == post_id), None)

    def add_post(self, *, author: str, text: str, nonce: int, reply_to: str | None = None, parent: str | None = None,
                 kind: str = "room_post", conversation: str = "") -> Post:
        """The one way a post lands. Refuses anything the exclusion
        filter would refuse at ingest: secrets, registered values,
        approval kinds and fields. The post joins `conversation` (a turn
        passes the one it started in), else the current conversation."""
        refused = refusal(text, kind)
        if refused:
            raise RoomError("a room carries text only; refused: " + refused)
        if reply_to is not None and self.post_by_id(reply_to) is None:
            raise RoomError(f"reply_to {reply_to!r} is not a post in this room")
        mentions = [m for m in dict.fromkeys(MENTION.findall(text)) if m in self.members]
        conv = self.conversation_by_id(conversation) or self.conversation
        post = Post(room_id=self.id, nonce=nonce, author=author, text=text, mentions=mentions, reply_to=reply_to, parent=parent,
                    conversation=conv.id)
        self.posts.append(post)
        self.last_active = conv.updated_at = post.at
        if author == "user" and not conv.title:
            conv.title = title_of(text)
        if self.ledger is not None:
            self.ledger.append("room_post", author, self.id, post_id=post.id, nonce=nonce, author=author, mentions=mentions,
                               reply_to=reply_to, parent=parent, chars=len(text))
        return post

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "members": list(self.members), "active_speaker": self.active_speaker,
            "open_turn": self.open_turn.to_dict() if self.open_turn else None, "last_nonce": self.last_nonce,
            "running_sessions": dict(self.running_sessions), "posts": [p.to_dict() for p in self.current_posts()],
            "lead": self.lead, "topic": self.topic, "archived": self.archived,
            "created_at": self.created_at, "last_active": self.last_active,
            "last_session": self.turns[-1].task_session if self.turns else None,
            "conversation_id": self.conversation_id,
            "conversations": [
                {"id": c.id, "title": c.title, "created_at": c.created_at, "updated_at": c.updated_at,
                 "posts": len(self.conversation_posts(c.id))}
                for _, c in sorted(enumerate(self.conversations), key=lambda ic: (ic[1].updated_at, ic[0]), reverse=True)
            ],  # newest first; within the same second, the later one first
        }

    def to_record(self) -> dict[str, Any]:
        """The persistence form: posts with attachment paths; no turns, no open turn."""
        return {
            "id": self.id, "name": self.name, "members": list(self.members), "last_nonce": self.last_nonce,
            "lead": self.lead, "archived": self.archived,
            "created_at": self.created_at, "last_active": self.last_active,
            "conversation_id": self.conversation_id,
            "conversations": [c.to_record() for c in self.conversations],
            "posts": [p.to_record() for p in self.posts],
        }

    @classmethod
    def from_record(cls, d: dict[str, Any], ledger: Any) -> "Room":
        posts = [Post.from_record(p) for p in d.get("posts", [])]
        created_at, last_active = d.get("created_at", _now()), d.get("last_active", _now())
        if "conversations" in d:
            conversations = [RoomConversation.from_record(c) for c in d["conversations"]]
        else:  # a room saved before conversations: all its posts become its first conversation
            first_ask = next((p.text for p in posts if p.author == "user"), "")
            conv = RoomConversation(title=title_of(first_ask) if first_ask else "", topic=d.get("topic", ""),
                                    created_at=created_at, updated_at=last_active)
            conversations = [conv]
            for p in posts:
                p.conversation = conv.id
        return cls(
            name=d["name"], members=list(d["members"]), id=d["id"], posts=posts,
            last_nonce=d.get("last_nonce", 0), ledger=ledger, lead=d.get("lead", ""),
            archived=d.get("archived", False), created_at=created_at, last_active=last_active,
            conversations=conversations, conversation_id=d.get("conversation_id", ""),
            migrated="conversations" not in d,
        )


class RoomService:
    def __init__(self, ledger: Any, loop: "TaskLoop", bots: dict[str, "Bot"], config: RoomConfig | None = None,
                 sessions: dict[str, "Session"] | None = None, tasks: dict[str, asyncio.Task[Any]] | None = None,
                 store: RoomStore | None = None) -> None:
        self.ledger = ledger
        self.store = store
        self.loop = loop
        self.bots = bots
        self.config = config or RoomConfig()
        self.sessions = sessions if sessions is not None else {}
        self.tasks = tasks if tasks is not None else {}  # session id -> task, shared with the harness so stop works
        self.turns = TurnTaker(ledger, self.config.turn_deadline_s)
        self.rooms: dict[str, Room] = {}
        self._tasks: dict[str, set[asyncio.Task[Any]]] = {}
        self._floor: dict[str, asyncio.Event] = {}  # room id -> set when the floor frees
        self.on_lead_post: Callable[[Room, Post], Awaitable[None]] | None = None  # after the lead posts from a review turn
        self.turn_of: dict[str, tuple[str, str]] = {}  # session id -> (room id, root) while a room turn's session runs
        self.turn_hop: dict[str, int] = {}  # session id -> the hop the post that turn delivers will carry
        self._attachments: dict[str, list[Attachment]] = {}  # session id -> files queued for the post the turn delivers
        # attach_file's path check; the harness sets its own (the workspace plus the allowed folders)
        self.attachable: Callable[[str], Path | str] | None = None
        # the same check for a named bot, which may resolve the path its own way (a studio bot's designs)
        self.attach_target: Callable[[str, str], Path | str] | None = None
        self._invites: dict[tuple[str, str], int] = {}  # (room id, root) -> invites so far; in memory only
        self.notes: TurnNotes | None = None  # each bot's turn notes; the harness sets it, None writes and reads none
        # (room id, trigger post id, failed bot) -> files a turn attached before it ended without a post; the lead's
        # failure post carries them
        self._carried: dict[tuple[str, str, str], list[Attachment]] = {}

    # -- membership ---------------------------------------------------------

    @staticmethod
    def joinable(bot) -> bool:
        """A closed bot (it reads untrusted pages) works in its own conversation and routine only: what it
        writes in a room would be read by bots with shell and browser."""
        return not getattr(bot, "closed", False)

    def _check_members(self, members: list[str]) -> list[str]:
        members = list(dict.fromkeys(members))
        c = self.config
        if not c.min_members <= len(members) <= c.max_members:
            raise RoomError(f"a room holds {c.min_members} to {c.max_members} bots; got {len(members)}")
        unknown = [m for m in members if m not in self.bots]
        if unknown:
            raise RoomError(f"unknown bots: {unknown}")
        closed = [m for m in members if not self.joinable(self.bots[m])]
        if closed:
            raise RoomError(f"closed bots cannot join a room: {closed}")
        return members

    def _persist(self, room: Room) -> None:
        if room.id not in self.rooms:  # deleted: a late write must not bring it back
            return
        if self.store is not None:
            self.store.save(room)

    def load(self) -> int:
        """Bring saved rooms back at startup; returns how many."""
        if self.store is None:
            return 0
        rooms = self.store.load_all(self.ledger)
        for r in rooms:
            self.rooms[r.id] = r
            if r.migrated:  # save the new shape now, or the first conversation's id changes on every load
                try:
                    self.store.save(r)
                    r.migrated = False
                except Exception as e:  # noqa: BLE001 - a failed write must not stop the other rooms loading
                    self.ledger.append("error", "", "", where="room_migrate", room=r.id, error=f"{type(e).__name__}: {e}")
        return len(rooms)

    def create(self, name: str, members: list[str], *, lead: str = "", topic: str = "") -> Room:
        room = Room(name=name, members=self._check_members(members), ledger=self.ledger, lead=lead)
        room.topic = topic  # the topic of the room's first conversation
        self.rooms[room.id] = room
        self._persist(room)
        self.ledger.append("room", "user", room.id, event="created", name=name, members=room.members)
        return room

    def set_members(self, room_id: str, members: list[str]) -> Room:
        room = self.get(room_id)
        room.members = self._check_members(members)
        self.ledger.append("room", "user", room.id, event="members", members=room.members)
        self._persist(room)
        return room

    def new_conversation(self, room_id: str) -> RoomConversation:
        """Start a fresh conversation in the room, same members, no posts, no topic.
        A turn still running keeps posting into the conversation it started in."""
        room = self.get(room_id)
        conv = room.new_conversation()
        self.ledger.append("room", "user", room.id, event="conversation", conversation=conv.id, new=True)
        self._persist(room)
        return conv

    def select_conversation(self, room_id: str, conv_id: str) -> RoomConversation:
        """Resume an earlier conversation: it becomes current again."""
        room = self.get(room_id)
        conv = room.select_conversation(conv_id)
        self.ledger.append("room", "user", room.id, event="conversation", conversation=conv.id, new=False)
        self._persist(room)
        return conv

    def archive(self, room_id: str, archived: bool) -> Room:
        room = self.get(room_id)
        room.archived = archived
        self.ledger.append("room", "user", room.id, event="archived", archived=archived)
        self._persist(room)
        return room

    def delete(self, room_id: str) -> None:
        room = self.get(room_id)
        if room.open_turn is not None or room.waiters:
            raise RoomError("room is busy")
        del self.rooms[room_id]
        if self.store is not None:
            self.store.delete(room_id)
        self.ledger.append("room", "user", room_id, event="deleted")

    def get(self, room_id: str) -> Room:
        try:
            return self.rooms[room_id]
        except KeyError:
            raise RoomError(f"no room {room_id!r}") from None

    def list(self) -> list[Room]:
        return list(self.rooms.values())

    # -- room-only tools -------------------------------------------------------

    def register_tools(self, tools: "ToolRunner", elsewhere: Callable[[str, str, str], str] | None = None) -> None:
        """Offer `invite_to_room` to room turns only and `attach_file` to every session. Each finds
        its room through the calling session's entry in `turn_of`; `attach_file` from a session that
        is not a room turn goes to `elsewhere(session_id, path, title)` (the harness's one-to-one
        attachments) when given."""
        from src.harness.tools import current_call  # local import keeps rooms free of a harness import cycle

        def invite_to_room(bot_id: str, job: str) -> str:
            caller, session_id = current_call.get()
            return self._invite(caller, session_id, bot_id, job)

        def attach_file(path: str, title: str = "") -> str:
            caller, session_id = current_call.get()
            if session_id not in self.turn_of and elsewhere is not None:
                return elsewhere(session_id, path, title)
            return self._attach(tools.cwd, session_id, path, title, bot_id=caller)

        tools.register(
            "invite_to_room",
            "Lead only: add a bot that is not in this room to it. Then @mention it in your reply with its specific job.",
            {"type": "object", "properties": {
                "bot_id": {"type": "string", "description": "id of the bot to add, from the list of bots not in this room"},
                "job": {"type": "string", "description": "what the bot is being brought in to do"},
            }, "required": ["bot_id", "job"]},
            invite_to_room, room_only=True,
        )
        tools.register(
            "attach_file",
            "Attach a file you made (a diagram, a page, a PDF, an image) to your reply, so the user sees it "
            "in the conversation. When you make a file the user should look at, save it in your working folder "
            "or the vault and call this with its path; do not copy it anywhere else first.",
            {"type": "object", "properties": {
                "path": {"type": "string", "description": "the file: relative to your working folder, or a full path in the vault or an allowed folder"},
                "title": {"type": "string", "description": "short label for the file; defaults to its name"},
            }, "required": ["path"]},
            attach_file,
        )

    def _invite(self, caller: str, session_id: str, bot_id: str, job: str) -> str:
        where = self.turn_of.get(session_id)
        room = self.rooms.get(where[0]) if where else None
        if where is None or room is None:
            return "invite_to_room works only during a room turn."
        root = where[1]
        if caller != room.lead:
            return "Only the room's lead can invite bots."
        if self.turn_hop.get(session_id, 0) >= self.config.max_hops:  # its post could not mention the invitee
            return "This conversation has used up its hand-offs for now; post a short note instead."
        if bot_id not in self.bots:
            return f"There is no bot @{bot_id}. Pick one from the list of bots not in this room."
        if not self.joinable(self.bots[bot_id]):
            return f"@{bot_id} is a closed bot and cannot join a room; it works only in its own conversation."
        if bot_id in room.members:
            return f"@{bot_id} is already in the room; @mention it instead."
        if len(room.members) >= self.config.max_members:
            return f"The room is full ({self.config.max_members} bots); answer with the members it has."
        key = (room.id, root)
        if self._invites.get(key, 0) >= self.config.max_invites:
            return f"No more invites for this request (limit {self.config.max_invites}); work with the members the room has."
        room.members.append(bot_id)
        self._invites[key] = self._invites.get(key, 0) + 1
        self._persist(room)
        self.ledger.append("room", caller, room.id, event="invited", bot=bot_id, job=job, root=root)
        return f"Added @{bot_id}. Mention @{bot_id} with the job in your reply to start it."

    def _attach(self, cwd: Path, session_id: str, path: str, title: str, bot_id: str = "") -> str:
        if session_id not in self.turn_of:
            return "attach_file works only during a room turn."
        title = title or Path(path).name  # the file's own name: a design is attached as its numbered copy
        if self.attach_target is not None:
            target = self.attach_target(bot_id, path)
        else:
            target = self.attachable(path) if self.attachable else workspace_file(cwd, path)
        if isinstance(target, str):
            return target
        att = Attachment(uuid4().hex[:10], title or target.name, str(target))
        self._attachments.setdefault(session_id, []).append(att)
        return f"Attached {att.title!r}; it will show on the post this turn delivers."

    # -- posting --------------------------------------------------------------

    async def post(self, room_id: str, author: str, text: str, reply_to: str | None = None, kind: str = "room_post",
                   schedule: bool = True) -> Post:
        room = self.get(room_id)
        post = room.add_post(author=author, text=text, nonce=0, reply_to=reply_to, kind=kind)
        post.root = post.id
        self._persist(room)
        if not schedule:
            return post
        targets = list(post.mentions)
        if not targets and reply_to is not None:
            replied = room.post_by_id(reply_to)
            if replied is not None and replied.author in room.members and replied.author != author:
                targets = [replied.author]
        if not targets and reply_to is None and room.lead and author == "user":
            targets = [room.lead]  # an unaddressed user post goes to the lead
        for bot_id in targets:
            self._schedule(room, bot_id, post)
        return post

    def _schedule(self, room: Room, bot_id: str, trigger: Post, mode: str = "act", note: str = "", *,
                  failed: str = "") -> None:
        task = asyncio.create_task(self._take_turn(room, bot_id, trigger, mode, note, failed=failed))
        self._tasks.setdefault(room.id, set()).add(task)
        task.add_done_callback(lambda t: self._tasks.get(room.id, set()).discard(t))

    async def settle(self, room_id: str) -> None:
        """Wait until every scheduled turn in the room has finished."""
        while self._tasks.get(room_id):
            await asyncio.gather(*list(self._tasks[room_id]), return_exceptions=True)

    def thread_text(self, room: Room, bot_id: str, trigger: Post, mode: str = "act", note: str = "", *,
                    failed: str = "") -> str:
        """The turn's task. Modes, for the lead of a led room: "act" (take a
        user's message), "review" (look over a member's post once),
        "failure" (a member's turn ended without a post; `failed` names it,
        `note` is why). Other bots and rooms without a lead ignore the mode."""
        bot = self.bots.get(bot_id)  # a member whose bot is gone is named by its id
        # the conversation the trigger belongs to: the current one, unless the user switched while this turn waited
        recent = room.conversation_posts(trigger.conversation or room.conversation_id)[-self.config.thread_posts:]
        lines = [_thread_line(p) for p in recent]
        head = (
            f"You are {bot.name if bot else bot_id} (@{bot_id}) in the room {room.name!r} with {', '.join('@' + m for m in room.members)}.\n"
            "Thread, latest last:\n" + "\n".join(lines) + "\n\n"
        )
        asked = f"@{trigger.author} asked you to act with: {trigger.text}\n"
        tail = (
            "Reply with exactly the text you want to post in the room. Text only. "
            "Never include keys, passwords, tokens, approvals, or drafts awaiting approval."
        )
        if not room.lead:
            return head + asked + tail
        if bot_id != room.lead:
            return head + asked + (
                "ZenBot brought you into this room with a specific job. Do it now; don't ask the user questions first, "
                "and don't offer to do it later. When the job asks for a diagram, chart or other visual, make it as a "
                "file the user can open (a self-contained HTML page works well), save it in your working folder, and "
                "call `attach_file` with its path. If you made any other file the user should open, attach it the same way.\n"
            ) + tail
        roster = [f"@{bid} — {b.name}: {b.role}" for bid, b in self.bots.items() if bid not in room.members and self.joinable(b)]
        others = "Bots not in this room:\n" + ("\n".join(roster) if roster else "(none)") + "\n"
        spent = trigger.hop + 1 >= self.config.max_hops  # the post this turn delivers cannot hand the floor on
        used_up = "The hand-off chain is used up for this message, so don't invite or mention anyone"
        if mode == "review" and spent:
            body = (
                f"Review @{trigger.author}'s post for the user: {trigger.text}\n"
                f"{used_up}: post a short focus note that does not restate the post, or reply exactly {PASS}.\n"
            )
        elif mode == "review":
            body = (
                f"Review @{trigger.author}'s post for the user: {trigger.text}\n" + others +
                "Either invite a bot with `invite_to_room` and @mention it with a specific job, "
                "or post a one-to-three sentence focus note that does not restate the post, "
                f"or reply exactly {PASS}.\n"
            )
        elif mode == "failure" and spent:
            body = f"@{failed}'s turn ended without a post ({note}). Tell the user plainly. {used_up}: answer yourself.\n"
        elif mode == "failure":
            body = (
                f"@{failed}'s turn ended without a post ({note}). Tell the user plainly, "
                "and either @mention it once to retry or answer yourself.\n"
            )
        elif spent:
            body = asked + f"{used_up}: answer briefly yourself.\n"
        else:
            body = asked + others + (
                "Act on the user's message: @mention the member who should take it, "
                "invite a bot with `invite_to_room` and @mention it, or answer briefly yourself.\n"
            )
        return head + body + tail

    def _wake(self, room: Room) -> None:
        ev = self._floor.setdefault(room.id, asyncio.Event())
        ev.set()
        ev.clear()

    async def _wait_for_floor(self, room: Room, bot_id: str) -> Turn:
        """FIFO: first mentioned, first to speak. No cap beyond membership;
        every turn ahead in the queue is itself deadline bounded."""
        room.waiters.append(bot_id)
        try:
            while True:
                self.turns.expire_overdue(room)
                if room.open_turn is None and room.waiters[0] == bot_id:
                    return self.turns.request(room, bot_id)
                ev = self._floor.setdefault(room.id, asyncio.Event())
                try:
                    await asyncio.wait_for(ev.wait(), 0.05)
                except (asyncio.TimeoutError, TimeoutError):
                    pass
        finally:
            room.waiters.remove(bot_id)

    async def _run_bounded(self, session: "Session", bot: "Bot", turn: Turn) -> "Session | None":
        """Run the session; only time spent running counts against the
        turn deadline. Paused or waiting-for-approval time stretches the
        deadline instead (on no or timeout, stay paused: never auto deny)."""
        from src.harness.loop import SessionState

        task = asyncio.create_task(self.loop.run(bot, session.task, session))
        self.tasks[session.id] = task
        budget = self.config.turn_deadline_s
        active, last = 0.0, time.monotonic()
        try:
            while not task.done():
                await asyncio.wait({task}, timeout=0.02)
                now = time.monotonic()
                if session.state not in (SessionState.paused, SessionState.waiting_approval):
                    active += now - last
                last = now
                turn.deadline = now + max(0.0, budget - active)
                if active > budget:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    return None
            return task.result()
        finally:
            self.tasks.pop(session.id, None)

    def _recover(self, room: Room, bot_id: str, trigger: Post, reason: str, *, note: str = "",
                 attachments: list[Attachment] | None = None) -> None:
        """A member's turn ended without a post: the lead gets one turn to
        tell the user and retry or answer. Never for the lead itself, never
        for a turn that was already a retry, and only below the hop cap.
        The lead sees what the turn left (its note's Made / Failed / Left
        lines, file names only), and its post carries the files the turn
        attached before it ended."""
        if room.lead and bot_id != room.lead and not trigger.recovery and trigger.hop + 1 < self.config.max_hops:
            left = for_another_bot(note) if note else ""
            if left:
                reason += "; what it left: " + " ".join(left.splitlines())
            if attachments:
                self._carried[(room.id, trigger.id, bot_id)] = list(attachments)
                reason += f"; the {len(attachments)} file(s) it attached will show on your post"
            self._schedule(room, room.lead, trigger, "failure", reason, failed=bot_id)

    async def _write_note(self, room: Room, conv_id: str, bot: "Bot", session: "Session", previous: str,
                          outcome: str) -> str:
        """Write the bot's turn note into the turn's conversation and save the room. Returns the note in force."""
        rc = room.conversation_by_id(conv_id)
        if self.notes is None or rc is None:
            return previous

        def save(note: str) -> None:
            rc.notes[bot.id] = note
            self._persist(room)

        return await self.notes.write(bot, session, outcome=outcome, previous=previous, save=save)

    async def _lead_posted(self, room: Room, post: Post) -> None:
        if self.on_lead_post is None:
            return
        try:
            await self.on_lead_post(room, post)
        except Exception as e:  # a broken hook must not break the room
            self.ledger.append("error", room.lead, room.id, where="on_lead_post", post_id=post.id,
                               error=f"{type(e).__name__}: {e}"[:300])

    async def _take_turn(self, room: Room, bot_id: str, trigger: Post, mode: str = "act", note: str = "", *,
                         failed: str = "") -> None:
        from src.harness.loop import Session, SessionState  # local import keeps rooms free of a harness import cycle

        if bot_id not in room.members:
            self.ledger.append("room_turn", bot_id, room.id, event="skipped", speaker=bot_id, reason="not a member")
            return
        if bot_id not in self.bots:  # a member whose bot was removed since (e.g. across a restart)
            self.ledger.append("room_turn", bot_id, room.id, event="skipped", speaker=bot_id, reason="unknown bot")
            return
        if not self.joinable(self.bots[bot_id]):  # closed after it joined: a saved room, or its prompt edited since
            self.ledger.append("room_turn", bot_id, room.id, event="skipped", speaker=bot_id, reason="closed bot")
            return
        conv = trigger.conversation or room.conversation_id  # a switch mid-turn does not move the turn's post
        turn = await self._wait_for_floor(room, bot_id)
        session: Session | None = None
        attachments: list[Attachment] = []
        # the lead's failure turn carries the files the failed turn attached
        carried = self._carried.pop((room.id, trigger.id, failed), []) if mode == "failure" and failed else []
        rc = room.conversation_by_id(conv)
        previous = rc.notes.get(bot_id, "") if self.notes is not None and rc is not None else ""
        try:
            bot = self.bots[bot_id]
            task = self.thread_text(room, bot_id, trigger, mode, note, failed=failed)
            # recall searches the post this turn answers, not the thread and instructions around it
            session = Session(bot_id=bot_id, task=task, room_id=room.id, recall_query=trigger.text)
            session.conversation_id = conv
            session.note = previous
            turn.task_session = session.id
            self.sessions[session.id] = session
            room.running_sessions[bot_id] = session.id
            self.turn_of[session.id] = (room.id, trigger.root or trigger.id)
            self.turn_hop[session.id] = trigger.hop + 1
            result = await self._run_bounded(session, bot, turn)
        except BaseException as e:  # a stop's cancel, or a crash: the floor must not stay held
            if room.open_turn is not None and room.open_turn.nonce == turn.nonce:
                self.turns.release(room, turn.nonce, reason=f"{type(e).__name__}")
            self._wake(room)
            if session is not None and isinstance(e, asyncio.CancelledError):  # a stop still leaves a note
                await self._write_note(room, conv, self.bots[bot_id], session, previous, "stopped")
            raise
        finally:
            if session is not None:
                room.running_sessions.pop(bot_id, None)
                self.turn_of.pop(session.id, None)
                self.turn_hop.pop(session.id, None)
                attachments = self._attachments.pop(session.id, [])  # a delivered post keeps them, or the lead's
        # written before any recovery is scheduled, so the lead's failure turn and a retry see it
        assert session is not None  # the try above either made it or raised
        noted = await self._write_note(room, conv, bot, session, previous,
                                       outcome_of(session, self.config.turn_deadline_s if result is None else None))
        reason = ""
        post: Post | None = None
        try:
            if result is None:
                reason = "deadline"
            elif result.state != SessionState.done or not result.result:
                reason = f"session {result.state}: {result.error or 'no text'}"
            elif bot_id == room.lead and mode == "review" and is_pass(result.result):
                self.turns.release(room, turn.nonce, reason="pass")  # the lead has nothing to add
                return
            else:
                try:
                    post = self.turns.deliver(room, turn.nonce, result.result.strip(), parent_post=trigger.id,
                                              conversation=conv)
                except RoomError as e:  # the bot tried to post something the room refuses
                    reason = f"refused: {e}"
            if post is None:
                self.turns.release(room, turn.nonce, reason=reason)
                self._recover(room, bot_id, trigger, reason, note=noted, attachments=attachments)
                return
            post.hop = trigger.hop + 1
            post.root = trigger.root or trigger.id
            post.recovery = bot_id == room.lead and mode == "failure"
            post.attachments = attachments + carried
            self._persist(room)
            if post.hop < self.config.max_hops:  # a bot may hand the floor on, a bounded number of times
                for m in post.mentions:
                    if m != bot_id:
                        self._schedule(room, m, post)
                if room.lead and bot_id != room.lead and room.lead not in post.mentions:
                    self._schedule(room, room.lead, post, "review")  # the lead looks over each member post once
        finally:
            self._wake(room)
        if post is not None and bot_id == room.lead and mode == "review":
            await self._lead_posted(room, post)

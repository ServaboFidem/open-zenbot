"""Dreaming: learning lasting facts from the user's own messages while the computer is idle.

What a dream reads from a conversation is the user's own messages, and only those: nothing but
`Exchange.user` is ever returned here, not the reply, the addendum, comments, attachments, the summary
or the note. `Dreamer` runs a pass over the quiet conversations, one at a time, under the rules of
"When it runs" in the design; a model call in flight may be cancelled, a store write never is."""

from __future__ import annotations

import asyncio
import json
import threading
import time
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..providers.interface import failure_kind
from ..providers.store import ProviderStoreError
from ..vault_settings import settings_dir
from .exclusion import ExclusionError
from .extract_queue import remember_shared
from .graph import SHARED, Tier
from .ledger import Kind
from .profile import OwnerUnknown

if TYPE_CHECKING:  # the harness imports this package, so the import is for types only
    from ..generation.generator import Generator
    from ..harness.conversations import Conversation
    from .extract_queue import ExtractQueue
    from .profile import ProfileMemory

MESSAGE_MAX = 4000
BATCH_MAX = 12000
QUIET_S = 1800
FORM_PREFIX = "Form answers ("
IDLE_MIN, IDLE_MAX, IDLE_DEFAULT = 1, 120, 5
RETRY_WAITS = (600.0, 3600.0)  # seconds to wait after the 1st and 2nd failed attempt
MAX_ATTEMPTS = 3
FACTS_MAX = 8
FACT_MAX = 300
REPORT_FRESH_S = 150.0
UNREACHABLE_WAIT_S = 600.0
TICK_S = 30.0  # how often the loop looks
GUARD_S = 2.0  # how often a model call in flight re-checks the rules
BASE_STORES = (("personal", "facts about the person"), ("world", "general knowledge"))


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


def _whole(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _mark_of(v: Any) -> Mark:
    """A stored mark, every value of the right type, else TypeError."""
    if not isinstance(v, dict):
        raise TypeError("a mark is not an object")
    m = Mark(**v)
    if not (_whole(m.read) and _whole(m.attempts) and _whole(m.seen)
            and isinstance(m.not_before, (int, float)) and not isinstance(m.not_before, bool)):
        raise TypeError("a mark holds a value of the wrong type")
    m.not_before = float(m.not_before)
    return m


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
            marks = {k: _mark_of(v) for k, v in d["conversations"].items()}
            learned, at = d["learned"], d["last_dream_at"]
            if not _whole(learned) or not isinstance(at, str):
                raise TypeError("learned or last_dream_at has the wrong type")
        except (ValueError, KeyError, TypeError, AttributeError):
            try:
                self.path.replace(self.path.with_name(self.path.name + ".bad"))  # keep the damaged file recoverable
            except OSError:
                pass  # could not move it: start empty all the same; the next save replaces it
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

    def reset_attempts(self) -> None:
        """Every conversation may be tried again at once: failed attempts and retry waits cleared. `read`, `seen`,
        the count learned and the time of the last dream stay."""
        with self._lock:
            stale = [m for m in self._marks.values() if m.attempts or m.not_before]
            for m in stale:
                m.attempts, m.not_before = 0, 0.0
            if stale:
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


SYSTEMIC = ("unreachable", "provider", "store")  # no attempt counted: the pass ends and automatic dreaming pauses


def error_kind(exc: BaseException) -> str:
    """The kind of a failed model call (`failure_kind` reads the provider's errors, so no client is imported here).
    Systemic, counting nothing (SYSTEMIC): `unreachable` when the provider could not be reached at all (a local model
    that is not running: a refused connection), and `provider` for a rejected key (401, 402, 403), a rate limit (429)
    or a server error (5xx). Everything else is `model` and counts an attempt against the conversation: a timeout (a
    model too slow for one batch), an answer that cannot be used (`ValueError`, or an API answer with no `choices`),
    a client error about this request (400, 404, 413, 422...: one conversation the model can never take must be
    skipped after MAX_ATTEMPTS, not hold every older one back for good), and any failure that cannot be told, since
    the per-conversation rule is bounded and the systemic one is not."""
    kind = failure_kind(exc)
    if kind == "network":
        return "unreachable"
    if kind in ("auth", "rate_limit", "server"):
        return "provider"
    return "model"


class Interrupted(Exception):
    """A rule stopped holding (or the harness is closing): the pass ends and nothing counts as a failure."""


class _Failed(Exception):
    """A conversation's attempt failed: `kind` is `model`, `unreachable`, `provider` or `store`; `name` the
    exception's class."""

    def __init__(self, kind: str, exc: BaseException) -> None:
        super().__init__(kind)
        self.kind, self.name = kind, type(exc).__name__


def _epoch(stamp: str) -> float | None:
    try:
        t = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.timestamp()


def _at(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _empty() -> dict[str, int]:
    return {"conversations": 0, "facts": 0, "dropped": 0, "refused": 0}


class Dreamer:
    """One dreaming pass at a time over the quiet one-to-one conversations, newest first.

    The rules (`_may_go`) are checked before each conversation, each model call and each write, and every
    GUARD_S while a model call is in flight: when one fails the call is cancelled, the conversation's mark
    stays and the pass ends. A store write is awaited directly, so it always finishes."""

    def __init__(self, *, vault_root: Path, marks: DreamMarks, ledger: Any,
                 conversations: Callable[[], list[Conversation]],
                 generator: Callable[[], Generator | None],
                 profile: Callable[[], ProfileMemory | None], queue: Callable[[], ExtractQueue | None],
                 busy: Callable[[], bool], clock: Callable[[], float] = time.time) -> None:
        self.vault_root = Path(vault_root)
        self.marks = marks
        self.ledger = ledger
        self._conversations = conversations
        self._generator = generator
        self._profile = profile
        self._queue = queue
        self._busy = busy
        self._clock = clock
        self.settings = DreamSettings.load(self.vault_root)
        self._report: tuple[float, float] | None = None  # (when it arrived, idle seconds it said)
        self._paused_until = 0.0
        self._error = ""  # the last pass's error kind: model, unreachable, provider, store or empty
        self._running = False
        self._closing = False
        self._task: asyncio.Task[Any] | None = None
        self._call: asyncio.Future[Any] | None = None

    # -- settings and the rules -------------------------------------------------------------------------

    def set_settings(self, s: DreamSettings) -> None:
        s.save(self.vault_root)
        self.settings = s

    def report_idle(self, idle_s: float) -> None:
        self._report = (self._clock(), float(idle_s))

    def _fresh_report(self) -> float | None:
        """The idle seconds of the newest report while it is under REPORT_FRESH_S old, else None."""
        if self._report is None:
            return None
        at, idle_s = self._report
        return idle_s if 0 <= self._clock() - at < REPORT_FRESH_S else None

    def machine_idle(self) -> bool:
        idle_s = self._fresh_report()
        return idle_s is not None and idle_s >= self.settings.idle_minutes * 60

    def reset_attempts(self) -> None:
        """A fresh try for everything (a new dreaming model was chosen): every mark's attempts and retry wait, the
        pause and the error are cleared. A call to the old model still in flight is cancelled first, so the pass
        ends as interrupted and a late failure of that model cannot bring the pause back; a memory write in flight
        is not a model call and finishes."""
        if self._call is not None and not self._call.done():
            self._call.cancel()
        self.marks.reset_attempts()
        self._paused_until = 0.0
        self._error = ""

    def _model(self) -> tuple[Generator | None, bool]:
        """The dreaming model, and whether its role names a provider that cannot be built."""
        try:
            return self._generator(), False
        except ProviderStoreError:
            return None, True

    def ready(self) -> bool:
        if not self.settings.enabled or self._model()[0] is None:
            return False
        profile = self._profile()
        return profile is not None and profile.people.owner() is not None

    def _may_go(self, manual: bool) -> bool:
        return not self._closing and self.ready() and not self._busy() and (manual or self.machine_idle())

    def _paused(self) -> bool:
        """Waiting out a systemic failure (SYSTEMIC); a clock set back cannot stretch the wait."""
        left = self._paused_until - self._clock()
        return 0 < left <= UNREACHABLE_WAIT_S

    # -- what is waiting ----------------------------------------------------------------------------------

    def _quiet(self, now: float) -> list[Conversation]:
        """Conversations untouched for QUIET_S, newest `updated_at` first."""
        out = [(_epoch(c.updated_at), c) for c in self._conversations() if self._is_quiet(c, now)]
        out.sort(key=lambda p: p[0], reverse=True)
        return [c for _, c in out]

    @staticmethod
    def _is_quiet(c: Conversation, now: float) -> bool:
        t = _epoch(c.updated_at)
        return t is not None and now - t >= QUIET_S

    def _due(self, c: Conversation, now: float) -> bool:
        """Something past the mark to read, not waiting out a retry, not skipped (unless it has grown)."""
        m = self.marks.get(c.id)
        n = len(c.exchanges)
        if m.attempts >= MAX_ATTEMPTS:
            if n <= m.seen:
                return False
        elif now < m.not_before <= now + RETRY_WAITS[-1]:
            return False
        return n > m.read and c.exchanges[m.read].outcome != "pending"

    def waiting(self) -> int:
        now = self._clock()
        return sum(1 for c in self._quiet(now) if self._due(c, now))

    def status(self) -> dict[str, Any]:
        broken = self._model()[1]
        return {
            "enabled": self.settings.enabled,
            "idle_minutes": self.settings.idle_minutes,
            "ready": self.ready(),
            "idle": self.machine_idle(),
            "dreaming": self._running or (self._task is not None and not self._task.done()),
            "waiting": self.waiting(),
            "last_dream_at": self.marks.last_dream_at,
            "learned": self.marks.learned,
            # `setup`: the dream choice itself cannot run; else the last pass's error kind (a systemic one stays while
            # its pause lasts, since no automatic pass runs meanwhile)
            "error": "setup" if broken else self._error,
            "idle_reported": self._fresh_report() is not None,
        }

    # -- a pass ------------------------------------------------------------------------------------------

    async def dream(self, *, manual: bool = False) -> dict[str, int]:
        """One pass over what is waiting. Returns the counts; does nothing while another pass runs or when the
        rules do not hold. An automatic pass also waits out the pause a systemic failure started; a manual one
        ("Dream now": the person has most likely just started their model or fixed its key) clears that pause and
        every conversation's failed attempts and retry wait, so a skipped one is tried again."""
        counts = _empty()
        if self._running or (not manual and self._paused()) or not self._may_go(manual):
            return counts
        if manual:
            self._paused_until = 0.0
            self.marks.reset_attempts()
        self._running = True
        error, interrupted, crashed = "", False, ""
        try:  # the first ledger entry too: a failed write must not leave `_running` set
            self.ledger.append(Kind.dream, "", "", event="pass_start", manual=manual)
            for conv in self._quiet(self._clock()):
                if not self._may_go(manual):
                    interrupted = True
                    break
                if not self._is_quiet(conv, self._clock()):  # touched since the pass listed it: a later pass reads it
                    continue
                m = self.marks.get(conv.id)
                if m.attempts >= MAX_ATTEMPTS and len(conv.exchanges) > m.seen:
                    self.marks.grown(conv.id)
                if not self._due(conv, self._clock()):
                    continue
                outcome = await self._conversation(conv, manual, counts)
                if outcome == "interrupted":
                    interrupted = True
                    break
                if outcome in SYSTEMIC:
                    error = outcome
                    break
                if outcome == "model":
                    error = "model"
        except BaseException as e:  # a bug, or the task cancelled: logged by class name, then raised
            crashed = type(e).__name__
            raise
        finally:
            self._running = False
            if error or not interrupted:
                self._error = error
            extra = {"exception": crashed} if crashed else {}
            self.ledger.append(Kind.dream, "", "", event="pass_end", **counts, error=error, interrupted=interrupted,
                               **extra)
        return counts

    async def _conversation(self, conv: Conversation, manual: bool, counts: dict[str, int]) -> str:
        """Read one conversation. Returns `done`, `interrupted`, or the error kind of a failed attempt."""
        start = self.marks.get(conv.id).read
        seen = len(conv.exchanges)
        got = {"messages": 0, "facts": 0, "dropped": 0, "refused": 0}
        outcome, name = "done", ""
        try:
            await self._read(conv, start, manual, got)
        except (Interrupted, OwnerUnknown):
            outcome = "interrupted"
        except _Failed as f:
            outcome, name = f.kind, f.name
            if f.kind in SYSTEMIC:  # an outage, not this conversation's fault: nothing counted, all dreaming waits
                self._paused_until = self._clock() + UNREACHABLE_WAIT_S
            else:
                self.marks.fail(conv.id, self._clock(), seen)
        for k in ("facts", "dropped", "refused"):
            counts[k] += got[k]
        if outcome == "done":
            counts["conversations"] += 1
        if outcome == "done" or got["facts"]:
            self.marks.add_learned(got["facts"], _at(self._clock()))
        extra: dict[str, Any] = {"error": "" if outcome in ("done", "interrupted") else outcome}
        if name:
            extra["exception"] = name
        if outcome == "interrupted":
            extra["interrupted"] = True
        self.ledger.append(Kind.dream, "", "", event="conversation", conversation=conv.id,
                           exchanges=self.marks.get(conv.id).read - start, **got, **extra)
        return outcome

    async def _read(self, conv: Conversation, start: int, manual: bool, got: dict[str, int]) -> None:
        generator, profile = self._model()[0], self._profile()
        if generator is None or profile is None:
            raise Interrupted()
        graph = profile.graph

        def clean(text: str) -> bool:
            try:
                graph.require_clean(text, SHARED)
            except ExclusionError:
                return False
            return True

        kept, end = unread_marked(conv, start, clean)
        offered = [*BASE_STORES, *((f"org:{o.slug}", o.name) for o in profile.orgs.all())]
        ids = {sid for sid, _ in offered}
        done = 0
        for group in batches([text for text, _ in kept]):
            done += len(group)
            mark = end if done == len(kept) else kept[done - 1][1]
            if not self._may_go(manual):
                raise Interrupted()
            known = {f.text for f in graph.facts(SHARED)}
            try:
                raw = await self._guarded(generator.dream_facts(group, offered), manual)
                facts = clean_facts(raw, ids, known)
            except Interrupted:
                raise
            except Exception as e:  # noqa: BLE001 - a bad answer or a failed call is one failed attempt
                raise _Failed(error_kind(e), e) from None
            got["dropped"] += len(raw["facts"]) - len(facts)
            for fact in facts:
                if not self._may_go(manual):
                    raise Interrupted()
                try:  # awaited directly: a write in flight is never cancelled
                    await remember_shared(profile, self._queue(), fact.text, bot_id="", source=f"dream:{conv.id}",
                                          stores=[fact.store], tier=Tier.project)
                except ExclusionError:
                    got["refused"] += 1
                    continue
                except OwnerUnknown:
                    raise
                except Exception as e:  # noqa: BLE001 - the store failed: systemic, the pass ends and dreaming pauses
                    raise _Failed("store", e) from None
                got["facts"] += 1
            self.marks.set_read(conv.id, mark)
            got["messages"] += len(group)
        if not kept:
            self.marks.set_read(conv.id, end)

    async def _guarded(self, coro: Awaitable[Any], manual: bool) -> Any:
        """Await a model call, re-checking the rules every GUARD_S; cancel it when one fails."""
        task = asyncio.ensure_future(coro)
        self._call = task
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=GUARD_S)
                if done:
                    if task.cancelled():  # close() or reset_attempts() cancelled it
                        raise Interrupted()
                    return task.result()
                if not self._may_go(manual):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise Interrupted()
        finally:
            self._call = None
            if not task.done():  # the pass itself was cancelled
                task.cancel()

    # -- running it ----------------------------------------------------------------------------------

    def start(self, *, manual: bool) -> bool:
        """A pass as a background task. False when one is under way or the harness is closing."""
        if self._closing or self._running or (self._task is not None and not self._task.done()):
            return False
        self._task = asyncio.get_running_loop().create_task(self._background(manual))
        return True

    async def _background(self, manual: bool) -> None:
        try:
            await self.dream(manual=manual)
        except Exception as e:  # noqa: BLE001 - a background pass must not die silently
            self.ledger.append(Kind.error, "", "", where="dream", error=type(e).__name__)

    async def run(self, stop: asyncio.Event) -> None:
        """Every TICK_S: start a pass when the rules hold and something is waiting."""
        while not stop.is_set():
            try:
                if (not self._paused() and self.ready() and self.machine_idle() and not self._busy()
                        and self.waiting()):
                    self.start(manual=False)
            except Exception as e:  # noqa: BLE001 - the loop outlives any one look
                self.ledger.append(Kind.error, "", "", where="dream", error=type(e).__name__)
            try:
                await asyncio.wait_for(stop.wait(), TICK_S)
            except TimeoutError:
                pass

    async def close(self) -> None:
        """A model call in flight is cancelled; a write in flight finishes; no pass starts after."""
        self._closing = True
        call = self._call
        if call is not None and not call.done():
            call.cancel()
        if self._task is not None:
            await asyncio.gather(self._task, return_exceptions=True)

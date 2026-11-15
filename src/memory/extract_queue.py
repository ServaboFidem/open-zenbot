"""Background extraction queue.

Imported documents are extracted into the memory store one job at a time,
from a queue persisted on disk (tmp-then-replace) so a restart resumes it.

Routing is pinned: the first time a job runs with `stores=None`, the routed
stores are persisted onto the job (`ProfileMemory.plan_stores`) before
extraction, so a retry after a partial failure never re-routes to a different
set. `ProfileMemory.extract` is idempotent per store, so the retry is safe.

Work that needs the owner (a personal or org store, or `stores=None`, which
may route there) waits without counting an attempt until the owner is known;
setting the owner `wake`s the queue so it need not wait out its idle poll.

Shutdown (`shutdown`) is not a cancel: the worker's store write runs in a thread
under the memory write lock, and cancelling the task leaves that thread
holding it. The stop flag is checked between stores instead, so shutdown
waits for at most the store in flight; the job stays on disk for next start.

`remember_shared` is the shared write for every caller outside the queue: it
goes through `ProfileMemory`, and before the owner is known it writes the
fact without store targets and queues its extraction.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Literal
from uuid import uuid4

from .exclusion import ExclusionError
from .graph import SHARED, Fact, Stopped, Tier
from .import_dir import extraction_calls
from .stores import PERSONAL, is_org
from .profile import OwnerUnknown, ProfileMemory

MAX_ATTEMPTS = 3
IDLE_WAIT = 2.0
BACKOFF = (30.0, 120.0)  # seconds before the 2nd and 3rd attempt
SHUTDOWN_GRACE = 10.0  # how long shutdown waits for the store being extracted; MemoryGraph.close then waits up to 30 s more,
# and scripts/dev.sh kills the harness 45 s after SIGTERM: both waits must end before that


@dataclass
class Job:
    id: str
    fact_id: str
    kind: Literal["doc", "fact"]
    source: str  # vault-relative path for a doc
    stores: list[str] | None
    attempts: int = 0
    not_before: float = 0.0  # epoch seconds; a failed job waits for its backoff


def new_job(kind: Literal["doc", "fact"], fact_id: str, source: str, stores: list[str] | None = None) -> Job:
    """A job for `stores`; with none it is not yet routed: the queue routes it once, then pins the stores."""
    return Job(id=uuid4().hex[:12], fact_id=fact_id, kind=kind, source=source, stores=stores)


def vault_copy(fact: Fact, vault_root: Path) -> Path | None:
    """The vault copy an imported document's fact points at (`vault:<rel>`), if it is still there."""
    if not fact.source.startswith("vault:"):
        return None
    root = Path(vault_root).resolve()
    copy = (root / fact.source[len("vault:"):]).resolve()
    return copy if copy.is_relative_to(root) and copy.is_file() else None


def vault_text(fact: Fact, vault_root: Path) -> str | None:
    """The document body behind a fact, read from its vault copy (as a rebuild reads it); None without one.
    The Hindsight store's `text_source`: a record that dropped its body gets it again here when resent."""
    copy = vault_copy(fact, vault_root)
    return copy.read_text(encoding="utf-8") if copy is not None else None


def rebuild_jobs(facts: list[Fact], vault_root: Path) -> list[tuple[Job, int]]:
    """One job per shared fact to extract it again, each with about how many
    model calls it takes. An onboarding answer goes to the personal store
    (pinned: one extraction, no routing call); an imported document is read
    again from its vault copy (`extraction_calls` of its size); anything
    else, a document whose copy is gone included, is routed and extracted
    from the fact's own text (one routing call, one extraction)."""
    out: list[tuple[Job, int]] = []
    for f in facts:
        if f.source.startswith("onboarding:"):
            out.append((new_job("fact", f.id, f.source, [PERSONAL]), 1))
            continue
        if (copy := vault_copy(f, vault_root)) is not None:
            out.append((new_job("doc", f.id, f.source[len("vault:"):]), extraction_calls(copy.stat().st_size)))
            continue
        out.append((new_job("fact", f.id, f.source), 2))
    return out


class ExtractQueue:
    def __init__(self, path: Path, profile: ProfileMemory, vault_root: Path, ledger: Any, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self.path = Path(path)
        self.profile = profile
        self.vault_root = Path(vault_root)
        self.ledger = ledger
        self._lock = threading.Lock()
        self._jobs: list[Job] = self._load()
        self._done = 0  # since this process started
        self._failed = 0
        self._current: str | None = None
        self._wake = asyncio.Event()
        self._stop = threading.Event()  # shutdown: read by the worker thread between stores

    def _load(self) -> list[Job]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        try:
            return [Job(**d) for d in json.loads(text)["jobs"]]
        except (ValueError, KeyError, TypeError):
            self.path.replace(self.path.with_name(self.path.name + ".bad"))  # keep the damaged file recoverable
            return []

    def _save(self) -> None:  # caller holds the lock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"jobs": [asdict(j) for j in self._jobs]}, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, job: Job) -> None:
        with self._lock:
            self._jobs.append(job)
            self._save()
        self._log(job, "queued")

    def pending(self) -> list[Job]:
        with self._lock:
            return list(self._jobs)

    def _drop(self, job: Job) -> None:
        with self._lock:
            self._jobs = [j for j in self._jobs if j.id != job.id]
            self._save()

    def _persist(self) -> None:
        with self._lock:
            self._save()

    @staticmethod
    def _needs_owner(job: Job) -> bool:
        """Personal and org stores need the owner; unrouted (`None`) work may route there."""
        return job.stores is None or any(s == PERSONAL or is_org(s) for s in job.stores)

    def _owner_known(self) -> bool:
        return self.profile.people.owner() is not None

    def waiting_for_owner(self) -> bool:
        return not self._owner_known() and any(self._needs_owner(j) for j in self.pending())

    def progress(self) -> dict[str, Any]:
        return {"queued": len(self.pending()), "done": self._done, "failed": self._failed,
                "current": self._current, "waiting_for_owner": self.waiting_for_owner()}

    def _text(self, job: Job) -> str | None:
        """The text to extract, or None when the fact was forgotten."""
        fact = self.profile.graph.get(job.fact_id)
        if fact is None:
            return None
        if job.kind == "fact":
            return fact.text
        return (self.vault_root / job.source).read_text(encoding="utf-8")

    def _log(self, job: Job, status: str, **extra: Any) -> None:
        self.ledger.append("extract", "", "", status=status, fact_id=job.fact_id, job=job.id, job_kind=job.kind, **extra)

    async def run_once(self) -> bool:
        """Run the first job that is due and can run now; True if one ran (even to a failure)."""
        owner_known = self._owner_known()
        now = self.clock()
        for job in self.pending():
            if self._stop.is_set():
                return False
            if (not owner_known and self._needs_owner(job)) or job.not_before > now:
                continue
            if await self._run(job):
                return True
        return False

    async def _run(self, job: Job) -> bool:
        """False when the job turned out to need the owner (nothing counted)."""
        self._current = job.id
        try:
            text = self._text(job)
            if text is None:  # forgotten: nothing to extract into
                self._drop(job)
                return True
            try:
                self.profile.preflight(job.fact_id, text)  # before any routing call is spent
            except TypeError as e:  # the store can never attach: retrying cannot help
                self._drop(job)
                self._failed += 1
                self._log(job, "failed", reason="store cannot attach", error=str(e))
                return True
            if job.stores is None:
                job.stores = await self.profile.plan_stores(text)
                self._persist()
            job.stores = await self.profile.extract(job.fact_id, text, job.stores, stop=self._stop)
            self._drop(job)
            self._done += 1
            self._log(job, "done", stores=job.stores)
        except OwnerUnknown:
            return False
        except Stopped:  # shutdown between stores: the job stays on disk, no attempt counted
            pass
        except ExclusionError as e:
            self._drop(job)
            self._log(job, "refused", error=str(e))
        except Exception as e:  # noqa: BLE001 - any failure retries, then lands in the ledger
            if isinstance(e, KeyError) and self.profile.graph.get(job.fact_id) is None:
                self._drop(job)  # forgotten between the check and the write
            else:
                self._retry_later(job, e)
        finally:
            self._current = None
        return True

    def _retry_later(self, job: Job, e: Exception) -> None:
        job.attempts += 1
        if job.attempts >= MAX_ATTEMPTS:
            self._drop(job)
            self._failed += 1
            self._log(job, "failed", attempts=job.attempts, error=f"{type(e).__name__}: {e}")
        else:
            job.not_before = self.clock() + BACKOFF[job.attempts - 1]
            self._persist()

    def wake(self) -> None:
        """Cut the idle wait short (the owner was just set). Call it from the loop `run` runs on."""
        self._wake.set()

    def stop(self) -> None:
        """Ask the worker to finish: the store being extracted completes, no
        other store or job starts. Call it from the loop `run` runs on."""
        self._stop.set()
        self._wake.set()

    async def shutdown(self, task: asyncio.Task[Any], grace: float = SHUTDOWN_GRACE) -> None:
        """Stop the worker `task` (running `run`) and wait for it, at most
        `grace` seconds: a store write in flight finishes and lands rather than
        being cut off under the memory write lock. Past the grace the task is
        cancelled; its job is still on disk and runs again at the next start."""
        self.stop()
        try:
            await asyncio.wait_for(asyncio.shield(task), grace)
        except TimeoutError:
            self.ledger.append("extract", "", "", status="shutdown_cut", job=self._current, grace=grace)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        except Exception:  # noqa: BLE001 - the worker's own failure must not stop the shutdown
            pass

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set() and not self._stop.is_set():
            self._wake.clear()  # before the pass: a wake during it still cuts the next wait short
            if await self.run_once():
                continue
            waits = [asyncio.ensure_future(stop.wait()), asyncio.ensure_future(self._wake.wait())]
            try:
                await asyncio.wait(waits, timeout=IDLE_WAIT, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for w in waits:
                    w.cancel()


async def remember_shared(profile: ProfileMemory, queue: ExtractQueue | None, text: str, *, bot_id: str, source: str,
                          entities: list[str] | None = None, stores: list[str] | None = None,
                          tier: Tier = Tier.project, session_id: str = "") -> Fact:
    """A `shared` write through the profile. Before the owner is known the
    fact is written without store targets (recallable at once, the exclusion
    filter still runs) and a `fact` job extracts it once the name is set.
    With no queue (the local store: registries, no graph extraction) an
    untargeted write skips routing and lands directly, as it always did there;
    a pinned write before the owner is known lands directly too."""
    if queue is None and stores is None:
        return await asyncio.to_thread(profile.graph.remember, text, SHARED, tier, bot_id=bot_id, entities=entities,
                                       source=source, session_id=session_id)
    try:
        return await profile.remember_shared(text, bot_id=bot_id, source=source, entities=entities, stores=stores, tier=tier,
                                           session_id=session_id)
    except OwnerUnknown:
        fact = await asyncio.to_thread(profile.graph.remember, text, SHARED, tier, bot_id=bot_id, entities=entities,
                                       source=source, session_id=session_id)
        if queue is not None:
            queue.add(new_job("fact", fact.id, source))
        return fact

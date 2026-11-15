"""Learn from a crawl.

A bot that has read a web page hands it to `learn_page`; the harness, not
the model, decides what memory keeps. The gate: a page teaches memory
something only when it names an organization or person from the registries
(name or alias) or a name an earlier learned page taught. Everything the
page says about those, and about new things it ties to them with a stated
relation (a product the known company sells, a feature of a known product),
is written as shared facts with the URL as the source. New people, and new
things the page does not tie to anything known, wait in Approvals; a denial
is remembered per site and name and never re-asked. The site itself is
recorded once per organization. Pages are deduplicated by md5, a changed
page replaces its earlier facts, and nothing of the page text is stored.

`plan_learning` is the pure part: the gate, the classification, and the
list of writes and holds. `Learner` runs it: redact, cap, dedup, extract
through the generator, write in the background, hold, and ledger.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse

from src.harness.approvals import ApprovalRequest, ApprovalState, ApprovalStore
from src.harness.tools import current_call

from .exclusion import ExclusionError, exclusion
from .ingest import _capped
from .learn_log import HoldStore, LearnEntry, LearnLog
from .registry import OrgEntry, OrgRegistry, PeopleRegistry

if TYPE_CHECKING:
    from .profile import ProfileMemory

__all__ = ["Hold", "LEARN_PAGE_DESCRIPTION", "LEARN_PAGE_SPEC", "LearnPlan", "Learner", "MAX_HOLDS", "Write", "host_of", "plan_learning"]

MAX_HOLDS = 10  # per page: a product index cannot flood Approvals
WORLD_STORE = "world"


def host_of(url: str) -> str:
    """The page's host, lowercased; empty when the URL has none."""
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _norm(name: str) -> str:
    return name.strip().lower()


@dataclass
class Write:
    text: str
    entities: list[str]


@dataclass
class Hold:
    name: str
    kind: str
    relation_text: str  # "<name> <relation> <related_to>." when the page states one, else ""
    writes: list[Write]


@dataclass
class LearnPlan:
    status: str  # learned | skipped
    reason: str = ""
    matched: list[str] = field(default_factory=list)  # canonical names of the known entities the page names
    org: OrgEntry | None = None  # the one registry organization matched, when exactly one
    stores: list[str] = field(default_factory=lambda: [WORLD_STORE])
    site_fact: Write | None = None
    writes: list[Write] = field(default_factory=list)
    holds: list[Hold] = field(default_factory=list)
    overflow: int = 0  # holds dropped past MAX_HOLDS
    denied: list[str] = field(default_factory=list)  # loose names the user denied before from this host
    skipped_facts: int = 0  # facts about a name the page's entity list does not carry
    seen: list[str] = field(default_factory=list)  # every extracted name
    connected: list[str] = field(default_factory=list)  # names of the connected discoveries
    learned_names: list[str] = field(default_factory=list)  # what the page teaches for later gates: matched names and products


def plan_learning(learning: Any, url: str, *, orgs: OrgRegistry, people: PeopleRegistry, learned: set[str],
                  site_known: Callable[[str, str], bool], is_denied: Callable[[str, str], bool]) -> LearnPlan:
    """The gate, the classification, and the writes and holds for one page.
    Pure: the registries and the three callables are its only inputs."""
    host = host_of(url)
    scheme = urlparse(url).scheme or "https"
    seen = [e.name for e in learning.entities]
    known: dict[str, str] = {}  # normalized extracted name -> canonical name
    org_hits: dict[str, OrgEntry] = {}  # normalized extracted name -> registry organization
    org_names: list[str] = []  # canonical names of the matched organizations (registry, or learned with kind organization)
    for e in learning.entities:
        n = _norm(e.name)
        if not n or n in known:
            continue
        if (org := orgs.find(e.name)) is not None:
            known[n] = org.name
            org_hits[n] = org
            if org.name not in org_names:
                org_names.append(org.name)
        elif (person := people.find(e.name)) is not None:
            known[n] = person.name
        elif n in learned:
            known[n] = e.name.strip()
            if e.kind == "organization" and known[n] not in org_names:
                org_names.append(known[n])
    matched = list(dict.fromkeys(known.values()))
    if not matched:
        return LearnPlan("skipped", "unrelated", seen=seen)

    # the rest: connected (tied by a stated relation to a known name, or to another connected discovery
    # on the same page, so a feature of a product the page introduced is connected too; never a person) or loose
    connected: dict[str, Any] = {}
    anchored: dict[str, str] = dict(known)  # normalized -> canonical, for known and connected names

    def canonical(name: str | None) -> str | None:
        return anchored.get(_norm(name)) if name else None

    rest = [e for e in learning.entities if _norm(e.name) and _norm(e.name) not in known]
    for _ in range(2):  # two passes: tied to a known name, then tied to one of those; no deeper (a chain of guesses)
        anchors = dict(anchored)
        for e in rest:
            n = _norm(e.name)
            if n in connected or e.kind == "person" or not e.relation or not e.related_to or _norm(e.related_to) not in anchors:
                continue
            connected[n] = e
            anchored[n] = e.name.strip()
    connected = {n: connected[n] for e in rest if (n := _norm(e.name)) in connected}  # page order
    loose: dict[str, Any] = {}
    for e in rest:
        n = _norm(e.name)
        if n not in connected and n not in loose:
            loose[n] = e

    def tied(e: Any) -> list[str]:
        c = canonical(e.related_to) if e is not None else None
        return [c] if c else []

    by_name: dict[str, Any] = {_norm(e.name): e for e in learning.entities if _norm(e.name)}
    writes: list[Write] = []
    hold_facts: dict[str, list[Write]] = {n: [] for n in loose}
    skipped_facts = 0
    for f in learning.facts:
        a = _norm(f.about)
        if a in known:
            writes.append(Write(f.text, list(dict.fromkeys([known[a], *tied(by_name.get(a))]))))
        elif a in connected:
            writes.append(Write(f.text, [connected[a].name.strip(), *tied(connected[a])]))
        elif a in loose:
            hold_facts[a].append(Write(f.text, [loose[a].name.strip(), *tied(loose[a])]))
        else:
            skipped_facts += 1
    # the relations the page states, for connected discoveries (the edge that creates the node) and for known
    # non-person entities too (a feature of a product learned earlier is a relation on a re-learn as well)
    for e in learning.entities:
        n = _norm(e.name)
        if e.kind == "person" or not e.relation or n not in anchored:
            continue
        rel = canonical(e.related_to) or ""
        if rel and _norm(rel) != n:
            writes.append(Write(f"{anchored[n]} {e.relation} {rel}.", [anchored[n], rel]))
    if learning.summary.strip():
        writes.append(Write(learning.summary.strip(), list(matched)))

    holds: list[Hold] = []
    denied: list[str] = []
    overflow = 0
    for n, e in loose.items():
        name = e.name.strip()
        if is_denied(host, name.lower()):
            denied.append(name)
            continue
        rel = canonical(e.related_to) or ""
        relation_text = f"{name} {e.relation} {rel}." if e.relation and rel else ""
        hw = list(hold_facts[n])
        if relation_text:
            hw.append(Write(relation_text, [name, rel]))
        if not hw:  # nothing the page says about it: nothing to approve
            continue
        if len(holds) >= MAX_HOLDS:
            overflow += 1
            continue
        holds.append(Hold(name, e.kind, relation_text, hw))

    registry_orgs = list(dict.fromkeys(o.slug for o in org_hits.values()))
    org = next(iter(org_hits.values())) if len(registry_orgs) == 1 else None
    stores = [f"org:{org.slug}"] if org is not None else [WORLD_STORE]
    site_fact = None
    reason = ""
    if len(org_names) == 1:
        org_name = org.name if org is not None else org_names[0]
        aliases = [org_name, *(org.aliases if org is not None else [])]
        if not _own_host(host, aliases):
            reason = "site_foreign"  # a page about the org on someone else's site: not its website
        elif host and not site_known(org_name, host):
            site_fact = Write(f"{org_name}'s website is {scheme}://{host}.", [org_name])
    else:
        reason = "site_ambiguous"
    learned_names = list(dict.fromkeys([*matched, *(e.name.strip() for e in connected.values() if e.kind == "product")]))
    return LearnPlan("learned", reason, matched=matched, org=org, stores=stores, site_fact=site_fact, writes=writes,
                     holds=holds, overflow=overflow, denied=denied, skipped_facts=skipped_facts, seen=seen,
                     connected=[e.name.strip() for e in connected.values()], learned_names=learned_names)


def _letters(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def _own_host(host: str, names: list[str]) -> bool:
    """Does the host carry the organization's name or an alias (letters only:
    northwindlabs.example for Northwind Labs, nwl.example for the alias NWL)?"""
    h = _letters(host)
    return any(len(_letters(n)) >= 3 and _letters(n) in h for n in names)


# -- the learner -------------------------------------------------------------------

LEARN_PAGE_DESCRIPTION = (
    "Hand the harness a web page you already fetched (its URL and markdown) so memory can learn from it. "
    "Memory keeps only what touches an organization, person, or product it already knows; "
    "connected products, features, and places are written, new people and loose discoveries wait for the user in Approvals, "
    "and a page that names nothing known teaches nothing. Returns what was matched, written, held, or skipped and why."
)
LEARN_PAGE_SPEC: dict[str, Any] = {
    "type": "object",
    "properties": {
        "url": {"type": "string", "description": "The page's address; becomes the facts' source."},
        "markdown": {"type": "string", "description": "The page text as fetched."},
        "focus": {"type": "string", "description": "What to read the page for, in plain words (optional)."},
    },
    "required": ["url", "markdown"],
}
HOLD_RULE = "learn:loose"
HOLD_TOOL = "learn_page"


def _skipped(reason: str, **extra: Any) -> dict[str, Any]:
    return {"status": "skipped", "reason": reason, "matched": [], "written": 0, "held": [], "skipped_facts": 0, **extra}


class Learner:
    """Runs `plan_learning` for a page and does the I/O around it. Writes
    run in a background task per page (a store write can take tens of
    seconds), so the tool returns at once; `drain` awaits them."""

    def __init__(self, vault_root: Path, generator: Any, profile: ProfileMemory, extract_queue: Any,
                 approvals: ApprovalStore, ledger: Any, bound: Callable[[], bool]) -> None:
        self.vault_root = Path(vault_root)
        self.generator = generator
        self.profile = profile
        self.extract_queue = extract_queue
        self.approvals = approvals
        self.ledger = ledger
        self.bound = bound
        self.log = LearnLog(self.vault_root / "learn_log.json")
        self.holds = HoldStore(self.vault_root / "learn_holds.json")
        self._tasks: set[asyncio.Task[Any]] = set()
        self._chain: dict[str, asyncio.Task[Any]] = {}  # the latest write task per URL: the next one waits for it
        self._locks: dict[str, asyncio.Lock] = {}  # per URL, from the unchanged check to the write's spawn

    def register(self, tools: Any) -> None:
        tools.register(HOLD_TOOL, LEARN_PAGE_DESCRIPTION, LEARN_PAGE_SPEC, self.learn)

    async def drain(self, timeout: float | None = None) -> None:
        """Await the background writes; with a timeout, give up on the rest
        (the log's `done` flag stays clear, so the page is learned again)."""
        while self._tasks:
            done, _ = await asyncio.wait(list(self._tasks), timeout=timeout)
            if not done:
                return

    def _spawn(self, coro: Any) -> asyncio.Task[Any]:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    def _writing(self, url: str) -> bool:
        t = self._chain.get(url)
        return t is not None and not t.done()

    def _hold_pending(self, host: str, name: str) -> bool:
        return any(_norm(p.get("host", "")) == _norm(host) and _norm(p.get("name", "")) == _norm(name) for _, p in self.holds.pending())

    def _ledger(self, bot_id: str, session_id: str, **data: Any) -> None:
        self.ledger.append("learn", bot_id, session_id, **data)

    def _known_in(self, text: str) -> list[str]:
        """The registry and learned names that occur in the page, for the prompt."""
        low = text.lower()
        names: list[str] = []
        for o in self.profile.orgs.all():
            names += [o.name, *o.aliases]
        for p in self.profile.people.all():
            names += [p.name, *p.aliases]
        names += sorted(self.log.learned())
        return [n for n in dict.fromkeys(names) if _norm(n) and _norm(n) in low]

    async def learn(self, url: str, markdown: str, focus: str = "") -> str:
        """The `learn_page` tool: a JSON object for the bot. Never raises:
        every failure is a skipped result and a ledger entry."""
        return json.dumps(await self._learn(url, markdown, focus))

    async def _learn(self, url: str, markdown: str, focus: str) -> dict[str, Any]:
        bot_id, session_id = current_call.get()
        base = {"url": url, "matched": [], "written": 0, "held": [], "redactions": 0, "chars": 0, "parsed": False}
        if not self.bound():
            self._ledger(bot_id, session_id, status="skipped", reason="unbound", **base)
            return _skipped("unbound")
        host = host_of(url)
        if not host or not urlparse(url).scheme:
            self._ledger(bot_id, session_id, status="skipped", reason="bad_url", **base)
            return _skipped("bad_url")
        if not (markdown or "").strip():
            self._ledger(bot_id, session_id, status="skipped", reason="empty", **base)
            return _skipped("empty")
        text, found = exclusion.redact(markdown)
        text = _capped(text, self.generator.chunk_chars)
        base.update(redactions=len(found), chars=len(text))
        try:
            exclusion.require({"kind": "vault_doc", "text": text}, "ingest")
        except ExclusionError as e:
            self._ledger(bot_id, session_id, status="skipped", reason="excluded", error=str(e), **base)
            return _skipped("excluded")
        md5 = hashlib.md5(text.encode("utf-8")).hexdigest()
        async with self._locks.setdefault(url, asyncio.Lock()):  # two learns of one page: the second sees the first's entry
            return await self._learn_locked(url, host, text, md5, focus, base, bot_id, session_id, len(found))

    def _lost(self, entry: LearnEntry) -> bool:
        """The entry names facts, and the store has none of them: they were written to another store (cognee,
        before the move to Hindsight). One forgotten on purpose leaves the others, so the page is not relearned."""
        ids = [*entry.fact_ids, *entry.approved_fact_ids]
        return bool(ids) and all(self.profile.graph.get(fid) is None for fid in ids)

    def _site_known(self, org: str, host: str) -> bool:
        """The site fact is recorded and (once written) still in the store; "" is a write still landing."""
        if not self.log.site_recorded(org, host):
            return False
        fid = self.log.site_fact_id(org, host)
        return not fid or self.profile.graph.get(fid) is not None

    async def _learn_locked(self, url: str, host: str, text: str, md5: str, focus: str, base: dict[str, Any],
                            bot_id: str, session_id: str, redactions: int) -> dict[str, Any]:
        previous = self.log.get(url)
        if previous is not None and self._lost(previous):
            previous = None  # the store no longer has any of its facts (an upgrade from cognee): learn it afresh
        # unchanged and finished (or still landing) is a skip; an unfinished one (a crash mid-write) is learned again
        if previous is not None and previous.md5 == md5 and (previous.done or self._writing(url)):
            self._ledger(bot_id, session_id, status="skipped", reason="unchanged", **base)
            return _skipped("unchanged")
        try:
            learning = await self.generator.learn_page(text, focus=focus, known=self._known_in(text))
        except Exception as e:  # noqa: BLE001 - a provider failure is data for the bot, never an exception in its loop
            err = f"{type(e).__name__}: {e}"
            self.ledger.append("error", bot_id, session_id, where="learn_page", url=url, error=err[:300])
            self._ledger(bot_id, session_id, status="skipped", reason="error", error=err[:300], **base)
            return _skipped("error", error=err[:300])
        base["parsed"] = learning.parsed
        if not learning.parsed:
            self._ledger(bot_id, session_id, status="skipped", reason="unparsed", **base)
            return _skipped("unparsed")
        plan = plan_learning(learning, url, orgs=self.profile.orgs, people=self.profile.people, learned=self.log.learned(),
                             site_known=self._site_known, is_denied=self.log.is_denied)
        if plan.status == "skipped":
            self._ledger(bot_id, session_id, status="skipped", reason=plan.reason, seen=plan.seen, **base)
            return _skipped(plan.reason, seen=plan.seen)

        # the entry for this version: the earlier version's facts (landed or not yet) are stale, forgotten before the new writes
        stale = [*previous.fact_ids, *previous.stale] if previous else []
        names = list(dict.fromkeys([*plan.learned_names, *(previous.names if previous and previous.md5 == md5 else [])]))
        self.log.put(LearnEntry(url, md5, [], list(previous.approved_fact_ids) if previous else [], names, stale=stale))
        if plan.site_fact is not None:
            self.log.record_site(plan.site_fact.entities[0], host)
        held: list[str] = []
        pending: list[str] = []
        for hold in plan.holds:
            if self._hold_pending(host, hold.name):  # already in Approvals from another page of this site: one question, not two
                pending.append(hold.name)
                continue
            detail = "\n".join([*([hold.relation_text] if hold.relation_text else []), *(w.text for w in hold.writes if w.text != hold.relation_text),
                                "Approve to remember these; deny to never ask about this name from this site again."])
            req = self.approvals.create(bot_id, session_id, title=f"Remember {hold.name} ({hold.kind}) from {host}?", detail=detail,
                                        rule=HOLD_RULE, tool=HOLD_TOOL)
            self.holds.put(req.to_dict(), {"url": url, "host": host, "name": hold.name, "stores": plan.stores,
                                           "writes": [asdict(w) for w in hold.writes]})
            self.ledger.append("approval_request", bot_id, session_id, approval_id=req.id, tool=HOLD_TOOL, rule=HOLD_RULE, title=req.title)
            held.append(hold.name)
        if pending:
            self._ledger(bot_id, session_id, event="held_pending", url=url, names=pending)
        if plan.overflow:
            self._ledger(bot_id, session_id, event="held_overflow", url=url, count=plan.overflow)
        if plan.denied:
            self._ledger(bot_id, session_id, event="held_denied_before", url=url, names=plan.denied)
        written = len(plan.writes) + (1 if plan.site_fact is not None else 0)
        self._chain[url] = self._spawn(self._write(url, md5, host, plan.site_fact, plan.writes, plan.stores, bot_id, session_id,
                                                   after=self._chain.get(url)))
        self._ledger(bot_id, session_id, status="learned", reason=plan.reason, matched=plan.matched, written=written, held=held,
                     redactions=redactions, chars=len(text), parsed=True, url=url, skipped_facts=plan.skipped_facts,
                     overflow=plan.overflow, denied=plan.denied)
        return {"status": "learned", "reason": plan.reason, "matched": plan.matched, "written": written, "held": held,
                "skipped_facts": plan.skipped_facts}

    # -- holds: decisions and restart ---------------------------------------------

    async def decide(self, request: ApprovalRequest, approved: bool) -> dict[str, Any]:
        """A decision on a learn hold. Approved: the held writes land (in the
        background) and are recorded as approved facts for their page, kept
        across re-learns, and the name becomes a known one for later pages.
        Denied: the name is remembered for that host and never asked about
        again. A denied hold leaves the store at once; an approved one once
        its writes landed, so a crash in between asks again after a restart."""
        hold = self.holds.get(request.id)
        if hold is None:
            self._ledger(request.bot_id, request.session_id, event="held_missing", approval_id=request.id)
            return {"event": "held_missing"}
        _, payload = hold
        url, host, name = payload["url"], payload["host"], payload["name"]
        if not approved:
            self.holds.pop(request.id)
            self.log.deny(host, name)
            self._ledger(request.bot_id, request.session_id, event="held_denied", approval_id=request.id, name=name, url=url)
            return {"event": "held_denied"}
        writes = [Write(w["text"], list(w["entities"])) for w in payload["writes"]]

        async def land() -> None:
            ids = await self._write_all(url, writes, list(payload["stores"]), request.bot_id, request.session_id)
            self.log.add_approved(url, ids, name=name)
            self.holds.pop(request.id)
            self._ledger(request.bot_id, request.session_id, event="held_written", approval_id=request.id, name=name, url=url, written=len(ids))

        self._spawn(land())
        return {"event": "held_written", "written": len(writes)}

    def restore(self) -> int:
        """Rehydrate the pending holds into the approval store after a restart."""
        n = 0
        for request, _ in self.holds.pending():
            if request.get("state", "pending") != "pending":
                continue
            fields = {k: v for k, v in request.items() if k in ApprovalRequest.__dataclass_fields__}
            fields["state"] = ApprovalState(fields.get("state", "pending"))
            self.approvals.restore(ApprovalRequest(**fields))
            n += 1
        if n:
            self._ledger("", "", event="holds_restored", count=n)
        return n

    async def _write_one(self, url: str, w: Write, stores: list[str], bot_id: str, session_id: str) -> str:
        """One fact into memory; its id, or "" when it was blocked or failed (both ledgered)."""
        from .extract_queue import remember_shared

        try:
            fact = await remember_shared(self.profile, self.extract_queue, w.text, bot_id=bot_id, source=f"web:{url}",
                                         entities=w.entities, stores=stores, session_id=session_id)
            return fact.id
        except ExclusionError as e:
            self._ledger(bot_id, session_id, event="write_blocked", url=url, error=str(e)[:300])
        except Exception as e:  # noqa: BLE001 - one failed write must not lose the rest
            self.ledger.append("error", bot_id, session_id, where="learn_write", url=url, error=f"{type(e).__name__}: {e}"[:300])
        return ""

    async def _write_all(self, url: str, writes: list[Write], stores: list[str], bot_id: str, session_id: str) -> list[str]:
        ids = [await self._write_one(url, w, stores, bot_id, session_id) for w in writes]
        return [i for i in ids if i]

    async def _write(self, url: str, md5: str, host: str, site_fact: Write | None, writes: list[Write], stores: list[str],
                     bot_id: str, session_id: str, after: asyncio.Task[Any] | None) -> None:
        """The background write of one learned page version. Earlier versions
        of the page finish first (their facts land as stale and are forgotten
        here); every id is logged as it lands, so a crash loses at most one;
        the site fact is kept apart from the page's facts, so a changed page
        does not forget it."""
        if after is not None and not after.done():
            await asyncio.wait([after])
        for fid in self.log.take_stale(url):  # the earlier version's facts go first (approved-hold facts are never stale)
            try:
                await asyncio.to_thread(self.profile.graph.forget, fid, bot_id=bot_id, session_id=session_id)
            except Exception as e:  # noqa: BLE001 - a forget that fails leaves a stale fact, not a lost page
                self.ledger.append("error", bot_id, session_id, where="learn_forget", url=url, fact_id=fid, error=f"{type(e).__name__}: {e}"[:300])
        written = 0
        if site_fact is not None:
            org = site_fact.entities[0]
            sid = await self._write_one(url, site_fact, stores, bot_id, session_id)
            if sid:
                self.log.set_site_fact(org, host, sid)
                written += 1
            else:
                self.log.clear_site(org, host)  # so the next page of this site tries again
        for w in writes:
            fid = await self._write_one(url, w, stores, bot_id, session_id)
            if fid:
                self.log.add_fact_id(url, md5, fid)
                written += 1
        self.log.mark_done(url, md5)
        self._ledger(bot_id, session_id, event="written", url=url, written=written)

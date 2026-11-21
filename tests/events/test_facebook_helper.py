"""Browser-helper anti-goal gate: the feature never acts, never guesses.

The Chrome helper watches Facebook events and birthdays and holds RSVP and
personal events for approval, feeds the rest to discovery, and nudges
birthdays a week out. Four anti-goals hold across the whole feature:

1. **No action**: the helper never posts to Facebook, never opens or drives
   tabs, and the harness never fetches Facebook. Reports flow one way, from
   the tab to ``POST /intake/facebook`` on loopback.
2. **Approval-only calendar path**: pool rows never become calendar events on
   their own. RSVP taps and friend-named events wait as approval cards, the
   rest land in the feed, likes create nothing, and a cancelled or un-RSVP'd
   row removes its hold instead of lingering. Only close-friend birthdays
   hold, as ordinary made-here events.
3. **Nudge timing**: the birthday nudge fires once, exactly seven days out,
   on both surfaces, and a failing delivery is counted, never raised.
4. **No bot reach**: the scout stays a closed bot with no helper tool, and no
   Facebook tool is offered to any other bot.

Plus the route rules the feature inherits: every new POST takes a required
JSON body (no simple cross-site request reaches one), and ledger entries
carry ids and counts only, never listing text.

Fixtures stand in for Facebook throughout: no test touches the network, and
no test needs a real model.

Until the feature slices merge, the feature modules are absent and the
behavioral tests skip; the structural tests (closed scout, no helper tool
registered anywhere, POST-body shape of the helper contract) run regardless.
Each behavioral test fails if its anti-goal breaks: revert the feature code
and the suite goes red.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.events.discover_store import DiscoverStore
from src.events.store import CalendarStore
from src.memory.registry import PeopleRegistry

REPO = Path(__file__).resolve().parents[2]
HELPER_DIR = REPO / "src" / "helper"


def _load(name: str):
    """Import ``name`` or skip: the feature slice has not merged yet."""
    try:
        return importlib.import_module(name)
    except ImportError:
        pytest.skip(f"feature module {name} has not merged yet")
    raise AssertionError("unreachable")  # pragma: no cover


# ---------------------------------------------------------------------------
# Shared fixtures (fixtures only: no network, no model).
# ---------------------------------------------------------------------------


@pytest.fixture
def people(tmp_path):
    reg = PeopleRegistry(tmp_path / "people.json")
    reg.set_owner("Alex Rivera", [])
    reg.add("Dana Reyes", relation="friend", tier="close")
    reg.add("Lee Morgan", relation="colleague", tier="known")
    reg.add_alias("Dana Reyes", "Dana R")
    return reg


@pytest.fixture
def calendar(tmp_path):
    store = CalendarStore(tmp_path / "calendar.db")
    yield store
    store.close()


@pytest.fixture
def pool(tmp_path):
    store = DiscoverStore(tmp_path / "discover.db")
    yield store
    store.close()


class Ledger:
    def __init__(self):
        self.entries: list[dict] = []

    def append(self, kind, bot_id, session_id, **data):
        self.entries.append({"kind": str(kind), **data})


@pytest.fixture
def ledger():
    return Ledger()


def _facebook_event_row(**over):
    row = {
        "id": "fb-12345",
        "source": "facebook",
        "name": "Block Party",
        "summary": "Music on the lawn.",
        "start_date": "2026-11-25",
        "start_time": "19:00",
        "end_date": "2026-11-25",
        "end_time": "21:00",
        "timezone": "America/New_York",
        "venue": "Hall",
        "address": "1 Main St",
        "city": "",
        "region": "",
        "image_url": "",
        "url": "https://www.facebook.com/events/12345/",
        "categories": [],
        "category_names": [],
        "format": "",
    }
    row.update(over)
    return row


def _birthday_row(name="Dana Reyes", start_date="2026-11-27", source="facebook-birthday"):
    return {
        "id": f"fb-bday-{name.lower().replace(' ', '-')}",
        "source": source,
        "name": name,
        "start_date": start_date,
        "start_time": "",
        "end_date": start_date,
        "end_time": "",
        "timezone": "",
    }


# ---------------------------------------------------------------------------
# Anti-goal 1: the helper never acts. It posts once (to loopback intake) and
# never to Facebook; it never opens, navigates or scripts tabs; the harness
# never fetches Facebook.
# ---------------------------------------------------------------------------


def _helper_src(name: str) -> str:
    path = HELPER_DIR / name
    if not path.exists():
        pytest.skip("src/helper has not merged yet")
    return path.read_text(encoding="utf-8")


class TestHelperNeverPosts:
    def test_sender_posts_only_to_loopback_intake(self):
        src = _helper_src("sender.js")
        assert "/intake/facebook" in src
        assert "127.0.0.1" in src
        assert "facebook.com" not in src

    def test_sender_validates_before_anything_is_sent(self):
        src = _helper_src("sender.js")
        assert src.index("validateReport(report)") < src.index("fetchImpl(url")

    def test_background_worker_never_drives_tabs(self):
        # It may start a named corner's own reader in a tab already there (Facebook moves between
        # pages without loading them); it never opens, navigates or reloads a tab, never runs a
        # function or code of its own in one, only the reader files.
        src = _helper_src("background.js")
        for token in ("tabs.create", "tabs.update", "tabs.reload", "tabs.remove", "tabs.goBack",
                      "tabs.goForward", "windows.create", "func:", "args:", "insertCSS"):
            assert token not in src, token
        assert src.count("executeScript(") == 1
        assert "executeScript({ target: { tabId }, files })" in src
        assert "const files = readerFiles(url);" in src

    def test_corner_readers_only_read_the_open_page(self):
        for name in ("corner-events.js", "corner-invites.js", "corner-birthdays.js", "page-text.js",
                     "corners.js", "clean.js", "schema.js"):
            assert "fetch(" not in _helper_src(name), name

    def test_manifest_reaches_facebook_only_on_the_named_corners(self):
        path = HELPER_DIR / "manifest.json"
        if not path.exists():
            pytest.skip("src/helper has not merged yet")
        manifest = json.loads(path.read_text(encoding="utf-8"))
        facebook = {"https://www.facebook.com/events*", "https://www.facebook.com/birthdays*"}
        for entry in manifest.get("host_permissions", []):
            assert entry.startswith("http://127.0.0.1") or entry in facebook, entry
        assert set(manifest.get("permissions", [])) <= {"alarms", "storage", "scripting"}

    def test_harness_intake_modules_make_no_outbound_requests(self):
        pool_mod = _load("src.events.facebook_pool")
        routes_mod = _load("src.events.facebook_routes")
        for mod in (pool_mod, routes_mod):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            assert "httpx.Client" not in src and "httpx.AsyncClient" not in src
            assert "urlopen" not in src and "import requests" not in src
            assert "fetch(" not in src


# ---------------------------------------------------------------------------
# Anti-goal 2: the calendar never fills with guesses. RSVP and personal rows
# wait as approval cards, everything else lands in the feed, likes create
# nothing, and cancelled or un-RSVP'd rows remove their holds.
# ---------------------------------------------------------------------------


class TestApprovalOnlySplit:
    def test_interested_and_going_wait_as_approval_cards(self, people):
        fc = _load("src.events.facebook_cards")
        for tap in ("Interested", "Going", "  going  "):
            out = fc.split([_facebook_event_row(rsvp=tap)], people)
            assert [(c["id"], c["reason"]) for c in out.approval] == [("fb-12345", "rsvp")]
            assert out.feed == [] and out.dropped == []

    def test_friend_named_event_waits_as_an_approval_card(self, people):
        fc = _load("src.events.facebook_cards")
        out = fc.split([_facebook_event_row(inviter="Dana Reyes")], people)
        assert [(c["id"], c["reason"]) for c in out.approval] == [("fb-12345", "personal")]
        assert out.feed == [] and out.dropped == []

    def test_ordinary_event_lands_in_the_feed_not_on_the_calendar(
        self, people, calendar
    ):
        fc = _load("src.events.facebook_cards")
        out = fc.split([_facebook_event_row()], people)
        assert [(c["id"], c["reason"]) for c in out.feed] == [("fb-12345", "other")]
        assert out.approval == [] and out.dropped == []
        assert calendar.events("2026-11-24", "2026-11-27") == []

    def test_a_like_creates_nothing(self, people, calendar):
        fc = _load("src.events.facebook_cards")
        out = fc.split(
            [_facebook_event_row(rsvp="Like", inviter="Dana Reyes")], people
        )
        assert out.approval == [] and out.feed == []
        assert [(c["id"], c["reason"]) for c in out.dropped] == [("fb-12345", "like")]
        assert calendar.events("2026-11-24", "2026-11-27") == []

    def test_split_reads_memory_without_writing_it(self, people):
        fc = _load("src.events.facebook_cards")
        before = sorted((e.name, e.tier, tuple(e.aliases)) for e in people.all())
        text = people.path.read_text(encoding="utf-8")
        fc.split([_facebook_event_row(inviter="Dana Reyes"),
                  _facebook_event_row(host="Lee Morgan")], people)
        assert sorted((e.name, e.tier, tuple(e.aliases)) for e in people.all()) == before
        assert people.path.read_text(encoding="utf-8") == text

    def test_split_never_imports_the_calendar(self):
        import ast

        fc = _load("src.events.facebook_cards")
        tree = ast.parse(Path(fc.__file__).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
                imported.update(f"{node.module}.{a.name}" for a in node.names)
        assert not any(
            "calendar" in name or "CalendarStore" in name for name in imported
        ), sorted(imported)


class TestBirthdayHolds:
    def test_close_friend_birthday_holds_as_a_made_here_event(
        self, people, calendar
    ):
        birthdays = _load("src.events.birthdays")
        kicks: list[bool] = []
        out = birthdays.hold_birthdays(
            [_birthday_row("Dana Reyes", "2026-12-10")], people, calendar,
            kick=lambda: kicks.append(True),
        )
        assert len(out["held"]) == 1 and out["skipped"] == {}
        assert kicks == [True]
        event = out["held"][0]
        assert event["title"] == "Dana Reyes's birthday"
        assert (event["all_day"], event["start"], event["end"]) == (
            True, "2026-12-10", "2026-12-11")

    def test_non_friend_birthday_holds_nothing(self, people, calendar):
        birthdays = _load("src.events.birthdays")
        out = birthdays.hold_birthdays(
            [_birthday_row("Lee Morgan", "2026-12-10"),
             _birthday_row("A Stranger", "2026-12-10")],
            people, calendar, kick=lambda: None,
        )
        assert out["held"] == [] and out["skipped"] == {"non_friend": 2}
        assert calendar.events("2026-12-10", "2026-12-11") == []

    def test_other_sources_never_hold(self, people, calendar):
        birthdays = _load("src.events.birthdays")
        out = birthdays.hold_birthdays(
            [_birthday_row("Dana Reyes", "2026-12-10", source="facebook"),
             _birthday_row("Dana Reyes", "2026-12-10", source="eventbrite")],
            people, calendar,
        )
        assert out["held"] == [] and out["skipped"] == {"not_birthday_source": 2}
        assert calendar.events("2026-12-10", "2026-12-11") == []


def _timed_hold(calendar, **over):
    fields = {
        "title": "Rooftop Jazz Night",
        "location": "The Roof, 1 High St",
        "description": "Live quartet.\n\nhttps://www.facebook.com/events/111",
        "all_day": False,
        "start": "2026-12-11T00:00:00Z",
        "end": "2026-12-11T02:00:00Z",
        "tz": "America/New_York",
    }
    fields.update(over)
    return calendar.add(fields)


TODAY = "2026-11-20"
NOW = "2026-11-20T12:00:00Z"


class TestHoldLifecycle:
    async def test_cancelled_row_purges_an_unpushed_hold(self, calendar):
        lifecycle = _load("src.events.facebook_lifecycle")
        hold = _timed_hold(calendar)
        kicks: list[int] = []
        out = await lifecycle.sync_hold(
            calendar, "fb-111", [hold["id"]],
            _facebook_event_row(id="fb-111", is_cancelled=True),
            today=TODAY, now=NOW, kick=lambda: kicks.append(1))
        assert out["status"] == "removed" and out["reason"] == "cancelled"
        assert calendar.event(hold["id"]) is None
        assert kicks == [1]

    async def test_unrsvp_removes_the_hold(self, calendar):
        lifecycle = _load("src.events.facebook_lifecycle")
        hold = _timed_hold(calendar)
        out = await lifecycle.sync_hold(
            calendar, "fb-111", [hold["id"]], _facebook_event_row(id="fb-111"),
            today=TODAY, now=NOW)
        assert out["status"] == "removed" and out["reason"] == "un_rsvp"
        assert calendar.events("2026-12-10", "2026-12-13") == []

    async def test_missing_row_never_creates_a_calendar_event(self, calendar):
        lifecycle = _load("src.events.facebook_lifecycle")
        out = await lifecycle.sync_hold(
            calendar, "fb-111", ["no-such-hold"],
            _facebook_event_row(id="fb-111", rsvp="Going"),
            today=TODAY, now=NOW)
        assert out["status"] == "missing"
        assert calendar.events("2026-12-10", "2026-12-13") == []

    async def test_unchanged_row_writes_nothing(self, calendar):
        lifecycle = _load("src.events.facebook_lifecycle")
        hold = _timed_hold(calendar)
        kicks: list[int] = []
        before = calendar.event(hold["id"])
        out = await lifecycle.sync_hold(
            calendar, "fb-111", [hold["id"]],
            _facebook_event_row(
                id="fb-111", name="Rooftop Jazz Night", summary="Live quartet.",
                url="https://www.facebook.com/events/111", rsvp="Going",
                venue="The Roof", address="1 High St",
                start_date="2026-12-10", end_date="2026-12-10"),
            body=SimpleNamespace(
                start="2026-12-11T00:00:00Z", end="2026-12-11T02:00:00Z"),
            today=TODAY, now=NOW, kick=lambda: kicks.append(1))
        assert out["status"] == "unchanged"
        assert kicks == []
        assert calendar.event(hold["id"]) == before

    async def test_garbage_row_never_wipes_a_hold(self, calendar):
        lifecycle = _load("src.events.facebook_lifecycle")
        hold = _timed_hold(calendar)
        with pytest.raises(ValueError):
            await lifecycle.sync_hold(
                calendar, "fb-111", [hold["id"]], {"no": "shape"},
                today=TODAY, now=NOW)
        assert calendar.event(hold["id"]) is not None


# ---------------------------------------------------------------------------
# Anti-goal 3: no birthday misses its nudge. Exactly seven days out, once, on
# both surfaces; a failing delivery is counted, never raised.
# ---------------------------------------------------------------------------


class TestBirthdayNudge:
    def test_nudge_goes_out_exactly_seven_days_before(self):
        birthdays = _load("src.events.birthdays")
        assert birthdays.NUDGE_DAYS_OUT == 7
        assert birthdays.is_due_for_nudge(_birthday_row(start_date="2026-11-27"), "2026-11-20")
        assert not birthdays.is_due_for_nudge(
            _birthday_row(start_date="2026-11-26"), "2026-11-20")
        assert not birthdays.is_due_for_nudge(
            _birthday_row(start_date="2026-11-28"), "2026-11-20")

    def test_other_sources_are_never_due(self):
        birthdays = _load("src.events.birthdays")
        assert not birthdays.is_due_for_nudge(
            _birthday_row("Dana Reyes", "2026-11-27", source="facebook"), "2026-11-20")

    def test_nudge_fires_once_on_both_surfaces(self):
        birthdays = _load("src.events.birthdays")
        sent: list[tuple] = []
        out = birthdays.send_nudges(
            [_birthday_row("Dana Reyes", "2026-11-27"),
             _birthday_row("Lee Morgan", "2026-11-26")],
            "2026-11-20", sink=lambda surface, nudge: sent.append((surface, nudge)))
        assert out == {"nudged": 1, "delivered": 2, "errors": 0}
        assert sorted(s for s, _ in sent) == ["chat", "feed"]
        assert all(n["name"] == "Dana Reyes" for _, n in sent)

    def test_a_failing_delivery_is_counted_never_raised(self):
        birthdays = _load("src.events.birthdays")
        def sink(surface, nudge):
            if surface == "chat":
                raise RuntimeError("feed is down")
        out = birthdays.send_nudges(
            [_birthday_row("Dana Reyes", "2026-11-27")], "2026-11-20", sink=sink)
        assert out == {"nudged": 1, "delivered": 1, "errors": 1}


# ---------------------------------------------------------------------------
# Anti-goal 4: the helper never acts like a bot. The scout stays closed with
# no helper tool, and no Facebook tool is offered to any other bot.
# ---------------------------------------------------------------------------


class TestHelperIsNoBot:
    def test_event_scout_stays_closed(self):
        prompt = REPO / "bot-prompts" / "event-scout.md"
        assert prompt.exists()
        head = prompt.read_text(encoding="utf-8").split("---")[1]
        assert "closed: true" in head
        assert "facebook" not in head

    def test_no_facebook_tool_is_registered_anywhere(self):
        hits = []
        for path in sorted((REPO / "src").rglob("*.py")):
            try:
                src = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for needle in ('group="facebook"', "group='facebook'",
                           'GROUP = "facebook"', "GROUP = 'facebook'",
                           "facebook_router", "events_facebook"):
                if needle in src and "test_facebook_helper" not in src:
                    hits.append(f"{path.relative_to(REPO)}: {needle}")
        allowed = {"src/events/facebook_routes.py"}
        unexpected = [h for h in hits
                      if not any(h.startswith(a + ":") for a in allowed)]
        assert unexpected == []

    def test_friend_check_writes_nothing(self, people):
        friend_check = _load("src.memory.friend_check")
        assert friend_check.is_friend(people, "Dana Reyes") is True
        assert friend_check.is_friend(people, "Dana R") is True
        assert friend_check.is_friend(people, "Lee Morgan") is False
        assert friend_check.is_friend(people, "Alex Rivera") is False
        assert friend_check.is_friend(people, "Nobody Here") is False
        assert friend_check.is_friend(people, "  ") is False
        before = sorted((e.name, e.tier, tuple(e.aliases)) for e in people.all())
        text = people.path.read_text(encoding="utf-8")
        friend_check.is_friend(people, "Dana Reyes")
        assert sorted((e.name, e.tier, tuple(e.aliases)) for e in people.all()) == before
        assert people.path.read_text(encoding="utf-8") == text


# ---------------------------------------------------------------------------
# Route rules: every new POST takes a required JSON body, and ledger entries
# carry ids and counts only, never listing text.
# ---------------------------------------------------------------------------


DASHBOARD = "https://www.facebook.com/events/"
BIRTHDAYS_URL = "https://www.facebook.com/birthdays/"
GOOD_EVENT = {
    "kind": "event", "id": "123456789", "title": "Zephyr Quinoa Festival",
    "start_date": "2026-11-29", "start_time": "10:00",
    "url": "https://www.facebook.com/events/123456789/", "venue": "",
}
GOOD_BIRTHDAY = {
    "kind": "birthday", "name": "Zephyr Quinoa",
    "profile_url": "https://www.facebook.com/profile.php?id=100042",
    "date_text": "November 27",
}


def _report(corner, url, items, **over):
    made = {"helper": "fb-events-v1", "observed_at": "2026-11-20T12:00:00.000Z",
            "corner": corner, "url": url, "items": items}
    made.update(over)
    return made


class TestIntakeRouteRules:
    @pytest.fixture
    async def app_client(self, pool, ledger):
        fr = _load("src.events.facebook_routes")
        httpx = pytest.importorskip("httpx")
        FastAPI = pytest.importorskip("fastapi").FastAPI

        discovery = SimpleNamespace(
            store=pool, ledger=ledger,
            today=lambda: "2026-11-20", now=lambda: "2026-11-20T12:00:00Z")
        app = FastAPI()
        app.include_router(fr.facebook_router(discovery))
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
                transport=transport, base_url="http://localhost") as client:
            yield client, pool, ledger

    async def test_valid_report_keeps_pool_rows(self, app_client):
        client, store, _ = app_client
        r = await client.post("/intake/facebook",
                              json=_report("events_dashboard", DASHBOARD, [GOOD_EVENT]))
        assert r.status_code == 200, r.text
        assert r.json()["kept"] == 1
        row = store.pool_row("fb-123456789")
        assert row is not None and row["source"] == "facebook"

    async def test_bad_shape_is_refused_and_keeps_nothing(self, app_client):
        client, store, _ = app_client
        r = await client.post(
            "/intake/facebook",
            json=_report("events_dashboard", DASHBOARD,
                        [{**GOOD_EVENT, "id": "abc"}]))
        assert r.status_code == 422, r.text
        assert store.pool_size("facebook") == 0
        assert "Zephyr Quinoa Festival" not in r.text

    async def test_every_new_post_needs_a_json_body(self, app_client):
        client, store, _ = app_client
        fr = _load("src.events.facebook_routes")

        discovery = SimpleNamespace(store=store, ledger=Ledger())
        paths = sorted(
            r.path for r in fr.facebook_router(discovery).routes
            if "POST" in (getattr(r, "methods", None) or ()))
        assert {"/intake/facebook", "/intake/facebook/refresh"} <= set(paths)
        foreign = {"Origin": "https://evil.example"}
        for path in paths:
            for body, headers in (
                (b"", foreign),
                (b"{}", {**foreign, "Content-Type": "text/plain"}),
                (b"{}", {**foreign,
                         "Content-Type": "application/x-www-form-urlencoded"}),
            ):
                r = await client.post(path, content=body, headers=headers)
                assert r.status_code == 422, (path, body, headers)
        assert store.pool_size("facebook") == 0
        assert store.pool_size("facebook-birthday") == 0

    async def test_ledger_entries_carry_counts_never_listing_text(self, app_client):
        client, _, ledger = app_client
        r = await client.post(
            "/intake/facebook",
            json=_report("birthdays", BIRTHDAYS_URL,
                         [GOOD_BIRTHDAY,
                          {**GOOD_BIRTHDAY, "date_text": "next Tuesday"}]))
        assert r.status_code == 200, r.text
        assert ledger.entries, "a kept report writes a ledger entry"
        blob = json.dumps(ledger.entries)
        assert "Zephyr Quinoa" not in blob
        assert "November 27" not in blob
        for entry in ledger.entries:
            assert entry["kind"] == "calendar"
            assert set(entry) <= {"kind", "bot_id", "session_id", "event",
                                  "kept", "dropped", "error"}

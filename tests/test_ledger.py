import json
from pathlib import Path

import pytest

from src.memory.exclusion import ExclusionError, exclusion
from src.memory.ledger import Kind, Ledger, ledger_path_for


def test_ledger_sits_beside_the_vault(tmp_path: Path):
    vault = tmp_path / "notes" / "my-vault"
    assert ledger_path_for(vault) == tmp_path / "notes" / "my-vault-ledger"
    vault.mkdir(parents=True)
    led = Ledger(vault)
    assert led.dir.is_dir() and led.dir.parent == vault.parent


def test_append_tail_and_filters(ledger: Ledger):
    ledger.append(Kind.session, "b1", "s1", event="start")
    ledger.append(Kind.turn, "b1", "s1", step=1, content="hello")
    ledger.append(Kind.turn, "b2", "s2", step=1, content="other")
    ledger.append(Kind.tool_call, "b1", "s1", tool="shell")

    rows = ledger.tail()
    assert [r.kind for r in rows] == ["session", "turn", "turn", "tool_call"]
    assert all(r.ts and r.id for r in rows)
    assert [r.kind for r in ledger.tail(kind="turn")] == ["turn", "turn"]
    assert [r.bot_id for r in ledger.tail(session_id="s2")] == ["b2"]
    assert len(ledger.tail(2)) == 2 and ledger.tail(0)[0].kind == "session"

    lines = ledger.file.read_text().splitlines()
    assert len(lines) == 4 and json.loads(lines[1])["data"]["content"] == "hello"


def test_secrets_are_redacted_and_marked(ledger: Ledger):
    exclusion.register_secret_value("sk-or-realkey-0123456789")
    e = ledger.append(Kind.tool_call, "b", "s", output="env shows OPENROUTER_API_KEY=sk-or-realkey-0123456789 and ghp_abcdefghijklmnopqrstuvwxyz0123",
                      nested={"args": ["password: hunter22"]})
    assert "sk-or-realkey" not in json.dumps(e.to_dict())
    assert "ghp_" not in json.dumps(e.to_dict())
    assert "hunter22" not in json.dumps(e.to_dict())
    assert "registered-secret" in e.redacted and "credential-assignment" in e.redacted
    on_disk = ledger.tail(1)[0]
    assert on_disk.redacted == e.redacted and "sk-or-realkey" not in on_disk.data["output"]


def test_clean_entries_are_not_marked(ledger: Ledger):
    assert ledger.append(Kind.turn, "b", "s", content="all clear").redacted == []


def test_refuses_approval_values(ledger: Ledger):
    with pytest.raises(ExclusionError):
        ledger.append("approval_grant", "b", "s")
    with pytest.raises(ExclusionError):
        ledger.append(Kind.approval_decision, "b", "s", decision="approved", draft_body="Dear team")
    # but recording that an approval happened is fine
    ledger.append(Kind.approval_decision, "b", "s", approval_id="a1", decision="approved")


async def test_subscribe_receives_live_entries(ledger: Ledger):
    q = ledger.subscribe()
    e = ledger.append(Kind.pause, "b", "s", reason="offline")
    assert (await q.get()).id == e.id
    ledger.unsubscribe(q)
    ledger.append(Kind.resume, "b", "s")
    assert q.empty()


async def test_append_from_a_worker_thread_wakes_subscribers_on_the_loop(ledger: Ledger):
    import asyncio

    q = ledger.subscribe()
    await asyncio.to_thread(ledger.append, Kind.ingest, "b", "", status="new")
    e = await asyncio.wait_for(q.get(), 2)
    assert e.kind == "ingest" and e.data["status"] == "new"


def test_appends_from_several_threads_never_tear_a_line(ledger: Ledger):
    import sys
    import threading

    def writer(n: int):
        for i in range(200):
            ledger.append(Kind.ingest, f"b{n}", "", status="new", source="x" * 500, i=i)

    old = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(old)
    assert len(list(ledger.iter())) == 800


def test_learn_is_a_ledger_kind():
    assert Kind.learn == "learn"

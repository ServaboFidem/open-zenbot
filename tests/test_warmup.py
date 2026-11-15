"""Start-up warm-up of the memory store: Hindsight is slow cold, so the harness
loads it when it starts and the GUI shows an overlay until it ends."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import httpx

from src.harness.api import Harness, create_app
from src.harness.config import HarnessConfig
from src.memory.graph import LocalGraph, MemoryGraph
from src.memory.ledger import Ledger
from src.memory.warmup import STEPS, Warmup
from src.skills.catalog import Catalog

from .conftest import ScriptedProvider


class ColdStore(LocalGraph):
    """Pretends to be Hindsight: a server to start, a search that loads indexes."""

    kind = "hindsight"

    def __init__(self, root: Path, fail: str = "", gate: threading.Event | None = None) -> None:
        super().__init__(root)
        self.fail, self.gate, self.calls = fail, gate, []

    def _hit(self, step: str) -> None:
        self.calls.append(step)
        if self.gate is not None:
            assert self.gate.wait(10)
        if step == self.fail:
            raise TimeoutError(f"{step} took too long")

    def start(self):
        self._hit("engine")

    def search(self, query, namespaces, limit=10):
        self._hit("store")
        return []


async def test_a_cold_store_warms_every_step_then_is_ready(vault: Path, ledger: Ledger):
    store = ColdStore(vault / "memory")
    w = Warmup(MemoryGraph(store, ledger), ledger)
    assert w.status()["state"] == "pending"
    await w.run()
    assert store.calls == ["engine", "store"]
    st = w.status()
    assert st["state"] == "ready" and st["done"] == st["total"] == len(STEPS) and st["step"] == ""
    assert ledger.tail(1, kind="warmup")[0].data["event"] == "ready"


async def test_status_names_the_running_step(vault: Path, ledger: Ledger):
    gate = threading.Event()
    store = ColdStore(vault / "memory", gate=gate)
    w = Warmup(MemoryGraph(store, ledger), ledger)
    task = asyncio.create_task(w.run())
    for _ in range(200):
        if store.calls:
            break
        await asyncio.sleep(0.01)
    st = w.status()
    assert st["state"] == "warming" and st["step"] == STEPS[0].label and st["done"] == 0 and st["step_expected_s"] > 0
    gate.set()
    await task
    assert w.state == "ready"


async def test_a_failed_step_ends_the_warmup_as_failed(vault: Path, ledger: Ledger):
    store = ColdStore(vault / "memory", fail="store")
    w = Warmup(MemoryGraph(store, ledger), ledger)
    await w.run()
    assert w.state == "failed" and "store: TimeoutError" in w.error and store.calls == ["engine", "store"]


async def test_a_local_store_or_no_key_skips(vault: Path, ledger: Ledger):
    w = Warmup(MemoryGraph(LocalGraph(vault / "memory"), ledger), ledger)
    await w.run()
    assert w.state == "skipped" and "no cold start" in w.reason
    store = ColdStore(vault / "memory2")
    w = Warmup(MemoryGraph(store, ledger), ledger, bound=lambda: False)
    await w.run()
    assert w.state == "skipped" and "no key" in w.reason and store.calls == []


async def test_the_harness_warms_at_start_and_reports_it(vault: Path, tmp_path: Path):
    led = Ledger(vault)
    gate = threading.Event()
    store = ColdStore(vault / "memory", gate=gate)
    h = Harness(config=HarnessConfig(vault_root=vault, shell_cwd=tmp_path, mcp_config=tmp_path / "none.json"),
                provider=ScriptedProvider([]), ledger=led, catalog=Catalog(), memory=MemoryGraph(store, led))
    app = create_app(h)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
            for _ in range(200):
                st = (await c.get("/warmup")).json()
                if st["state"] == "warming":
                    break
                await asyncio.sleep(0.01)
            assert st["state"] == "warming" and st["step"] == STEPS[0].label
            assert (await c.get("/health")).json()["warmup"] == "warming"  # the API answers while it warms
            gate.set()
            for _ in range(200):
                st = (await c.get("/warmup")).json()
                if st["state"] != "warming":
                    break
                await asyncio.sleep(0.01)
            assert st["state"] == "ready" and store.calls == ["engine", "store"]


async def test_the_harness_closes_memory_on_shutdown(vault: Path, tmp_path: Path):
    led = Ledger(vault)
    closed = []

    class Closable(LocalGraph):
        def close(self) -> None:
            closed.append(True)

    h = Harness(config=HarnessConfig(vault_root=vault, shell_cwd=tmp_path, mcp_config=tmp_path / "none.json"),
                provider=ScriptedProvider([]), ledger=led, catalog=Catalog(), memory=MemoryGraph(Closable(vault / "memory"), led))
    app = create_app(h)
    async with app.router.lifespan_context(app):
        assert closed == []
    assert closed == [True]


async def test_hindsight_store_warms_by_starting_its_server(vault: Path, ledger: Ledger):
    class HindsightLike(LocalGraph):
        kind = "hindsight"

        def __init__(self, root: Path) -> None:
            super().__init__(root)
            self.calls: list[str] = []

        def start(self) -> None:
            self.calls.append("start")

        def search(self, query, namespaces, limit=10, timeout=None):
            self.calls.append("search")
            return []

    store = HindsightLike(vault / "memory")
    w = Warmup(MemoryGraph(store, ledger), ledger)
    await w.run()
    assert store.calls == ["start", "search"]
    st = w.status()
    assert st["state"] == "ready" and st["done"] == st["total"] == 2
    store2 = HindsightLike(vault / "memory2")
    w2 = Warmup(MemoryGraph(store2, ledger), ledger, bound=lambda: False)
    await w2.run()
    assert w2.state == "skipped" and "no key" in w2.reason and store2.calls == []


class _Startable(LocalGraph):
    kind = "hindsight"

    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.calls: list[str] = []

    def start(self) -> None:
        self.calls.append("start")

    def search(self, query, namespaces, limit=10, timeout=None):
        self.calls.append("search")
        return []


class _Unbound(ScriptedProvider):
    kind = "unbound"


def _rewarm_harness(vault: Path, tmp_path: Path, store, provider):
    led = Ledger(vault)
    return Harness(config=HarnessConfig(vault_root=vault, shell_cwd=tmp_path, mcp_config=tmp_path / "none.json"),
                   provider=provider, ledger=led, catalog=Catalog(), memory=MemoryGraph(store, led))


async def _settled(h: Harness, state: str) -> None:
    for _ in range(300):
        if h.warmup.state == state:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"warmup is {h.warmup.state!r}, not {state!r}")


async def test_binding_a_key_after_start_starts_a_hindsight_store(vault: Path, tmp_path: Path):
    store = _Startable(vault / "memory")
    h = _rewarm_harness(vault, tmp_path, store, _Unbound([]))
    app = create_app(h)
    async with app.router.lifespan_context(app):
        await _settled(h, "skipped")
        assert "once a key is bound" in h.warmup.reason and store.calls == []
        h.rebind(ScriptedProvider([]), None)  # Settings binds a provider, on the event loop
        await _settled(h, "ready")
        assert store.calls == ["start", "search"] and h.warmup.status()["done"] == 2


async def test_rebind_leaves_a_ready_warmup_alone(vault: Path, tmp_path: Path):
    store = _Startable(vault / "memory")
    h = _rewarm_harness(vault, tmp_path, store, ScriptedProvider([]))
    app = create_app(h)
    async with app.router.lifespan_context(app):
        await _settled(h, "ready")
        h.rebind(ScriptedProvider([]), None)
        await asyncio.sleep(0.1)
        assert store.calls == ["start", "search"] and h.warmup.state == "ready"


async def test_rebind_does_not_warm_a_local_store(vault: Path, tmp_path: Path):
    h = _rewarm_harness(vault, tmp_path, LocalGraph(vault / "memory"), _Unbound([]))
    app = create_app(h)
    async with app.router.lifespan_context(app):
        await _settled(h, "skipped")
        h.rebind(ScriptedProvider([]), None)
        await asyncio.sleep(0.05)
        assert h.warmup.state == "skipped" and "no cold start" in h.warmup.reason

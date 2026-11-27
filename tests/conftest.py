from __future__ import annotations

from pathlib import Path

import os

import pytest

os.environ.setdefault("ZEN_MEMORY_BACKEND", "local")  # the suite runs offline; no test starts a Hindsight server
# a turn note's summary call would take items from other tests' scripted providers; tests/test_turn_notes.py turns them on
os.environ["ZEN_TURN_NOTES"] = "0"
for _name in [n for n in os.environ if n.startswith("LANGFUSE_")]:  # never trace a test run to a real Langfuse
    del os.environ[_name]


@pytest.fixture(autouse=True)
def _bot_prompts_dir(monkeypatch, tmp_path_factory):
    """Bots created in tests persist under a scratch bot-prompts/, never the repo's."""
    monkeypatch.setenv("ZEN_BOT_PROMPTS", str(tmp_path_factory.mktemp("bot-prompts")))

@pytest.fixture(autouse=True)
def _vault_is_scratch(monkeypatch, tmp_path_factory):
    """A bare HarnessConfig() reads ZEN_VAULT: without this it is the user's own ~/.zen-agents/vault, with the
    saved keys and tracing settings beside it. A test that wants another vault sets ZEN_VAULT or passes one."""
    monkeypatch.setenv("ZEN_VAULT", str(tmp_path_factory.mktemp("scratch") / "vault"))


@pytest.fixture(autouse=True)
def _sonos_inert(monkeypatch):
    """A harness built in a test makes a SonosService, whose default speakers are the real ones over SoCo, and the
    app's lifespan starts a look. No test may send a multicast or reach a speaker: the default finds nothing.
    tests/sonos/ passes its own FakeSpeakers; tests/sonos/test_devices.py tests SocoSpeakers against stand-ins."""
    from src.sonos import service

    class NoSpeakers:
        def discover(self, known, scan, timeout):
            return []

        def __getattr__(self, name):
            def refuse(*args, **kwargs):
                from src.sonos.devices import SpeakerError
                raise SpeakerError("network", "no speakers in tests")
            return refuse

    monkeypatch.setattr(service, "SocoSpeakers", NoSpeakers)


@pytest.fixture(autouse=True)
def _speakers_inert(monkeypatch):
    """A harness built in a test makes a SpeakerService, whose default finders are the real ones (zeroconf, SSDP).
    No test may listen on the network: the default finders find nothing. tests/speakers/ passes its own fakes."""
    from src.speakers import service

    class NoSpeakers:
        async def find(self, timeout):
            return []

    monkeypatch.setattr(service, "real_finders", lambda: {p: NoSpeakers() for p in ("airplay", "chromecast", "dlna")})


@pytest.fixture(autouse=True)
def _kokoro_inert(monkeypatch):
    """No test may load the real Kokoro model. tests/kokoro/ passes its own fake library."""
    from src.kokoro import engine

    def refuse(*args, **kwargs):
        # pytest's Failed is a BaseException: the engine's `except Exception` cannot turn it into EngineFailed
        pytest.fail("a test tried to load the Kokoro model", pytrace=False)

    monkeypatch.setattr(engine, "real_load", refuse)


def pytest_configure(config):
    config.addinivalue_line("markers", "real_agent_probe: run agent_clis.probe for real (against fake CLIs)")


@pytest.fixture(autouse=True)
def _agent_probes_inert(request, monkeypatch):
    """The harness probes the claude and codex programs at start; a test never runs the real ones. They read as
    not found, unless the test module is marked `real_agent_probe` (tests/test_agent_clis.py, which uses fake CLIs)."""
    if request.node.get_closest_marker("real_agent_probe"):
        return
    from src.harness import agent_clis

    def probe(vault_root, key, timeout=10.0):
        a, sw = agent_clis.AGENTS[key], agent_clis.load_settings(vault_root)[key]
        return {"id": key, "name": a.program.name, "vendor": a.vendor, "path": None, "source": "", "version": None,
                "auth": "unknown", "confinable": False, "runnable": not agent_clis.RUNNABLE.get(key, ""),
                "enabled": sw.enabled, "ask": sw.ask, "model": sw.model, "usable": False,
                "reason": f"{a.program.name} was not found (probing is off in tests).", "sign_in": a.sign_in}

    monkeypatch.setattr(agent_clis, "probe", probe)


from src.memory.exclusion import exclusion
from src.memory.ledger import Ledger
from src.providers.interface import Completion, Message, ProviderError, ToolCall, Usage

REPO = Path(__file__).resolve().parent.parent


def fake_program(dir: Path, name: str, sh: str = "echo 1.0", cmd: str = "@echo 1.0") -> Path:
    """A program named `name` in `dir` that the host can find and run: a `#!/bin/sh` script running `sh`, or on Windows
    `<name>.cmd` running `cmd`. Answers the path as `shutil.which` finds it."""
    import shutil
    dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        (dir / f"{name}.cmd").write_text(cmd + "\r\n", encoding="utf-8")
    else:
        p = dir / name
        p.write_text(f"#!/bin/sh\n{sh}\n", encoding="utf-8")
        p.chmod(0o755)
    found = shutil.which(name, path=str(dir))
    assert found, f"{name} was written to {dir} but cannot be found there"
    return Path(found)


def completion(text: str = "", calls: list[tuple[str, str, dict]] | None = None, finish: str | None = None) -> Completion:
    tool_calls = [ToolCall(id=i, name=n, arguments=a) for i, n, a in (calls or [])]
    return Completion(
        message=Message("assistant", text, tool_calls=tool_calls),
        finish_reason=finish or ("tool_calls" if tool_calls else "stop"),
        model="mock-model",
        usage=Usage(10, 5, 0.0001),
        kind="mock",
    )


class ScriptedProvider:
    """Returns completions (or raises errors) from a script, in order."""

    kind = "mock"

    def __init__(self, script: list[Completion | ProviderError] | None = None, probe: bool = True):
        self.script = list(script or [])
        self.calls: list[list[Message]] = []
        self.tools: list[list[str]] = []  # the tool names offered on each call
        self.session_ids: list[str | None] = []
        self.probe_result = probe

    async def complete(self, messages, *, tools=None, model=None, session_id=None) -> Completion:
        self.calls.append(list(messages))
        self.tools.append([t.name for t in tools or []])
        self.session_ids.append(session_id)
        if not self.script:
            raise AssertionError("scripted provider ran out of script")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def probe(self) -> bool:
        return self.probe_result


class MockScorer:
    """Answers from a function of the request, or from a script of answer
    dicts. Counts calls so caching tests can see them."""

    kind = "mock-scorer"

    def __init__(self, answer=None, script=None, probe: bool = True):
        self.answer = answer
        self.script = list(script or [])
        self.requests = []
        self.probe_result = probe

    async def decide(self, request):
        from src.decider.interface import DecisionResult, DecisionUsage

        self.requests.append(request)
        if self.answer is not None:
            answers = self.answer(request)
        elif self.script:
            answers = self.script.pop(0)
        else:
            raise AssertionError("mock scorer has nothing to answer with")
        if isinstance(answers, Exception):
            raise answers
        return DecisionResult(answers=answers, model="mock-1", kind=self.kind, usage=DecisionUsage(7, 1, 0.000002), id=f"dec-{len(self.requests)}")

    async def probe(self):
        return self.probe_result


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    return v


@pytest.fixture
def ledger(vault: Path) -> Ledger:
    return Ledger(vault)


@pytest.fixture(autouse=True)
def _clean_exclusion():
    exclusion.clear_values()
    yield
    exclusion.clear_values()


@pytest.fixture
def example_skill_dir() -> Path:
    return REPO / "skills" / "example-brief"

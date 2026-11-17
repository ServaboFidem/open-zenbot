import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.memory import hindsight_process as hp
from src.memory.hindsight_http import HindsightError
from src.memory.hindsight_process import (HindsightConfig, HindsightServer, api_env, instance_name, kill_tree,
                                          ui_argv)
from src.memory import hindsight_store as hs
from src.memory.hindsight_store import HindsightGraph


class FakeProc:
    _next = 100

    def __init__(self, argv, **kw):
        FakeProc._next += 1
        self.pid, self.argv, self.kw, self.code, self.reaped = FakeProc._next, argv, kw, None, False

    def poll(self):
        if self.code is not None:
            self.reaped = True
        return self.code

    def wait(self, timeout=None):
        return 0


class Spawner:
    def __init__(self):
        self.procs = []

    def __call__(self, argv, **kw):
        p = FakeProc(argv, **kw)
        self.procs.append(p)
        return p


class FakeHTTP:
    def __init__(self, ready=True):
        self._ready, self.bank = ready, 0

    def ready(self):
        return self._ready

    def ensure_bank(self):
        self.bank += 1


class FakeJobs:
    def __init__(self, fail_assign=False, events=None):
        self.fail_assign, self.events = fail_assign, events if events is not None else []
        self.terminated, self.closed = [], []

    def assign(self, proc):
        return None if self.fail_assign else ("job", proc.pid)

    def terminate(self, job):
        self.terminated.append(job[1])
        self.events.append(("terminate", job[1]))
        return True

    def close(self, job):
        self.closed.append(job[1])


class FakeAtexit:
    def __init__(self):
        self.registered = []

    def register(self, fn):
        self.registered.append(fn)

    def unregister(self, fn):
        self.registered = [f for f in self.registered if f != fn]


def server(tmp_path, cfg=None, which=lambda n: "C:/node/npx.cmd", http=None, jobs=None, busy=()):
    """`busy`: the ports something else already listens on (no real port is ever probed)."""
    sp, runs = Spawner(), []
    s = HindsightServer(cfg or HindsightConfig(), tmp_path, popen=sp, which=which, http=http or FakeHTTP(),
                        run=lambda argv, **kw: runs.append(argv), jobs=jobs, atexit_=FakeAtexit(),
                        listening=lambda port: port in busy, pg_stop=lambda name: s.pg_stops.append(name))
    s.pg_stops = []
    return s, sp, runs


def test_api_env_binds_loopback_and_utf8(tmp_path):
    env = api_env(HindsightConfig(), tmp_path, "sk-secret")
    assert env["HINDSIGHT_API_HOST"] == "127.0.0.1"
    assert env["PYTHONUTF8"] == "1" and env["PYTHONIOENCODING"] == "utf-8"
    assert env["HINDSIGHT_API_PORT"] == "18888"
    assert re.fullmatch(r"pg0://zen-[0-9a-f]{8}:5556", env["HINDSIGHT_API_DATABASE_URL"])
    assert env["HINDSIGHT_API_LLM_PROVIDER"] == "openrouter"
    assert env["HINDSIGHT_API_LLM_MODEL"] == "google/gemma-4-31b-it"
    assert env["HINDSIGHT_API_LLM_API_KEY"] == "sk-secret"
    assert env["HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS"] == "16000"


def test_api_env_turns_on_llm_traces_observations_and_audit_log(tmp_path, monkeypatch):
    for name in ("HINDSIGHT_API_LLM_TRACE_ENABLED", "HINDSIGHT_API_ENABLE_OBSERVATIONS", "HINDSIGHT_API_AUDIT_LOG_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    env = api_env(HindsightConfig(), tmp_path, "k")
    assert env["HINDSIGHT_API_LLM_TRACE_ENABLED"] == "true"
    assert env["HINDSIGHT_API_ENABLE_OBSERVATIONS"] == "true"
    assert env["HINDSIGHT_API_AUDIT_LOG_ENABLED"] == "true"


def test_api_env_gives_reflect_calls_120s_unless_the_environment_says(tmp_path, monkeypatch):
    monkeypatch.delenv("HINDSIGHT_API_REFLECT_LLM_TIMEOUT", raising=False)
    assert api_env(HindsightConfig(), tmp_path, "k")["HINDSIGHT_API_REFLECT_LLM_TIMEOUT"] == "120"
    monkeypatch.setenv("HINDSIGHT_API_REFLECT_LLM_TIMEOUT", "45")
    assert api_env(HindsightConfig(), tmp_path, "k")["HINDSIGHT_API_REFLECT_LLM_TIMEOUT"] == "45"


def test_api_env_lets_the_environment_turn_them_off(tmp_path, monkeypatch):
    monkeypatch.setenv("HINDSIGHT_API_LLM_TRACE_ENABLED", "false")
    monkeypatch.setenv("HINDSIGHT_API_ENABLE_OBSERVATIONS", "false")
    monkeypatch.setenv("HINDSIGHT_API_AUDIT_LOG_ENABLED", "false")
    env = api_env(HindsightConfig(), tmp_path, "k")
    assert env["HINDSIGHT_API_LLM_TRACE_ENABLED"] == "false"
    assert env["HINDSIGHT_API_ENABLE_OBSERVATIONS"] == "false"
    assert env["HINDSIGHT_API_AUDIT_LOG_ENABLED"] == "false"


def test_key_is_only_in_the_environment_and_log(tmp_path):
    s, sp, _ = server(tmp_path)
    s.start("sk-secret")
    for p in sp.procs:
        assert "sk-secret" not in " ".join(p.argv)
    assert "sk-secret" not in (tmp_path / "hindsight" / "hindsight.log").read_text(errors="ignore")
    assert sp.procs[0].kw["env"]["HINDSIGHT_API_LLM_API_KEY"] == "sk-secret"
    s.stop()


def test_instance_name_differs_per_memory_root(tmp_path):
    a, b = instance_name(tmp_path / "a"), instance_name(tmp_path / "b")
    assert a != b and a == instance_name(tmp_path / "a")
    assert re.fullmatch(r"zen-[0-9a-f]{8}", a)


def test_external_url_starts_nothing(tmp_path):
    s, sp, _ = server(tmp_path, HindsightConfig(url="http://10.0.0.5:18888"))
    s.start("k")
    assert sp.procs == []
    assert s.config.api_url == "http://10.0.0.5:18888"


def test_start_spawns_both_in_new_groups_with_logs_and_is_idempotent(tmp_path):
    s, sp, _ = server(tmp_path)
    s.start("k")
    s.start("k")
    assert len(sp.procs) == 2
    api, ui = sp.procs
    assert Path(api.argv[0]).name.startswith("hindsight-api")
    assert api.kw["env"]["HINDSIGHT_API_HOST"] == "127.0.0.1"
    assert ui.kw["env"]["PORT"] == "9999" and ui.kw["env"]["HOSTNAME"] == "127.0.0.1"
    assert ui.kw["env"]["HINDSIGHT_CP_DATAPLANE_API_URL"] == "http://127.0.0.1:18888"
    for p in sp.procs:
        if sys.platform == "win32":
            assert p.kw["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            assert p.kw["start_new_session"] is True
    assert (tmp_path / "hindsight" / "hindsight.log").exists()
    assert (tmp_path / "hindsight" / "control-plane.log").exists()
    s.stop()


def test_ui_off_starts_only_the_api(tmp_path):
    s, sp, _ = server(tmp_path, HindsightConfig(ui=False))
    s.start("k")
    assert len(sp.procs) == 1
    assert s.ui_status()["state"] == "off"
    s.stop()


def test_ui_argv_pins_the_version_and_loopback():
    assert ui_argv(HindsightConfig(), "npx") == [
        "npx", "-y", "@vectorize-io/hindsight-control-plane@1.2.0", "--port", "9999", "--hostname", "127.0.0.1",
        "--api-url", "http://127.0.0.1:18888"]


def test_missing_npx_reports_unavailable(tmp_path):
    s, sp, _ = server(tmp_path, which=lambda n: None)
    s.start("k")
    assert len(sp.procs) == 1  # the API still runs
    st = s.ui_status()
    assert st["state"] == "unavailable" and st["reason"] == "Node's npx was not found"
    s.stop()


def test_ui_status_starting_then_ready(tmp_path, monkeypatch):
    s, _, _ = server(tmp_path)
    s.start("k")
    monkeypatch.setattr(s, "_api_listening", lambda: True)
    monkeypatch.setattr(s, "_ui_listening", lambda: False)
    assert s.ui_status() == {"url": "http://127.0.0.1:9999", "state": "starting", "reason": ""}
    monkeypatch.setattr(s, "_ui_listening", lambda: True)
    assert s.ui_status()["state"] == "ready"
    s.stop()


def test_ui_status_waits_for_the_api(tmp_path, monkeypatch):
    """The dashboard reads its feature flags once on load: loaded before the API answers, it shows them all off."""
    s, _, _ = server(tmp_path)
    s.start("k")
    monkeypatch.setattr(s, "_ui_listening", lambda: True)
    monkeypatch.setattr(s, "_api_listening", lambda: False)
    assert s.ui_status()["state"] == "starting"
    monkeypatch.setattr(s, "_api_listening", lambda: True)
    assert s.ui_status()["state"] == "ready"
    s.stop()


def test_stop_kills_the_whole_tree_windows(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(hp.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    s, sp, runs = server(tmp_path)
    s.start("k")
    s.stop()
    assert [str(p.pid) for p in sp.procs] == [r[2] for r in runs]
    for p in sp.procs:
        assert ["taskkill", "/PID", str(p.pid), "/T", "/F"] in runs
    s.stop()  # a second stop is a no-op
    assert len(runs) == 2


def test_stop_kills_the_whole_tree_posix(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    sent = []

    def killpg(pg, sig):
        if sig == 0:
            raise ProcessLookupError  # the group is gone after SIGTERM
        sent.append((pg, sig))
        for p in sp.procs:
            if p.pid == pg:
                p.code = 0  # SIGTERM ends the leader

    monkeypatch.setattr(hp.os, "killpg", killpg, raising=False)
    s, sp, _ = server(tmp_path)
    s.start("k")
    s.stop()
    for p in sp.procs:
        assert (p.pid, hp.signal.SIGTERM) in sent


def test_kill_tree_escalates_to_sigkill_posix(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    sent = []
    monkeypatch.setattr(hp.os, "killpg", lambda pg, sig: sent.append(sig), raising=False)
    monkeypatch.setattr(hp.signal, "SIGKILL", 9, raising=False)
    kill_tree(5, grace=0.0)
    assert sent == [hp.signal.SIGTERM, 9]


def test_kill_tree_survives_a_gone_process(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")

    def gone(pg, sig):
        raise ProcessLookupError

    monkeypatch.setattr(hp.os, "killpg", gone, raising=False)
    kill_tree(5)


def test_restart_stops_then_starts_with_the_new_key(tmp_path, monkeypatch):
    # Counted through the Windows tree kill (taskkill goes through `run`, which the fake records). Unpinned, this ran
    # the POSIX path on Linux and macOS: no `run` at all, and a real os.killpg on the fake pids.
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(hp.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    s, sp, runs = server(tmp_path)
    s.start("old")
    first = list(sp.procs)
    s.restart("new")
    assert len(runs) == 2  # both old trees killed
    assert len(sp.procs) == 4
    assert sp.procs[2].kw["env"]["HINDSIGHT_API_LLM_API_KEY"] == "new"
    assert first[0] is not sp.procs[2]
    s.stop()


def test_wait_ready_gives_up_when_the_api_dies(tmp_path):
    s, sp, _ = server(tmp_path, http=FakeHTTP(ready=False))
    s.start("k")
    sp.procs[0].code = 1
    assert s.wait_ready(30) is False


def test_from_env_reads_overrides_and_pins_the_key(monkeypatch):
    for k in ("URL", "PORT", "PG_PORT", "LLM_MODEL", "KEY_REF", "UI", "UI_PORT", "READY_TIMEOUT"):
        monkeypatch.delenv("ZEN_HINDSIGHT_" + k, raising=False)
    d = HindsightConfig.from_env()
    assert d == HindsightConfig() and not d.pinned and d.api_url == "http://127.0.0.1:18888"
    for k, v in {"URL": "http://h:1", "PORT": "1", "PG_PORT": "2", "LLM_MODEL": "m", "KEY_REF": "env:K",
                 "UI": "0", "UI_PORT": "3", "READY_TIMEOUT": "4"}.items():
        monkeypatch.setenv("ZEN_HINDSIGHT_" + k, v)
    c = HindsightConfig.from_env()
    assert (c.url, c.port, c.pg_port, c.llm_model, c.key_ref, c.ui, c.ui_port, c.ready_timeout_s) == (
        "http://h:1", 1, 2, "m", "env:K", False, 3, 4.0)
    assert c.pinned and c.api_url == "http://h:1"


# -- HindsightGraph lifecycle -------------------------------------------------

class FakeServer:
    def __init__(self, ready=True):
        self.ready, self.calls = ready, []

    def start(self, key):
        self.calls.append(("start", key))

    def wait_ready(self, timeout, stop=None):
        self.calls.append(("wait", timeout))
        return self.ready

    def stop(self):
        self.calls.append(("stop",))

    def restart(self, key):
        self.calls.append(("restart", key))

    def ui_status(self):
        return {"url": "u", "state": "off", "reason": ""}


def graph(tmp_path, monkeypatch, server=None, **cfg):
    monkeypatch.setenv("HS_TEST_KEY", "k1")
    monkeypatch.setenv("HS_TEST_KEY2", "k2")
    c = HindsightConfig(key_ref="env:HS_TEST_KEY", ready_timeout_s=7.0, **cfg)
    http = FakeHTTP()
    g = HindsightGraph(tmp_path, c, http=http, server=server or FakeServer())
    return g, http


def test_graph_start_boots_server_ensures_bank_and_reconciles(tmp_path, monkeypatch):
    g, http = graph(tmp_path, monkeypatch)
    seen = []
    monkeypatch.setattr(g, "reconcile", lambda: seen.append(1) or {})
    g.start()
    try:
        assert g.server.calls == [("start", "k1"), ("wait", 7.0)]
        assert http.bank == 1 and seen == [1]
        assert g._reconciler is not None and g._reconciler.daemon and g._reconciler.is_alive()
        n = len(g.server.calls)
        g.start()  # idempotent
        assert len(g.server.calls) == n
    finally:
        g.close()
    assert not g._reconciler or not g._reconciler.is_alive()
    assert g.server.calls[-1] == ("stop",)


def test_graph_start_raises_when_not_ready(tmp_path, monkeypatch):
    g, http = graph(tmp_path, monkeypatch, server=FakeServer(ready=False))
    with pytest.raises(HindsightError):
        g.start()
    assert http.bank == 0
    assert g.server.calls[-1] == ("stop",)  # no children left running
    g.close()


def test_graph_rebind_key_restarts_unless_pinned(tmp_path, monkeypatch):
    g, _ = graph(tmp_path, monkeypatch)
    monkeypatch.setattr(g, "reconcile", lambda: {})
    g.start()
    try:
        g.server.calls.clear()
        g.rebind_key("env:HS_TEST_KEY2")
        assert g._wait_rebind()  # the restart runs on its own thread
        assert g.config.key_ref == "env:HS_TEST_KEY2"
        assert g.server.calls == [("restart", "k2")]
        g.rebind_key("env:HS_TEST_KEY2")  # unchanged: nothing
        assert g._wait_rebind()
        assert len(g.server.calls) == 1
        g.config.pinned = True
        g.rebind_key("env:HS_TEST_KEY")
        assert g._wait_rebind()
        assert g.config.key_ref == "env:HS_TEST_KEY2" and len(g.server.calls) == 1
    finally:
        g.close()


def test_graph_ui_status_delegates_and_is_off_without_a_server(tmp_path, monkeypatch):
    g, _ = graph(tmp_path, monkeypatch)
    assert g.ui_status()["url"] == "u"
    g.server = None
    assert g.ui_status()["state"] == "off"


def test_reconciler_thread_runs_periodically_and_stops(tmp_path, monkeypatch):
    g, _ = graph(tmp_path, monkeypatch)
    monkeypatch.setattr("src.memory.hindsight_store.RECONCILE_S", 0.01)
    ticks = threading.Event()
    n = []
    monkeypatch.setattr(g, "reconcile", lambda: (n.append(1), len(n) >= 3 and ticks.set(), {})[2])
    g.start()
    assert ticks.wait(5)
    t = g._reconciler
    g.close()
    assert not t.is_alive()


# -- fix round 1 --------------------------------------------------------------

def test_kill_tree_reaches_the_group_of_a_reaped_leader_posix(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    sent = []

    def killpg(pg, sig):
        if sig == 0:
            raise ProcessLookupError
        sent.append((pg, sig))

    monkeypatch.setattr(hp.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(hp.os, "getpgid", lambda pid: (_ for _ in ()).throw(ProcessLookupError()), raising=False)
    proc = FakeProc(["x"])
    proc.code = 1  # the leader already exited and was reaped; a Postgres may remain in its group
    kill_tree(proc)
    assert (proc.pid, hp.signal.SIGTERM) in sent


def test_kill_tree_does_not_wait_the_grace_once_the_leader_exited(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    proc = FakeProc(["x"])

    def killpg(pg, sig):
        if sig == 0:  # a zombie leader keeps its group visible until it is reaped by poll()
            if proc.reaped:
                raise ProcessLookupError
            return
        proc.code = 0  # SIGTERM: the leader exits

    monkeypatch.setattr(hp.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(hp.signal, "SIGKILL", 9, raising=False)
    t0 = time.monotonic()
    kill_tree(proc, grace=5.0)
    assert time.monotonic() - t0 < 2.0


def test_stop_runs_both_kills_at_once(tmp_path, monkeypatch):
    def slow_kill(target, **kw):
        time.sleep(0.5)

    monkeypatch.setattr(hp, "kill_tree", slow_kill)
    s, _, _ = server(tmp_path)
    s.start("k")
    t0 = time.monotonic()
    s.stop()
    assert time.monotonic() - t0 < 0.9


def test_windows_job_terminates_the_tree_and_is_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(hp.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    jobs = FakeJobs()
    s, sp, runs = server(tmp_path, jobs=jobs)
    s.start("k")
    s.stop()
    assert sorted(jobs.terminated) == sorted(p.pid for p in sp.procs)
    assert sorted(jobs.closed) == sorted(p.pid for p in sp.procs)
    assert runs == []  # the job was enough; no taskkill


def test_windows_falls_back_to_taskkill_when_there_is_no_job(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(hp.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    s, sp, runs = server(tmp_path, jobs=FakeJobs(fail_assign=True))
    s.start("k")
    s.stop()
    for p in sp.procs:
        assert ["taskkill", "/PID", str(p.pid), "/T", "/F"] in runs


def test_a_dead_api_is_killed_before_it_is_respawned(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(hp.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    events = []
    jobs = FakeJobs(events=events)
    s, sp, _ = server(tmp_path, jobs=jobs)
    real_popen = s._popen
    s._popen = lambda argv, **kw: (events.append(("spawn", argv[0])), real_popen(argv, **kw))[1]
    s.start("k")
    dead = sp.procs[0]
    dead.code = 1
    events.clear()
    s.start("k2")
    assert events[0] == ("terminate", dead.pid)
    assert events[1][0] == "spawn"
    assert sp.procs[2].kw["env"]["HINDSIGHT_API_LLM_API_KEY"] == "k2"
    s.stop()


def test_wait_ready_does_not_accept_a_stale_server_when_our_child_died(tmp_path):
    s, sp, _ = server(tmp_path, http=FakeHTTP(ready=True))
    s.start("k")
    assert s.wait_ready(5) is True
    sp.procs[0].code = 1  # our child failed to bind; something else answers on the port
    assert s.wait_ready(5) is False
    s.stop()


def test_wait_ready_before_any_start_is_false_not_a_stale_pass(tmp_path):
    s, _, _ = server(tmp_path, http=FakeHTTP(ready=True))
    assert s.wait_ready(0.2) is False


def test_wait_ready_returns_early_when_stopped(tmp_path):
    s, _, _ = server(tmp_path, http=FakeHTTP(ready=False))
    s.start("k")
    ev = threading.Event()
    threading.Timer(0.2, ev.set).start()
    t0 = time.monotonic()
    assert s.wait_ready(60, ev) is False
    assert time.monotonic() - t0 < 5
    s.stop()


def test_ui_child_gets_no_api_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    monkeypatch.setenv("MY_OWN_KEY", "sk-mine")
    monkeypatch.setenv("UNRELATED", "keep")
    s, sp, _ = server(tmp_path, HindsightConfig(key_ref="env:MY_OWN_KEY"))
    s.start("sk-mine")
    api, ui = sp.procs
    assert api.kw["env"]["HINDSIGHT_API_LLM_API_KEY"] == "sk-mine"
    assert api.kw["env"]["UNRELATED"] == "keep"  # the API child inherits our environment
    for name in ("OPENROUTER_API_KEY", "MY_OWN_KEY", "HINDSIGHT_API_LLM_API_KEY", "UNRELATED"):
        assert name not in ui.kw["env"]
    s.stop()


def test_ui_child_inherits_only_the_allow_list(tmp_path, monkeypatch):
    # A deny-list missed VAULT_<NAME> and ZEN_GUI_KEY: the control plane now gets only what node needs.
    monkeypatch.setenv("ZEN_GUI_KEY", "sk-gui")
    monkeypatch.setenv("VAULT_WORK", "C:/secret/vault")
    monkeypatch.setenv("ACME_SECRET_TOKEN", "tok")
    monkeypatch.setenv("PATH", "C:/node")
    monkeypatch.setenv("https_proxy", "http://proxy:3128")
    s, sp, _ = server(tmp_path)
    s.start("k")
    env = sp.procs[1].kw["env"]
    for name in ("ZEN_GUI_KEY", "VAULT_WORK", "ACME_SECRET_TOKEN"):
        assert not [k for k in env if k.upper() == name]
    assert [v for k, v in env.items() if k.upper() == "PATH"] == ["C:/node"]
    assert [v for k, v in env.items() if k.upper() == "HTTPS_PROXY"] == ["http://proxy:3128"]
    assert env["PORT"] == "9999" and env["HINDSIGHT_CP_DATAPLANE_API_URL"] == "http://127.0.0.1:18888"
    assert set(k.upper() for k in env) <= hp.UI_ENV_ALLOW | set(hp.ui_env(HindsightConfig()))
    s.stop()


def test_atexit_hook_is_registered_while_children_run_and_removed_on_stop(tmp_path):
    s, _, _ = server(tmp_path)
    s.start("k")
    assert s._atexit.registered == [s.stop]
    s.stop()
    assert s._atexit.registered == []


def test_graph_close_during_a_slow_start_returns_promptly_and_stops_the_children(tmp_path, monkeypatch):
    class SlowServer(FakeServer):
        def wait_ready(self, timeout, stop=None):
            self.calls.append(("wait", timeout))
            self.waiting.set()
            stop.wait(30)
            return False

    srv = SlowServer()
    srv.waiting = threading.Event()
    g, _ = graph(tmp_path, monkeypatch, server=srv)
    errors = []

    def boot():
        try:
            g.start()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    t = threading.Thread(target=boot)
    t.start()
    assert srv.waiting.wait(5)
    t0 = time.monotonic()
    g.close()
    assert time.monotonic() - t0 < 5
    t.join(5)
    assert not t.is_alive() and errors == []
    assert ("stop",) in srv.calls


def test_graph_rebind_key_before_start_only_updates_the_config(tmp_path, monkeypatch):
    g, _ = graph(tmp_path, monkeypatch)
    g.rebind_key("env:HS_TEST_KEY2")
    assert g.config.key_ref == "env:HS_TEST_KEY2"
    assert g.server.calls == []  # nothing spawned


def test_graph_rebind_key_after_close_spawns_nothing(tmp_path, monkeypatch):
    g, _ = graph(tmp_path, monkeypatch)
    monkeypatch.setattr(g, "reconcile", lambda: {})
    g.start()
    g.close()
    g.server.calls.clear()
    g.rebind_key("env:HS_TEST_KEY2")
    assert g.server.calls == []


def test_graph_rebind_key_keeps_the_old_ref_when_the_new_key_does_not_resolve(tmp_path, monkeypatch):
    g, _ = graph(tmp_path, monkeypatch)
    monkeypatch.setattr(g, "reconcile", lambda: {})
    g.start()
    try:
        monkeypatch.delenv("HS_NOPE", raising=False)
        g.rebind_key("env:HS_NOPE")  # logged, not raised
        assert g.config.key_ref == "env:HS_TEST_KEY"
        assert ("restart", "k1") not in g.server.calls and not any(c[0] == "restart" for c in g.server.calls)
    finally:
        g.close()


def test_a_stuck_old_reconciler_is_not_revived_by_the_next_start(tmp_path, monkeypatch):
    g, _ = graph(tmp_path, monkeypatch)
    monkeypatch.setattr("src.memory.hindsight_store.RECONCILE_S", 0.01)
    monkeypatch.setattr("src.memory.hindsight_store.CLOSE_JOIN_S", 0.05)
    gate, entered = threading.Event(), threading.Event()

    def reconcile():
        if threading.current_thread().name == "hindsight-reconciler":
            entered.set()
            gate.wait(10)  # a pass stuck on the network
        return {}

    monkeypatch.setattr(g, "reconcile", reconcile)
    g.start()
    first = g._reconciler
    assert entered.wait(5)
    g.close()  # join times out: the thread is still stuck
    assert first.is_alive()
    entered.clear()
    g.start()
    gate.set()
    first.join(5)
    assert not first.is_alive()  # it saw its own event, not the new one
    try:
        alive = [t for t in threading.enumerate() if t.name == "hindsight-reconciler" and t.is_alive()]
        assert alive == [g._reconciler]
    finally:
        g.close()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Objects")
def test_winjobs_terminates_a_real_child_tree():
    jobs = hp.WinJobs()
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    job = jobs.assign(proc)
    try:
        assert job
        assert proc.poll() is None
        assert jobs.terminate(job)
        proc.wait(timeout=10)
        assert proc.poll() is not None
    finally:
        if job:
            jobs.close(job)
        if proc.poll() is None:
            proc.kill()


# -- fix round 2 --------------------------------------------------------------

def test_ui_env_drops_the_key_whatever_the_case_of_its_name(tmp_path, monkeypatch):
    monkeypatch.setenv("MY_CASE_KEY", "sk-mine")
    s, sp, _ = server(tmp_path, HindsightConfig(key_ref="env:my_case_key"))
    s.start("sk-mine")
    ui = sp.procs[1]
    assert not [k for k in ui.kw["env"] if k.upper() in ("MY_CASE_KEY", "OPENROUTER_API_KEY")]
    s.stop()


def test_a_partial_server_start_does_not_leak_the_api_child(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_TEST_KEY", "k1")
    sp, runs = Spawner(), []

    def popen(argv, **kw):
        if len(sp.procs) == 1:
            raise OSError("cannot start the control plane")
        return sp(argv, **kw)

    srv = HindsightServer(HindsightConfig(key_ref="env:HS_TEST_KEY"), tmp_path, popen=popen,
                          which=lambda n: "npx", http=FakeHTTP(), run=lambda argv, **kw: runs.append(argv),
                          jobs=None, atexit_=FakeAtexit(), listening=lambda port: False, pg_stop=lambda name: None)
    g = HindsightGraph(tmp_path, srv.config, http=FakeHTTP(), server=srv)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(hp.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    with pytest.raises(OSError):
        g.start()
    assert ["taskkill", "/PID", str(sp.procs[0].pid), "/T", "/F"] in runs


class EmptyingJobs(FakeJobs):
    """A job whose processes die some time after TerminateJobObject (as on Windows: it is asynchronous)."""

    def __init__(self, events):
        super().__init__(events=events)
        self.waited = []

    def wait_empty(self, job, timeout):
        self.waited.append((job[1], timeout))
        self.events.append(("empty", job[1]))
        return True

    def close(self, job):
        super().close(job)
        self.events.append(("close", job[1]))


def test_windows_stop_returns_only_once_the_job_is_empty(tmp_path, monkeypatch):
    # TerminateJobObject returns before the processes are gone: Postgres still listened for ~0.2 s after
    # stop() returned, so a restart could find the old one. stop() waits for the job to empty first.
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(hp.subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    events = []
    jobs = EmptyingJobs(events)
    s, sp, runs = server(tmp_path, cfg=HindsightConfig(ui=False), jobs=jobs)
    s.start("k")
    pid = sp.procs[0].pid
    s.stop()
    assert events == [("terminate", pid), ("empty", pid), ("close", pid)]
    assert jobs.waited == [(pid, hp.KILL_GRACE_S)]
    assert runs == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")
def test_winjobs_wait_empty_sees_a_terminated_tree_gone():
    # A real job: a child and its grandchild, both ended by terminate(); wait_empty returns once both are gone.
    code = ("import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "print('up', flush=True); time.sleep(60)")
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, **hp._group_flags())
    jobs = hp.WinJobs()
    job = jobs.assign(proc)
    try:
        assert job is not None
        assert proc.stdout.readline().strip() == "up"  # the grandchild exists (in the job: it was born there)
        assert jobs.active(job) >= 2
        assert jobs.terminate(job)
        assert jobs.wait_empty(job, 10.0)
        assert jobs.active(job) == 0
        assert proc.poll() is not None  # exited, not only counted out of the job: no wait needed
    finally:  # a failure part way must not leak the tree, the member handles terminate() took, or the pipe
        try:
            if job is not None:
                jobs.terminate(job)
                jobs.wait_empty(job, 10.0)  # closes the member handles terminate() kept
                jobs.close(job)
            elif proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
        finally:
            proc.stdout.close()


# -- final fix wave -------------------------------------------------------------

@pytest.mark.parametrize("port", [18888, 5556])
def test_a_busy_api_or_postgres_port_spawns_nothing_and_names_the_port(tmp_path, port):
    # Something already answering there is not ours: a second harness would write into the first one's database.
    s, sp, _ = server(tmp_path, busy={port})
    with pytest.raises(HindsightError) as e:
        s.start("k")
    assert sp.procs == []
    assert f"port {port} is already in use by another program" in str(e.value)
    assert "ZEN_HINDSIGHT_PORT (and ZEN_HINDSIGHT_PG_PORT)" in str(e.value)


def test_a_busy_ui_port_leaves_the_api_running_and_the_dashboard_unavailable(tmp_path):
    s, sp, _ = server(tmp_path, busy={9999})
    s.start("k")
    assert len(sp.procs) == 1 and Path(sp.procs[0].argv[0]).name.startswith("hindsight-api")
    st = s.ui_status()
    assert st["state"] == "unavailable" and "port 9999 is already in use" in st["reason"]
    assert "ZEN_HINDSIGHT_UI_PORT" in st["reason"]
    s.stop()


def test_a_busy_api_port_fails_the_graph_start_with_the_reason(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_TEST_KEY", "k1")
    s, sp, _ = server(tmp_path, HindsightConfig(key_ref="env:HS_TEST_KEY"), busy={18888})
    g = HindsightGraph(tmp_path, s.config, http=FakeHTTP(), server=s)
    with pytest.raises(HindsightError, match="port 18888 is already in use"):
        g.start()
    assert sp.procs == []


class GatedServer(FakeServer):
    """wait_ready blocks until the test opens the gate (a start() that holds _life for minutes)."""

    def __init__(self):
        super().__init__()
        self.waiting, self.gate = threading.Event(), threading.Event()

    def wait_ready(self, timeout, stop=None):
        self.calls.append(("wait", timeout))
        self.waiting.set()
        self.gate.wait(10)
        return True


def boot_in_background(g):
    t = threading.Thread(target=g.start, daemon=True)
    t.start()
    return t


def test_rebind_key_returns_at_once_while_start_waits_for_hindsight(tmp_path, monkeypatch):
    srv = GatedServer()
    g, _ = graph(tmp_path, monkeypatch, server=srv)
    monkeypatch.setattr(g, "reconcile", lambda: {})
    t = boot_in_background(g)
    try:
        assert srv.waiting.wait(5)
        t0 = time.monotonic()
        g.rebind_key("env:HS_TEST_KEY2")
        assert time.monotonic() - t0 < 0.5  # the event loop calls this: it must never wait for _life
        srv.gate.set()
        t.join(5)
        assert g._wait_rebind()
        assert srv.calls[0] == ("start", "k1") and srv.calls[-1] == ("restart", "k2")
        assert g.config.key_ref == "env:HS_TEST_KEY2"
    finally:
        srv.gate.set()
        g.close()


def test_the_same_ref_with_a_new_value_restarts_and_the_same_value_does_not(tmp_path, monkeypatch):
    # Settings always binds `env:ZEN_GUI_KEY`: comparing refs never noticed a new key under it.
    g, _ = graph(tmp_path, monkeypatch)
    monkeypatch.setattr(g, "reconcile", lambda: {})
    g.start()
    try:
        g.server.calls.clear()
        g.rebind_key("env:HS_TEST_KEY")  # same ref, same value
        assert g._wait_rebind() and g.server.calls == []
        monkeypatch.setenv("HS_TEST_KEY", "k1-new")
        g.rebind_key("env:HS_TEST_KEY")
        assert g._wait_rebind() and g.server.calls == [("restart", "k1-new")]
        monkeypatch.setenv("HS_TEST_KEY2", "k1-new")  # another name for the running key: no restart
        g.rebind_key("env:HS_TEST_KEY2")
        assert g._wait_rebind() and g.server.calls == [("restart", "k1-new")]
        assert g.config.key_ref == "env:HS_TEST_KEY2"
    finally:
        g.close()


def failed_record(g, text="Dana likes tea"):
    from src.memory.graph import SHARED, Fact, Tier
    f = Fact(text=text, namespace=SHARED, tier=Tier.project)
    g._graph.put(f)
    g._records[f.id] = {"tags": ["personal"], "sent_tags": ["personal"], "text_hash": "h", "mission": 0,
                        "op": "op-dead", "state": "failed", "attempts": 4, "sent": True}
    return f.id


def test_failed_retains_get_a_new_round_on_start_on_a_key_change_and_hourly(tmp_path, monkeypatch):
    g, _ = graph(tmp_path, monkeypatch)
    monkeypatch.setattr(g, "reconcile", lambda: {})
    fid = failed_record(g)
    g.start()
    try:
        rec = g._records[fid]
        assert (rec["state"], rec["attempts"], rec["op"], rec["epoch"]) == ("unsent", 0, None, 1)  # on start

        rec.update(state="failed", attempts=4)
        monkeypatch.setenv("HS_TEST_KEY", "k1-new")
        g.rebind_key("env:HS_TEST_KEY")
        assert g._wait_rebind()
        assert (rec["state"], rec["attempts"], rec["epoch"]) == ("unsent", 0, 2)  # on a key change

        rec.update(state="failed", attempts=4)
        now = [time.monotonic()]
        g._clock = lambda: now[0]
        g._failed_round_at = now[0]
        g._tick()
        assert rec["state"] == "failed"  # not yet due
        now[0] += hs.RETRY_FAILED_S
        g._tick()
        assert (rec["state"], rec["attempts"], rec["epoch"]) == ("unsent", 0, 3)  # the hourly round
    finally:
        g.close()


class Ledger:
    def __init__(self):
        self.entries = []

    def append(self, kind, bot_id, session_id, **data):
        self.entries.append((kind, data))


def test_the_supervisor_restarts_a_dead_api_at_most_once_per_period(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_TEST_KEY", "k1")
    s, sp, _ = server(tmp_path, HindsightConfig(key_ref="env:HS_TEST_KEY", ui=False))
    g = HindsightGraph(tmp_path, s.config, http=FakeHTTP(), server=s)
    g.ledger = Ledger()
    monkeypatch.setattr(g, "reconcile", lambda: {})
    now = [1000.0]
    g._clock = lambda: now[0]
    g.start()
    try:
        assert len(sp.procs) == 1
        g._tick()
        assert len(sp.procs) == 1  # alive: nothing to do
        sp.procs[0].code = 1  # it crashed
        g._tick()
        assert len(sp.procs) == 2 and sp.procs[1].kw["env"]["HINDSIGHT_API_LLM_API_KEY"] == "k1"
        assert [d["event"] for k, d in g.ledger.entries if k == "memory"] == ["server_restarted"]
        sp.procs[1].code = 1  # and again at once: wait out the period
        now[0] += hs.RECONCILE_S / 2
        g._tick()
        assert len(sp.procs) == 2
        now[0] += hs.RECONCILE_S
        g._tick()
        assert len(sp.procs) == 3
        assert [d["event"] for k, d in g.ledger.entries if k == "memory"] == ["server_restarted"] * 2
    finally:
        g.close()


def test_the_supervisor_leaves_a_closed_graph_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_TEST_KEY", "k1")
    s, sp, _ = server(tmp_path, HindsightConfig(key_ref="env:HS_TEST_KEY", ui=False))
    g = HindsightGraph(tmp_path, s.config, http=FakeHTTP(), server=s)
    monkeypatch.setattr(g, "reconcile", lambda: {})
    g.start()
    proc = sp.procs[0]
    g.close()
    proc.code = 1
    s._api = proc  # as if it died during close
    g._supervise()
    assert len(sp.procs) == 1


def test_the_dashboard_waits_for_a_key_then_reports_a_failed_start(tmp_path, monkeypatch):
    g, _ = graph(tmp_path, monkeypatch, server=FakeServer(ready=False))
    st = g.ui_status()
    assert st["state"] == "unavailable" and st["reason"] == "The memory dashboard starts once a key is bound."
    with pytest.raises(HindsightError):
        g.start()
    st = g.ui_status()
    assert st["state"] == "unavailable" and "not ready within 7s" in st["reason"]
    g.server.ready = True
    monkeypatch.setattr(g, "reconcile", lambda: {})
    g.start()
    try:
        assert g.ui_status() == {"url": "u", "state": "off", "reason": ""}  # started: what the server says
    finally:
        g.close()


def test_the_dashboard_is_plainly_off_when_disabled_or_external(tmp_path, monkeypatch):
    g, _ = graph(tmp_path, monkeypatch, ui=False)
    assert g.ui_status() == {"url": "u", "state": "off", "reason": ""}
    g, _ = graph(tmp_path, monkeypatch, url="http://10.0.0.5:18888")
    g.server = None
    assert g.ui_status()["state"] == "off"


# -- residual fixes -------------------------------------------------------------

def supervised(tmp_path, monkeypatch, busy):
    monkeypatch.setenv("HS_TEST_KEY", "k1")
    monkeypatch.setattr(hp, "PORT_FREE_WAIT_S", 0.05, raising=False)
    s, sp, _ = server(tmp_path, HindsightConfig(key_ref="env:HS_TEST_KEY", ui=False), busy=busy)
    g = HindsightGraph(tmp_path, s.config, http=FakeHTTP(), server=s)
    g.ledger = Ledger()
    monkeypatch.setattr(g, "reconcile", lambda: {})
    now = [1000.0]
    g._clock = lambda: now[0]
    return g, s, sp, now


def test_a_restart_that_hits_a_busy_port_is_retried_in_a_later_period(tmp_path, monkeypatch):
    busy = set()
    g, s, sp, now = supervised(tmp_path, monkeypatch, busy)
    g.start()
    try:
        sp.procs[0].code = 1  # crashed
        busy.add(18888)  # and something else took its port meanwhile
        g._tick()
        assert len(sp.procs) == 1 and s._api is None
        assert s.api_exited()  # still wanted: a failed start must not end the supervision
        (first,) = [d for k, d in g.ledger.entries if d.get("event") == "server_restarted"]
        assert "port 18888 is already in use" in first["error"]
        busy.clear()
        now[0] += hs.RECONCILE_S
        g._tick()
        assert len(sp.procs) == 2 and not s.api_exited()
    finally:
        g.close()


def test_after_stop_the_supervisor_never_spawns(tmp_path, monkeypatch):
    g, s, sp, now = supervised(tmp_path, monkeypatch, set())
    g.start()
    try:
        s.stop()
        assert not s.api_exited()
        now[0] += hs.RECONCILE_S
        g._tick()
        assert len(sp.procs) == 1
    finally:
        g.close()


def test_the_ports_of_our_own_killed_tree_get_a_moment_to_close(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "PORT_FREE_WAIT_S", 2.0, raising=False)
    s, sp, _ = server(tmp_path, HindsightConfig(ui=False))
    s.start("k")
    sp.procs[0].code = 1
    answers = iter([True, True, False])  # its Postgres still answers twice after the kill, then is gone
    s._listening = lambda port: next(answers, False)
    s.start("k")
    assert len(sp.procs) == 2
    s.stop()
    s._listening = lambda port: True  # a never-started API: no grace, a foreign server at once
    t0 = time.monotonic()
    with pytest.raises(HindsightError):
        s.start("k")
    assert time.monotonic() - t0 < 1.0


class WatchedLock:
    """_life, telling the test when the rebind thread has reached it."""

    def __init__(self):
        self._lock, self.rebind_waiting = threading.Lock(), threading.Event()

    def acquire(self, blocking=True, timeout=-1):
        if threading.current_thread().name == "hindsight-rebind":
            self.rebind_waiting.set()
        return self._lock.acquire(blocking, timeout)

    def release(self):
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def test_a_newer_key_supersedes_one_still_waiting_to_be_applied(tmp_path, monkeypatch):
    # Deterministic: the second key is handed over only once the rebind thread waits on _life.
    monkeypatch.setenv("HS_TEST_KEY3", "k3")
    srv = GatedServer()
    g, _ = graph(tmp_path, monkeypatch, server=srv)
    g._life = life = WatchedLock()
    monkeypatch.setattr(g, "reconcile", lambda: {})
    t = boot_in_background(g)
    try:
        assert srv.waiting.wait(5)
        g.rebind_key("env:HS_TEST_KEY2")
        assert life.rebind_waiting.wait(5)
        g.rebind_key("env:HS_TEST_KEY3")
        srv.gate.set()
        t.join(5)
        assert g._wait_rebind()
        assert [c for c in srv.calls if c[0] == "restart"] == [("restart", "k3")]  # k2 never restarted
        assert g.config.key_ref == "env:HS_TEST_KEY3"
    finally:
        srv.gate.set()
        g.close()


def test_start_is_never_neither_starting_nor_started_before_it_ends(tmp_path, monkeypatch):
    # A rebind_key landing where both flags were False only moved the ref: the restart was lost.
    class Recording(HindsightGraph):
        def __setattr__(self, name, value):
            super().__setattr__(name, value)
            if name in ("_starting", "_started") and "_starting" in self.__dict__ and "_started" in self.__dict__:
                self.__dict__.setdefault("seen", []).append((self._starting, self._started))

    monkeypatch.setenv("HS_TEST_KEY", "k1")
    g = Recording(tmp_path, HindsightConfig(key_ref="env:HS_TEST_KEY"), http=FakeHTTP(), server=FakeServer())
    monkeypatch.setattr(g, "reconcile", lambda: {})
    g.start()
    try:
        seen = g.seen[g.seen.index((True, False)):]
        assert (False, False) not in seen and seen[-1] == (False, True)
    finally:
        g.close()


# -- leftovers: a harness killed before stop() must not leave Hindsight or its Postgres running ----

import json  # noqa: E402


def test_start_records_the_children_with_the_harness_pid_and_stop_forgets_them(tmp_path):
    s, sp, _ = server(tmp_path)
    s.start("k")
    rec = json.loads((tmp_path / "hindsight" / "children.json").read_text())
    assert rec == {"harness": hp.os.getpid(), "api": sp.procs[0].pid, "ui": sp.procs[1].pid,
                   "pg": instance_name(tmp_path)}
    s.stop()
    assert not (tmp_path / "hindsight" / "children.json").exists()


def test_stop_also_stops_the_postgres_instance(tmp_path):
    """pg0 runs Postgres in its own session: killing the API's group does not reach it."""
    s, _, _ = server(tmp_path)
    s.start("k")
    s.stop()
    assert s.pg_stops == [instance_name(tmp_path)]
    s.stop()  # nothing of ours is running: Postgres is not asked again
    assert s.pg_stops == [instance_name(tmp_path)]


def leftovers(tmp_path, monkeypatch, *, owner_alive=False, lines=None, pg=None):
    """A record left by harness 4242 (api 501, ui 502), and the fakes a reap would touch."""
    (tmp_path / "hindsight").mkdir(parents=True, exist_ok=True)
    (tmp_path / "hindsight" / "children.json").write_text(json.dumps(
        {"harness": 4242, "api": 501, "ui": 502, "pg": pg or instance_name(tmp_path)}))
    lines = lines if lines is not None else {501: "/venv/bin/python3 /venv/bin/hindsight-api",
                                             502: "npm exec @vectorize-io/hindsight-control-plane@1.2.0 --port 9999"}
    killed = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(hp, "pid_alive", lambda pid: pid in lines or (pid == 4242 and owner_alive))
    monkeypatch.setattr(hp, "cmdline", lambda pid: lines.get(pid, ""))
    monkeypatch.setattr(hp, "kill_tree", lambda pid, **kw: killed.append(pid))
    return killed


def test_a_start_stops_what_a_dead_harness_left_behind(tmp_path, monkeypatch):
    killed = leftovers(tmp_path, monkeypatch)
    s, sp, _ = server(tmp_path)
    s.start("k")
    assert sorted(killed) == [501, 502]
    assert s.pg_stops == [instance_name(tmp_path)]
    assert json.loads((tmp_path / "hindsight" / "children.json").read_text())["harness"] == hp.os.getpid()
    assert len(sp.procs) == 2  # then our own children start


def test_a_live_harness_children_are_left_alone(tmp_path, monkeypatch):
    killed = leftovers(tmp_path, monkeypatch, owner_alive=True)
    s, _, _ = server(tmp_path)
    s.start("k")
    assert killed == [] and s.pg_stops == []


def test_a_reused_pid_is_not_killed(tmp_path, monkeypatch):
    killed = leftovers(tmp_path, monkeypatch, lines={501: "/usr/bin/firefox", 502: "bash"})
    s, _, _ = server(tmp_path)
    s.start("k")
    assert killed == []
    assert s.pg_stops == [instance_name(tmp_path)]  # its Postgres may still be running: pg0 stop is a no-op if not


def test_another_memory_roots_postgres_is_never_stopped(tmp_path, monkeypatch):
    leftovers(tmp_path, monkeypatch, pg="zen-deadbeef")
    s, _, _ = server(tmp_path)
    s.start("k")
    assert s.pg_stops == []


def test_windows_leaves_leftovers_to_the_job_object(tmp_path, monkeypatch):
    killed = leftovers(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "platform", "win32")
    s, _, _ = server(tmp_path, jobs=FakeJobs())
    s._reap_leftovers()
    assert killed == [] and s.pg_stops == []

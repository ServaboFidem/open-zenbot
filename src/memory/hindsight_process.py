"""Hindsight's child processes: `hindsight-api` (which spawns an embedded Postgres via pg0)
and the web control plane (`npx`). Both bind 127.0.0.1 only. Stopping kills both process trees and
then stops the pg0 Postgres instance: on POSIX pg0 starts Postgres in a session of its own, outside the
API's process group, so a force-killed API would leave it running. The children are recorded in
`hindsight/children.json` with the harness's pid; a start that finds a record left by a harness that no
longer runs (it was killed before it could stop them) stops those leftovers before it spawns. The LLM key travels in the child's environment only; it is
never put on a command line, in a log, or in an exception message.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .hindsight_http import HindsightError, HindsightHTTP

log = logging.getLogger(__name__)

LOOPBACK = "127.0.0.1"
CONTROL_PLANE = "@vectorize-io/hindsight-control-plane@1.2.0"
KILL_GRACE_S = 10.0
PORT_FREE_WAIT_S = 2.0  # after killing our own dead tree: how long its ports may take to close
CHILDREN_FILE = "children.json"  # under log_dir: {harness, api, ui, pg} of the children last started
# what a recorded pid's command line must contain to still be the child we started (a pid can be reused)
CHILD_MARKERS = {"api": "hindsight-api", "ui": "hindsight-control-plane"}


@dataclass
class HindsightConfig:
    url: str = ""  # an external Hindsight server: start nothing
    port: int = 18888
    pg_port: int = 5556
    llm_model: str = "google/gemma-4-31b-it"
    key_ref: str = "env:OPENROUTER_API_KEY"
    pinned: bool = False  # ZEN_HINDSIGHT_KEY_REF was set: Settings must not move the key
    ui: bool = True
    ui_port: int = 9999
    max_completion_tokens: int = 16000
    ready_timeout_s: float = 300.0

    @classmethod
    def from_env(cls) -> "HindsightConfig":
        e = os.environ
        d = cls()
        return cls(
            url=e.get("ZEN_HINDSIGHT_URL", d.url),
            port=int(e.get("ZEN_HINDSIGHT_PORT", d.port)),
            pg_port=int(e.get("ZEN_HINDSIGHT_PG_PORT", d.pg_port)),
            llm_model=e.get("ZEN_HINDSIGHT_LLM_MODEL", d.llm_model),
            key_ref=e.get("ZEN_HINDSIGHT_KEY_REF", d.key_ref),
            pinned="ZEN_HINDSIGHT_KEY_REF" in e,
            ui=e.get("ZEN_HINDSIGHT_UI", "1") != "0",
            ui_port=int(e.get("ZEN_HINDSIGHT_UI_PORT", d.ui_port)),
            ready_timeout_s=float(e.get("ZEN_HINDSIGHT_READY_TIMEOUT", d.ready_timeout_s)),
        )

    @property
    def api_url(self) -> str:
        return self.url or f"http://{LOOPBACK}:{self.port}"


def instance_name(memory_root: Path) -> str:
    """pg0 instance: one per memory root, so two roots never share a database."""
    resolved = str(Path(memory_root).resolve())
    return "zen-" + hashlib.md5(resolved.encode("utf-8")).hexdigest()[:8]


def api_env(config: HindsightConfig, memory_root: Path, key: str) -> dict[str, str]:
    return {
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "HINDSIGHT_API_HOST": LOOPBACK,
        "HINDSIGHT_API_PORT": str(config.port),
        "HINDSIGHT_API_DATABASE_URL": f"pg0://{instance_name(memory_root)}:{config.pg_port}",
        "HINDSIGHT_API_LLM_PROVIDER": "openrouter",
        "HINDSIGHT_API_LLM_MODEL": config.llm_model,
        "HINDSIGHT_API_LLM_API_KEY": key,
        "HINDSIGHT_API_RETAIN_MAX_COMPLETION_TOKENS": str(config.max_completion_tokens),
        # on by default (LLM call traces in the control plane, observation consolidation, the audit log); the environment can turn them off
        "HINDSIGHT_API_LLM_TRACE_ENABLED": os.environ.get("HINDSIGHT_API_LLM_TRACE_ENABLED", "true"),
        "HINDSIGHT_API_ENABLE_OBSERVATIONS": os.environ.get("HINDSIGHT_API_ENABLE_OBSERVATIONS", "true"),
        "HINDSIGHT_API_AUDIT_LOG_ENABLED": os.environ.get("HINDSIGHT_API_AUDIT_LOG_ENABLED", "true"),
        # Hindsight's own 30s per reflect LLM call cannot fit a ~50k-token synthesis on our model, so every
        # broad reflect fails after its retries; retain and consolidation already get 120s
        "HINDSIGHT_API_REFLECT_LLM_TIMEOUT": os.environ.get("HINDSIGHT_API_REFLECT_LLM_TIMEOUT", "120"),
    }


def ui_argv(config: HindsightConfig, npx: str) -> list[str]:
    return [npx, "-y", CONTROL_PLANE, "--port", str(config.ui_port), "--hostname", LOOPBACK,
            "--api-url", config.api_url]


def ui_env(config: HindsightConfig) -> dict[str, str]:
    return {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PORT": str(config.ui_port), "HOSTNAME": LOOPBACK,
            "HINDSIGHT_CP_DATAPLANE_API_URL": config.api_url}


def api_command() -> str:
    """The `hindsight-api` script beside the running interpreter."""
    return str(Path(sys.executable).parent / ("hindsight-api.exe" if sys.platform == "win32" else "hindsight-api"))


class WinJobs:
    """Windows Job Objects (ctypes, no dependency): a child assigned to a job with KILL_ON_JOB_CLOSE dies with
    the job, so terminating the job ends the whole tree, and a crashed harness (its handle closed by the OS)
    takes the children down too. Every method returns a failure value instead of raising."""

    _KILL_ON_CLOSE = 0x2000
    _BASIC_ACCOUNTING_INFO = 1
    _PROCESS_ID_LIST = 3
    _SYNCHRONIZE = 0x00100000
    _MAX_MEMBERS = 1024
    _EXTENDED_LIMIT_INFO = 9

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes
        self._c = ctypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
                                                ctypes.c_void_p]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.WaitForSingleObject.restype = wintypes.DWORD
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._k = k
        self._dying: dict[int, list[Any]] = {}  # job -> handles of its members when it was terminated

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes",
                                                       "WriteBytes", "OtherBytes")]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        class Accounting(ctypes.Structure):
            _fields_ = [*((n, ctypes.c_int64) for n in ("TotalUserTime", "TotalKernelTime", "ThisPeriodTotalUserTime",
                                                         "ThisPeriodTotalKernelTime")),
                        *((n, wintypes.DWORD) for n in ("TotalPageFaultCount", "TotalProcesses", "ActiveProcesses",
                                                        "TotalTerminatedProcesses"))]

        class Members(ctypes.Structure):
            _fields_ = [("Assigned", wintypes.DWORD), ("Listed", wintypes.DWORD),
                        ("Ids", ctypes.c_size_t * self._MAX_MEMBERS)]

        self._Extended = Extended
        self._Accounting = Accounting
        self._Members = Members

    def assign(self, proc: Any) -> Any:
        """A job handle holding `proc`, or None when that could not be done."""
        try:
            job = self._k.CreateJobObjectW(None, None)
            if not job:
                return None
            info = self._Extended()
            info.Basic.LimitFlags = self._KILL_ON_CLOSE
            ok = self._k.SetInformationJobObject(job, self._EXTENDED_LIMIT_INFO, self._c.byref(info),
                                                 self._c.sizeof(info))
            if not ok or not self._k.AssignProcessToJobObject(job, int(proc._handle)):
                self._k.CloseHandle(job)
                return None
            return job
        except Exception:  # noqa: BLE001 - no job: kill_tree falls back to taskkill
            return None

    def _member_handles(self, job: Any) -> list[Any]:
        """A SYNCHRONIZE handle to each process in the job (one that is already gone is skipped)."""
        try:
            info = self._Members()
            if not self._k.QueryInformationJobObject(job, self._PROCESS_ID_LIST, self._c.byref(info),
                                                     self._c.sizeof(info), None):
                return []
            handles = [self._k.OpenProcess(self._SYNCHRONIZE, False, int(info.Ids[i])) for i in range(info.Listed)]
            return [h for h in handles if h]
        except Exception:  # noqa: BLE001
            return []

    def terminate(self, job: Any) -> bool:
        """Start killing every process in the job. The handles to them are kept for `wait_empty`."""
        handles = self._member_handles(job)
        try:
            ok = bool(self._k.TerminateJobObject(job, 1))
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            self._dying[int(job)] = handles
        else:
            self._close_all(handles)
        return ok

    def active(self, job: Any) -> int:
        """How many processes are still in the job; -1 when that could not be read."""
        try:
            info = self._Accounting()
            ok = self._k.QueryInformationJobObject(job, self._BASIC_ACCOUNTING_INFO, self._c.byref(info),
                                                   self._c.sizeof(info), None)
            return int(info.ActiveProcesses) if ok else -1
        except Exception:  # noqa: BLE001
            return -1

    def wait_empty(self, job: Any, timeout: float) -> bool:
        """True once every process the job held at `terminate` has exited. TerminateJobObject only starts the
        kill (the job's own count drops to 0 at once): until each process is signalled, a terminated Postgres
        may still listen on its port and hold its data directory."""
        handles = self._dying.pop(int(job), [])
        deadline = time.monotonic() + timeout
        try:
            for h in handles:
                left_ms = max(0, int((deadline - time.monotonic()) * 1000))
                if self._k.WaitForSingleObject(h, left_ms) != 0:  # WAIT_OBJECT_0
                    return False
            return self.active(job) in (0, -1)
        except Exception:  # noqa: BLE001
            return False
        finally:
            self._close_all(handles)

    def _close_all(self, handles: list[Any]) -> None:
        for h in handles:
            self.close(h)

    def close(self, job: Any) -> None:
        try:
            self._k.CloseHandle(job)
        except Exception:  # noqa: BLE001
            pass


def _alive(proc: Any) -> bool:
    return proc is not None and proc.poll() is None


def kill_tree(target: Any, *, run: Callable[..., Any] = subprocess.run, grace: float = KILL_GRACE_S,
              job: Any = None, jobs: Any = None) -> None:
    """End a process and everything it started. `target` is a Popen (preferred: it is polled, so the leader
    is reaped and the wait ends early) or a pid. Never raises: the process may already be gone.

    Windows: terminate the job holding it and wait (up to `grace`) until its processes are gone, else
    `taskkill /T /F`. POSIX: the child was started with
    start_new_session, so its process group id is its pid, which stays valid after the leader is reaped.
    pg0's Postgres is not in that group (pg0 starts it in a session of its own): see stop_postgres."""
    pid = getattr(target, "pid", target)
    proc = target if hasattr(target, "poll") else None
    try:
        if sys.platform == "win32":
            if job is not None and jobs is not None and jobs.terminate(job):
                wait = getattr(jobs, "wait_empty", None)
                if wait is not None and not wait(job, grace):
                    log.warning("hindsight: processes of pid %s were still exiting after %g s", pid, grace)
                return
            run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, check=False)
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if proc is None or proc.poll() is not None:  # poll() reaps the zombie leader
                try:
                    os.killpg(pid, 0)
                except ProcessLookupError:
                    return
            time.sleep(0.1)
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def _group_flags() -> dict[str, Any]:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)}
    return {"start_new_session": True}


# The only variables of ours the control plane (npx/node) inherits: what Windows, node and a proxy need. Upper-cased;
# names are compared case-insensitively (Windows env names are, and lowercase proxy variables are common).
UI_ENV_ALLOW = frozenset({
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE",
    "HOMEPATH", "HOME", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)",
    "COMMONPROGRAMFILES", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "LANG", "LC_ALL", "TERM",
    "NODE_OPTIONS", "NODE_EXTRA_CA_CERTS", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
})


def _ui_inherited() -> dict[str, str]:
    """The allow-listed part of our environment: no key, no vault path, nothing else of the harness's."""
    return {k: v for k, v in os.environ.items() if k.upper() in UI_ENV_ALLOW}


def port_in_use(port: int) -> bool:
    """Something answers a TCP connect on 127.0.0.1:`port`."""
    try:
        with socket.create_connection((LOOPBACK, port), timeout=0.3):
            return True
    except OSError:
        return False


def busy_port_reason(port: int, fix: str) -> str:
    return f"port {port} is already in use by another program; set {fix}"


def stop_postgres(instance: str) -> None:
    """Stop the pg0 Postgres instance `instance` (a fast, clean shutdown). A no-op when it is not running;
    never raises. hindsight-api stops it itself on a clean exit; this covers an API that was force-killed."""
    try:
        import pg0  # installed with hindsight-api

        pg0.stop(instance)
    except Exception as e:  # noqa: BLE001 - shutdown and cleanup go on without it
        log.warning("hindsight: could not stop Postgres instance %s: %s", instance, e)


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, but not ours to signal
    except OSError:
        return False
    return True


def cmdline(pid: int) -> str:
    """The process's command line, or "" when it cannot be read (gone, or no /proc and no ps)."""
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        pass
    try:
        r = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


class HindsightServer:
    def __init__(self, config: HindsightConfig, memory_root: Path, *, popen: Callable[..., Any] = subprocess.Popen,
                 which: Callable[[str], str | None] = shutil.which, http: HindsightHTTP | None = None,
                 run: Callable[..., Any] = subprocess.run, jobs: Any = "auto", atexit_: Any = atexit,
                 listening: Callable[[int], bool] = port_in_use,
                 pg_stop: Callable[[str], None] = stop_postgres) -> None:
        self.config = config
        self.memory_root = Path(memory_root)
        self._popen, self._which, self._run, self._atexit = popen, which, run, atexit_
        self._listening = listening
        self._pg_stop = pg_stop
        self.http = http if http is not None else HindsightHTTP(config.api_url)
        self._jobs = (WinJobs() if sys.platform == "win32" else None) if jobs == "auto" else jobs
        self._api: Any = None
        self._ui: Any = None
        self._api_job: Any = None
        self._ui_job: Any = None
        self._ui_reason = ""
        self._wanted = False  # start() was asked for and stop() was not: the API should be running
        self._lock = threading.Lock()
        self._logs: list[Any] = []
        self._hooked = False

    @property
    def log_dir(self) -> Path:
        return self.memory_root / "hindsight"

    def _spawn(self, argv: list[str], env: dict[str, str], log_name: str,
               inherit: dict[str, str] | None = None) -> tuple[Any, Any]:
        """`inherit`: the part of our environment the child gets (all of it when None)."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        fh = open(self.log_dir / log_name, "ab")
        self._logs.append(fh)
        child_env = dict(os.environ) if inherit is None else inherit
        proc = self._popen(argv, env={**child_env, **env}, stdout=fh, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, **_group_flags())
        job = self._jobs.assign(proc) if self._jobs is not None else None
        if not self._hooked:  # safety net: a harness that exits without stop() still ends the children
            self._atexit.register(self.stop)
            self._hooked = True
        return proc, job

    @property
    def _children_file(self) -> Path:
        return self.log_dir / CHILDREN_FILE

    def _record(self) -> None:
        """Note which children are ours, so a later harness can stop them if this one dies without stop()."""
        data = {"harness": os.getpid(), "api": getattr(self._api, "pid", None), "ui": getattr(self._ui, "pid", None),
                "pg": instance_name(self.memory_root)}
        try:
            self._children_file.write_text(json.dumps(data), encoding="utf-8")
        except OSError as e:
            log.warning("hindsight: could not record the children: %s", e)

    def _forget(self) -> None:
        try:
            self._children_file.unlink()
        except OSError:
            pass

    def _reap_leftovers(self) -> None:
        """Stop the children a dead harness left behind (it was killed before stop() ran). POSIX only: on
        Windows the job object ends them with the harness. Only a record whose harness no longer runs is
        acted on, and only a pid whose command line is still the child's is killed."""
        if sys.platform == "win32":
            return
        try:
            data = json.loads(self._children_file.read_text(encoding="utf-8"))
            owner = int(data["harness"])
        except (OSError, ValueError, KeyError, TypeError):
            return
        if owner == os.getpid() or pid_alive(owner):
            return  # ours, or a live harness's: not leftovers
        found = []
        for role, marker in CHILD_MARKERS.items():
            pid = data.get(role)
            if isinstance(pid, int) and pid_alive(pid) and marker in cmdline(pid):
                found.append(pid)
        threads = [threading.Thread(target=kill_tree, args=(pid,), kwargs={"run": self._run}) for pid in found]
        for t in threads:  # both trees at once, as in stop()
            t.start()
        for t in threads:
            t.join()
        if found:
            log.warning("hindsight: stopped processes left by harness %s: %s", owner, found)
        instance = data.get("pg")
        if instance == instance_name(self.memory_root):  # only the instance this memory root owns
            self._pg_stop(instance)
        self._forget()

    def _kill(self, proc: Any, job: Any) -> None:
        kill_tree(proc, run=self._run, job=job, jobs=self._jobs)
        if job is not None and self._jobs is not None:
            self._jobs.close(job)

    def start(self, key: str) -> None:
        """Start the API and (unless off) the control plane. Non-blocking; idempotent; nothing for an external URL.
        A child that died is killed first (its Postgres may still hold the port) and then respawned.
        Nothing is spawned on a port something already answers on: that is not ours, and taking it for ours
        would write into another harness's database. A busy API or Postgres port raises HindsightError; a busy
        control-plane port leaves the dashboard unavailable and the API running."""
        if self.config.url:
            return
        with self._lock:
            self._wanted = True  # even if this start raises: the supervisor keeps trying until stop()
            killed = False
            if self._api is not None and not _alive(self._api):
                self._kill(self._api, self._api_job)
                self._api = self._api_job = None
                killed = True
            if self._api is None:
                self._reap_leftovers()
                for port in (self.config.port, self.config.pg_port):
                    if self._busy(port, PORT_FREE_WAIT_S if killed else 0.0):
                        raise HindsightError(busy_port_reason(
                            port, "ZEN_HINDSIGHT_PORT (and ZEN_HINDSIGHT_PG_PORT) to free ports"))
                self._api, self._api_job = self._spawn([api_command()], api_env(self.config, self.memory_root, key),
                                                       "hindsight.log")
                self._record()
            if not self.config.ui:
                return
            if self._ui is not None and not _alive(self._ui):
                self._kill(self._ui, self._ui_job)
                self._ui = self._ui_job = None
            if self._ui is not None:
                return
            npx = self._which("npx")
            if npx is None:
                self._ui_reason = "Node's npx was not found"
                return
            if self._listening(self.config.ui_port):
                self._ui_reason = busy_port_reason(self.config.ui_port, "ZEN_HINDSIGHT_UI_PORT to a free port")
                return
            self._ui_reason = ""
            self._ui, self._ui_job = self._spawn(ui_argv(self.config, npx), ui_env(self.config), "control-plane.log",
                                                 inherit=_ui_inherited())
            self._record()

    def _busy(self, port: int, grace: float) -> bool:
        """Something answers on `port`, still after `grace` seconds (the tree we just killed may take a moment
        to close its ports; anything that outlasts that is not ours)."""
        deadline = time.monotonic() + grace
        while self._listening(port):
            if time.monotonic() >= deadline:
                return True
            time.sleep(0.1)
        return False

    def wait_ready(self, timeout: float, stop: threading.Event | None = None) -> bool:
        """True once the API answers and (when we started it) our own child is still alive: a stale server on
        the port must not pass for ours. Returns early (False) if `stop` is set or the child died."""
        deadline = time.monotonic() + timeout
        while True:
            if stop is not None and stop.is_set():
                return False
            answered = self.http.ready()
            if not self.config.url and not _alive(self._api):
                return False  # never started, or died: waiting longer will not help
            if answered:
                return True
            left = deadline - time.monotonic()
            if left <= 0:
                return False
            if stop is not None:
                stop.wait(min(1.0, left))
            else:
                time.sleep(min(1.0, left))

    def stop(self) -> None:
        with self._lock:
            self._wanted = False
            pairs = [(self._api, self._api_job), (self._ui, self._ui_job)]
            self._api = self._ui = self._api_job = self._ui_job = None
            logs, self._logs = self._logs, []
            hooked, self._hooked = self._hooked, False
        if hooked:
            self._atexit.unregister(self.stop)
        threads = [threading.Thread(target=self._kill, args=pair) for pair in pairs if pair[0] is not None]
        for t in threads:  # both trees at once: two graces must not run one after the other
            t.start()
        for t in threads:
            t.join()
        for proc, _ in pairs:
            if proc is not None:
                try:
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001 - already killed; do not block shutdown
                    pass
        if pairs[0][0] is not None:  # the API was ours: its Postgres is too, even if the API was force-killed
            self._pg_stop(instance_name(self.memory_root))
            self._forget()
        for fh in logs:
            try:
                fh.close()
            except OSError:
                pass

    def restart(self, key: str) -> None:
        self.stop()
        self.start(key)

    def api_exited(self) -> bool:
        """The API should be running (start() was asked for, stop() was not) and is not: it crashed, was killed
        from outside, or the last start raised before spawning it (a busy port, a restart that failed)."""
        api = self._api
        return self._wanted and (api is None or not _alive(api))

    def _ui_listening(self) -> bool:
        return self._listening(self.config.ui_port)

    def _api_listening(self) -> bool:
        """The API's port answers. uvicorn binds only after the app's startup (its Postgres) is done."""
        return self._listening(self.config.port)

    def ui_status(self) -> dict[str, str]:
        url = f"http://{LOOPBACK}:{self.config.ui_port}"
        if not self.config.ui or self.config.url:
            return {"url": url, "state": "off", "reason": ""}
        if self._ui_reason:
            return {"url": url, "state": "unavailable", "reason": self._ui_reason}
        if self._ui is None:
            return {"url": url, "state": "unavailable", "reason": "The memory dashboard is not running."}
        if self._ui.poll() is not None:
            return {"url": url, "state": "unavailable", "reason": "the control plane exited; see control-plane.log"}
        # ready only once the API answers too: the dashboard reads its feature flags (observations, audit log...)
        # once on load, and a load before the API is up leaves them all showing off until a reload
        up = self._ui_listening() and self._api_listening()
        return {"url": url, "state": "ready" if up else "starting", "reason": ""}

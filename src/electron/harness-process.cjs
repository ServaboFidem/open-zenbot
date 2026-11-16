// Starts, adopts and stops the Python harness for the installed app. A harness already answering /health on our
// port (a developer's, or one left by another window) is adopted and never stopped by us. Otherwise the venv's
// Python runs `src.harness.main` in the bundled harness folder, with the managed environment, and /health is
// polled for as long as the child is alive: the first start downloads tools and memory models and can take many
// minutes, so there is deliberately no deadline.
//
// Everything with a side effect is passed in, so this file needs no Electron:
//   spawn(cmd, args, opts) -> child (pid, stdout, stderr, on('exit'|'error'))   no shell
//   fetch(url) -> { ok, json() }; portInUse(port) -> Promise<boolean>
//   killTree(pid) -> Promise<void>   Windows: taskkill /PID <pid> /T /F
//   kill(pid, signal)                process.kill (negative pid = the process group on Linux and macOS)
//   logStream: { write(chunk) }; sleep(ms) -> Promise<void>
const path = require('node:path');
const { uvEnv } = require('./runtime-paths.cjs');

const POLL_MS = 500;
const STOP_STEP_MS = 250;
const STOP_STEPS = 180; // 45 s, as scripts/dev.sh
const TAIL_LINES = 40;
const GRACE_MS = 100;
const GRACE_STEPS = 20;

// Keep harness.log and one harness.log.1: call before opening the log for a new start.
function rotateLog({ dir, fs, platform = process.platform }) {
  const p = platform === 'win32' ? path.win32 : path.posix;
  fs.mkdirSync(dir, { recursive: true });
  const current = p.join(dir, 'harness.log');
  if (!fs.existsSync(current)) return;
  const old = p.join(dir, 'harness.log.1');
  fs.rmSync(old, { force: true });
  fs.renameSync(current, old);
}

function createHarness({ paths, platform, port = 8765, baseEnv = process.env, spawn, fetch, portInUse, killTree, kill = process.kill.bind(process), logStream, sleep }) {
  const p = platform === 'win32' ? path.win32 : path.posix;
  const sep = platform === 'win32' ? ';' : ':';
  const url = `http://127.0.0.1:${port}`;
  let child = null;
  let exited = true;
  let closed = true;
  let flushAll = () => {};
  let stopping = false;
  let starting = null;
  let started = false;
  const exitCallbacks = [];
  let tail = [];

  function childEnv() {
    const env = uvEnv(paths, baseEnv);
    const key = platform === 'win32' ? (Object.keys(env).find(k => k.toUpperCase() === 'PATH') || 'PATH') : 'PATH';
    const lead = [p.dirname(paths.uv), paths.nodeBin].join(sep);
    env[key] = env[key] ? lead + sep + env[key] : lead;
    Object.assign(env, {
      ZEN_PORT: String(port),
      ZEN_HARNESS_URL: url,
      ZEN_USER_CONTENT: '1',
      ZEN_ENV_FILE: paths.envFile,
      PYTHONUNBUFFERED: '1',
    });
    return env;
  }

  async function healthy() {
    try {
      const res = await fetch(`${url}/health`);
      if (!res || !res.ok) return false;
      const body = await res.json();
      return !!body && body.ok === true;
    } catch {
      return false;
    }
  }

  // Returns a flush that moves a last line without a newline into the tail.
  function collect(stream) {
    if (!stream || typeof stream.on !== 'function') return () => {};
    let partial = '';
    const push = l => { tail.push(l); if (tail.length > TAIL_LINES) tail = tail.slice(-TAIL_LINES); };
    stream.on('data', chunk => {
      try { logStream.write(chunk); } catch { /* a broken log must not stop the harness */ }
      const lines = (partial + String(chunk)).split(/\r?\n/);
      partial = lines.pop();
      lines.forEach(push);
    });
    const flush = () => { if (partial) { push(partial); partial = ''; } };
    stream.on('end', flush);
    return flush;
  }

  function launch() {
    tail = [];
    stopping = false;
    started = false;
    exited = false;
    closed = false;
    const c = spawn(paths.python, ['-m', 'src.harness.main'], {
      cwd: paths.harness,
      env: childEnv(),
      detached: platform !== 'win32',
      windowsHide: true,
    });
    child = c;
    const flushes = [collect(c.stdout), collect(c.stderr)];
    flushAll = () => flushes.forEach(f => f());
    // 'exit' means the process is gone (stop() and onExit act on it); 'close' comes after its output streams end,
    // so start() waits for that before it reads the tail.
    const gone = () => {
      if (child !== c || exited) return;
      exited = true;
      if (started && !stopping) for (const cb of exitCallbacks) cb();
    };
    c.on('exit', gone);
    c.on('close', () => { gone(); flushAll(); closed = true; });
    c.on('error', err => { gone(); tail.push(`could not start: ${err && err.message}`); closed = true; });
  }

  async function doStart() {
    if (await healthy()) return { ok: true, adopted: true };
    if (await portInUse(port)) return { ok: false, reason: 'port-in-use', detail: `Port ${port} is in use by another program.` };
    launch();
    let grace = GRACE_STEPS;
    while (!closed) {
      if (exited) {
        // gone, but its output may still be arriving: wait a little, never for ever
        if (grace-- <= 0) break;
        await sleep(GRACE_MS);
        continue;
      }
      if (await healthy()) {
        if (exited) continue;
        started = true;
        return { ok: true, adopted: false };
      }
      await sleep(POLL_MS);
    }
    flushAll();
    return { ok: false, reason: 'exited', detail: tail.slice(-TAIL_LINES).join('\n') };
  }

  function start() {
    if (!starting) starting = doStart().finally(() => { starting = null; });
    return starting;
  }

  async function stop() {
    const c = child;
    if (!c || exited) return;
    stopping = true;
    if (platform === 'win32') {
      await killTree(c.pid);
      return;
    }
    const signal = sig => { try { kill(-c.pid, sig); } catch { /* already gone */ } };
    signal('SIGTERM');
    for (let i = 0; i < STOP_STEPS; i++) {
      if (exited) return;
      await sleep(STOP_STEP_MS);
    }
    if (!exited) signal('SIGKILL');
  }

  return {
    start,
    stop,
    onExit: cb => { exitCallbacks.push(cb); },
    get url() { return url; },
  };
}

module.exports = { createHarness, rotateLog };

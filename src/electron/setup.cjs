// First-launch setup for the installed app: checks free space, installs Python and the harness's packages with
// the bundled uv, downloads Node.js when the machine has no node >= 24, then writes the stamp. Steps that finished
// are not run again on retry(), and the stamp is written only by a run that got through every step, so a failed
// or cancelled run is set up again at the next launch.
//
// Everything with a side effect is passed in, so this file needs no Electron:
//   run(cmd, args, { env, cwd, onLine, signal }) -> Promise<{ code }>   start a child from an argument array, no
//       shell; call onLine for each output line; on signal's 'abort' stop the child (its whole tree) and resolve.
//   download({ url, dest, sha256, onProgress }) -> Promise<dest>        downloadVerified with fetch and fs bound.
//   extract(archive, destDir) -> Promise<void>                          unpack as it is, top folder included.
//   freeBytes(dir) -> Promise<number>; findNode() -> Promise<string|null> (a node >= 24 on PATH, else null).
//   fs: the node:fs API (only fs.promises mkdir, writeFile, rm, rename are used); emit(state) on every change.
const path = require('node:path');
const { pinFor, OfflineError } = require('./download.cjs');
const { uvEnv } = require('./runtime-paths.cjs');

const NEEDED_BYTES = 6 * 1024 ** 3;
const DETAIL_MAX = 200;
const PYTHON = '3.12';
const OFFLINE_MESSAGE = 'No internet connection. Connect and press Retry.';

const STEPS = [
  ['disk', 'Checking free space'],
  ['python', 'Installing Python'],
  ['packages', "Installing OpenZenBot's Python packages"],
  ['node', 'Installing Node.js'],
  ['stamp', 'Finishing'],
];

// uv's wording when it cannot reach the network (reqwest/hyper errors on Linux and Windows): a refused or reset
// connection, a failed DNS lookup, a timeout, an unreachable network. Kept small on purpose: anything else is
// shown as the step failing, with the output behind "Show details".
const OFFLINE_RE = /connection refused|connection reset|dns error|name resolution|failed to lookup address|no such host is known|name or service not known|timed out|network is unreachable/i;
const isOfflineOutput = lines => lines.some(l => OFFLINE_RE.test(l));

function needsSetup({ stamp, appVersion, lockHash }) {
  if (!stamp || typeof stamp !== 'object' || Array.isArray(stamp)) return true;
  if (typeof stamp.appVersion !== 'string' || typeof stamp.lockHash !== 'string') return true;
  return stamp.appVersion !== appVersion || stamp.lockHash !== lockHash;
}

class StepError extends Error {}

function createSetup({ paths, platform, arch, appVersion, lockHash, pins, run, download, extract, freeBytes, findNode, fs, emit = () => {}, baseEnv = process.env }) {
  const p = platform === 'win32' ? path.win32 : path.posix;
  const state = { steps: STEPS.map(([id, title]) => ({ id, title, status: 'pending' })), line: '', detail: [], error: null };
  let running = null;
  let controller = null;
  let cancelled = false;

  const snapshot = () => ({ ...state, steps: state.steps.map(s => ({ ...s })), detail: [...state.detail] });
  const changed = () => emit(snapshot());
  const addLine = line => {
    const text = String(line);
    state.line = text;
    state.detail.push(text);
    if (state.detail.length > DETAIL_MAX) state.detail.splice(0, state.detail.length - DETAIL_MAX);
    changed();
  };

  async function uv(args, cwd) {
    controller = new AbortController();
    const out = [];
    let code;
    try {
      ({ code } = await run(paths.uv, args, {
        env: uvEnv(paths, baseEnv), cwd, signal: controller.signal,
        onLine: l => { out.push(String(l)); addLine(l); },
      }));
    } finally { controller = null; }
    if (cancelled) return;
    if (code !== 0) throw new StepError(isOfflineOutput(out) ? OFFLINE_MESSAGE : undefined);
  }

  const gb = n => String(Number((n / 1024 ** 3).toFixed(1)));

  const work = {
    async disk() {
      await fs.promises.mkdir(paths.root, { recursive: true });
      const free = await freeBytes(paths.root);
      if (free < NEEDED_BYTES) throw new StepError(`OpenZenBot needs about ${gb(NEEDED_BYTES)} GB free on this disk; ${gb(free)} GB is free.`);
    },
    python: () => uv(['python', 'install', PYTHON], paths.root),
    packages: () => uv(['sync', '--frozen', '--no-dev', '--python', PYTHON, '--project', paths.harness], paths.harness),
    async node() {
      const found = await findNode();
      if (found) { addLine(`Using Node.js at ${found}`); return 'skipped'; }
      const pin = pinFor(pins, 'node', platform, arch);
      const top = `node-v${pins.node.version}-${platform === 'win32' ? 'win' : platform}-${arch}`;
      const archive = p.join(paths.downloads, path.posix.basename(new URL(pin.url).pathname));
      const staging = paths.node + '.part';
      await fs.promises.mkdir(paths.downloads, { recursive: true });
      let shown = -1;
      await download({
        url: pin.url, dest: archive, sha256: pin.sha256,
        onProgress: ({ received, total }) => {
          const mb = Math.floor(received / 1024 ** 2);
          if (mb === shown) return;
          shown = mb;
          state.line = total ? `Downloading Node.js: ${mb} of ${Math.ceil(total / 1024 ** 2)} MB` : `Downloading Node.js: ${mb} MB`;
          changed();
        },
      });
      if (cancelled) return;
      await fs.promises.rm(staging, { recursive: true, force: true });
      await fs.promises.mkdir(staging, { recursive: true });
      await extract(archive, staging);
      await fs.promises.rm(paths.node, { recursive: true, force: true });
      await fs.promises.rename(p.join(staging, top), paths.node);
      await fs.promises.rm(staging, { recursive: true, force: true });
      await fs.promises.rm(archive, { force: true });
    },
    async stamp() {
      await fs.promises.mkdir(paths.root, { recursive: true });
      await fs.promises.writeFile(paths.stamp, JSON.stringify({ appVersion, lockHash }));
    },
  };

  async function go() {
    cancelled = false;
    state.error = null;
    for (const step of state.steps) {
      if (step.status === 'done' || step.status === 'skipped') continue;
      if (cancelled) break;
      step.status = 'running';
      state.line = step.title;
      changed();
      try {
        const result = await work[step.id]();
        if (cancelled) { step.status = 'pending'; break; }
        step.status = result === 'skipped' ? 'skipped' : 'done';
        changed();
      } catch (e) {
        if (cancelled) { step.status = 'pending'; break; }
        step.status = 'failed';
        if (!(e instanceof StepError) && e && e.message) state.detail.push(String(e.message));
        if (state.detail.length > DETAIL_MAX) state.detail.splice(0, state.detail.length - DETAIL_MAX);
        state.error = e instanceof OfflineError ? OFFLINE_MESSAGE : (e instanceof StepError && e.message) || `${step.title} failed.`;
        changed();
        return { ok: false };
      }
    }
    if (cancelled) { state.line = 'Setup was stopped.'; changed(); return { ok: false, cancelled: true }; }
    return { ok: true };
  }

  function start() {
    if (!running) running = go().finally(() => { running = null; });
    return running;
  }

  return {
    start,
    retry: start,
    cancel() {
      if (!running) return;
      cancelled = true;
      if (controller) controller.abort();
    },
    state: snapshot,
  };
}

module.exports = { needsSetup, createSetup, isOfflineOutput, NEEDED_BYTES };

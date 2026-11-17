// First-launch setup: where the runtime lives and the resumable step runner. run, download, extract,
// freeBytes, findNode and fs are fakes; no test runs a real uv, a real download or a real extraction.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { runtimePaths, uvEnv } = require('../../src/electron/runtime-paths.cjs');
const { needsSetup, NEEDED_BYTES, UPDATE_NEEDED_BYTES, createSetup, isOfflineOutput } = require('../../src/electron/setup.cjs');
const { OfflineError } = require('../../src/electron/download.cjs');
const pins = require('../../src/electron/pins.json');

const GB = 1024 ** 3;

function fakeFs() {
  const written = {};
  const ops = [];
  return {
    written, ops,
    promises: {
      mkdir: async (p, o) => { ops.push(['mkdir', p, o]); },
      writeFile: async (p, data) => { ops.push(['writeFile', p]); written[p] = String(data); },
      rm: async (p, o) => { ops.push(['rm', p, o]); },
      rename: async (a, b) => { ops.push(['rename', a, b]); },
    },
  };
}

// A harness of fakes. opts.runs maps a uv subcommand ('python' | 'sync') to a function (args, opts, n) giving
// { code, lines } or a promise; by default every run succeeds.
function harness({ platform = 'linux', userData = '/home/u/.config/OpenZenBot', resources = '/opt/OpenZenBot/resources',
  free = 50 * GB, node = null, runs = {}, download, appVersion = '0.1.0', runtimeExists } = {}) {
  const paths = runtimePaths({ userData, resources, platform });
  const fs = fakeFs();
  const calls = [];
  const runOpts = [];
  const counts = { python: 0, sync: 0 };
  const downloads = [];
  const extracts = [];
  const emitted = [];
  const run = async (cmd, args, o) => {
    calls.push([cmd, ...args]);
    runOpts.push(o);
    const kind = args[0];
    counts[kind] += 1;
    const r = await (runs[kind] ? runs[kind](args, o, counts[kind]) : { code: 0, lines: [] });
    for (const l of r.lines || []) o.onLine(l);
    return { code: r.code };
  };
  const setup = createSetup({
    paths, platform, arch: 'x64', appVersion, lockHash: 'abc', pins, run,
    download: download || (async a => { downloads.push(a); a.onProgress && a.onProgress({ received: 5, total: 10 }); return a.dest; }),
    extract: async (archive, dest) => { extracts.push([archive, dest]); },
    freeBytes: async () => free,
    findNode: async () => node,
    ...(runtimeExists === undefined ? {} : { runtimeExists: async () => runtimeExists }),
    fs,
    emit: s => emitted.push(s),
  });
  return { paths, fs, written: fs.written, calls, runOpts, counts, downloads, extracts, emitted, setup };
}

test('needsSetup: no stamp, another version, another lock hash, a match', () => {
  const want = { appVersion: '0.1.0', lockHash: 'abc' };
  assert.equal(needsSetup({ stamp: null, ...want }), true);
  assert.equal(needsSetup({ stamp: undefined, ...want }), true);
  assert.equal(needsSetup({ stamp: 'abc', ...want }), true);
  assert.equal(needsSetup({ stamp: ['0.1.0', 'abc'], ...want }), true);
  assert.equal(needsSetup({ stamp: {}, ...want }), true);
  assert.equal(needsSetup({ stamp: { appVersion: '0.0.9', lockHash: 'abc' }, ...want }), true);
  assert.equal(needsSetup({ stamp: { appVersion: '0.1.0', lockHash: 'def' }, ...want }), true);
  assert.equal(needsSetup({ stamp: { appVersion: '0.1.0' }, ...want }), true);
  assert.equal(needsSetup({ stamp: { appVersion: '0.1.0', lockHash: 'abc' }, ...want }), false);
  assert.equal(needsSetup({ stamp: { appVersion: '0.1.0', lockHash: 'abc' }, ...want, pythonPresent: true }), false);
});

test("needsSetup: a matching stamp with the venv's Python gone sets up again", () => {
  const stamp = { appVersion: '0.1.0', lockHash: 'abc' };
  assert.equal(needsSetup({ stamp, appVersion: '0.1.0', lockHash: 'abc', pythonPresent: false }), true);
});

test('runtimePaths puts everything under userData/runtime and names the right python per platform', () => {
  const p = runtimePaths({ userData: '/home/u/.config/OpenZenBot', resources: '/opt/OZB/resources', platform: 'linux' });
  assert.equal(p.root, '/home/u/.config/OpenZenBot/runtime');
  for (const k of ['venv', 'python', 'pythonInstalls', 'uvCache', 'uvTools', 'node', 'nodeBin', 'downloads', 'stamp']) {
    assert.ok(p[k].startsWith(p.root + '/'), `${k} is under root: ${p[k]}`);
  }
  assert.equal(p.python, '/home/u/.config/OpenZenBot/runtime/venv/bin/python');
  assert.equal(p.nodeBin, '/home/u/.config/OpenZenBot/runtime/node/bin');
  assert.equal(p.uv, '/opt/OZB/resources/bin/uv');
  assert.equal(p.uvx, '/opt/OZB/resources/bin/uvx');
  assert.equal(p.harness, '/opt/OZB/resources/harness');
  assert.equal(p.logs, '/home/u/.config/OpenZenBot/logs');
  assert.equal(p.envFile, '/home/u/.config/OpenZenBot/.env');

  const w = runtimePaths({ userData: 'C:\\Users\\Zoë Ünal\\App Data\\OpenZenBot', resources: 'C:\\Program Files\\OZB\\resources', platform: 'win32' });
  assert.equal(w.root, 'C:\\Users\\Zoë Ünal\\App Data\\OpenZenBot\\runtime');
  assert.equal(w.python, 'C:\\Users\\Zoë Ünal\\App Data\\OpenZenBot\\runtime\\venv\\Scripts\\python.exe');
  assert.equal(w.nodeBin, 'C:\\Users\\Zoë Ünal\\App Data\\OpenZenBot\\runtime\\node');
  assert.equal(w.uv, 'C:\\Program Files\\OZB\\resources\\bin\\uv.exe');
  assert.equal(w.uvx, 'C:\\Program Files\\OZB\\resources\\bin\\uvx.exe');
  assert.equal(w.envFile, 'C:\\Users\\Zoë Ünal\\App Data\\OpenZenBot\\.env');
});

test('uvEnv adds the uv variables to the base environment', () => {
  const p = runtimePaths({ userData: '/d', resources: '/r', platform: 'linux' });
  const env = uvEnv(p, { PATH: '/usr/bin', HOME: '/home/u' });
  assert.deepEqual(env, {
    PATH: '/usr/bin', HOME: '/home/u',
    UV_PYTHON_INSTALL_DIR: p.pythonInstalls, UV_PROJECT_ENVIRONMENT: p.venv, UV_CACHE_DIR: p.uvCache,
    UV_TOOL_DIR: p.uvTools, UV_PYTHON_PREFERENCE: 'only-managed', UV_NO_CONFIG: '1',
  });
});

test('a clean run does every step in order and writes the stamp', async () => {
  const { setup, calls, runOpts, paths, written, downloads, extracts, fs, emitted } = harness();
  assert.deepEqual(setup.state().steps.map(s => [s.id, s.title, s.status]), [
    ['disk', 'Checking free space', 'pending'],
    ['python', 'Installing Python', 'pending'],
    ['packages', "Installing OpenZenBot's Python packages", 'pending'],
    ['node', 'Installing Node.js', 'pending'],
    ['stamp', 'Finishing', 'pending'],
  ]);
  assert.deepEqual(await setup.start(), { ok: true });
  assert.deepEqual(calls.map(c => c.slice(0, 3)), [[paths.uv, 'python', 'install'], [paths.uv, 'sync', '--frozen']]);
  // nothing outside <userData>/runtime: no python3.12 in ~/.local/bin, no Windows registry entry
  assert.deepEqual(calls[0], [paths.uv, 'python', 'install', '--no-bin', '--no-registry', '3.12']);
  assert.deepEqual(calls[1], [paths.uv, 'sync', '--frozen', '--no-dev', '--python', '3.12', '--project', paths.harness]);
  assert.equal(runOpts[0].env.UV_PYTHON_PREFERENCE, 'only-managed');
  assert.equal(runOpts[0].env.UV_PROJECT_ENVIRONMENT, paths.venv);
  assert.deepEqual(setup.state().steps.map(s => s.status), ['done', 'done', 'done', 'done', 'done']);
  assert.equal(setup.state().error, null);
  assert.deepEqual(JSON.parse(written[paths.stamp]), { appVersion: '0.1.0', lockHash: 'abc' });

  // Node: verified download into downloads/, extracted beside the final folder, its top folder moved into place,
  // the archive deleted.
  const pin = pins.node['linux-x64'];
  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].url, pin.url);
  assert.equal(downloads[0].sha256, pin.sha256);
  assert.equal(downloads[0].dest, `${paths.downloads}/node-v${pins.node.version}-linux-x64.tar.gz`);
  assert.deepEqual(extracts, [[downloads[0].dest, `${paths.node}.part`]]);
  assert.ok(fs.ops.some(o => o[0] === 'rename' && o[1] === `${paths.node}.part/node-v${pins.node.version}-linux-x64` && o[2] === paths.node));
  assert.ok(fs.ops.some(o => o[0] === 'rm' && o[1] === downloads[0].dest));
  assert.ok(emitted.length > 5);
});

test('a suitable node on PATH skips the node step and downloads nothing', async () => {
  const { setup, downloads, extracts } = harness({ node: '/usr/bin/node' });
  assert.deepEqual(await setup.start(), { ok: true });
  assert.deepEqual(setup.state().steps.map(s => s.status), ['done', 'done', 'done', 'skipped', 'done']);
  assert.equal(downloads.length, 0);
  assert.equal(extracts.length, 0);
});

test('too little disk fails the first step with the size in the message', async () => {
  const { setup, calls, written, paths } = harness({ free: 2.5 * GB });
  assert.equal(NEEDED_BYTES, 5 * 1024 ** 3);
  assert.deepEqual(await setup.start(), { ok: false });
  assert.equal(setup.state().error, 'OpenZenBot needs about 5 GB free on this disk; 2.5 GB is free.');
  assert.deepEqual(setup.state().steps.map(s => s.status), ['failed', 'pending', 'pending', 'pending', 'pending']);
  assert.equal(calls.length, 0);
  assert.equal(written[paths.stamp], undefined);
});

test('an update over an existing runtime needs only 1 GB free, and a fresh install still needs 5', async () => {
  assert.equal(UPDATE_NEEDED_BYTES, 1 * 1024 ** 3);
  const update = harness({ free: 4 * GB, runtimeExists: true });
  assert.deepEqual(await update.setup.start(), { ok: true });
  const fresh = harness({ free: 4 * GB, runtimeExists: false });
  assert.deepEqual(await fresh.setup.start(), { ok: false });
  assert.equal(fresh.setup.state().error, 'OpenZenBot needs about 5 GB free on this disk; 4 GB is free.');
  const tight = harness({ free: 0.5 * GB, runtimeExists: true });
  assert.deepEqual(await tight.setup.start(), { ok: false });
  assert.equal(tight.setup.state().error, 'OpenZenBot needs about 1 GB free on this disk; 0.5 GB is free.');
});

test('isOfflineOutput knows uv connection and DNS errors, and not others', () => {
  assert.ok(isOfflineOutput(['error: Failed to fetch: `https://pypi.org/simple/httpx/`',
    '  Caused by: dns error: failed to lookup address information: Temporary failure in name resolution']));
  assert.ok(isOfflineOutput(['  Caused by: tcp connect error: Connection refused (os error 111)']));
  assert.ok(isOfflineOutput(['  Caused by: operation timed out']));
  assert.ok(isOfflineOutput(['  Caused by: No such host is known. (os error 11001)']));
  assert.equal(isOfflineOutput(['error: The lockfile at `uv.lock` needs to be updated, but `--frozen` was provided.']), false);
});

test('offline: the step fails with the no-internet message, and retry resumes at that step', async () => {
  let online = false;
  const { setup, counts, written, paths } = harness({
    runs: { sync: () => (online ? { code: 0 } : { code: 2, lines: ['Resolved 80 packages', 'error: Failed to fetch: `https://files.pythonhosted.org/x.whl`', '  Caused by: dns error: failed to lookup address information: Name or service not known'] }) },
  });
  assert.deepEqual(await setup.start(), { ok: false });
  assert.equal(setup.state().error, 'No internet connection. Connect and press Retry.');
  assert.deepEqual(setup.state().steps.map(s => s.status), ['done', 'done', 'failed', 'pending', 'pending']);
  online = true;
  assert.deepEqual(await setup.retry(), { ok: true });
  const pythonInstalls = counts.python;
  assert.equal(pythonInstalls, 1);            // the finished python step is not run again
  assert.equal(counts.sync, 2);
  assert.equal(setup.state().error, null);
  assert.deepEqual(setup.state().steps.map(s => s.status), ['done', 'done', 'done', 'done', 'done']);
  assert.ok(written[paths.stamp]);
});

test('an OfflineError from the Node download shows the no-internet message', async () => {
  const { setup } = harness({ download: async () => { throw new OfflineError(); } });
  assert.deepEqual(await setup.start(), { ok: false });
  assert.equal(setup.state().error, 'No internet connection. Connect and press Retry.');
  assert.equal(setup.state().steps[3].status, 'failed');
});

test('any other failure names the step and keeps the output for Show details', async () => {
  const { setup } = harness({ runs: { python: () => ({ code: 1, lines: ['error: No download found for request: cpython-3.12'] }) } });
  assert.deepEqual(await setup.start(), { ok: false });
  assert.equal(setup.state().error, 'Installing Python failed.');
  assert.ok(setup.state().detail.includes('error: No download found for request: cpython-3.12'));
});

test('no stamp is written when a step fails, so the next launch runs setup again', async () => {
  const { setup, written, paths } = harness({ download: async () => { throw new Error('disk full'); } });
  assert.deepEqual(await setup.start(), { ok: false });
  assert.equal(setup.state().error, 'Installing Node.js failed.');
  assert.equal(written[paths.stamp], undefined);
  assert.equal(needsSetup({ stamp: null, appVersion: '0.1.0', lockHash: 'abc' }), true);
});

test('a stamp from an older app version runs setup again', async () => {
  const old = { appVersion: '0.0.9', lockHash: 'abc' };
  assert.equal(needsSetup({ stamp: old, appVersion: '0.1.0', lockHash: 'abc' }), true);
  const { setup, written, paths, counts } = harness({ appVersion: '0.1.0' });
  assert.deepEqual(await setup.start(), { ok: true });
  assert.equal(counts.sync, 1);
  const stamp = JSON.parse(written[paths.stamp]);
  assert.equal(needsSetup({ stamp, appVersion: '0.1.0', lockHash: 'abc' }), false);
});

test('cancel stops the running child', async () => {
  let seenSignal = null;
  const { setup, written, paths, counts } = harness({
    runs: {
      sync: (args, o, n) => (n > 1 ? { code: 0 } : new Promise(resolve => {
        seenSignal = o.signal;
        o.signal.addEventListener('abort', () => resolve({ code: null }));
      })),
    },
  });
  const done = setup.start();
  while (!seenSignal) await new Promise(r => setImmediate(r));
  assert.equal(seenSignal.aborted, false);
  setup.cancel();
  assert.equal(seenSignal.aborted, true);
  assert.deepEqual(await done, { ok: false, cancelled: true });
  assert.equal(written[paths.stamp], undefined);
  assert.deepEqual(setup.state().steps.map(s => s.status), ['done', 'done', 'pending', 'pending', 'pending']);
  assert.equal(setup.state().error, null);
  // A later retry picks up at the cancelled step.
  assert.deepEqual(await setup.retry(), { ok: true });
  assert.equal(counts.python, 1);
  assert.equal(counts.sync, 2);
  assert.ok(written[paths.stamp]);
});

test('cancel aborts a Node.js download under way, and retry downloads again', async () => {
  let seenSignal = null;
  let n = 0;
  const { setup, written, paths } = harness({
    download: a => {
      n += 1;
      if (n > 1) return Promise.resolve(a.dest);
      seenSignal = a.signal;
      return new Promise((_, reject) => a.signal.addEventListener('abort', () => reject(new Error('aborted'))));  // a stalled download
    },
  });
  const done = setup.start();
  while (!seenSignal) await new Promise(r => setImmediate(r));
  setup.cancel();
  assert.equal(seenSignal.aborted, true);
  assert.deepEqual(await done, { ok: false, cancelled: true });
  assert.deepEqual(setup.state().steps.map(s => s.status), ['done', 'done', 'done', 'pending', 'pending']);
  assert.equal(setup.state().error, null);
  assert.deepEqual(await setup.retry(), { ok: true });
  assert.equal(n, 2);
  assert.ok(written[paths.stamp]);
});

test('every command is an argument array whose paths survive spaces and non-ASCII', async () => {
  const { setup, calls, paths, downloads, extracts, written } = harness({
    platform: 'win32', userData: 'C:\\Users\\Zoë Ünal\\App Data', resources: 'C:\\Program Files\\Open ZenBot\\resources',
  });
  assert.deepEqual(await setup.start(), { ok: true });
  assert.ok(calls.every(c => Array.isArray(c) && c.every(a => typeof a === 'string')));
  assert.equal(calls[0][0], 'C:\\Program Files\\Open ZenBot\\resources\\bin\\uv.exe');
  assert.equal(calls[1].at(-1), 'C:\\Program Files\\Open ZenBot\\resources\\harness');
  assert.equal(downloads[0].dest, `C:\\Users\\Zoë Ünal\\App Data\\runtime\\downloads\\node-v${pins.node.version}-win-x64.zip`);
  assert.deepEqual(extracts[0], [downloads[0].dest, 'C:\\Users\\Zoë Ünal\\App Data\\runtime\\node.part']);
  assert.ok(written[paths.stamp]);
  assert.equal(paths.stamp, 'C:\\Users\\Zoë Ünal\\App Data\\runtime\\setup.json');
});

test('detail keeps only the last 200 lines', async () => {
  const lines = Array.from({ length: 250 }, (_, i) => `line ${i}`);
  const { setup } = harness({ runs: { python: () => ({ code: 0, lines }) } });
  await setup.start();
  const { detail } = setup.state();
  assert.equal(detail.length, 200);
  assert.equal(detail[0], 'line 50');
  assert.equal(detail.at(-1), 'line 249');
});

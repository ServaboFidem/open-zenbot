// Starting, adopting and stopping the harness. Nothing real runs: spawn, fetch, the port probe, the kills and the
// clock are all fakes, so "wait 45 s" and "100 failed polls" take no time.
import test from 'node:test';
import assert from 'node:assert/strict';
import os from 'node:os';
import fs from 'node:fs';
import path from 'node:path';
import { EventEmitter } from 'node:events';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { createHarness, rotateLog, ownPort } = require('../../src/electron/harness-process.cjs');
const { runtimePaths } = require('../../src/electron/runtime-paths.cjs');

function fakeChild(pid = 4242) {
  const c = new EventEmitter();
  c.pid = pid;
  c.stdout = new EventEmitter();
  c.stderr = new EventEmitter();
  return c;
}

function rig({ platform = 'linux', healthy = () => false, portBusy = false, baseEnv = { PATH: '/usr/bin' } } = {}) {
  const resources = platform === 'win32' ? 'C:\\App\\resources' : '/opt/OpenZenBot/resources';
  const userData = platform === 'win32' ? 'C:\\Users\\a\\AppData\\Roaming\\OpenZenBot' : '/home/a/.config/OpenZenBot';
  const paths = runtimePaths({ userData, resources, platform });
  const t = { spawned: [], kills: [], tree: [], log: [], sleeps: 0, polls: 0, child: null, onSleep: () => {}, groupAlive: true };
  const h = createHarness({
    paths, platform, baseEnv,
    spawn: (cmd, args, opts) => { t.spawned.push([cmd, args, opts]); t.child = fakeChild(); return t.child; },
    fetch: async () => { t.polls++; return { ok: true, json: async () => ({ ok: healthy(t) }) }; },
    portInUse: async () => portBusy,
    killTree: async pid => { t.tree.push(pid); },
    // signal 0 only asks whether the group is there (t.groupAlive); it is not recorded as a kill
    kill: (pid, sig) => {
      if (sig === 0) { if (!t.groupAlive) throw Object.assign(new Error('no such process'), { code: 'ESRCH' }); return; }
      t.kills.push([pid, sig]);
    },
    logStream: { write: c => t.log.push(String(c)) },
    sleep: async () => { t.sleeps++; await t.onSleep(t); },
  });
  return { t, h, paths, resources };
}

test('a harness already answering /health is adopted and never stopped', async () => {
  const { t, h } = rig({ healthy: () => true });
  assert.deepEqual(await h.start(), { ok: true, adopted: true });
  assert.equal(t.spawned.length, 0);
  await h.stop();
  assert.deepEqual([t.kills, t.tree], [[], []]);
});

test('the port held by something else is reported, and nothing is spawned', async () => {
  const { t, h } = rig({ portBusy: true });
  assert.deepEqual(await h.start(), { ok: false, reason: 'port-in-use', detail: 'Port 8765 is in use by another program.' });
  assert.equal(t.spawned.length, 0);
});

test('start spawns the venv python in the harness folder with the managed environment', async () => {
  const { t, h, paths, resources } = rig({ healthy: t2 => t2.child !== null });
  assert.deepEqual(await h.start(), { ok: true, adopted: false });
  const [cmd, args, opts] = t.spawned[0];
  assert.deepEqual([cmd, args, opts.cwd], [paths.python, ['-m', 'src.harness.main'], paths.harness]);
  assert.equal(opts.detached, true);
  assert.equal(opts.windowsHide, true);
  assert.equal(opts.env.ZEN_USER_CONTENT, '1');
  assert.equal(opts.env.ZEN_PORT, '8765');
  assert.equal(opts.env.ZEN_HARNESS_URL, 'http://127.0.0.1:8765');
  assert.equal(opts.env.ZEN_ENV_FILE, paths.envFile);
  assert.equal(opts.env.PYTHONUNBUFFERED, '1');
  // the harness's own venv is never a uv project target for a bot's shell or a stdio server (I1)
  assert.equal('UV_PROJECT_ENVIRONMENT' in opts.env, false);
  assert.equal(opts.env.UV_PYTHON_INSTALL_DIR, paths.pythonInstalls);
  assert.equal(opts.env.UV_CACHE_DIR, paths.uvCache);
  assert.equal(opts.env.UV_TOOL_DIR, paths.uvTools);
  // the venv's python leads PATH as under `uv run`, but VIRTUAL_ENV stays unset so `--active` cannot reach it
  assert.equal('VIRTUAL_ENV' in opts.env, false);
  assert.equal(opts.env.PATH, `${path.posix.dirname(paths.python)}:${path.posix.join(resources, 'bin')}:${paths.nodeBin}:/usr/bin`);  // the rig is linux
  assert.equal(h.url, 'http://127.0.0.1:8765');
});

test('on windows the existing Path spelling leads with ; and no second PATH appears', async () => {
  const { t, h, paths } = rig({ platform: 'win32', baseEnv: { Path: 'C:\\Windows' }, healthy: t2 => t2.child !== null });
  await h.start();
  const { env, detached } = t.spawned[0][2];
  assert.equal(detached, false);
  assert.equal(env.Path, `${path.win32.dirname(paths.python)};C:\\App\\resources\\bin;${paths.nodeBin};C:\\Windows`);
  assert.equal(path.win32.dirname(paths.python), path.win32.join(paths.venv, 'Scripts'));
  assert.equal('UV_PROJECT_ENVIRONMENT' in env, false);
  assert.equal('VIRTUAL_ENV' in env, false);
  assert.deepEqual(Object.keys(env).filter(k => k.toUpperCase() === 'PATH'), ['Path']);
});

test('start waits as long as the child lives, and resolves when /health answers', async () => {
  const { t, h } = rig({ healthy: t2 => t2.polls > 101 });
  const r = await h.start();
  assert.deepEqual(r, { ok: true, adopted: false });
  assert.ok(t.sleeps >= 100);
});

test('a VIRTUAL_ENV or UV_PROJECT_ENVIRONMENT from the launching environment does not reach the harness', async () => {
  const { t, h } = rig({ baseEnv: { PATH: '/usr/bin', VIRTUAL_ENV: '/elsewhere/.venv', UV_PROJECT_ENVIRONMENT: '/elsewhere/env' }, healthy: t2 => t2.child !== null });
  await h.start();
  const { env } = t.spawned[0][2];
  assert.equal('VIRTUAL_ENV' in env, false);
  assert.equal('UV_PROJECT_ENVIRONMENT' in env, false);
});

test('stop() during the first moments of start() means nothing is launched', async () => {
  let release;
  const gate = new Promise(r => { release = r; });
  const t = { spawned: [], kills: [] };
  const h = createHarness({
    paths: runtimePaths({ userData: '/home/a/.config/OpenZenBot', resources: '/opt/OpenZenBot/resources', platform: 'linux' }),
    platform: 'linux', baseEnv: { PATH: '/usr/bin' },
    // a child that dies at once, so a wrong launch ends the start instead of polling for ever
    spawn: (...a) => { t.spawned.push(a); const c = fakeChild(); setImmediate(() => c.emit('close', 1, null)); return c; },
    fetch: async () => ({ ok: false }),
    portInUse: async () => { await gate; return false; },  // start() is held here, just before it would launch
    killTree: async () => {}, kill: (pid, sig) => { t.kills.push([pid, sig]); },
    logStream: { write: () => {} }, sleep: () => new Promise(r => setImmediate(r)),
  });
  const started = h.start();
  await h.stop();
  release();
  const r = await started;
  assert.deepEqual([r.ok, r.reason, t.spawned.length, t.kills], [false, 'stopped', 0, []]);
  // and a later start() does not launch either
  assert.equal((await h.start()).reason, 'stopped');
  assert.equal(t.spawned.length, 0);
});

test('start called while a start is in flight returns the same promise', async () => {
  const { t, h } = rig({ healthy: t2 => t2.polls > 3 });
  const a = h.start();
  const b = h.start();
  assert.equal(a, b);
  await a;
  assert.equal(t.spawned.length, 1);
});

test('the child dying before /health is reason "exited" with its last lines', async () => {
  const { t, h } = rig();
  t.onSleep = () => {
    if (t.sleeps === 1) {
      t.child.stdout.emit('data', Array.from({ length: 60 }, (_, i) => `line ${i}`).join('\n') + '\n');
      t.child.emit('exit', 1, null);
    }
  };
  const r = await h.start();
  assert.equal(r.ok, false);
  assert.equal(r.reason, 'exited');
  const lines = r.detail.split('\n');
  assert.equal(lines.length, 40);
  assert.equal(lines.at(-1), 'line 59');
  assert.ok(t.log.join('').includes('line 0'));
});

test('stop on linux sends TERM to the group, then KILL after 45 s', async () => {
  const { t, h } = rig({ healthy: t2 => t2.child !== null });
  await h.start();
  const sleepsBefore = t.sleeps;
  await h.stop();
  assert.deepEqual(t.kills, [[-4242, 'SIGTERM'], [-4242, 'SIGKILL']]);
  assert.equal((t.sleeps - sleepsBefore) * 250, 45000);
});

test('stop on linux ends early when the whole group is gone after TERM', async () => {
  const { t, h } = rig({ healthy: t2 => t2.child !== null });
  await h.start();
  t.onSleep = () => { t.groupAlive = false; t.child.emit('exit', 0, 'SIGTERM'); };
  await h.stop();
  assert.deepEqual(t.kills, [[-4242, 'SIGTERM']]);
});

test('stop on linux still kills the group when the harness exits but something it started stays', async () => {
  const { t, h } = rig({ healthy: t2 => t2.child !== null });
  await h.start();
  t.onSleep = () => t.child.emit('exit', 0, 'SIGTERM');  // the leader goes; a shell command ignores TERM
  const sleepsBefore = t.sleeps;
  await h.stop();
  assert.deepEqual(t.kills, [[-4242, 'SIGTERM'], [-4242, 'SIGKILL']]);
  assert.equal((t.sleeps - sleepsBefore) * 250, 45000);
});

test('a harness that dies on its own has its group asked to end; on windows nothing is signalled', async () => {
  const a = rig({ healthy: t2 => t2.child !== null });
  await a.h.start();
  a.t.child.emit('exit', 1, null);
  assert.deepEqual(a.t.kills, [[-4242, 'SIGTERM']]);
  await a.h.stop();  // already gone: nothing more
  assert.deepEqual(a.t.kills, [[-4242, 'SIGTERM']]);

  const b = rig({ platform: 'win32', healthy: t2 => t2.child !== null });
  await b.h.start();
  b.t.child.emit('exit', 1, null);
  assert.deepEqual([b.t.kills, b.t.tree], [[], []]);
});

test('ownPort takes only a plain http 127.0.0.1 url with a port', () => {
  assert.equal(ownPort('http://127.0.0.1:9876'), 9876);
  assert.equal(ownPort('http://127.0.0.1:9876/'), 9876);
  for (const url of [undefined, '', 'nonsense', 'http://127.0.0.1', 'https://127.0.0.1:9876', 'http://localhost:9876', 'http://example.com:9876']) {
    assert.equal(ownPort(url), null, String(url));
  }
});

test('stop on windows runs the tree kill', async () => {
  const { t, h } = rig({ platform: 'win32', healthy: t2 => t2.child !== null });
  await h.start();
  await h.stop();
  assert.deepEqual(t.tree, [4242]);
  assert.deepEqual(t.kills, []);
});

test('onExit fires when the harness dies after start, not after stop()', async () => {
  const a = rig({ healthy: t2 => t2.child !== null });
  let fired = 0;
  a.h.onExit(() => fired++);
  await a.h.start();
  a.t.child.emit('exit', 1, null);
  assert.equal(fired, 1);

  const b = rig({ healthy: t2 => t2.child !== null });
  b.h.onExit(() => fired++);
  await b.h.start();
  b.t.onSleep = () => { b.t.groupAlive = false; b.t.child.emit('exit', 0, 'SIGTERM'); };
  await b.h.stop();
  assert.equal(fired, 1);
});

test('onExit does not fire for a start that failed', async () => {
  const { t, h } = rig();
  let fired = 0;
  h.onExit(() => fired++);
  t.onSleep = () => t.child.emit('exit', 1, null);
  assert.equal((await h.start()).reason, 'exited');
  assert.equal(fired, 0);
});

test('rotateLog keeps harness.log and one harness.log.1', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'zen-log-'));
  try {
    const logs = path.join(dir, 'logs');
    rotateLog({ dir: logs, fs, platform: 'linux' });
    assert.deepEqual(fs.readdirSync(logs), []);
    fs.writeFileSync(path.join(logs, 'harness.log'), 'first');
    rotateLog({ dir: logs, fs, platform: 'linux' });
    fs.writeFileSync(path.join(logs, 'harness.log'), 'second');
    rotateLog({ dir: logs, fs, platform: 'linux' });
    assert.deepEqual(fs.readdirSync(logs).sort(), ['harness.log.1']);
    assert.equal(fs.readFileSync(path.join(logs, 'harness.log.1'), 'utf8'), 'second');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('output arriving after "exit" but before "close" is still in detail, with an unterminated last line', async () => {
  const { t, h } = rig();
  t.onSleep = () => {
    if (t.sleeps === 1) t.child.emit('exit', 1, null);
    if (t.sleeps === 2) t.child.stderr.emit('data', 'Traceback (most recent call last):\n  File "x.py"\nImportError: no module');
    if (t.sleeps === 3) t.child.emit('close', 1, null);
  };
  const r = await h.start();
  assert.equal(r.reason, 'exited');
  assert.deepEqual(r.detail.split('\n'), ['Traceback (most recent call last):', '  File "x.py"', 'ImportError: no module']);
});

test('a child whose streams never close does not hang start() or stop()', async () => {
  const a = rig();
  a.t.onSleep = () => { if (a.t.sleeps === 1) a.t.child.emit('exit', 1, null); };
  assert.equal((await a.h.start()).reason, 'exited');

  const b = rig({ healthy: t2 => t2.child !== null });
  await b.h.start();
  b.t.onSleep = () => { b.t.groupAlive = false; b.t.child.emit('exit', 0, 'SIGTERM'); };
  await b.h.stop();
  assert.deepEqual(b.t.kills, [[-4242, 'SIGTERM']]);
});

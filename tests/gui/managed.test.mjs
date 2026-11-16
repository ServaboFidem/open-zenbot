// The installed app's launch sequence (src/electron/managed.cjs): update offer, setup, harness, workspace, and
// what Restart and quitting do. Every part is a fake; nothing is downloaded, run or started.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const { createManaged } = createRequire(import.meta.url)('../../src/electron/managed.cjs');

function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}
const tick = () => new Promise(r => setImmediate(r));

function fakeChecker({ notify = false, auto = true } = {}) {
  const state = { current: '0.1.0', latest: notify ? '0.2.0' : '0.1.0', notify, auto, notes: '' };
  return { checks: 0, opened: 0, get: () => state, async check() { this.checks++; return state; }, open() { this.opened++; return true; } };
}

function fakeSetup(results) {
  const s = { starts: 0, cancels: 0, pending: null };
  s.start = () => {
    s.starts++;
    const r = results.shift();
    if (r === 'hang') { s.pending = deferred(); return s.pending.promise; }
    return Promise.resolve(r);
  };
  s.retry = s.start;
  s.cancel = () => { s.cancels++; };
  s.state = () => ({ steps: [], line: '', detail: [], error: null });
  return s;
}

function fakeHarness(result) {
  const h = { starts: 0, stops: 0, exitCbs: [], pending: null };
  h.start = () => {
    h.starts++;
    if (result === 'hang') { h.pending = deferred(); return h.pending.promise; }
    return Promise.resolve(result);
  };
  h.stop = async () => { h.stops++; if (h.pending) h.pending.resolve({ ok: false, reason: 'exited', detail: '' }); };
  h.onExit = cb => { h.exitCbs.push(cb); };
  h.exit = () => h.exitCbs.forEach(cb => cb());
  return h;
}

function make({ checker = fakeChecker(), setupNeeded = false, setup = fakeSetup([{ ok: true }]), harnesses = [fakeHarness({ ok: true, adopted: false })] } = {}) {
  const calls = { shown: [], sent: [], checkerStarts: 0, checkerStops: 0, made: [] };
  const m = createManaged({
    checker,
    needsSetup: async () => setupNeeded,
    makeSetup: emit => { setup.emit = emit; return setup; },
    makeHarness: async () => { const h = harnesses.shift(); calls.made.push(h); return h; },
    show: page => calls.shown.push(page),
    send: s => calls.sent.push(s),
    startChecker: () => { calls.checkerStarts++; return () => { calls.checkerStops++; }; },
  });
  return { m, calls, checker, setup };
}

test('nothing newer and nothing to set up: the harness starts and the workspace opens', async () => {
  const { m, calls, checker } = make();
  await m.run();
  assert.equal(checker.checks, 1);
  assert.deepEqual(calls.shown, ['setup', 'workspace']);
  assert.deepEqual([...new Set(calls.sent.map(s => s.phase))], ['update', 'starting']);
  assert.equal(calls.made.length, 1);
  assert.equal(calls.checkerStarts, 1);
  assert.equal(m.state().harness.firstStart, false);
});

test('a newer release waits for continue before setup or the harness', async () => {
  const { m, calls, checker } = make({ checker: fakeChecker({ notify: true }), setupNeeded: true });
  const done = m.run();
  await tick();
  assert.equal(m.state().phase, 'update');
  assert.equal(m.state().update.latest, '0.2.0');
  assert.equal(calls.made.length, 0);
  m.retry(); m.restartHarness();  // not this phase's buttons
  await tick();
  assert.equal(m.state().phase, 'update');
  assert.equal(m.openUpdate(), true);
  assert.equal(checker.opened, 1);
  m.proceed();
  await done;
  assert.deepEqual(calls.shown, ['setup', 'workspace']);
});

test('with automatic checks off there is no check at launch', async () => {
  const { m, checker, calls } = make({ checker: fakeChecker({ auto: false }) });
  await m.run();
  assert.equal(checker.checks, 0);
  assert.ok(!calls.sent.some(s => s.phase === 'update'));
});

test('a failed setup waits for Retry, then the first start is marked as the first', async () => {
  const setup = fakeSetup([{ ok: false }, { ok: true }]);
  const { m, calls } = make({ setupNeeded: true, setup });
  const done = m.run();
  await tick(); await tick();
  assert.equal(m.state().phase, 'setup');
  assert.equal(setup.starts, 1);
  assert.equal(calls.made.length, 0);
  m.proceed();  // not this phase's button
  await tick();
  assert.equal(setup.starts, 1);
  m.retry();
  await done;
  assert.equal(setup.starts, 2);
  assert.equal(m.state().harness.firstStart, true);
  assert.deepEqual(calls.shown, ['setup', 'workspace']);
});

test("setup's own state changes reach the page", async () => {
  const setup = fakeSetup(['hang']);
  const { m, calls } = make({ setupNeeded: true, setup });
  m.run();
  await tick(); await tick();
  setup.emit({ steps: [{ id: 'disk', title: 'Checking free space', status: 'running' }], line: 'x', detail: [], error: null });
  assert.equal(calls.sent.at(-1).setup.line, 'x');
  assert.equal(m.state().setup.steps[0].status, 'running');
});

test('a port in use shows the failed phase and no workspace', async () => {
  const { m, calls } = make({ harnesses: [fakeHarness({ ok: false, reason: 'port-in-use', detail: 'Port 8765 is in use by another program.' })] });
  await m.run();
  assert.deepEqual(calls.shown, ['setup']);
  assert.equal(m.state().phase, 'failed');
  assert.equal(m.state().harness.reason, 'port-in-use');
  assert.equal(calls.checkerStarts, 0);
});

test('a harness that stops shows the failed page; Restart makes a new harness after the old one stopped', async () => {
  const first = fakeHarness({ ok: true, adopted: false });
  const second = fakeHarness({ ok: true, adopted: false });
  const { m, calls } = make({ harnesses: [first, second] });
  await m.run();
  first.exit();
  assert.deepEqual(calls.shown, ['setup', 'workspace', 'setup']);
  assert.equal(m.state().phase, 'failed');
  assert.equal(m.state().harness.reason, 'exited');
  await m.restartHarness();
  assert.equal(first.stops, 1);
  assert.equal(first.starts, 1);
  assert.equal(second.starts, 1);
  assert.deepEqual(calls.shown, ['setup', 'workspace', 'setup', 'workspace']);
  assert.equal(calls.checkerStarts, 1);  // the periodic check is started once
  first.exit();  // the old one is no longer watched
  assert.deepEqual(calls.shown, ['setup', 'workspace', 'setup', 'workspace']);
});

test('Restart is only for a harness that stopped', async () => {
  const { m, calls } = make({ harnesses: [fakeHarness({ ok: false, reason: 'port-in-use', detail: '' })] });
  await m.run();
  await m.restartHarness();
  assert.equal(calls.made.length, 1);
});

test('quitting during setup cancels it without waiting for it, and nothing starts after', async () => {
  const setup = fakeSetup(['hang']);
  const { m, calls } = make({ setupNeeded: true, setup });
  const done = m.run();
  await tick(); await tick();
  await m.shutdown();  // resolves although setup has not settled
  assert.equal(setup.cancels, 1);
  setup.pending.resolve({ ok: false, cancelled: true });
  await done;
  assert.equal(calls.made.length, 0);
  assert.deepEqual(calls.shown, ['setup']);
});

test('quitting while the harness starts stops it and ignores the start result', async () => {
  const h = fakeHarness('hang');
  const { m, calls } = make({ harnesses: [h] });
  const done = m.run();
  await tick(); await tick();
  assert.equal(m.state().phase, 'starting');
  await m.shutdown();
  await done;
  assert.equal(h.stops, 1);
  assert.equal(m.state().phase, 'starting');
  assert.deepEqual(calls.shown, ['setup']);
});

test('quitting after the workspace opened stops the harness and the periodic check, once', async () => {
  const h = fakeHarness({ ok: true, adopted: false });
  const { m, calls } = make({ harnesses: [h] });
  await m.run();
  await Promise.all([m.shutdown(), m.shutdown()]);
  assert.equal(h.stops, 1);
  assert.equal(calls.checkerStops, 1);
  h.exit();
  assert.deepEqual(calls.shown, ['setup', 'workspace']);
});

test('quitting while the update offer waits ends the launch', async () => {
  const { m, calls } = make({ checker: fakeChecker({ notify: true }) });
  const done = m.run();
  await tick();
  await m.shutdown();
  await done;
  assert.equal(calls.made.length, 0);
});

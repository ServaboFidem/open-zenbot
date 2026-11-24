import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { createBriefingKeeper, shouldShowWindow, LATE_LIMIT_MS } = require('../../src/electron/briefing-keeper.cjs');

const MIN = 60_000;
function status(slots, extra = {}) {
  return { briefings: [{ kind: 'morning', enabled: true, slots, next_run: null, state: 'idle', ...extra }] };
}
function rig({ platform = 'win32', st = status([{ days: [0, 2], time: '06:45' }]), managed = true, failCreate = false } = {}) {
  const calls = [], xmls = [], removed = [], blocker = { started: 0, stopped: [], id: 0 };
  const box = { st, now: Date.parse('2026-11-24T01:00:00'), login: false, loginArgs: null };
  const keeper = createBriefingKeeper({
    platform, managed,
    fetchStatus: async () => { if (box.fail) throw new Error('down'); return box.st; },
    runSchtasks: async args => {
      calls.push(args);
      if (failCreate && args[0] === '/Create') throw new Error('denied');
    },
    writeTaskXml: async xml => { xmls.push(xml); return { path: `C:\temp\t${xmls.length}.xml`, remove: async () => removed.push(xmls.length) }; },
    powerSaveBlocker: { start: kind => { assert.equal(kind, 'prevent-app-suspension'); blocker.started += 1; return ++blocker.id; }, stop: id => blocker.stopped.push(id) },
    setLoginItem: o => { box.login = o.openAtLogin; box.loginArgs = o.args; },
    getLoginItem: () => box.login,
    now: () => box.now,
    setTimer: () => 0,
  });
  return { keeper, calls, xmls, removed, blocker, box };
}

test('one wake task per enabled line, 8 minutes early', async () => {
  const r = rig();
  await r.keeper.sync();
  assert.equal(r.xmls.length, 1);
  assert.match(r.xmls[0], /<StartBoundary>[^<]*T06:37:00<\/StartBoundary>/);
  assert.match(r.xmls[0], /<Monday\/>/);
  assert.match(r.xmls[0], /<Wednesday\/>/);
  assert.doesNotMatch(r.xmls[0], /<Tuesday\/>/);
  assert.match(r.xmls[0], /<WakeToRun>true<\/WakeToRun>/);
  assert.match(r.xmls[0], /<LogonType>InteractiveToken<\/LogonType>/);
  assert.match(r.xmls[0], /<Command>cmd\.exe<\/Command>\s*<Arguments>\/c exit 0<\/Arguments>/);
  const create = r.calls.find(a => a[0] === '/Create');
  assert.deepEqual(create.slice(0, 2), ['/Create', '/TN']);
  assert.equal(create[2], 'OpenZenBot briefing wake 1');
  assert.deepEqual(r.removed, [1]);
});

test('00:05 wakes on the previous day at 23:57', async () => {
  const r = rig({ st: status([{ days: [0], time: '00:05' }]) });
  await r.keeper.sync();
  assert.match(r.xmls[0], /T23:57:00<\/StartBoundary>/);
  assert.match(r.xmls[0], /<Sunday\/>/);
  assert.doesNotMatch(r.xmls[0], /<Monday\/>/);
});

test('tasks beyond the line count are deleted', async () => {
  const r = rig();
  await r.keeper.sync();
  const deleted = r.calls.filter(a => a[0] === '/Delete').map(a => a[2]);
  assert.deepEqual(deleted, [2, 3, 4, 5, 6, 7].map(n => `OpenZenBot briefing wake ${n}`));
  for (const a of r.calls.filter(c => c[0] === '/Delete')) assert.ok(a.includes('/F'));
});

test('schtasks gets an argument array, never a shell string', async () => {
  const r = rig();
  await r.keeper.sync();
  assert.ok(r.calls.length > 0);
  for (const a of r.calls) {
    assert.ok(Array.isArray(a));
    for (const x of a) assert.equal(typeof x, 'string');
  }
  const src = require('node:fs').readFileSync(new URL('../../src/electron/briefing-keeper.cjs', import.meta.url), 'utf8');
  assert.doesNotMatch(src, /child_process|[^.\w]exec\(|shell: *true/);
});

test('no tasks on linux', async () => {
  const r = rig({ platform: 'linux' });
  await r.keeper.sync();
  assert.equal(r.calls.length, 0);
  assert.equal(r.xmls.length, 0);
});

test('a failed task sets wake_error', async () => {
  const r = rig({ failCreate: true });
  await r.keeper.sync();
  assert.equal(r.keeper.loginState().wake_error, 'Windows would not set a wake-up time for the briefing.');
  const ok = rig();
  await ok.keeper.sync();
  assert.equal(ok.keeper.loginState().wake_error, null);
});

test('the blocker is held only inside the window', async () => {
  const at = '2026-11-24T06:45:00';
  const r = rig({ st: status([{ days: [1], time: '06:45' }], { next_run: at + 'Z' }) });
  const slot = Date.parse(at + 'Z');
  r.box.now = slot - 20 * MIN; await r.keeper.tick();
  assert.equal(r.blocker.started, 0);
  r.box.now = slot - 7 * MIN; await r.keeper.tick();
  assert.equal(r.blocker.started, 1);
  r.box.now = slot - 6 * MIN; await r.keeper.tick();
  assert.equal(r.blocker.started, 1, 'held once');
  r.box.now = slot + 1 * MIN; r.box.st = status([{ days: [1], time: '06:45' }], { next_run: at + 'Z', state: 'playing' });
  await r.keeper.tick();
  assert.deepEqual(r.blocker.stopped, [], 'playing keeps it');
  r.box.now = slot + 5 * MIN; r.box.st = status([{ days: [1], time: '06:45' }], { next_run: at + 'Z', state: 'idle' });
  await r.keeper.tick();
  assert.deepEqual(r.blocker.stopped, [], 'still inside the late limit');
  r.box.now = slot + LATE_LIMIT_MS + MIN; await r.keeper.tick();
  assert.deepEqual(r.blocker.stopped, [1]);
  r.box.now = slot + LATE_LIMIT_MS + 2 * MIN; await r.keeper.tick();
  assert.equal(r.blocker.started, 1, 'not taken again');
});

test('sign-in is only offered in the installed app', async () => {
  const dev = rig({ managed: false });
  assert.equal(dev.keeper.loginState().managed, false);
  await dev.keeper.setLogin(true);
  assert.equal(dev.box.login, false);
  const inst = rig({ managed: true });
  await inst.keeper.setLogin(true);
  assert.equal(inst.box.login, true);
  assert.deepEqual(inst.box.loginArgs, ['--hidden']);
  assert.deepEqual(inst.keeper.loginState(), { managed: true, platform: 'win32', login: true, wake_error: null });
});

test('a hidden start opens no window', () => {
  assert.equal(shouldShowWindow(['app.exe', '--hidden']), false);
  assert.equal(shouldShowWindow(['app.exe']), true);
});

test('the blocker is released after the deadline even when the status read fails', async () => {
  const at = '2026-11-24T06:45:00Z';
  const r = rig({ st: status([{ days: [1], time: '06:45' }], { next_run: at }) });
  const slot = Date.parse(at);
  r.box.now = slot - 5 * MIN; await r.keeper.tick();
  assert.equal(r.blocker.started, 1);
  r.box.fail = true;
  r.box.now = slot + 5 * MIN; await r.keeper.tick();
  assert.deepEqual(r.blocker.stopped, [], 'held until the deadline');
  r.box.now = slot + LATE_LIMIT_MS + MIN; await r.keeper.tick();
  assert.deepEqual(r.blocker.stopped, [1]);
});

test('a busy status whose next_run is tomorrow releases about 15 minutes after the last good read', async () => {
  const tomorrow = '2026-11-25T06:45:00Z';
  const r = rig({ st: status([{ days: [1], time: '06:45' }], { next_run: tomorrow, state: 'playing' }) });
  const read = Date.parse('2026-11-24T06:46:00Z');
  r.box.now = read; await r.keeper.tick();
  assert.equal(r.blocker.started, 1);
  r.box.fail = true;
  r.box.now = read + 10 * MIN; await r.keeper.tick();
  assert.deepEqual(r.blocker.stopped, [], 'still inside the limit');
  r.box.now = read + LATE_LIMIT_MS + MIN; await r.keeper.tick();
  assert.deepEqual(r.blocker.stopped, [1], 'released long before tomorrow');
});

test('a status that is null or not an object does not throw', async () => {
  const r = rig();
  for (const bad of [null, undefined, 5, 'x', { briefings: 7 }, { briefings: [null, 3] }]) {
    r.box.st = bad;
    await r.keeper.tick();
    await r.keeper.sync();
  }
  assert.equal(r.blocker.started, 0);
});

test('the timer callback never leaves an unhandled rejection', async () => {
  let cb = null, fail = false, t = 0;
  const rejections = [];
  const onRej = e => rejections.push(e);
  process.on('unhandledRejection', onRej);
  let stopCalls = 0;
  const k = createBriefingKeeper({
    platform: 'win32', managed: true,
    fetchStatus: async () => { if (fail) throw new Error('down'); return status([{ days: [1], time: '06:45' }], { next_run: new Date(5 * MIN).toISOString() }); },
    runSchtasks: async () => {}, writeTaskXml: async () => ({ path: 'x', remove() {} }),
    powerSaveBlocker: { start: () => 1, stop: () => { stopCalls += 1; throw new Error('stop failed'); } },
    setLoginItem() {}, getLoginItem: () => false, now: () => t, setTimer: f => { cb = f; return 1; }, clearTimer() {},
  });
  k.start();  // a good read inside the window: the hold is taken
  await new Promise(r => setImmediate(r)); await new Promise(r => setImmediate(r));
  fail = true; t = 5 * MIN + LATE_LIMIT_MS + MIN;
  cb();  // the read fails past the deadline: the release throws inside tick
  await new Promise(r => setImmediate(r)); await new Promise(r => setImmediate(r));
  process.off('unhandledRejection', onRej);
  assert.ok(stopCalls >= 1, 'the release was attempted');
  assert.deepEqual(rejections, []);
});

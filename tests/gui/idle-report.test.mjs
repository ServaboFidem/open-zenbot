// The Electron main process's idle report: the computer's idle seconds go to the harness's /dream/idle every
// 60 s, and at once when the idle time drops (the user came back; checked every 5 s); a failed post (harness not up
// yet) is swallowed.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const { createIdleReport } = createRequire(import.meta.url)('../../src/electron/idle-report.cjs');

test('posts the idle time as JSON to /dream/idle', async () => {
  const sent = [];
  const r = createIdleReport({ idleSeconds: () => 312, post: async (p, b) => { sent.push([p, b]); } });
  await r.tick();
  assert.deepEqual(sent, [['/dream/idle', { idle_s: 312 }]]);
});

test('a failed post is swallowed', async () => {
  const r = createIdleReport({ idleSeconds: () => 1, post: async () => { throw new Error('ECONNREFUSED'); } });
  await r.tick();
});

test('a throwing idle reader is swallowed too', async () => {
  const r = createIdleReport({ idleSeconds: () => { throw new Error('no monitor'); }, post: async () => {} });
  await r.tick();
});

// Fake timers: each setInterval gets an id; `fire(ms)` runs the timer set with that period.
function fakeTimers() {
  const timers = new Map(), cleared = [];
  let next = 1;
  return {
    timers, cleared,
    setInterval: (fn, ms) => { const id = next++; timers.set(id, [fn, ms]); return id; },
    clearInterval: id => { cleared.push(id); timers.delete(id); },
    fire(ms) { for (const [fn, every] of [...timers.values()]) if (every === ms) fn(); },
  };
}
const settle = () => new Promise(r => setImmediate(r));

test('start posts once, then every 60 s and watches every 5 s, only once; stop clears both', async () => {
  const sent = [], t = fakeTimers();
  const r = createIdleReport({ idleSeconds: () => 7, post: async (p, b) => { sent.push([p, b]); }, setInterval: t.setInterval, clearInterval: t.clearInterval });
  r.start(); r.start();
  assert.deepEqual([...t.timers.values()].map(([, ms]) => ms).sort((a, b) => a - b), [5000, 60000]);
  await settle();
  assert.equal(sent.length, 1);
  t.fire(60000);
  await settle();
  assert.equal(sent.length, 2);
  r.stop(); r.stop();
  assert.equal(t.cleared.length, 2);
  assert.equal(t.timers.size, 0);
});

test('a drop in idle time (the user came back) posts at once', async () => {
  const sent = [], t = fakeTimers();
  let idle = 400;
  const r = createIdleReport({ idleSeconds: () => idle, post: async (p, b) => { sent.push(b.idle_s); }, setInterval: t.setInterval, clearInterval: t.clearInterval });
  r.start();
  await settle();
  assert.deepEqual(sent, [400]);
  idle = 2;  // a key was pressed
  t.fire(5000);
  await settle();
  assert.deepEqual(sent, [400, 2]);
  idle = 1;  // still active: a smaller value again counts as a return too
  t.fire(5000);
  await settle();
  assert.deepEqual(sent, [400, 2, 1]);
});

test('a rising idle time does not post between the 60 s ticks', async () => {
  const sent = [], t = fakeTimers();
  let idle = 10;
  const r = createIdleReport({ idleSeconds: () => idle, post: async (p, b) => { sent.push(b.idle_s); }, setInterval: t.setInterval, clearInterval: t.clearInterval });
  r.start();
  await settle();
  for (let i = 0; i < 11; i++) { idle += 5; t.fire(5000); }
  await settle();
  assert.deepEqual(sent, [10]);
  t.fire(60000);
  await settle();
  assert.deepEqual(sent, [10, 65]);
});

test('a throwing idle reader in the watch is swallowed', async () => {
  const t = fakeTimers();
  const r = createIdleReport({ idleSeconds: () => { throw new Error('no monitor'); }, post: async () => {}, setInterval: t.setInterval, clearInterval: t.clearInterval });
  r.start();
  t.fire(5000);
  await settle();
  r.stop();
});

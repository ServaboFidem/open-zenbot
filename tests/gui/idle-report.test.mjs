// The Electron main process's idle report: the computer's idle seconds go to the harness's /dream/idle every
// 60 s; a failed post (harness not up yet) is swallowed.
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

test('start ticks once, then every 60 s, and only once; stop clears it', async () => {
  const sent = [], timers = [], cleared = [];
  const r = createIdleReport({
    idleSeconds: () => 7, post: async (p, b) => { sent.push([p, b]); },
    setInterval: (fn, ms) => { timers.push([fn, ms]); return 42; },
    clearInterval: id => cleared.push(id),
  });
  r.start(); r.start();
  assert.equal(timers.length, 1);
  assert.equal(timers[0][1], 60000);
  assert.equal(sent.length, 1);
  timers[0][0]();
  await Promise.resolve();
  assert.equal(sent.length, 2);
  r.stop(); r.stop();
  assert.deepEqual(cleared, [42]);
});

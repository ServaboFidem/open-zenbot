// The Electron main process's digest notice: one native notification per unread weekly digest, remembered
// across restarts; none for on-demand, none with notify off, silent when the harness does not answer.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const { createArrival, arrivalText } = createRequire(import.meta.url)('../../src/electron/arrival.cjs');

const NOW = Date.parse('2026-11-13T10:00:00Z');
const weekly = (id, { n = 4, read_at = null, created_at = '2026-11-13T09:00:00Z', kind = 'weekly' } = {}) => ({ id, kind, read_at, created_at, cards: Array.from({ length: n }, (_, i) => i) });
function setup({ status = { unread: 1, notify: true }, digest, stored = null } = {}) {
  const state = { status, digest, fail: false, shown: [], stored, gets: [] };
  const make = () => createArrival({
    get: async path => { state.gets.push(path); if (state.fail) throw new Error('down'); return path.startsWith('/music/status') ? state.status : [state.digest]; },
    show: n => state.shown.push(n),
    load: () => state.stored,
    save: ids => { state.stored = ids; },
    now: () => NOW,
  });
  return { state, make, arrival: make() };
}

test('a digest unread at the first poll notifies once', async () => {
  const { state, arrival } = setup({ digest: weekly('d1') });
  await arrival.poll(); await arrival.poll();
  assert.deepEqual(state.shown, [{ title: 'OpenZenBot', body: "This week's 4 albums are in", digestId: 'd1' }]);
  assert.ok(state.gets.includes('/music/digests?limit=1'));
});

test('after a restart (new instance, same store) it does not notify again', async () => {
  const { state, make, arrival } = setup({ digest: weekly('d1') });
  await arrival.poll();
  await make().poll();
  assert.equal(state.shown.length, 1);
});

test('one digest read and another arriving with the same unread count still notifies', async () => {
  const { state, arrival } = setup({ digest: weekly('d1') });
  await arrival.poll();
  state.digest = weekly('d2');  // unread is still 1
  await arrival.poll();
  assert.deepEqual(state.shown.map(n => n.digestId), ['d1', 'd2']);
});

test('on-demand, read, and notify-off digests never notify', async () => {
  const { state, arrival } = setup({ digest: weekly('d1', { kind: 'on_demand' }) });
  await arrival.poll();
  state.digest = weekly('d2', { read_at: '2026-11-13T09:30:00Z' }); await arrival.poll();
  state.digest = weekly('d3'); state.status = { unread: 1, notify: false }; await arrival.poll();
  state.status = { unread: 0, notify: true }; await arrival.poll();
  assert.equal(state.shown.length, 0);
});

test('first install: an old unread digest is recorded without a notice, a fresh one notifies', async () => {
  const old = setup({ digest: weekly('d1', { created_at: '2026-11-10T09:00:00Z' }) });
  await old.arrival.poll();
  assert.equal(old.state.shown.length, 0);
  assert.deepEqual(old.state.stored, ['d1']);
  const fresh = setup({ digest: weekly('d2') });
  await fresh.arrival.poll();
  assert.equal(fresh.state.shown.length, 1);
  const known = setup({ digest: weekly('d3', { created_at: '2026-11-10T09:00:00Z' }), stored: [] });  // a store exists: not a first install
  await known.arrival.poll();
  assert.equal(known.state.shown.length, 1);
});

test('the stored ids are capped', async () => {
  const stored = Array.from({ length: 50 }, (_, i) => `old${i}`);
  const { state, arrival } = setup({ digest: weekly('new'), stored });
  await arrival.poll();
  assert.equal(state.stored.length, 50);
  assert.equal(state.stored.at(-1), 'new');
  assert.equal(state.stored[0], 'old1');
});

test('a harness that does not answer is silent, and notifies when it comes back', async () => {
  const { state, arrival } = setup({ digest: weekly('d1') });
  state.fail = true; await arrival.poll();
  assert.equal(state.shown.length, 0);
  state.fail = false; await arrival.poll();
  assert.equal(state.shown.length, 1);
});

test('one album reads as one', async () => {
  const { state, arrival } = setup({ digest: weekly('d1', { n: 1 }) });
  await arrival.poll();
  assert.equal(state.shown[0].body, "This week's album is in");
  assert.equal(arrivalText(3), "This week's 3 albums are in");
});

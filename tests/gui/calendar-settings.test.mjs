import test from 'node:test';
import assert from 'node:assert/strict';
import { APP_STEPS, bringInList, defaultDestination, destinations, pushFailure, pushSentence, shownSections, signInChanged } from '../../src/gui/calendar-settings.js';

const st = (state, can_write = false) => ({ google: { state, can_write } });
const cal = (id, o = {}) => ({ id, name: id, source: 'google', writable: true, primary: false, ...o });

test('four steps, the last names the Desktop app type', () => {
  assert.equal(APP_STEPS.length, 4);
  assert.match(APP_STEPS[3], /"Desktop app"/);
});
test('calendars and push show only when connected', () => {
  for (const s of ['not_set_up', 'ready', 'needs_reconnect']) assert.deepEqual(shownSections(st(s)), { google: true, calendars: false, push: false }, s);
  assert.deepEqual(shownSections(st('connected')), { google: true, calendars: true, push: true });
  assert.equal(shownSections(null).calendars, false);
});
test('a sign-in is noticed by the state or the write permission changing', () => {
  assert.equal(signInChanged(st('ready'), st('ready')), false);
  assert.equal(signInChanged(st('ready'), st('connected')), true);
  assert.equal(signInChanged(st('connected', false), st('connected', true)), true);
  assert.equal(signInChanged(null, st('connected')), false);
});
test('only Google calendars are listed, and only writable ones are destinations', () => {
  const list = [cal('a'), cal('b', { writable: false }), { id: 'local', source: 'local', writable: true }, null];
  assert.deepEqual(bringInList(list).map(c => c.id), ['a', 'b']);
  assert.deepEqual(destinations(list).map(c => c.id), ['a']);
  assert.deepEqual(destinations(undefined), []);
});
test('the default destination is the saved one, else primary, else first, else empty', () => {
  const list = [cal('a'), cal('b', { primary: true }), cal('c', { writable: false })];
  assert.equal(defaultDestination(list, 'a'), 'a');
  assert.equal(defaultDestination(list, 'c'), 'b');
  assert.equal(defaultDestination([cal('a'), cal('d')], ''), 'a');
  assert.equal(defaultDestination([cal('c', { writable: false })], ''), '');
});
test('the push sentence names only the non-zero counts', () => {
  assert.equal(pushSentence({ add: 12, update: 3, remove: 1 }, 'Work'), '12 added, 3 updated, 1 removed on Google');
  assert.equal(pushSentence({ add: 2, update: 0, remove: 0 }, 'Work'), '2 added on Google');
  assert.equal(pushSentence({ add: 0, update: 0, remove: 0 }, 'Work'), 'Nothing to send yet. Events you make from now on will be added to Work.');
});
test('a 409 on switching push on asks for write access; anything else shows the sentence', () => {
  assert.deepEqual(pushFailure({ status: 409, message: 'Allow OpenZenBot to add events to Google first.' }), { kind: 'allow' });
  assert.deepEqual(pushFailure({ status: 422, message: 'Pick a Google calendar.' }), { kind: 'show', text: 'Pick a Google calendar.' });
  assert.deepEqual(pushFailure(new Error('')), { kind: 'show', text: 'Something went wrong.' });
  assert.deepEqual(pushFailure(null), { kind: 'show', text: 'Something went wrong.' });
});

import { watchStatus } from '../../src/gui/calendar-settings.js';

// A fake clock and timers; each status request is a promise the test settles itself.
function rig() {
  const r = { t: 0, timers: [], requests: [], calls: [], timeouts: 0 };
  r.setTimer = (fn, ms) => { const x = { fn, at: r.t + ms, live: true }; r.timers.push(x); return x; };
  r.clearTimer = x => { x.live = false; };
  r.fetchStatus = () => new Promise((res, rej) => r.requests.push({ res, rej }));
  r.now = () => r.t;
  // advance the clock to the next live timer and run it
  r.fire = async () => { const x = r.timers.find(x => x.live); if (!x) return false; x.live = false; r.t = x.at; x.fn(); await Promise.resolve(); return true; };
  r.start = (changed, tag = 'a') => watchStatus({
    fetchStatus: r.fetchStatus, changed, now: r.now, setTimer: r.setTimer, clearTimer: r.clearTimer,
    onChange: async n => { r.calls.push(`${tag}:change:${n}`); }, onDone: n => r.calls.push(`${tag}:done:${n}`), onTimeout: () => { r.timeouts++; r.calls.push(`${tag}:timeout`); },
  });
  r.settle = async (i, v) => { r.requests[i].res(v); for (let k = 0; k < 5; k++) await Promise.resolve(); };
  return r;
}
const isOne = n => n === 1;

test('a change calls onChange then onDone and stops asking', async () => {
  const r = rig(); r.start(isOne);
  await r.fire(); await r.settle(0, 0);
  assert.equal(r.requests.length, 1); assert.deepEqual(r.calls, []);
  await r.fire(); await r.settle(1, 1);
  assert.deepEqual(r.calls, ['a:change:1', 'a:done:1']);
  assert.equal(await r.fire(), false);
});
test('the limit ends it with onTimeout after 10 minutes', async () => {
  const r = rig(); r.start(isOne);
  let n = 0;
  while (r.timeouts === 0 && n < 400) { if (!(await r.fire())) break; await r.settle(r.requests.length - 1, 0); n++; }
  assert.equal(r.timeouts, 1); assert.ok(r.t >= 600000 && r.t < 604000, String(r.t));
  assert.equal(await r.fire(), false);
});
test('cancel while a request is in flight: no callback, no more requests, no timeout', async () => {
  const r = rig(); const cancel = r.start(isOne);
  await r.fire(); cancel();
  await r.settle(0, 1);
  assert.deepEqual(r.calls, []);
  assert.equal(await r.fire(), false); assert.equal(r.requests.length, 1); assert.equal(r.timeouts, 0);
});
test('cancel with a stale unchanged answer does not reschedule or time out', async () => {
  const r = rig(); const cancel = r.start(isOne);
  await r.fire(); cancel(); await r.settle(0, 0);
  assert.equal(await r.fire(), false); assert.equal(r.timeouts, 0);
});
test('a second watch started while the first is in flight: only the second can call back', async () => {
  const r = rig(); const first = r.start(isOne, 'first');
  await r.fire();            // first's request is in flight
  first();                   // what the component does before it starts the next watch
  r.start(isOne, 'second');
  await r.settle(0, 1);      // the old answer arrives, changed
  assert.deepEqual(r.calls, []);
  await r.fire(); await r.settle(1, 1);
  assert.deepEqual(r.calls, ['second:change:1', 'second:done:1']);
});
test('cancel during onChange keeps onDone from running', async () => {
  const r = rig(); let cancel;
  cancel = watchStatus({ fetchStatus: r.fetchStatus, changed: isOne, now: r.now, setTimer: r.setTimer, clearTimer: r.clearTimer,
    onChange: async () => { cancel(); await Promise.resolve(); }, onDone: () => r.calls.push('done'), onTimeout: () => {} });
  await r.fire(); await r.settle(0, 1);
  assert.deepEqual(r.calls, []);
});

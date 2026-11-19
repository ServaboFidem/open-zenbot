import test from 'node:test';
import assert from 'node:assert/strict';
import { canEnable, canRunNow, dreamApi, errorLine, needsDesktopNote, statusLine, validMinutes } from '../../src/gui/dream.js';

test('canEnable needs a chosen model', () => {
  assert.equal(canEnable({ model: { provider: 'p', model: 'm' } }), true);
  assert.equal(canEnable({ model: null }), false);
  assert.equal(canEnable(null), false);
});
test('canRunNow needs ready, online and not dreaming', () => {
  assert.equal(canRunNow({ ready: true, dreaming: false }, true), true);
  assert.equal(canRunNow({ ready: false }, true), false);
  assert.equal(canRunNow({ ready: true }, false), false);
  assert.equal(canRunNow({ ready: true, dreaming: true }, true), false);
});
test('validMinutes is an integer 1 to 120', () => {
  assert.equal(validMinutes('5'), true); assert.equal(validMinutes(120), true); assert.equal(validMinutes(1), true);
  for (const v of [0, 121, 5.5, '', '5.5', 'x', '-3', null]) assert.equal(validMinutes(v), false, String(v));
});
test('statusLine in its three forms', () => {
  const now = new Date(2026, 10, 19, 12, 0);
  const at = new Date(2026, 10, 19, 3, 12).toISOString();
  assert.equal(statusLine({ waiting: 12, learned: 37, last_dream_at: at }, now), 'Waiting: 12 conversations · Learned so far: 37 facts · Last dream: today 03:12');
  assert.equal(statusLine({ waiting: 0, learned: 0, last_dream_at: '' }, now), 'Waiting: 0 conversations · Learned so far: 0 facts · Last dream: never');
  assert.equal(statusLine({ waiting: 1, learned: 1, last_dream_at: '', dreaming: true }, now), 'Dreaming now · Waiting: 1 conversation · Learned so far: 1 fact · Last dream: never');
});
test('statusLine names another day', () => {
  const l = statusLine({ waiting: 0, learned: 2, last_dream_at: new Date(2026, 10, 17, 22, 5).toISOString() }, new Date(2026, 10, 19, 12, 0));
  assert.match(l, /Last dream: Nov 17 22:05$/);
});
test('errorLine', () => {
  assert.equal(errorLine({ error: 'unreachable' }), 'The dreaming model could not be reached. Trying again in 10 minutes.');
  assert.equal(errorLine({ error: 'model' }), 'The dreaming model gave an answer that could not be used.');
  assert.equal(errorLine({ error: '' }), ''); assert.equal(errorLine(null), '');
});
test('the desktop-app note shows only with dreaming on and no report', () => {
  assert.equal(needsDesktopNote({ enabled: true, idle_reported: false }), true);
  assert.equal(needsDesktopNote({ enabled: true, idle_reported: true }), false);
  assert.equal(needsDesktopNote({ enabled: false, idle_reported: false }), false);
});
test('dreamApi paths and bodies', async () => {
  const calls = [];
  const api = dreamApi(async (...a) => { calls.push(a); return {}; });
  await api.status(); await api.saveSettings({ enabled: true, idle_minutes: 7, extra: 1 }); await api.runNow(); await api.facts(); await api.forget('a/b');
  assert.deepEqual(calls, [
    ['GET', '/dream/status'],
    ['PUT', '/dream/settings', { enabled: true, idle_minutes: 7 }],
    ['POST', '/dream/run', {}],
    ['GET', '/dream/facts'],
    ['DELETE', '/memory/a%2Fb'],
  ]);
});

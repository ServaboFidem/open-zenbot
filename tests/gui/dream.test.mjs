import test from 'node:test';
import assert from 'node:assert/strict';
import { PRIVACY, canEnable, canRunNow, dreamApi, errorLine, factsChanged, modelsKnown, needsDesktopNote, statusLine, validMinutes } from '../../src/gui/dream.js';
import { SECTIONS } from '../../src/gui/settings-index.js';

test('the privacy sentence says where the messages and the facts go', () => {
  assert.equal(PRIVACY, "Only the messages you typed are read, never a bot's reply. They are sent to the dreaming model you choose: a hosted model's provider receives them, a local model reads them on this computer. The facts it writes are then stored by the memory engine, which uses its own model.");
  const entry = SECTIONS.find(s => s.title === 'Dreaming');
  assert.ok(entry && JSON.stringify(entry).includes(PRIVACY));
  assert.ok(!JSON.stringify(SECTIONS).includes('keeps them on this computer'));
});

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
  assert.equal(errorLine({ error: 'provider' }), "The dreaming model's provider refused the request. Check its key and limits. Trying again in 10 minutes.");
  assert.equal(errorLine({ error: 'store' }), 'A fact could not be saved to memory. Trying again in 10 minutes.');
  assert.equal(errorLine({ error: 'setup' }), 'The dreaming model cannot be used. Pick it again under Dreaming model.');
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

test('the learned facts are read again when a pass ends or the totals move', () => {
  const s = { dreaming: false, learned: 3, last_dream_at: '2026-11-19T03:12:00Z' };
  assert.equal(factsChanged(null, s), false);  // the first status: the facts are loaded with it
  assert.equal(factsChanged(s, { ...s }), false);
  assert.equal(factsChanged({ ...s, dreaming: true }, s), true);
  assert.equal(factsChanged(s, { ...s, dreaming: true }), false);
  assert.equal(factsChanged(s, { ...s, learned: 4 }), true);  // a pass that began and ended between two looks
  assert.equal(factsChanged(s, { ...s, last_dream_at: '2026-11-19T04:00:00Z' }), true);
});

test('the Dreaming model field waits for the providers and the roles before it judges a model', () => {
  assert.equal(modelsKnown(null, null), false);
  assert.equal(modelsKnown([], null), false);
  assert.equal(modelsKnown(null, { roles: {} }), false);
  assert.equal(modelsKnown([], { roles: {} }), true);
  assert.equal(modelsKnown([{ id: 'p', models: [] }], { roles: { dream: 'text' } }), true);
});

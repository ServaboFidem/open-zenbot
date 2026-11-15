// The old localStorage routines move to the harness once: the key is cleared only after a 200,
// what could not be parsed comes back to show, and the schedule editor's helpers.
import test from 'node:test';
import assert from 'node:assert/strict';
import { legacyRoutines, importLegacyRoutines, persistWorkspace, dropLegacyRoutines, importNotice } from '../../src/gui/routines-import.js';
import { defaultSchedule, validateSchedule, describeOutcome, toggleDay, botSkills } from '../../src/gui/routine-schedule.js';
import { weeklyArrival, arrivalText } from '../../src/gui/music/helpers.js';

const KEY = 'openzenbot-workspace-v1';
const mem = (value) => {
  const m = new Map(value === undefined ? [] : [[KEY, JSON.stringify(value)]]);
  return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, v), blob: () => JSON.parse(m.get(KEY)) };
};
const old = [{ id: 'a', name: 'Brief', bot: 'b', skill: 's', schedule: 'Fridays 9:00', enabled: true }];

test('legacyRoutines reads the routines from the workspace blob and tolerates nothing there', () => {
  assert.deepEqual(legacyRoutines(mem({ routines: old, meta: {} }), KEY), old);
  assert.deepEqual(legacyRoutines(mem({ meta: {} }), KEY), []);
  assert.deepEqual(legacyRoutines(mem(), KEY), []);
  assert.deepEqual(legacyRoutines({ getItem() { throw new Error('blocked'); } }, KEY), []);
});

test('import posts once and drops only the routines after a 200', async () => {
  const storage = mem({ routines: old, meta: { x: 1 } });
  const posted = [];
  const api = { importRoutines: async r => { posted.push(r); return { imported: [{}], existing: [], unparsed: [] }; } };
  const result = await importLegacyRoutines({ api, storage, key: KEY });
  assert.deepEqual(posted, [old]);
  assert.deepEqual(result.imported.length, 1);
  assert.deepEqual(storage.blob(), { meta: { x: 1 } });
  assert.equal(await importLegacyRoutines({ api, storage, key: KEY }), null);  // nothing left to send
  assert.equal(posted.length, 1);
});

test('a failed import keeps the routines for the next load', async () => {
  const storage = mem({ routines: old });
  const api = { importRoutines: async () => { throw new Error('503'); } };
  assert.equal(await importLegacyRoutines({ api, storage, key: KEY }), null);
  assert.deepEqual(storage.blob().routines, old);
});

test('unparsed routines are returned and stay in storage until dismissed for good', async () => {
  const odd = { id: 'o', name: 'Odd', bot: 'b', skill: 's', schedule: 'when it rains', enabled: true };
  const storage = mem({ routines: [...old, odd], meta: {} });
  const api = { importRoutines: async () => ({ imported: [{ name: 'Brief' }], existing: [], unparsed: [{ ...odd }] }) };
  const result = await importLegacyRoutines({ api, storage, key: KEY });
  assert.deepEqual(result.unparsed, [odd]);
  assert.deepEqual(storage.blob().routines, [odd]);  // the parsed one left, the unparsed one waits
  assert.ok(await importLegacyRoutines({ api, storage, key: KEY }));  // sent again next load
  dropLegacyRoutines(storage, KEY);
  assert.deepEqual(storage.blob(), { meta: {} });
});

test('the import notice says the moved routines are paused and names what could not move', () => {
  assert.equal(importNotice({ imported: [], unparsed: [] }), '');
  assert.match(importNotice({ imported: [{ name: 'A' }], unparsed: [] }), /One routine .* paused/);
  assert.match(importNotice({ imported: [{ name: 'A' }, { name: 'B', note: 'Skill x is not attached to b.' }], unparsed: [] }), /2 routines .*B: Skill x is not attached/);
  assert.match(importNotice({ imported: [], unparsed: [{ name: 'Odd', schedule: 'when it rains' }] }), /Odd \(when it rains\).*dismiss it for good/);
});

test('saving the workspace keeps routines still waiting to be imported', () => {
  const storage = mem({ routines: old, meta: {} });
  persistWorkspace(storage, KEY, { meta: { y: 2 }, connections: [] });
  assert.deepEqual(storage.blob(), { meta: { y: 2 }, connections: [], routines: old });
  const clean = mem({ meta: {} });
  persistWorkspace(clean, KEY, { meta: {} });
  assert.deepEqual(clean.blob(), { meta: {} });
});

test('schedule helpers: defaults, validation, day toggling, outcome words', () => {
  assert.deepEqual(defaultSchedule(), { kind: 'weekly', days: [4], time: '09:00', hours: 24 });
  assert.equal(validateSchedule(defaultSchedule()), '');
  assert.match(validateSchedule({ kind: 'weekly', days: [], time: '09:00', hours: 24 }), /day/);
  assert.match(validateSchedule({ kind: 'daily', days: [], time: '', hours: 24 }), /time/);
  assert.match(validateSchedule({ kind: 'interval', days: [], time: '', hours: 0 }), /1 to 168/);
  assert.equal(validateSchedule({ kind: 'interval', days: [], time: '', hours: 6 }), '');
  assert.deepEqual(toggleDay([4], 0), [0, 4]);
  assert.deepEqual(toggleDay([0, 4], 4), [0]);
  assert.equal(describeOutcome('ok'), 'finished');
  assert.match(describeOutcome('skipped_overlap'), /still running/);
  assert.match(describeOutcome('skipped_missed'), /missed/);
  assert.equal(describeOutcome('failed'), 'failed');
  assert.equal(describeOutcome('started'), 'running');
  assert.equal(describeOutcome('failed', 'no digest saved'), 'failed: no digest saved');
  assert.equal(describeOutcome('ok', 'ignored'), 'finished');
});

test('the routine dialog offers only the chosen bot\'s attached skills', () => {
  const bots = [{ id: 'a', skills: ['x'] }, { id: 'b', skills: [] }];
  const skills = [{ ref: 'x', name: 'X' }, { ref: 'y', name: 'Y' }];
  assert.deepEqual(botSkills('a', bots, skills).map(s => s.ref), ['x']);
  assert.deepEqual(botSkills('b', bots, skills), []);
  assert.deepEqual(botSkills('ghost', bots, skills), []);
});

test('the in-app notice is for an unread weekly digest only', () => {
  const weekly = { id: 'd1', kind: 'weekly', read_at: null, cards: [1, 2, 3, 4] };
  assert.deepEqual(weeklyArrival(weekly), { id: 'd1', count: 4 });
  assert.equal(weeklyArrival({ ...weekly, read_at: '2026-11-13T09:00:00' }), null);
  assert.equal(weeklyArrival({ ...weekly, kind: 'on_demand' }), null);
  assert.equal(weeklyArrival(null), null);
  assert.equal(arrivalText(1), "This week's album is in");
  assert.equal(arrivalText(4), "This week's 4 albums are in");
});

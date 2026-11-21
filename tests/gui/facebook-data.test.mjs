// The Facebook tab's data wiring: the lists route's answer shaped for the panels, the helper status line,
// and where the reads live (the hook and the api, never the panels or the mount).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { EMPTY, LISTS_FAILED, helperStatus, shapeLists, withChange } from '../../src/gui/events/facebook.js';
import { readLabel } from '../../src/gui/events/all-events.js';

const events = id => readFileSync(new URL(`../../src/gui/events/${id}`, import.meta.url), 'utf8');
const NOW = Date.parse('2026-11-20T12:00:30Z');
const answer = () => ({
  invites: [{ id: 'fb-1', name: 'Housewarming', start_date: '2026-12-05', start_time: '', state: '' }],
  approvals: [{ id: 'fb-2', name: 'Dinner', start_date: '2026-12-06', start_time: '', state: '', why: 'A close friend is part of this event.' }],
  birthdays: [{ id: 'fb-bday-1', name: 'Sam Rivera', date: '2026-11-21', start_date: '2026-11-21', daysUntil: 1 }],
  status: { last_read: '2026-11-20T12:00:00Z', error: '', ready: true, pool: { facebook: 2, 'facebook-birthday': 1 } },
});

test('the answer becomes the three lists the panels take', () => {
  const l = shapeLists(answer());
  assert.deepEqual(l.invites.map(i => i.id), ['fb-1']);
  assert.equal(l.approvals[0].why, 'A close friend is part of this event.');
  assert.equal(l.birthdays[0].daysUntil, 1);
  assert.equal(l.birthdays[0].date, 'Sat 21 Nov');
});

test('an answer of the wrong shape is three empty lists, and rows without an id are left out', () => {
  assert.deepEqual(shapeLists(null), EMPTY);
  assert.deepEqual(shapeLists({ invites: 'x', approvals: {}, birthdays: 3 }), EMPTY);
  assert.deepEqual(shapeLists({ invites: [null, {}, { id: '' }, { id: 'fb-1' }] }).invites, [{ id: 'fb-1' }]);
});

// FacebookPanels.jsx's helperStatusText reads these four fields: an error first, then not ready, then
// refreshing, then readLabel(lastRead). node cannot load the .jsx, so the fields are checked here.
test('the status maps to what the status line reads', () => {
  const s = helperStatus(answer().status);
  assert.deepEqual(s, { lastRead: '2026-11-20T12:00:00Z', error: '', ready: true, refreshing: false });
  assert.equal(readLabel(s.lastRead, NOW), 'Read just now');
  assert.equal(readLabel(helperStatus({ last_read: '', error: '', ready: true }).lastRead, NOW), 'Not read yet');
  assert.equal(helperStatus({ last_read: 7, ready: 1 }).lastRead, '');
});

test('before the first answer the line says reading, then not ready', () => {
  assert.deepEqual(helperStatus(null, { loading: true }), { lastRead: '', error: '', ready: true, refreshing: true });
  assert.deepEqual(helperStatus(null), { lastRead: '', error: '', ready: false, refreshing: false });
  // a later look keeps the last read on show
  assert.equal(helperStatus(answer().status, { loading: true }).refreshing, false);
});

test('an error is one of two sentences, never the stored value', () => {
  const saved = helperStatus({ last_read: '2026-11-20T12:00:00Z', error: 'provider', ready: true });
  assert.equal(saved.error, 'The last Facebook report could not be saved.');
  assert.equal(saved.lastRead, '2026-11-20T12:00:00Z');
  assert.equal(helperStatus({ last_read: '', error: 'unknown corner <b>', ready: true }).error, 'The last Facebook report was refused.');
  assert.equal(helperStatus(null, { failed: LISTS_FAILED }).error, LISTS_FAILED);
});

test('a change a panel reports shows at once', () => {
  const l = shapeLists(answer());
  assert.equal(withChange(l, { id: 'fb-1', state: 'added' }).invites[0].state, 'added');
  assert.equal(withChange(l, { id: 'fb-2', state: 'added' }).approvals[0].state, 'added');
  assert.deepEqual(withChange(l, { id: 'fb-2', state: 'dismissed' }).approvals, []);
  assert.equal(withChange(l, null), l);
});

test('the reads live in the hook and the api; the panels and the mount stay as they were', () => {
  const hook = events('useFacebook.js');
  assert.ok(hook.includes('discover.facebookLists()'), 'reads through the api');
  assert.ok(!hook.includes('fetch('), 'no fetch outside createApi.call');
  assert.ok(!hook.includes('addEvent') && !hook.includes('.add(') && !hook.includes('ismiss('), 'the hook writes nothing');
  assert.ok(!events('facebook.js').includes('fetch('));
  const area = events('EventsArea.jsx');
  assert.ok(area.includes(`useFacebook(api, { online, active: tab === 'facebook' })`), 'loads only while the tab is open');
  assert.ok(area.includes('{...facebook}'), 'hands the lists and status to the panels');
  assert.ok(!events('FacebookPanels.jsx').includes('fetch('));
});

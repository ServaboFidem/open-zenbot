// The Discover page's pure decisions: what to show, how times read, when to ask "Did you sign up?",
// and the instants sent when a card is added to the calendar.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  LOOK_LIMIT_MS, addTimes, canAdd, refreshNotice, shownCards, signupStep, viewState, whenLabel, whereLabel,
} from '../../src/gui/events/discover.js';
import { wallToInstant, localZone } from '../../src/gui/events/dates.js';
import { OFFLINE } from '../../src/gui/events/page-state.js';

const card = (over = {}) => ({
  id: 'c1', digest_id: 'd1', event_id: 'e1', why: 'Because.', state: 'new', calendar_event_id: '', image: null,
  name: 'Market', summary: '', start_date: '2026-11-22', start_time: '10:00', end_date: '2026-11-22', end_time: '16:00',
  timezone: 'America/New_York', venue: '', address: '', city: '', region: '', url: 'https://www.eventbrite.com/e/1',
  category_names: [], format: '', ...over,
});
const st = (over = {}) => ({ set_up: true, running: false, refresh: { last: '', error: '', pool: 0 }, ...over });
const stErr = (error, pool = 0) => st({ refresh: { last: '', error, pool } });

test('LOOK_LIMIT_MS matches the Music area DIG_LIMIT_MS (15 minutes)', () => {
  assert.equal(LOOK_LIMIT_MS, 15 * 60 * 1000);
});

test('addTimes: a timed card gives instants in its own zone', () => {
  assert.deepEqual(addTimes(card()), { start: '2026-11-22T15:00:00Z', end: '2026-11-22T21:00:00Z' });
});
test('addTimes: 23:30 in Los Angeles lands on the next UTC day', () => {
  assert.equal(addTimes(card({ start_date: '2026-11-22', start_time: '23:30', end_date: '2026-11-23', end_time: '01:00', timezone: 'America/Los_Angeles' })).start, '2026-11-23T07:30:00Z');
});
test('addTimes: no listed end is one hour', () => {
  assert.deepEqual(addTimes(card({ end_date: '', end_time: '' })), { start: '2026-11-22T15:00:00Z', end: '2026-11-22T16:00:00Z' });
  assert.deepEqual(addTimes(card({ end_date: '2026-11-22', end_time: '' })), { start: '2026-11-22T15:00:00Z', end: '2026-11-22T16:00:00Z' });
});
test('addTimes: an all-day card gives dates, the end exclusive', () => {
  assert.deepEqual(addTimes(card({ start_date: '2026-11-22', start_time: '', end_date: '2026-11-23', end_time: '' })), { start: '2026-11-22', end: '2026-11-24' });
  // an empty end date means the start date; the end rolls over a month
  assert.deepEqual(addTimes(card({ start_date: '2026-11-30', start_time: '', end_date: '', end_time: '' })), { start: '2026-11-30', end: '2026-12-01' });
});
test('addTimes: a start inside a daylight-saving gap moves to the first real minute', () => {
  const t = addTimes(card({ start_date: '2027-03-14', start_time: '02:30', end_date: '2027-03-14', end_time: '05:00', timezone: 'America/New_York' }));
  assert.equal(t.start, '2027-03-14T07:00:00Z');
  assert.equal(t.end, '2027-03-14T09:00:00Z');
});
test('addTimes: an unknown zone falls back to the computer zone rather than throwing', () => {
  const t = addTimes(card({ timezone: 'Mars/Olympus' }));
  assert.equal(t.start, wallToInstant('2026-11-22', '10:00', localZone()));
  assert.equal(t.end, wallToInstant('2026-11-22', '16:00', localZone()));
  assert.deepEqual(addTimes(card({ timezone: '' })).start, wallToInstant('2026-11-22', '10:00', localZone()));
});

test('whenLabel names the zone only when it differs from the computer zone', () => {
  const c = card();
  assert.equal(whenLabel(c, 'America/New_York'), 'Sun 22 Nov, 10:00 – 16:00');
  assert.equal(whenLabel(c, 'America/Los_Angeles'), 'Sun 22 Nov, 10:00 – 16:00 EST');
  assert.equal(whenLabel(card({ timezone: '' }), 'America/Los_Angeles'), 'Sun 22 Nov, 10:00 – 16:00');
  assert.equal(whenLabel(card({ timezone: 'Mars/Olympus' }), 'America/Los_Angeles'), 'Sun 22 Nov, 10:00 – 16:00');
});
test('whenLabel: one day, across days, all-day, no end', () => {
  const z = 'America/New_York';
  assert.equal(whenLabel(card(), z), 'Sun 22 Nov, 10:00 – 16:00');
  assert.equal(whenLabel(card({ start_time: '22:00', end_date: '2026-11-23', end_time: '01:00' }), z), 'Sun 22 Nov, 22:00 – Mon 23 Nov, 01:00');
  assert.equal(whenLabel(card({ start_time: '', end_time: '', end_date: '2026-11-22' }), z), 'Sun 22 Nov, all day');
  assert.equal(whenLabel(card({ start_time: '', end_time: '', end_date: '' }), z), 'Sun 22 Nov, all day');
  assert.equal(whenLabel(card({ start_time: '', end_time: '', end_date: '2026-11-23' }), z), 'Sun 22 Nov – Mon 23 Nov, all day');
  assert.equal(whenLabel(card({ end_date: '', end_time: '' }), z), 'Sun 22 Nov, 10:00');
});
test('whereLabel: venue and city, or either alone, or empty', () => {
  assert.equal(whereLabel(card({ venue: 'Pinnacle Golf Club', city: 'Grove City' })), 'Pinnacle Golf Club, Grove City');
  assert.equal(whereLabel(card({ venue: 'Pinnacle Golf Club' })), 'Pinnacle Golf Club');
  assert.equal(whereLabel(card({ city: 'Grove City' })), 'Grove City');
  assert.equal(whereLabel(card()), '');
});

test('viewState: each kind, in the order offline, loading, not_set_up, looking, empty, digest', () => {
  const ok = { online: true, loaded: true, looking: false };
  const digest = { id: 'd1', cards: [card()] };
  assert.deepEqual(viewState(st(), digest, { ...ok, online: false }), { kind: 'offline', text: OFFLINE, action: 'retry' });
  // offline wins over everything else
  assert.equal(viewState(null, null, { online: false, loaded: false, looking: true }).kind, 'offline');
  assert.equal(viewState(null, null, { ...ok, loaded: false }).kind, 'loading');
  assert.equal(viewState(st({ set_up: false }), digest, { ...ok, loaded: false }).kind, 'loading');
  const off = viewState(st({ set_up: false }), digest, { ...ok, looking: true });
  assert.equal(off.kind, 'not_set_up'); assert.equal(off.action, 'settings');
  assert.equal(viewState(st(), digest, { ...ok, looking: true }).kind, 'looking');
  assert.equal(viewState(st({ running: true }), digest, ok).kind, 'looking');
  const empty = viewState(st(), null, ok);
  assert.equal(empty.kind, 'empty'); assert.equal(empty.action, 'run');
  assert.equal(viewState(st(), { id: 'd', cards: [] }, ok).kind, 'empty');
  assert.deepEqual(viewState(st(), digest, ok), { kind: 'digest', text: '', action: '' });
  // every card dismissed: still the digest, with a sentence
  const spent = viewState(st(), { id: 'd', cards: [card({ state: 'dismissed' })] }, ok);
  assert.equal(spent.kind, 'digest'); assert.notEqual(spent.text, '');
});

test('refreshNotice words each error kind; source_changed says Eventbrite’s page has changed', () => {
  assert.equal(refreshNotice(stErr('source_changed')), 'Eventbrite’s page has changed and OpenZenBot cannot read it yet.');
  assert.equal(refreshNotice(stErr('network')), 'Could not reach Eventbrite.');
  assert.equal(refreshNotice(stErr('refused')), 'Eventbrite is turning requests away for now.');
  assert.equal(refreshNotice(stErr('provider')), 'Eventbrite sent an answer OpenZenBot could not use.');
});
test('refreshNotice: clean, unknown kind, no status, and a cut-short refresh that kept events', () => {
  assert.equal(refreshNotice(st()), '');
  assert.equal(refreshNotice(null), '');
  assert.equal(refreshNotice(stErr('weird')), 'The last refresh did not finish.');
  assert.equal(refreshNotice(stErr('network', 12)), 'Could not reach Eventbrite. Showing what was read.');
  assert.equal(refreshNotice(stErr('source_changed', 3)), 'Eventbrite’s page has changed and OpenZenBot cannot read it yet. Showing what was read.');
});

test('signupStep: opened then focus asks once; a second focus asks nothing; answered clears', () => {
  let r = signupStep(null, { type: 'opened', cardId: 'c1' });
  assert.deepEqual(r, { pending: 'c1', ask: null });
  r = signupStep(r.pending, { type: 'focus' });
  assert.deepEqual(r, { pending: null, ask: 'c1' });
  // asked once: the next focus (the dialog closing, "Not yet") asks nothing until a link is followed again
  r = signupStep(r.pending, { type: 'focus' });
  assert.deepEqual(r, { pending: null, ask: null });
  r = signupStep('c1', { type: 'answered' });
  assert.deepEqual(r, { pending: null, ask: null });
});
test('signupStep: focus with nothing opened asks nothing; a second opened replaces the first', () => {
  assert.deepEqual(signupStep(null, { type: 'focus' }), { pending: null, ask: null });
  assert.deepEqual(signupStep('c1', { type: 'opened', cardId: 'c2' }), { pending: 'c2', ask: null });
  assert.deepEqual(signupStep('c1', { type: 'nothing' }), { pending: 'c1', ask: null });
});

test('shownCards leaves out dismissed cards and keeps added ones', () => {
  const d = { cards: [card({ id: 'a' }), card({ id: 'b', state: 'dismissed' }), card({ id: 'c', state: 'added' })] };
  assert.deepEqual(shownCards(d).map(c => c.id), ['a', 'c']);
  assert.deepEqual(shownCards(null), []);
  assert.deepEqual(shownCards({}), []);
});

test('addTimes: the repeated hour in autumn takes the first one', () => {
  const t = addTimes(card({ start_date: '2026-11-01', start_time: '01:30', end_date: '2026-11-01', end_time: '03:00' }));
  assert.equal(t.start, '2026-11-01T05:30:00Z');
});

test('malformed cards: addTimes gives null for an unusable start date or a present, unusable start time', () => {
  for (const over of [{ start_date: 'tbd' }, { start_date: '' }, { start_date: '2026-02-30' }, { start_date: null }, { start_date: 20261122 },
    { start_time: 'soon' }, { start_time: '25:00' }, { start_time: '10:60' }, { start_time: 10 }]) {
    assert.equal(addTimes(card(over)), null, JSON.stringify(over));
    assert.equal(canAdd(card(over)), false);
  }
  assert.equal(addTimes(card({ start_date: 'x', start_time: '', end_date: '', end_time: '' })), null);
  assert.equal(canAdd(card()), true);
  assert.equal(canAdd(null), false);
});
test('malformed cards: an unusable end counts as no listed end', () => {
  const hour = { start: '2026-11-22T15:00:00Z', end: '2026-11-22T16:00:00Z' };
  assert.deepEqual(addTimes(card({ end_time: 'late' })), hour);
  assert.deepEqual(addTimes(card({ end_date: 'tbd' })), hour);
  assert.deepEqual(addTimes(card({ end_date: undefined, end_time: undefined })), hour);
  // an end not after the start
  assert.deepEqual(addTimes(card({ end_time: '10:00' })), hour);
  assert.deepEqual(addTimes(card({ end_time: '09:00' })), hour);
  // all-day: an unusable or earlier end gives the start date's own day
  const day = { start: '2026-11-22', end: '2026-11-23' };
  assert.deepEqual(addTimes(card({ start_time: '', end_date: 'x', end_time: '' })), day);
  assert.deepEqual(addTimes(card({ start_time: '', end_date: '2026-11-20', end_time: '' })), day);
  assert.deepEqual(addTimes(card({ start_time: null, end_date: null, end_time: null })), day);
});
test('malformed cards: whenLabel never throws', () => {
  const z = 'America/New_York';
  assert.equal(whenLabel(card({ start_date: 'tbd' }), z), 'Date not given');
  assert.equal(whenLabel(card({ start_date: 'x', start_time: '', end_date: '', end_time: '' }), z), 'Date not given');
  assert.equal(whenLabel(card({ start_date: null }), z), 'Date not given');
  assert.equal(whenLabel(card({ start_time: 'soon' }), z), 'Sun 22 Nov');
  assert.equal(whenLabel(card({ end_time: 'late' }), z), 'Sun 22 Nov, 10:00');
  assert.equal(whenLabel(card({ end_date: 'tbd' }), z), 'Sun 22 Nov, 10:00 – 16:00');
  assert.equal(whenLabel(null, z), 'Date not given');
});
test('nothing throws for fields that are strings, empty, null, undefined or numbers', () => {
  const values = ['', 'x', null, undefined, 5, '2026-11-22', '10:00', 'Mars/Olympus', {}];
  const keys = ['start_date', 'start_time', 'end_date', 'end_time', 'timezone', 'venue', 'city'];
  for (const k of keys) {
    for (const v of values) {
      const c = card({ [k]: v });
      assert.doesNotThrow(() => { addTimes(c); canAdd(c); whenLabel(c, 'America/Los_Angeles'); whereLabel(c); }, `${k}=${String(v)}`);
    }
  }
  assert.doesNotThrow(() => { addTimes(undefined); whenLabel(undefined); whereLabel(undefined); });
  assert.equal(whereLabel(card({ venue: 5, city: 'Grove City' })), 'Grove City');
  assert.deepEqual(shownCards({ cards: 'nope' }), []);
});

// The All Events grid's pure decisions: filtering, sorting, labels, states.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  COLUMNS, DEFAULT_SORT, EMPTY_FILTERS, MARKS, NO_EVENTS, NO_MATCH, PAGE, READING, SOURCE_NAMES,
  canRefresh, categoryOptions, cells, countLabel, filterEvents, gridCredit, gridState, isBusy, keepFilters, nearEnd,
  nextSort, panelCredit, readLabel, rowKeyStep, showSourceFilter, sortEvents, sourcesPresent, visibleCount, withState,
} from '../../src/gui/events/all-events.js';

const ev = (over = {}) => ({
  id: 'e1', name: 'Jazz', start_date: '2026-11-21', start_time: '19:00', end_date: '', end_time: '',
  venue: 'Lincoln Theatre', city: 'Dublin', category_names: ['Music'], source: 'eventbrite', state: '', ...over,
});
const names = list => list.map(e => e.name);

test('constants', () => {
  assert.deepEqual(EMPTY_FILTERS, { text: '', category: '', source: '', from: '', to: '' });
  assert.deepEqual(COLUMNS.map(c => c.key), ['date', 'time', 'name', 'venue', 'category', 'source']);
  assert.deepEqual(DEFAULT_SORT, { key: 'date', dir: 'asc' });
  assert.deepEqual(SOURCE_NAMES, { eventbrite: 'Eventbrite', findlocal: 'FindLocal' });
  assert.equal(MARKS.recommended.glyph, '★');
  assert.equal(MARKS.added.label, 'On your calendar');
  assert.equal(PAGE, 200);
  assert.equal(READING, 'Reading events… this takes a couple of minutes.');
  assert.equal(NO_EVENTS, 'No events yet.');
  assert.equal(NO_MATCH, 'No events match.');
});

test('filterEvents: text is case-insensitive over name, venue and city', () => {
  const list = [ev({ id: 'a', name: 'Jazz at the Lincoln Theatre', venue: '', city: '' }), ev({ id: 'b', name: 'Other', venue: 'Hall', city: 'Lincoln' }), ev({ id: 'c', name: 'No', venue: 'Zed', city: 'X' })];
  assert.deepEqual(filterEvents(list, { ...EMPTY_FILTERS, text: 'LINCOLN' }).map(e => e.id), ['a', 'b']);
  assert.deepEqual(filterEvents(list, { ...EMPTY_FILTERS, text: 'zed' }).map(e => e.id), ['c']);
});
test('filterEvents: category, source and inclusive dates combine with and', () => {
  const list = [
    ev({ id: 'a', start_date: '2026-11-20', category_names: ['Music', 'Food'] }),
    ev({ id: 'b', start_date: '2026-11-21', source: 'findlocal', category_names: ['Music'] }),
    ev({ id: 'c', start_date: '2026-11-22', category_names: [] }),
  ];
  assert.deepEqual(filterEvents(list, { ...EMPTY_FILTERS, category: 'Food' }).map(e => e.id), ['a']);
  assert.deepEqual(filterEvents(list, { ...EMPTY_FILTERS, source: 'findlocal' }).map(e => e.id), ['b']);
  assert.deepEqual(filterEvents(list, { ...EMPTY_FILTERS, from: '2026-11-21' }).map(e => e.id), ['b', 'c']);
  assert.deepEqual(filterEvents(list, { ...EMPTY_FILTERS, to: '2026-11-21' }).map(e => e.id), ['a', 'b']);
  assert.deepEqual(filterEvents(list, { ...EMPTY_FILTERS, category: 'Music', from: '2026-11-21', to: '2026-11-21' }).map(e => e.id), ['b']);
  assert.equal(filterEvents(list, EMPTY_FILTERS).length, 3);
  assert.deepEqual(filterEvents(null, EMPTY_FILTERS), []);
  assert.equal(filterEvents(list, undefined).length, 3);
});

test('nextSort flips the same key and starts a new key ascending', () => {
  assert.deepEqual(nextSort({ key: 'date', dir: 'asc' }, 'date'), { key: 'date', dir: 'desc' });
  assert.deepEqual(nextSort({ key: 'date', dir: 'desc' }, 'date'), { key: 'date', dir: 'asc' });
  assert.deepEqual(nextSort({ key: 'date', dir: 'desc' }, 'venue'), { key: 'venue', dir: 'asc' });
});

test('sortEvents: default order is date, then time with all-day first, then name; input untouched', () => {
  const list = [
    ev({ id: '1', name: 'B', start_time: '19:00' }),
    ev({ id: '2', name: 'A', start_time: '19:00' }),
    ev({ id: '3', name: 'Z', start_time: '' }),
    ev({ id: '4', name: 'Y', start_date: '2026-11-20' }),
  ];
  const before = list.map(e => e.id);
  const out = sortEvents(list, DEFAULT_SORT);
  assert.deepEqual(out.map(e => e.id), ['4', '3', '2', '1']);
  assert.deepEqual(list.map(e => e.id), before);
  assert.notEqual(out, list);
});
test('sortEvents: name compares without case; descending reverses', () => {
  const list = [ev({ name: 'banana' }), ev({ name: 'Apple' }), ev({ name: 'cherry' })];
  assert.deepEqual(names(sortEvents(list, { key: 'name', dir: 'asc' })), ['Apple', 'banana', 'cherry']);
  assert.deepEqual(names(sortEvents(list, { key: 'name', dir: 'desc' })), ['cherry', 'banana', 'Apple']);
});
test('sortEvents: an empty venue or category sorts last in both directions', () => {
  const list = [ev({ name: 'none', venue: '' }), ev({ name: 'a', venue: 'Alpha' }), ev({ name: 'z', venue: 'Zulu' })];
  assert.deepEqual(names(sortEvents(list, { key: 'venue', dir: 'asc' })), ['a', 'z', 'none']);
  assert.deepEqual(names(sortEvents(list, { key: 'venue', dir: 'desc' })), ['z', 'a', 'none']);
  const cat = [ev({ name: 'none', category_names: [] }), ev({ name: 'm', category_names: ['Music'] }), ev({ name: 'c', category_names: ['Comedy'] })];
  assert.deepEqual(names(sortEvents(cat, { key: 'category', dir: 'desc' })), ['m', 'c', 'none']);
  assert.deepEqual(names(sortEvents(cat, { key: 'category', dir: 'asc' })), ['c', 'm', 'none']);
});
test('sortEvents: source and time keys, ties fall back to date, time, name', () => {
  const list = [ev({ name: 'b', source: 'findlocal' }), ev({ name: 'a', source: 'findlocal' }), ev({ name: 'c', source: 'eventbrite' })];
  assert.deepEqual(names(sortEvents(list, { key: 'source', dir: 'asc' })), ['c', 'a', 'b']);
  const t = [ev({ name: 'late', start_time: '20:00' }), ev({ name: 'allday', start_time: '' }), ev({ name: 'early', start_time: '09:00' })];
  assert.deepEqual(names(sortEvents(t, { key: 'time', dir: 'asc' })), ['allday', 'early', 'late']);
  assert.deepEqual(names(sortEvents(t, { key: 'time', dir: 'desc' })), ['late', 'early', 'allday']);
});
test('sortEvents tolerates missing fields', () => {
  assert.deepEqual(sortEvents([{ id: 'x' }, { id: 'y' }], DEFAULT_SORT).length, 2);
  assert.deepEqual(sortEvents(null, DEFAULT_SORT), []);
});

test('categoryOptions, sourcesPresent, showSourceFilter', () => {
  const list = [ev({ category_names: ['Music', 'Art'] }), ev({ category_names: ['Art'], source: 'findlocal' }), ev({ category_names: [] })];
  assert.deepEqual(categoryOptions(list), ['Art', 'Music']);
  assert.deepEqual(sourcesPresent(list), ['eventbrite', 'findlocal']);
  assert.equal(showSourceFilter(list), true);
  assert.equal(showSourceFilter([ev()]), false);
  assert.equal(showSourceFilter([]), false);
  assert.deepEqual(categoryOptions(null), []);
});

test('cells: date, time, name, venue, category, source as strings', () => {
  const c = cells(ev());
  assert.deepEqual(c, { date: 'Sat 21 Nov', time: '7:00 PM', name: 'Jazz', venue: 'Lincoln Theatre, Dublin', category: 'Music', source: 'Eventbrite' });
});
test('cells: all day, noon and midnight, city already in the venue', () => {
  assert.equal(cells(ev({ start_time: '' })).time, 'All day');
  assert.equal(cells(ev({ start_time: '12:05' })).time, '12:05 PM');
  assert.equal(cells(ev({ start_time: '00:30' })).time, '12:30 AM');
  assert.equal(cells(ev({ start_time: '09:00' })).time, '9:00 AM');
  assert.equal(cells(ev({ venue: 'Dublin Pub', city: 'Dublin' })).venue, 'Dublin Pub');
  assert.equal(cells(ev({ venue: 'Hall', city: '' })).venue, 'Hall');
});
test('cells: a bare row (no venue, category, end, city) does not throw', () => {
  const c = cells({ id: 'x', name: 'Bare', start_date: '2026-11-21', start_time: '' });
  assert.deepEqual(c, { date: 'Sat 21 Nov', time: 'All day', name: 'Bare', venue: '', category: '', source: '' });
  assert.deepEqual(cells(null), { date: '', time: '', name: '', venue: '', category: '', source: '' });
  assert.equal(cells(ev({ venue: '', city: 'Dublin' })).venue, 'Dublin');
});
test('cells: the date does not move with the zone argument', () => {
  for (const z of ['Pacific/Kiritimati', 'Pacific/Pago_Pago', 'UTC']) assert.equal(cells(ev(), z).date, 'Sat 21 Nov');
});
test('cells: a bad date or time reads as empty rather than throwing', () => {
  assert.equal(cells(ev({ start_date: 'soon' })).date, '');
  assert.equal(cells(ev({ start_time: 'late' })).time, '');
});
test('cells: an unknown source shows as given', () => {
  assert.equal(cells(ev({ source: 'other' })).source, 'other');
});

test('countLabel', () => {
  assert.equal(countLabel(1640, 1640), '1,640 events');
  assert.equal(countLabel(1, 1), '1 event');
  assert.equal(countLabel(612, 1640), '612 of 1,640 events');
  assert.equal(countLabel(0, 1), '0 of 1 event');
  assert.equal(countLabel(0, 0), '0 events');
});

test('readLabel boundaries', () => {
  const at = '2026-11-18T12:00:00Z';
  const t = Date.parse(at);
  assert.equal(readLabel('', t), 'Not read yet');
  assert.equal(readLabel(undefined, t), 'Not read yet');
  assert.equal(readLabel('garbage', t), 'Not read yet');
  assert.equal(readLabel(at, t), 'Read just now');
  assert.equal(readLabel(at, t + 59999), 'Read just now');
  assert.equal(readLabel(at, t + 60000), 'Read 1 minute ago');
  assert.equal(readLabel(at, t + 5 * 60000), 'Read 5 minutes ago');
  assert.equal(readLabel(at, t + 3599999), 'Read 59 minutes ago');
  assert.equal(readLabel(at, t + 3600000), 'Read 1 hour ago');
  assert.equal(readLabel(at, t + 3 * 3600000), 'Read 3 hours ago');
  assert.equal(readLabel(at, t + 24 * 3600000 - 1), 'Read 23 hours ago');
  assert.equal(readLabel(at, t + 24 * 3600000), 'Read 1 day ago');
  assert.equal(readLabel(at, t + 2 * 86400000), 'Read 2 days ago');
  assert.equal(readLabel(at, t - 5000), 'Read just now');
});

test('canRefresh', () => {
  const last = '2026-11-18T00:00:00Z';
  const t = Date.parse(last);
  const st = (over = {}) => ({ set_up: true, refreshing: false, refresh: { last, error: '', pool: 5 }, ...over });
  assert.equal(canRefresh(st(), t + 12 * 3600000 - 60000), false);   // 11 h 59 m
  assert.equal(canRefresh(st(), t + 12 * 3600000), true);
  assert.equal(canRefresh(st({ refresh: { last: '' } }), t), true);
  assert.equal(canRefresh(st({ refreshing: true }), t + 13 * 3600000), false);
  assert.equal(canRefresh(st({ set_up: false }), t + 13 * 3600000), false);
  assert.equal(canRefresh(st(), t + 13 * 3600000, { online: false }), false);
  assert.equal(canRefresh(null, t), false);
  assert.equal(canRefresh(st({ refresh: undefined }), t), true);
});

test('gridState', () => {
  const some = [ev()];
  const base = { online: true, loaded: true, error: '', status: { set_up: true }, events: some, shown: some };
  assert.equal(gridState({ ...base, online: false }), 'offline');
  assert.equal(gridState({ ...base, loaded: false }), 'loading');
  assert.equal(gridState({ ...base, error: 'boom' }), 'error');
  assert.equal(gridState({ ...base, status: { set_up: false } }), 'setup');
  assert.equal(gridState({ ...base, events: [], shown: [] }), 'empty');
  assert.equal(gridState({ ...base, shown: [] }), 'nomatch');
  assert.equal(gridState(base), 'ready');
  assert.equal(gridState({ ...base, loaded: false, error: 'boom' }), 'error');
});

test('visibleCount and nearEnd', () => {
  assert.equal(visibleCount(200, 1640), 400);
  assert.equal(visibleCount(1600, 1640), 1640);
  assert.equal(visibleCount(0, 50), 50);
  assert.equal(nearEnd({ scrollTop: 400, clientHeight: 500, scrollHeight: 1500 }), true);   // 600 from the end
  assert.equal(nearEnd({ scrollTop: 399, clientHeight: 500, scrollHeight: 1500 }), false);
  assert.equal(nearEnd({}), true);
});

test('withState replaces one event\'s state without touching the rest or the input', () => {
  const list = [ev({ id: 'a' }), ev({ id: 'b', state: 'recommended' })];
  const out = withState(list, 'a', 'added');
  assert.deepEqual(out.map(e => e.state), ['added', 'recommended']);
  assert.equal(list[0].state, '');
  assert.equal(out[1], list[1]);
  assert.deepEqual(withState(list, 'zzz', 'added').map(e => e.state), ['', 'recommended']);
  assert.deepEqual(withState(null, 'a', 'added'), []);
});

test('rowKeyStep', () => {
  assert.deepEqual(rowKeyStep('ArrowDown', 2, 5), { index: 3 });
  assert.deepEqual(rowKeyStep('ArrowDown', 4, 5), { index: 4 });
  assert.deepEqual(rowKeyStep('ArrowUp', 2, 5), { index: 1 });
  assert.deepEqual(rowKeyStep('ArrowUp', 0, 5), { index: 0 });
  assert.deepEqual(rowKeyStep('Home', 3, 5), { index: 0 });
  assert.deepEqual(rowKeyStep('End', 1, 5), { index: 4 });
  assert.deepEqual(rowKeyStep('Enter', 1, 5), { open: true });
  assert.deepEqual(rowKeyStep(' ', 1, 5), { open: true });
  assert.equal(rowKeyStep('a', 1, 5), null);
  assert.equal(rowKeyStep('Tab', 1, 5), null);
  assert.deepEqual(rowKeyStep('End', 0, 0), { index: 0 });
});

test('credit: FindLocal only', () => {
  assert.equal(panelCredit(ev({ source: 'findlocal' })), true);
  assert.equal(panelCredit(ev()), false);
  assert.equal(panelCredit(null), false);
  assert.equal(gridCredit([ev(), ev({ source: 'findlocal' })]), true);
  assert.equal(gridCredit([ev()]), false);
  assert.equal(gridCredit(null), false);
});

test('cells: the date reads as the cards and the panel write it (dayName)', () => {
  assert.equal(cells(ev({ start_date: '2026-11-22' })).date, 'Sun 22 Nov');
  assert.equal(cells(ev({ start_date: '2026-02-30' })).date, '');
});

import { readFileSync } from 'node:fs';
const gui = f => readFileSync(new URL(`../../src/gui/events/${f}`, import.meta.url), 'utf8');
test('the All Events tab and its panel draw listings as text and fetch nothing themselves', () => {
  for (const f of ['AllEventsView.jsx', 'EventPanel.jsx']) {
    const src = gui(f);
    assert.ok(!src.includes('dangerouslySetInnerHTML'), f);
    assert.ok(!src.includes('fetch('), f);
  }
});
test('the panel opens its link in a new window without an opener, and only by cardLink', () => {
  const src = gui('EventPanel.jsx');
  assert.ok(src.includes('cardLink('));
  assert.ok(src.includes('target="_blank" rel="noopener noreferrer"'));
});

test('isBusy: reading or the harness refreshing, until the poll limit has run out', () => {
  assert.equal(isBusy({ reading: false, timedOut: false, status: null }), false);
  assert.equal(isBusy({ reading: true, timedOut: false, status: { refreshing: false } }), true);
  assert.equal(isBusy({ reading: false, timedOut: false, status: { refreshing: true } }), true);
  // after the limit the reading line goes, although the stored status still says refreshing
  assert.equal(isBusy({ reading: false, timedOut: true, status: { refreshing: true } }), false);
  assert.equal(isBusy({ reading: true, timedOut: true, status: { refreshing: true } }), false);
  assert.equal(isBusy({}), false);
  assert.equal(isBusy(), false);
});

test('keepFilters clears a category or source no longer in the list, and keeps the rest', () => {
  const list = [ev({ id: 'a', category_names: ['Music'] }), ev({ id: 'b', category_names: ['Food'], source: 'findlocal' })];
  const set = { ...EMPTY_FILTERS, text: 'jazz', category: 'Music', source: 'findlocal', from: '2026-11-20', to: '2026-11-30' };
  assert.equal(keepFilters(set, list), set);                       // all still present: the same object
  const onlyFood = [ev({ id: 'b', category_names: ['Food'], source: 'eventbrite' })];
  assert.deepEqual(keepFilters(set, onlyFood), { ...set, category: '', source: '' });
  assert.deepEqual(keepFilters({ ...set, category: 'Food' }, onlyFood), { ...set, category: 'Food', source: '' });
  assert.equal(keepFilters(EMPTY_FILTERS, []), EMPTY_FILTERS);
  assert.deepEqual(keepFilters({ ...EMPTY_FILTERS, category: 'Music' }, []), EMPTY_FILTERS);
  assert.deepEqual(keepFilters(undefined, list), EMPTY_FILTERS);
});

// Date helpers for the Events page: day keys, month grid, zone conversion.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  dayKey, addDays, todayKey, monthGrid, gridRange, instantToParts, wallToInstant,
  eventDays, eventsByDay, timeLabel, moveKey, fetchRange, dayTitle,
} from '../../src/gui/events/dates.js';

const NY = 'America/New_York';

test('monthGrid covers November 2026 in whole weeks from Monday', () => {
  const g = monthGrid(2026, 11);
  assert.equal(g.length, 6); assert.equal(g[0][0], '2026-10-26'); assert.equal(g[5][6], '2026-12-06');
  assert.deepEqual(gridRange(g), { start: '2026-10-26', end: '2026-12-07' });
});
test('monthGrid has 4 rows when February starts on the week start, and honours Sunday starts', () => {
  const g = monthGrid(2027, 2);   // 1 Feb 2027 is a Monday, 28 days
  assert.equal(g.length, 4); assert.equal(g[0][0], '2027-02-01'); assert.equal(g[3][6], '2027-02-28');
  const s = monthGrid(2026, 11, 0);
  assert.equal(s[0][0], '2026-11-01');
  for (const row of s) assert.equal(row.length, 7);
});
test('dayKey pads', () => { assert.equal(dayKey(2026, 3, 5), '2026-03-05'); });
test('todayKey reads the date in the zone', () => {
  const now = new Date('2026-11-17T03:00:00Z');
  assert.equal(todayKey(now, NY), '2026-11-16');
  assert.equal(todayKey(now, 'Asia/Tokyo'), '2026-11-17');
});
test('an all-day event never shifts a day, west or east of UTC', () => {
  const e = { all_day: true, start: '2026-11-17', end: '2026-11-18' };
  for (const z of ['Pacific/Honolulu', 'America/New_York', 'UTC', 'Asia/Tokyo', 'Pacific/Kiritimati']) assert.deepEqual(eventDays(e, z), ['2026-11-17']);
});
test('an event across midnight shows on both days and the counts agree with the agenda', () => {
  const e = { all_day: false, start: '2026-11-18T03:00:00Z', end: '2026-11-18T07:00:00Z' };   // 22:00 to 02:00 New York
  assert.deepEqual(eventDays(e, NY), ['2026-11-17', '2026-11-18']);
  const by = eventsByDay([e], NY);
  assert.equal(by.get('2026-11-17').length, 1); assert.equal(by.get('2026-11-18').length, 1);
});
test('an event ending exactly at midnight stays on its day', () => {
  const e = { all_day: false, start: '2026-11-18T01:00:00Z', end: '2026-11-18T05:00:00Z' };   // 20:00 to 00:00 New York
  assert.deepEqual(eventDays(e, NY), ['2026-11-17']);
});
test('a zero-length timed event covers its one day and shows only its start', () => {
  const e = { all_day: false, start: '2026-11-17T14:00:00Z', end: '2026-11-17T14:00:00Z' };
  assert.deepEqual(eventDays(e, NY), ['2026-11-17']);
  assert.equal(timeLabel(e, NY), '09:00');
});
test('a three-day all-day event covers three days', () => {
  const e = { all_day: true, start: '2026-11-17', end: '2026-11-20' };
  assert.deepEqual(eventDays(e, NY), ['2026-11-17', '2026-11-18', '2026-11-19']);
});
test('eventsByDay puts all-day first, then by start', () => {
  const a = { id: 'a', all_day: false, start: '2026-11-17T20:00:00Z', end: '2026-11-17T21:00:00Z' };
  const b = { id: 'b', all_day: false, start: '2026-11-17T15:00:00Z', end: '2026-11-17T16:00:00Z' };
  const c = { id: 'c', all_day: true, start: '2026-11-17', end: '2026-11-18' };
  assert.deepEqual(eventsByDay([a, b, c], NY).get('2026-11-17').map(e => e.id), ['c', 'b', 'a']);
});
test('timeLabel for timed, all-day and multi-day events', () => {
  const t = { all_day: false, start: '2026-11-17T14:00:00Z', end: '2026-11-17T15:00:00Z' };
  assert.equal(timeLabel(t, NY), '09:00 – 10:00');
  assert.equal(timeLabel({ all_day: true, start: '2026-11-17', end: '2026-11-18' }, NY), 'All day');
  const m = { all_day: false, start: '2026-11-17T03:00:00Z', end: '2026-11-19T07:00:00Z' };   // 16 Nov 22:00 to 19 Nov 02:00
  assert.equal(timeLabel(m, NY, '2026-11-16'), '22:00 →');
  assert.equal(timeLabel(m, NY, '2026-11-17'), 'All day');
  assert.equal(timeLabel(m, NY, '2026-11-19'), '→ 02:00');
});
test('wallToInstant and instantToParts round-trip, DST included', () => {
  assert.equal(wallToInstant('2026-11-17', '09:00', NY), '2026-11-17T14:00:00Z');
  assert.equal(wallToInstant('2026-07-17', '09:00', NY), '2026-07-17T13:00:00Z');
  assert.equal(wallToInstant('2026-03-08', '02:30', NY), '2026-03-08T07:30:00Z');   // the gap: 03:30 EDT
  assert.deepEqual(instantToParts('2026-11-17T14:00:00Z', NY), { key: '2026-11-17', time: '09:00' });
});
test('an ambiguous wall time takes the earlier instant', () => {
  assert.equal(wallToInstant('2026-11-01', '01:30', NY), '2026-11-01T05:30:00Z');   // EDT, not EST
});
test('addDays crosses months and years without Date parsing', () => {
  assert.equal(addDays('2026-12-31', 1), '2027-01-01'); assert.equal(addDays('2026-03-01', -1), '2026-02-28');
});
test('arrow keys, Home and End move the selected day', () => {
  assert.equal(moveKey('2026-11-18', 'ArrowLeft'), '2026-11-17');
  assert.equal(moveKey('2026-11-18', 'ArrowRight'), '2026-11-19');
  assert.equal(moveKey('2026-11-18', 'ArrowUp'), '2026-11-11');
  assert.equal(moveKey('2026-11-18', 'ArrowDown'), '2026-11-25');
  assert.equal(moveKey('2026-11-18', 'Home'), '2026-11-16');   // Monday
  assert.equal(moveKey('2026-11-18', 'End'), '2026-11-22');    // Sunday
  assert.equal(moveKey('2026-11-18', 'x'), '2026-11-18');
});

// what the harness does with the range: day keys compared with UTC instants as text
const harnessHas = (ev, r) => ev.start < r.end && ev.end > r.start;
test('fetchRange brings back a New York evening on the grid\'s last day', () => {
  const g = monthGrid(2026, 5);   // May 2026: 27 Apr to 31 May
  assert.deepEqual(gridRange(g), { start: '2026-04-27', end: '2026-06-01' });
  const ev = { all_day: false, start: wallToInstant('2026-05-31', '20:00', NY), end: wallToInstant('2026-05-31', '21:00', NY) };
  assert.equal(ev.start, '2026-06-01T00:00:00Z');
  assert.equal(harnessHas(ev, gridRange(g)), false);   // the unpadded range drops it
  const r = fetchRange(g);
  assert.deepEqual(r, { start: '2026-04-26', end: '2026-06-02' });
  assert.equal(harnessHas(ev, r), true);
  assert.deepEqual(eventsByDay([ev], NY).get('2026-05-31'), [ev]);
});
test('fetchRange brings back a Berlin small-hours event on the grid\'s first day', () => {
  const BER = 'Europe/Berlin';
  const g = monthGrid(2026, 6);   // 1 June 2026 is a Monday
  assert.equal(g[0][0], '2026-06-01');
  const ev = { all_day: false, start: wallToInstant('2026-06-01', '00:30', BER), end: wallToInstant('2026-06-01', '01:30', BER) };
  assert.equal(ev.start, '2026-05-31T22:30:00Z');
  assert.equal(harnessHas(ev, gridRange(g)), false);
  assert.equal(harnessHas(ev, fetchRange(g)), true);
  assert.deepEqual(eventsByDay([ev], BER).get('2026-06-01'), [ev]);
});
test('dayTitle writes a day key out without a zone moving it', () => {
  assert.equal(dayTitle('2026-11-17'), 'Tuesday 17 November');
  assert.equal(dayTitle('2026-11-01', { month: 'long', year: 'numeric' }), 'November 2026');
});

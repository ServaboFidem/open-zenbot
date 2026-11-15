import test from 'node:test';
import assert from 'node:assert/strict';
import { formatTime, draftFromRoutine, scheduleFromDraft, scheduleLabel, withCard, scanProgress, listenUrl, toPercent, fromPercent, digestTitle } from '../../src/gui/music/helpers.js';

const weekly = { schedule: { kind: 'weekly', days: [4], time: '09:00', hours: 0 } };

test('schedule words and the draft round trip keep the routine kind', () => {
  assert.equal(formatTime('13:05'), '1:05 PM');
  assert.equal(scheduleLabel(weekly), 'Friday at 9:00 AM');
  assert.deepEqual(draftFromRoutine(weekly), { kind: 'weekly', day: 4, time: '09:00', hours: 0 });
  assert.deepEqual(scheduleFromDraft(weekly, { day: 0, time: '18:30' }), { kind: 'weekly', days: [0], time: '18:30', hours: 0 });
  const daily = { schedule: { kind: 'daily', days: [], time: '08:00', hours: 0 } };
  assert.deepEqual(scheduleFromDraft(daily, { day: 2, time: '07:00' }), { kind: 'daily', days: [], time: '07:00', hours: 0 });
  const every = { schedule: { kind: 'interval', days: [], time: '', hours: 6 } };
  assert.equal(scheduleFromDraft(every, { day: 2, time: '07:00' }), every.schedule);
});

test('a card update replaces only that card', () => {
  const ds = [{ id: 'd1', cards: [{ id: 'a', reaction: null }, { id: 'b', reaction: null }] }, { id: 'd2', cards: [{ id: 'c' }] }];
  const out = withCard(ds, { id: 'b', reaction: 'passed' });
  assert.equal(out[0].cards[1].reaction, 'passed');
  assert.equal(out[0].cards[0].reaction, null);
  assert.equal(out[1], ds[1]);
});

test('listen opens the album link, else a YouTube Music search', () => {
  assert.equal(listenUrl({ artist: 'A', album: 'B', links: { youtube_music: 'https://music.youtube.com/browse/x' } }), 'https://music.youtube.com/browse/x');
  assert.equal(listenUrl({ artist: 'A & B', album: 'C', links: { youtube_music: '' } }), 'https://music.youtube.com/search?q=A%20%26%20B%20C');
});

test('scan progress follows the stage; era percent round trips', () => {
  assert.equal(scanProgress({ state: 'done' }).value, 1);
  assert.deepEqual(scanProgress({ state: 'running', stage: 'mapping' }), { value: 0.7, reading: true, mapping: false, finding: false });
  assert.ok(scanProgress({ state: 'running', stage: 'reading', done: 1, total: 4 }).value < 0.6);
  assert.equal(toPercent(0.35), 35);
  assert.equal(fromPercent(35), 0.35);
  assert.equal(digestTitle({ kind: 'on_demand' }), 'On request');
});

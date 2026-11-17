// The Events client: one function per /calendar route.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createCalendarApi } from '../../src/gui/calendar-api.js';

const rec = () => {
  const seen = [];
  const api = createCalendarApi(async (method, path, body, params) => { seen.push({ method, path, body, params }); return {}; });
  return { api, seen };
};

test('each function hits its route with the right method, path, body and params', async () => {
  const { api, seen } = rec();
  await api.status();
  await api.events('2026-10-26', '2026-12-07');
  await api.create({ title: 'a', all_day: true, start: '2026-11-17', end: '2026-11-18' });
  await api.update('abc-1', { title: 'b' });
  await api.remove('abc-1');
  await api.calendars();
  await api.saveCredentials('id', 'secret');
  await api.clearCredentials();
  await api.connect(true);
  await api.disconnect();
  await api.pushPreview('primary');
  await api.setPush(true, 'primary');
  await api.sync();
  const got = seen.map(s => [s.method, s.path, s.body, s.params]);
  assert.deepEqual(got, [
    ['GET', '/calendar/status', undefined, undefined],
    ['GET', '/calendar/events', undefined, { start: '2026-10-26', end: '2026-12-07' }],
    ['POST', '/calendar/events', { title: 'a', all_day: true, start: '2026-11-17', end: '2026-11-18' }, undefined],
    ['PUT', '/calendar/events/abc-1', { title: 'b' }, undefined],
    ['DELETE', '/calendar/events/abc-1', undefined, undefined],
    ['GET', '/calendar/calendars', undefined, undefined],
    ['PUT', '/calendar/google/credentials', { client_id: 'id', client_secret: 'secret' }, undefined],
    ['DELETE', '/calendar/google/credentials', undefined, undefined],
    ['POST', '/calendar/google/connect', { write: true }, undefined],
    ['POST', '/calendar/google/disconnect', {}, undefined],
    ['GET', '/calendar/push/preview', undefined, { destination: 'primary' }],
    ['PUT', '/calendar/push', { on: true, destination: 'primary' }, undefined],
    ['POST', '/calendar/sync', {}, undefined],
  ]);
});

test('connect sends write false unless asked', async () => {
  const { api, seen } = rec();
  await api.connect();
  assert.deepEqual(seen[0].body, { write: false });
});

test('setCalendar encodes the id', async () => {
  const { api, seen } = rec();
  await api.setCalendar('en.usa#holiday@group.v.calendar.google.com', { selected: true });
  assert.equal(seen[0].method, 'PUT');
  assert.equal(seen[0].path, '/calendar/calendars/en.usa%23holiday%40group.v.calendar.google.com');
  assert.deepEqual(seen[0].body, { selected: true });
});

test('an event id is encoded in its path', async () => {
  const { api, seen } = rec();
  await api.remove('a/b');
  assert.equal(seen[0].path, '/calendar/events/a%2Fb');
});

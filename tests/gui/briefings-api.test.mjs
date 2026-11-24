// The briefings client: one function per /briefings route.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createBriefingsApi } from '../../src/gui/briefings-api.js';

test('each function hits its route with the right method, path and body', async () => {
  const seen = [];
  const api = createBriefingsApi(async (method, path, body, params) => { seen.push([method, path, body, params]); return {}; });
  const body = { enabled: true, slots: [{ days: [0], time: '06:45' }], speakers: null, volume: 25 };
  await api.list();
  await api.save('morning', body);
  await api.remove('morning');
  await api.play('morning');
  assert.deepEqual(seen, [
    ['GET', '/briefings', undefined, undefined],
    ['PUT', '/briefings/morning', body, undefined],
    ['DELETE', '/briefings/morning', undefined, undefined],
    ['POST', '/briefings/morning/play', {}, undefined],
  ]);
});

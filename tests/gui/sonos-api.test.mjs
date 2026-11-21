// The Sonos client: one function per /sonos route.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createSonosApi } from '../../src/gui/sonos-api.js';

test('each function hits its route with the right method, path and body', async () => {
  const seen = [];
  const api = createSonosApi(async (method, path, body, params) => { seen.push([method, path, body, params]); return {}; });
  await api.status();
  await api.saveSettings(false, 45);
  await api.lookAgain();
  assert.deepEqual(seen, [
    ['GET', '/sonos/status', undefined, undefined],
    ['PUT', '/sonos/settings', { enabled: false, max_volume: 45 }, undefined],
    ['POST', '/sonos/discover', {}, undefined],
  ]);
});

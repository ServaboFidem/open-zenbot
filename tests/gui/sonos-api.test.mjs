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
  await api.rooms();
  await api.favorites();
  await api.play(['Kitchen', 'Patio'], 'Morning Jazz');
  await api.control('Kitchen', 'pause');
  await api.volume('Kitchen', 35);
  assert.deepEqual(seen, [
    ['GET', '/sonos/status', undefined, undefined],
    ['PUT', '/sonos/settings', { enabled: false, max_volume: 45 }, undefined],
    ['POST', '/sonos/discover', {}, undefined],
    ['GET', '/sonos/rooms', undefined, undefined],
    ['GET', '/sonos/favorites', undefined, undefined],
    ['POST', '/sonos/play', { rooms: ['Kitchen', 'Patio'], name: 'Morning Jazz' }, undefined],
    ['POST', '/sonos/control', { room: 'Kitchen', action: 'pause' }, undefined],
    ['POST', '/sonos/volume', { room: 'Kitchen', level: 35 }, undefined],
  ]);
});

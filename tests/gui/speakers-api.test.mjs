// The Wi-Fi speakers client: one function per /speakers route.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createSpeakersApi } from '../../src/gui/speakers-api.js';

test('each function hits its route with the right method, path and body', async () => {
  const seen = [];
  const api = createSpeakersApi(async (method, path, body, params) => { seen.push([method, path, body, params]); return {}; });
  await api.status();
  await api.saveSwitches({ dlna: true });
  await api.setDefault('airplay:AA');
  await api.setDefault(null);
  await api.lookAgain();
  assert.deepEqual(seen, [
    ['GET', '/speakers/status', undefined, undefined],
    ['PUT', '/speakers/settings', { dlna: true }, undefined],
    ['PUT', '/speakers/default', { key: 'airplay:AA' }, undefined],
    ['PUT', '/speakers/default', { key: null }, undefined],
    ['POST', '/speakers/look', {}, undefined],
  ]);
});

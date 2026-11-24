// The Telnyx client: one function per /telnyx route.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createTelnyxApi } from '../../src/gui/telnyx-api.js';

test('each function hits its route with the right method, path and body', async () => {
  const seen = [];
  const api = createTelnyxApi(async (method, path, body, params) => { seen.push([method, path, body, params]); return {}; });
  await api.status();
  await api.saveKey('KEY0SECRET1234');
  await api.forgetKey();
  await api.numbers();
  await api.chooseNumber('n1');
  await api.test();
  assert.deepEqual(seen, [
    ['GET', '/telnyx/status', undefined, undefined],
    ['PUT', '/telnyx/key', { key: 'KEY0SECRET1234' }, undefined],
    ['DELETE', '/telnyx/key', undefined, undefined],
    ['GET', '/telnyx/numbers', undefined, undefined],
    ['PUT', '/telnyx/number', { id: 'n1' }, undefined],
    ['POST', '/telnyx/test', {}, undefined],
  ]);
  assert.ok(seen.every(([, path]) => !path.includes('SECRET')));
});

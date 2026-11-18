// The Discover client: one function per /discover route.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createDiscoverApi } from '../../src/gui/discover-api.js';

const rec = () => {
  const seen = [];
  const api = createDiscoverApi(async (method, path, body, params) => { seen.push({ method, path, body, params }); return {}; });
  return { api, seen };
};

test('each function hits its route with the right method, path, body and params', async () => {
  const { api, seen } = rec();
  await api.status();
  await api.saveSettings({ city: 'Columbus' });
  await api.digests();
  await api.digests('2026-11-10');
  await api.digest('d1');
  await api.run();
  await api.dismiss('c1');
  await api.add('c1', { start: '2026-11-22T15:00:00Z', end: '2026-11-22T21:00:00Z' });
  assert.deepEqual(seen.map(s => [s.method, s.path, s.body, s.params]), [
    ['GET', '/discover/status', undefined, undefined],
    ['PUT', '/discover/settings', { city: 'Columbus' }, undefined],
    ['GET', '/discover/digests', undefined, undefined],
    ['GET', '/discover/digests', undefined, { before: '2026-11-10' }],
    ['GET', '/discover/digests/d1', undefined, undefined],
    ['POST', '/discover/run', {}, undefined],
    ['POST', '/discover/cards/c1/dismiss', {}, undefined],
    ['POST', '/discover/cards/c1/add', { start: '2026-11-22T15:00:00Z', end: '2026-11-22T21:00:00Z' }, undefined],
  ]);
});

test('every POST carries a body and ids are quoted', async () => {
  const { api, seen } = rec();
  await api.run();
  await api.dismiss('a/b c?');
  await api.add('a/b c?', { start: '2026-11-22', end: '2026-11-23' });
  await api.digest('x/../y');
  for (const s of seen.filter(s => s.method === 'POST')) assert.equal(typeof s.body, 'object', s.path);
  assert.equal(seen[1].path, '/discover/cards/a%2Fb%20c%3F/dismiss');
  assert.equal(seen[2].path, '/discover/cards/a%2Fb%20c%3F/add');
  assert.equal(seen[3].path, '/discover/digests/x%2F..%2Fy');
});

test('imageSrc joins the harness origin and the card path, and is empty without a picture', () => {
  const { api } = rec();
  assert.equal(api.imageSrc('http://127.0.0.1:8765', { image: '/discover/images/c1' }), 'http://127.0.0.1:8765/discover/images/c1');
  assert.equal(api.imageSrc('http://127.0.0.1:8765/', { image: '/discover/images/c1' }), 'http://127.0.0.1:8765/discover/images/c1');
  assert.equal(api.imageSrc('http://127.0.0.1:8765', { image: null }), '');
});

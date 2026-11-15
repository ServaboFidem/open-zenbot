// The Music client: one function per /music route, JSON in and out. The Discogs token travels in
// one PUT/POST body and is kept nowhere.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createApi } from '../../src/gui/api.js';
import { createMusicApi, coverSrc, wantNote } from '../../src/gui/music-api.js';

function fakeFetch(responses = {}) {
  const calls = [];
  const fetch = async (url, init = {}) => {
    const u = new URL(url);
    calls.push({ path: u.pathname, search: u.search, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : undefined });
    const r = responses[`${init.method || 'GET'} ${u.pathname}`] || { status: 200, body: {} };
    return { ok: r.status < 400, status: r.status, json: async () => r.body, text: async () => JSON.stringify(r.body) };
  };
  return { fetch, calls };
}
const setup = responses => {
  const { fetch, calls } = fakeFetch(responses);
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
  return { api, music: api.music, calls };
};

test('each function hits its route with the right method, path and body', async () => {
  const { music, calls } = setup();
  await music.status();
  await music.connect('collector', 'tok');
  await music.connect('collector');
  await music.saveToken('tok');
  await music.deleteToken();
  await music.settings({ focus: ['blues rock'], count: 4 });
  await music.scan();
  await music.map();
  await music.genre('british blues');
  await music.genre('R&B / soul');
  await music.digests({ before: '2026-11-13T09:00:00', limit: 10 });
  await music.digests();
  await music.digest('d1');
  await music.react('c1', 'passed');
  await music.want('c1');
  await music.unwant('c1');
  await music.discover('blues rock');
  await music.markRead('d1');
  assert.deepEqual(calls.map(c => `${c.method} ${c.path}${c.search}`), [
    'GET /music/status', 'POST /music/connect', 'POST /music/connect', 'PUT /music/token', 'DELETE /music/token',
    'PUT /music/settings', 'POST /music/scan', 'GET /music/map', 'GET /music/genres/british%20blues',
    'GET /music/genres/R%26B%20%2F%20soul', 'GET /music/digests?before=2026-11-13T09%3A00%3A00&limit=10',
    'GET /music/digests', 'GET /music/digests/d1', 'PUT /music/cards/c1/reaction', 'POST /music/cards/c1/want',
    'DELETE /music/cards/c1/want', 'POST /music/discover', 'POST /music/digests/d1/read']);
  assert.deepEqual(calls[1].body, { username: 'collector', token: 'tok' });
  assert.deepEqual(calls[2].body, { username: 'collector' });
  assert.deepEqual(calls[3].body, { token: 'tok' });
  assert.deepEqual(calls[5].body, { focus: ['blues rock'], count: 4 });
  assert.deepEqual(calls[16].body, { genre: 'blues rock' });
});

test('react(id, null) sends {"reaction": null}; wanted is never sent through react', async () => {
  const { music, calls } = setup();
  await music.react('c1', null);
  assert.deepEqual(calls[0].body, { reaction: null });
  await assert.rejects(() => music.react('c1', 'wanted'), /wantlist/);
  assert.equal(calls.length, 1);
});

test('the token is in one request body and nowhere else', async () => {
  const { music, calls } = setup();
  const secret = 'SECRET-TOKEN-123';
  await music.saveToken(secret);
  await music.connect('u', secret);
  assert.ok(calls.every(c => !c.path.includes(secret) && !c.search.includes(secret)));
  assert.ok(!JSON.stringify(music, (_, v) => (typeof v === 'function' ? undefined : v)).includes(secret));
  assert.ok(!JSON.stringify(Object.keys(music)).includes(secret));
});

test('want answers 200 {ok:false, error} on a Discogs failure and wantNote words it', async () => {
  const { music } = setup({ 'POST /music/cards/c1/want': { status: 200, body: { ok: false, error: 'token_invalid' } } });
  const r = await music.want('c1');
  assert.equal(r.ok, false);
  assert.match(wantNote(r.error), /token/i);
  assert.match(wantNote('network'), /reach Discogs/i);
  assert.match(wantNote('something_else'), /wantlist/i);
});

test('cover src is the harness base plus cover_url; null stays null', () => {
  assert.equal(coverSrc('http://127.0.0.1:8765/', '/music/covers/42'), 'http://127.0.0.1:8765/music/covers/42');
  assert.equal(coverSrc('http://127.0.0.1:8765', null), null);
});

test('routine functions go through the routines routes and keep managed_by on save', async () => {
  const { api, calls } = setup();
  await api.routines();
  await api.saveRoutine({ name: 'Weekly album digest', bot: 'music-librarian', skill: 'weekly-digest', enabled: true, schedule: { kind: 'weekly', days: [4], time: '09:00', hours: 0 }, managed_by: 'music' });
  await api.saveRoutine({ id: 'r1', name: 'n', bot: 'b', skill: 's', enabled: false, schedule: { kind: 'daily', days: [], time: '08:00', hours: 0 }, managed_by: 'music', next_run: 'x', last_run: null, error: '' });
  await api.runRoutine('r1');
  await api.deleteRoutine('r1');
  await api.importRoutines([{ name: 'a' }]);
  assert.deepEqual(calls.map(c => `${c.method} ${c.path}`), ['GET /routines', 'POST /routines', 'PUT /routines/r1', 'POST /routines/r1/run', 'DELETE /routines/r1', 'POST /routines/import']);
  assert.equal(calls[1].body.managed_by, 'music');
  assert.deepEqual(Object.keys(calls[2].body).sort(), ['bot', 'enabled', 'managed_by', 'name', 'schedule', 'skill']);
  assert.deepEqual(calls[5].body, { routines: [{ name: 'a' }] });
});

test('createMusicApi works on any call function', async () => {
  const seen = [];
  const m = createMusicApi(async (...a) => { seen.push(a); return {}; });
  await m.status();
  assert.deepEqual(seen[0], ['GET', '/music/status', undefined, undefined]);
});

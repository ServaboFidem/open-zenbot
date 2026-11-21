// Reading replies aloud: the toggle's memory, which reply to read, and the player.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createPlayer, readSpeakOn, SPEAK_KEY, toSpeak, writeSpeakOn } from '../../src/gui/speech.js';
import { createApi, HarnessError } from '../../src/gui/api.js';

const memory = () => { const m = new Map(); return { getItem: k => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), m }; };
const broken = { getItem() { throw new Error('denied'); }, setItem() { throw new Error('denied'); } };

test('the toggle is remembered, and storage that throws reads as off', () => {
  const s = memory();
  assert.equal(readSpeakOn(s), false);
  writeSpeakOn(s, true); assert.equal(readSpeakOn(s), true); assert.equal(s.m.get(SPEAK_KEY), '1');
  writeSpeakOn(s, false); assert.equal(readSpeakOn(s), false);
  assert.equal(readSpeakOn(broken), false);
  assert.doesNotThrow(() => writeSpeakOn(broken, true));
  assert.equal(readSpeakOn(null), false);
});

test('toSpeak reads only a finished reply of a session started while listening', () => {
  const waiting = new Set(['s2', 's3']);
  const ex = (session_id, outcome, reply) => ({ session_id, outcome, reply });
  assert.equal(toSpeak([], waiting), null);
  assert.equal(toSpeak([ex('s1', 'done', 'old reply')], waiting), null);  // not waited for
  assert.equal(toSpeak([ex('s2', 'pending', '')], waiting), null);
  assert.equal(toSpeak([ex('s2', 'done', '   ')], waiting), null);  // blank: nothing to read (an attachment only)
  assert.deepEqual(toSpeak([ex('s2', 'done', 'first'), ex('s3', 'done', 'second')], waiting), { sessionId: 's3', text: 'second' });
  assert.deepEqual(toSpeak([ex('s2', 'failed', 'It failed because...')], waiting), { sessionId: 's2', text: 'It failed because...' });
  assert.equal(toSpeak(null, waiting), null);
});

test('the player plays one reply at a time and frees the last one', () => {
  const made = []; const revoked = [];
  class FakeAudio { constructor(src) { this.src = src; this.paused = true; made.push(this); } play() { this.paused = false; return Promise.resolve(); } pause() { this.paused = true; } }
  let n = 0;
  const URLs = { createObjectURL: () => `blob:${++n}`, revokeObjectURL: u => revoked.push(u) };
  const player = createPlayer({ Audio: FakeAudio, URL: URLs });
  player.play('wav-1');
  assert.equal(made[0].src, 'blob:1'); assert.equal(made[0].paused, false);
  player.play('wav-2');
  assert.equal(made[0].paused, true); assert.deepEqual(revoked, ['blob:1']); assert.equal(made[1].paused, false);
  player.stop();
  assert.equal(made[1].paused, true); assert.deepEqual(revoked, ['blob:1', 'blob:2']);
  assert.doesNotThrow(() => player.stop());
});

test('api.speak posts the text as JSON and answers the audio blob', async () => {
  const calls = [];
  const fetch = async (url, init) => { calls.push({ url, init }); return { ok: true, status: 200, blob: async () => 'WAV' }; };
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
  assert.equal(await api.speak('Hello'), 'WAV');
  assert.equal(calls[0].url, 'http://127.0.0.1:8765/speech');
  assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(calls[0].init.body), { text: 'Hello' });
});

test('api.speak carries the harness sentence on a refusal', async () => {
  const fetch = async () => ({ ok: false, status: 409, json: async () => ({ detail: 'Reading aloud is off.' }), text: async () => '' });
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
  await assert.rejects(api.speak('Hello'), e => e instanceof HarnessError && e.status === 409 && e.message === 'Reading aloud is off.');
});

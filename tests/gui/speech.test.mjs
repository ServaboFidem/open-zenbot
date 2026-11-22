// Reading replies aloud: the toggle's memory, which reply to read, the frame parser, the player and the reader.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createFrameParser, createPlayer, createReader, END_MISSING, FRAME_MAX, readSpeakOn, SPEAK_KEY, SPEAK_MAX, toSpeak, writeSpeakOn } from '../../src/gui/speech.js';
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

test('toSpeak sends at most what the Narrator reads', () => {
  assert.equal(SPEAK_MAX, 12000);
  const got = toSpeak([{ session_id: 's', outcome: 'done', reply: 'x'.repeat(30000) }], new Set(['s']));
  assert.equal(got.text.length, 12000);
});

const frame = bytes => { const n = bytes.length; return Uint8Array.from([n >>> 24, (n >>> 16) & 255, (n >>> 8) & 255, n & 255, ...bytes]); };
const END = Uint8Array.from([0, 0, 0, 0]);
const cat = (...parts) => Uint8Array.from(parts.flatMap(p => [...p]));
const body = chunks => new ReadableStream({ start(c) { for (const k of chunks) c.enqueue(k); c.close(); } });

test('the parser reads a frame split over three chunks', () => {
  const p = createFrameParser(); const f = frame([1, 2, 3, 4, 5]);
  assert.deepEqual(p.push(f.slice(0, 2)), []);
  assert.deepEqual(p.push(f.slice(2, 6)), []);
  const out = p.push(f.slice(6));
  assert.equal(out.length, 1); assert.deepEqual([...out[0]], [1, 2, 3, 4, 5]); assert.equal(p.ended, false);
});

test('the parser reads two frames in one chunk and a length split over two chunks', () => {
  const p = createFrameParser();
  assert.deepEqual(p.push(cat(frame([1]), frame([2, 3]))).map(o => [...o]), [[1], [2, 3]]);
  const f = frame([9, 9]);
  assert.deepEqual(p.push(f.slice(0, 1)), []);
  assert.deepEqual(p.push(f.slice(1)).map(o => [...o]), [[9, 9]]);
});

test('the end mark ends the parser and later bytes are ignored', () => {
  const p = createFrameParser();
  assert.deepEqual(p.push(cat(frame([1]), END, frame([2]))).map(o => [...o]), [[1]]);
  assert.equal(p.ended, true);
  assert.deepEqual(p.push(frame([3])), []);
});

test('a body that stops inside a frame yields nothing more and is not ended', () => {
  const p = createFrameParser();
  assert.deepEqual(p.push(frame([1, 2, 3, 4]).slice(0, 6)), []);
  assert.equal(p.ended, false);
});

test('a length over the cap throws', () => {
  const n = FRAME_MAX + 1;
  assert.throws(() => createFrameParser().push(Uint8Array.from([n >>> 24, (n >>> 16) & 255, (n >>> 8) & 255, n & 255])));
});

function fakes(failFirst = false) {
  const made = []; const revoked = []; let n = 0;
  class FakeAudio {
    constructor(src) { this.src = src; this.paused = true; this.fail = failFirst && made.length === 0; made.push(this); }
    play() { this.paused = false; return this.fail ? Promise.reject(new Error('no')) : Promise.resolve(); }
    pause() { this.paused = true; }
  }
  return { made, revoked, Audio: FakeAudio, URL: { createObjectURL: () => `blob:${++n}`, revokeObjectURL: u => revoked.push(u) } };
}

test('the second piece plays when the first ends, and playing() ends with the last', () => {
  const f = fakes(); const player = createPlayer(f);
  assert.equal(player.playing(), false);
  player.enqueue('a'); player.enqueue('b');
  assert.equal(f.made.length, 1); assert.equal(f.made[0].src, 'blob:1'); assert.equal(player.playing(), true);
  f.made[0].onended();
  assert.equal(f.made.length, 2); assert.equal(f.made[1].src, 'blob:2'); assert.deepEqual(f.revoked, ['blob:1']);
  f.made[1].onended();
  assert.equal(player.playing(), false); assert.deepEqual(f.revoked, ['blob:1', 'blob:2']);
});

test('stop clears the queue, stops the sound and revokes every URL', () => {
  const f = fakes(); const player = createPlayer(f);
  player.enqueue('a'); player.enqueue('b'); player.enqueue('c');
  player.stop();
  assert.equal(f.made[0].paused, true); assert.deepEqual([...f.revoked].sort(), ['blob:1', 'blob:2', 'blob:3']);
  assert.equal(player.playing(), false); assert.equal(f.made.length, 1);
  assert.doesNotThrow(() => player.stop());
});

test('a piece that fails to play is skipped and the next plays', async () => {
  const f = fakes(true); const player = createPlayer(f);
  player.enqueue('a'); player.enqueue('b');
  await new Promise(r => setImmediate(r));
  assert.equal(f.made.length, 2); assert.equal(f.made[1].src, 'blob:2'); assert.deepEqual(f.revoked, ['blob:1']);
});

const pieces = async function* (items, then) { for (const i of items) yield i; if (then) throw then; };
const quietPlayer = () => { const log = []; return { log, enqueue: b => log.push(['enqueue', b]), stop: () => log.push(['stop']), playing: () => false }; };
const enqueued = player => player.log.filter(l => l[0] === 'enqueue');

test('a newer reading aborts the older request and none of its late pieces play', async () => {
  const player = quietPlayer(); const signals = []; let release;
  const speak = (text, signal) => {
    signals.push(signal);
    if (text === 'old') return (async function* () { await new Promise(r => { release = r; }); yield 'late'; })();
    return pieces(['new']);
  };
  const reader = createReader({ speak, player, onError() { assert.fail('no error'); } });
  const old = reader.read({ sessionId: 's1', text: 'old' }, () => true);
  await reader.read({ sessionId: 's2', text: 'new' }, () => true);
  assert.equal(signals[0].aborted, true);
  release(); await old;
  assert.deepEqual(enqueued(player), [['enqueue', 'new']]);
});

test('cancel aborts the request', () => {
  let sig;
  const reader = createReader({ speak: (t, s) => { sig = s; return (async function* () { await new Promise(() => {}); })(); }, player: quietPlayer(), onError() {} });
  reader.read({ sessionId: 's1', text: 'a' }, () => true);
  reader.cancel();
  assert.equal(sig.aborted, true);
});

test('the toggle off mid-stream stops enqueuing', async () => {
  const player = quietPlayer(); let on = true;
  const speak = async function* () { yield 'a'; on = false; yield 'b'; yield 'c'; };
  await createReader({ speak, player, onError() {} }).read({ sessionId: 's', text: 'x' }, () => on);
  assert.deepEqual(enqueued(player), [['enqueue', 'a']]);
});

test('an error reports whether any audio was queued, and an abort reports nothing', async () => {
  const errors = [];
  const onError = (item, e, info) => errors.push([e.message, info.played]);
  const run = speak => createReader({ speak, player: quietPlayer(), onError }).read({ sessionId: 's', text: 'x' }, () => true);
  await run(() => pieces([], new Error('off')));
  await run(() => pieces(['a', 'b'], new Error(END_MISSING)));
  await run(() => pieces(['a'], Object.assign(new Error('aborted'), { name: 'AbortError' })));
  assert.deepEqual(errors, [['off', false], [END_MISSING, true]]);
});

test('a new reading stops the one playing before it fetches', async () => {
  const calls = [];
  const reader = createReader({ speak: () => { calls.push('speak'); return pieces([]); }, player: { enqueue() {}, stop() { calls.push('stop'); }, playing: () => false }, onError() {} });
  await reader.read({ sessionId: 's2', text: 'b' }, () => true);
  assert.deepEqual(calls, ['stop', 'speak']);
});

const apiWith = fetch => createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
const collect = async gen => { const out = []; for await (const b of gen) out.push(b); return out; };

test('api.speak yields a blob per frame and passes the signal to fetch', async () => {
  const calls = [];
  const fetch = async (url, init) => { calls.push({ url, init }); return new Response(body([cat(frame([1, 2]), frame([3])), END]), { status: 200 }); };
  const ac = new AbortController();
  const blobs = await collect(apiWith(fetch).speak('Hello', ac.signal));
  assert.equal(blobs.length, 2); assert.equal(blobs[0].type, 'audio/wav'); assert.equal(blobs[0].size, 2);
  assert.equal(calls[0].url, 'http://127.0.0.1:8765/speech'); assert.equal(calls[0].init.method, 'POST');
  assert.equal(calls[0].init.signal, ac.signal); assert.deepEqual(JSON.parse(calls[0].init.body), { text: 'Hello' });
});

test('api.speak throws END_MISSING without the end mark, after the pieces it had', async () => {
  const fetch = async () => new Response(body([frame([1])]), { status: 200 });
  const got = [];
  await assert.rejects(async () => { for await (const b of apiWith(fetch).speak('Hello')) got.push(b); }, e => e.message === END_MISSING);
  assert.equal(got.length, 1);
});

test('api.speak carries the harness sentence on a refusal, before any piece', async () => {
  const fetch = async () => new Response(JSON.stringify({ detail: 'Reading aloud is off.' }), { status: 409 });
  await assert.rejects(collect(apiWith(fetch).speak('Hello')), e => e instanceof HarnessError && e.status === 409 && e.message === 'Reading aloud is off.');
});

test('an aborted fetch is rethrown as it is', async () => {
  const abort = Object.assign(new Error('aborted'), { name: 'AbortError' });
  await assert.rejects(collect(apiWith(async () => { throw abort; }).speak('Hello')), e => e === abort);
});

test('the local voice calls hit their routes', async () => {
  const calls = [];
  const fetch = async (url, init) => { calls.push({ url, init }); return init.headers.Accept === 'audio/wav' ? new Response(new Blob(['W']), { status: 200 }) : new Response(JSON.stringify({ ok: 1 }), { status: 200 }); };
  const api = apiWith(fetch);
  assert.deepEqual(await api.speechDownload(), { ok: 1 });
  assert.deepEqual(await api.speechRemoveModel(), { ok: 1 });
  assert.equal(await (await api.speechSample('af_heart')).text(), 'W');
  assert.deepEqual(calls.map(c => [c.init.method, c.url.replace('http://127.0.0.1:8765', '')]), [['POST', '/speech/local/download'], ['DELETE', '/speech/local/model'], ['POST', '/speech/local/sample']]);
  assert.deepEqual(JSON.parse(calls[0].init.body), {}); assert.equal(calls[1].init.body, undefined);
  assert.deepEqual(JSON.parse(calls[2].init.body), { voice: 'af_heart' });
});

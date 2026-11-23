// Reading replies aloud: the toggle's memory, which reply to read, the frame parser, the player and the reader.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createFrameParser, createPlayer, createReader, END_MISSING, FRAME_MAX, readSpeakOn, SPEAK_KEY, SPEAK_MAX, toSpeak, writeSpeakOn } from '../../src/gui/speech.js';

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

test('a piece that fails to play is skipped, the next plays, and the failure is heard', async () => {
  const f = fakes(true); const player = createPlayer(f); const heard = [];
  player.enqueue('a', played => heard.push(played)); player.enqueue('b');
  await new Promise(r => setImmediate(r));
  assert.equal(f.made.length, 2); assert.equal(f.made[1].src, 'blob:2'); assert.deepEqual(f.revoked, ['blob:1']);
  assert.deepEqual(heard, [false]);
});

test('the player passes a playback refusal on', async () => {
  class Refusing { constructor() {} play() { return Promise.reject(new Error('NotAllowedError')); } pause() {} }
  const heard = [];
  const player = createPlayer({ Audio: Refusing, URL: { createObjectURL: () => 'blob:1', revokeObjectURL() {} } });
  player.enqueue('wav', played => heard.push(played));
  await new Promise(r => setImmediate(r));
  assert.deepEqual(heard, [false]);
});

test('an Audio that cannot be made frees its URL and is heard', () => {
  const revoked = []; const heard = [];
  class Broken { constructor() { throw new Error('no'); } }
  const player = createPlayer({ Audio: Broken, URL: { createObjectURL: () => 'blob:1', revokeObjectURL: u => revoked.push(u) } });
  player.enqueue('wav', played => heard.push(played));
  assert.deepEqual(revoked, ['blob:1']); assert.deepEqual(heard, [false]); assert.equal(player.playing(), false);
});

test('the parser reads a 1 MB frame fed in 1 KB chunks', () => {
  const wav = Uint8Array.from({ length: 1 << 20 }, (_, i) => i % 251);
  const all = cat(frame(wav), END); const p = createFrameParser(); const out = [];
  for (let at = 0; at < all.length; at += 1024) out.push(...p.push(all.subarray(at, at + 1024)));
  assert.equal(out.length, 1); assert.deepEqual(out[0], wav); assert.equal(p.ended, true);
});

const pieces = async function* (items, then) { for (const i of items) yield i; if (then) throw then; };
const quietPlayer = (started = false) => { const log = []; return { log, enqueue: b => log.push(['enqueue', b]), stop: () => log.push(['stop']), playing: () => false, started: () => started }; };
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

test('an error reports whether any audio started playing, and an abort reports nothing', async () => {
  const errors = [];
  const onError = (item, e, info) => errors.push([e.message, info.played]);
  const run = (speak, started) => createReader({ speak, player: quietPlayer(started), onError }).read({ sessionId: 's', text: 'x' }, () => true);
  await run(() => pieces([], new Error('off')));
  await run(() => pieces(['a', 'b'], new Error(END_MISSING)), true);
  await run(() => pieces(['a'], Object.assign(new Error('aborted'), { name: 'AbortError' })), true);
  assert.deepEqual(errors, [['off', false], [END_MISSING, true]]);
});

test('a missing end mark after pieces were queued but before any played reports played false', async () => {
  const f = fakes(); const pending = [];
  f.Audio.prototype.play = function () { return new Promise(r => { pending.push(r); }); };
  const player = createPlayer(f); const errors = [];
  const reader = createReader({ speak: () => pieces(['a', 'b'], new Error(END_MISSING)), player, onError: (item, e, info) => errors.push([e.message, info.played]) });
  await reader.read({ sessionId: 's', text: 'x' }, () => true);
  assert.deepEqual(errors, [[END_MISSING, false]]);  // two frames queued, nothing heard
  pending[0](); await new Promise(r => setImmediate(r));
  assert.equal(player.started(), true);  // the first piece starts after the error: it plays on
  await reader.read({ sessionId: 's', text: 'x' }, () => true);
  assert.deepEqual(errors, [[END_MISSING, false], [END_MISSING, false]]);  // a new reading stopped the old sound first
});

test('the player says when a piece has started, and stop forgets it', async () => {
  const f = fakes(); const player = createPlayer(f);
  assert.equal(player.started(), false);
  player.enqueue('a');
  assert.equal(player.started(), false);  // play() has not resolved yet
  await new Promise(r => setImmediate(r));
  assert.equal(player.started(), true);
  player.stop();
  assert.equal(player.started(), false);
});

test('a reading that failed after a piece started reports played true', async () => {
  const f = fakes(); const player = createPlayer(f); const errors = [];
  let go;
  const speak = async function* () { yield 'a'; await new Promise(r => { go = r; }); throw new Error(END_MISSING); };
  const going = createReader({ speak, player, onError: (item, e, info) => errors.push(info.played) }).read({ sessionId: 's', text: 'x' }, () => true);
  await new Promise(r => setImmediate(r));
  assert.equal(player.started(), true);
  go(); await going;
  assert.deepEqual(errors, [true]);
});

test('an old element\'s play resolving after stop and a new piece does not count as played', async () => {
  const made = []; const resolvers = []; const heard = [];
  class Held {
    constructor(src) { this.src = src; made.push(this); }
    play() { return new Promise((res, rej) => { resolvers.push({ res, rej }); }); }
    pause() {}
  }
  const player = createPlayer({ Audio: Held, URL: { createObjectURL: () => `blob:${made.length + 1}`, revokeObjectURL() {} } });
  player.enqueue('a');
  player.stop();
  player.enqueue('b', played => heard.push(played));
  resolvers[0].res(); await new Promise(r => setImmediate(r));  // the stopped element's play() resolves late
  assert.equal(player.started(), false);
  resolvers[1].rej(new Error('no')); await new Promise(r => setImmediate(r));
  assert.deepEqual(heard, [false]);
});

test('a playback failure then a missing end mark reports once', async () => {
  const errors = []; let fail;
  const player = { enqueue: (b, onFail) => { fail = onFail; }, stop() {}, playing: () => false, started: () => false };
  let go;
  const speak = async function* () { yield 'a'; await new Promise(r => { go = r; }); throw new Error(END_MISSING); };
  const going = createReader({ speak, player, onError: (item, e, info) => errors.push(e.message) }).read({ sessionId: 's', text: 'x' }, () => true);
  await new Promise(r => setImmediate(r));
  fail(false);
  go(); await going;
  assert.deepEqual(errors, ['The reply could not be played.']);
});

test('a new reading stops the one playing before it fetches', async () => {
  const calls = [];
  const reader = createReader({ speak: () => { calls.push('speak'); return pieces([]); }, player: { enqueue() {}, stop() { calls.push('stop'); }, playing: () => false }, onError() {} });
  await reader.read({ sessionId: 's2', text: 'b' }, () => true);
  assert.deepEqual(calls, ['stop', 'speak']);
});

test('a reading whose pieces will not play reports once, played false', async () => {
  const errors = []; let fail;
  const player = { enqueue: (b, onFail) => { fail = onFail; }, stop() {}, playing: () => false };
  await createReader({ speak: () => pieces(['a', 'b']), player, onError: (item, e, info) => errors.push([e.message, info.played]) }).read({ sessionId: 's', text: 'x' }, () => true);
  fail(false); fail(false);
  assert.deepEqual(errors, [['The reply could not be played.', false]]);
});

test('a playback failure after audio played reports played true, and none after a cancel', async () => {
  const errors = []; let fail;
  const player = { enqueue: (b, onFail) => { fail = onFail; }, stop() {}, playing: () => false };
  const reader = createReader({ speak: () => pieces(['a']), player, onError: (item, e, info) => errors.push(info.played) });
  await reader.read({ sessionId: 's', text: 'x' }, () => true);
  fail(true); assert.deepEqual(errors, [true]);
  await reader.read({ sessionId: 's', text: 'x' }, () => true);
  reader.cancel(); fail(false); assert.deepEqual(errors, [true]);
});

test('a cancel before the first piece reaches the pending read', async () => {
  const errors = []; let sig;
  const reader = createReader({ speak: (t, s) => { sig = s; return (async function* () { await new Promise((_, rej) => s.addEventListener('abort', () => rej(Object.assign(new Error('aborted'), { name: 'AbortError' })))); })(); }, player: quietPlayer(), onError: e => errors.push(e) });
  const going = reader.read({ sessionId: 's', text: 'a' }, () => true);
  reader.cancel(); await going;
  assert.equal(sig.aborted, true); assert.deepEqual(errors, []);
});

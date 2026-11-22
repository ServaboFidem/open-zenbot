// The Read aloud card's local-voice decisions.
import test from 'node:test';
import assert from 'node:assert/strict';
import { ERROR_LINES, LOCAL, LOCAL_LABEL, LOCAL_MODEL, NOTE, REMOVE_ASK, canSample, errorKey, liveLine, localAction, localLine, mb, oneAtATime, shouldPoll, showsNoSpeakerAlert, voiceFor, voiceGroups } from '../../src/gui/speech-settings.js';

const make = (n, accent, gender, p) => Array.from({ length: n }, (_, i) => ({ name: `${p}${i}`, label: `${p}${i}`, accent, gender }));
const VOICES = [...make(11, 'American', 'female', 'af_'), ...make(9, 'American', 'male', 'am_'), ...make(4, 'British', 'female', 'bf_'), ...make(4, 'British', 'male', 'bm_')];
VOICES[0] = { name: 'af_heart', label: 'Heart', accent: 'American', gender: 'female' };
const local = (over = {}) => ({ state: 'absent', done: 0, total: 353_719_767, error: '', voices: VOICES, default_voice: 'af_heart', ...over });

test('the constants', () => {
  assert.equal(LOCAL, 'kokoro'); assert.equal(LOCAL_MODEL, 'kokoro-v1.0'); assert.equal(LOCAL_LABEL, 'Kokoro (free, no key)');
  assert.match(NOTE, /^Kokoro makes the speech on this computer\./);
  assert.equal(REMOVE_ASK, 'Remove the downloaded voice? Read aloud will need it again.');
});

test('mb rounds whole megabytes of 1,000,000 bytes', () => {
  assert.equal(mb(353_719_767), 354); assert.equal(mb(120_000_000), 120); assert.equal(mb(0), 0); assert.equal(mb(undefined), 0);
});

test('the status line by state', () => {
  assert.equal(localLine(local()), 'The voice is not downloaded yet (354 MB).');
  assert.equal(localLine(local({ state: 'downloading', done: 120_000_000 })), 'Downloading… 120 of 354 MB');
  assert.equal(localLine(local({ state: 'ready' })), 'Downloaded.');
  for (const k of Object.keys(ERROR_LINES)) assert.equal(localLine(local({ state: 'failed', error: k })), ERROR_LINES[k]);
  assert.equal(localLine(local({ state: 'failed', error: 'odd' })), ERROR_LINES.provider);
  assert.equal(ERROR_LINES.network, 'The download could not reach GitHub. Check the connection and try again.');
  assert.equal(localLine(null), 'The voice is not downloaded yet (354 MB).');
});

test('the action by state', () => {
  assert.equal(localAction(local()), 'download');
  assert.equal(localAction(local({ state: 'failed' })), 'retry');
  assert.equal(localAction(local({ state: 'ready' })), 'remove');
  assert.equal(localAction(local({ state: 'downloading' })), null);
});

test('voice groups: four in order, an empty one dropped', () => {
  const g = voiceGroups(VOICES);
  assert.deepEqual(g.map(x => x.label), ['American, female', 'American, male', 'British, female', 'British, male']);
  assert.deepEqual(g.map(x => x.voices.length), [11, 9, 4, 4]);
  assert.deepEqual(voiceGroups(VOICES.filter(v => v.gender === 'female')).map(x => x.label), ['American, female', 'British, female']);
  assert.deepEqual(voiceGroups(null), []);
});

test('sampling and polling', () => {
  assert.equal(canSample(local({ state: 'ready' }), false), true);
  assert.equal(canSample(local({ state: 'ready' }), true), false);
  assert.equal(canSample(local(), false), false);
  assert.equal(shouldPoll(local({ state: 'downloading' })), true);
  assert.equal(shouldPoll(local({ state: 'ready' })), false);
  assert.equal(shouldPoll(undefined), false);
});

test('voiceFor keeps a Kokoro voice, else the default', () => {
  assert.equal(voiceFor(local(), 'am_1'), 'am_1');
  assert.equal(voiceFor(local(), 'Zephyr'), 'af_heart');
  assert.equal(voiceFor(local(), undefined), 'af_heart');
});

import { createSampler, speechDirty, speechForm, speechPatch } from '../../src/gui/speech-settings.js';
const prov = { provider: 'openrouter', model: 'google/gemini-tts', voice: 'Zephyr', local: local({ state: 'ready' }) };
const kok = { provider: 'kokoro', model: 'kokoro-v1.0', voice: 'af_heart', local: local({ state: 'ready' }) };

test('a Kokoro voice never reaches a provider model', () => {
  const f = { ...speechForm(prov), choice: 'kokoro::kokoro-v1.0', localVoice: 'am_adam' };
  const back = { ...f, choice: 'openrouter::google/gemini-tts' };
  assert.equal(speechDirty(prov, back), false);
  assert.deepEqual(speechPatch(prov, back), {});
  assert.equal(speechForm(prov).voice, 'Zephyr');
});
test('Kokoro: a picked voice is dirty and sent; switching sends the default', () => {
  assert.equal(speechDirty(kok, speechForm(kok)), false);
  const f = { ...speechForm(kok), localVoice: 'bm_george' };
  assert.equal(speechDirty(kok, { ...f, localVoice: 'bm_0' }), true);
  assert.deepEqual(speechPatch(kok, { ...f, localVoice: 'bm_0' }), { provider: 'kokoro', model: 'kokoro-v1.0', voice: 'bm_0' });
  assert.deepEqual(speechPatch(prov, { ...speechForm(prov), choice: 'kokoro::kokoro-v1.0' }), { provider: 'kokoro', model: 'kokoro-v1.0', voice: 'af_heart' });
});
test('saved Kokoro to a provider model: no Kokoro voice name sent', () => {
  const f = { ...speechForm(kok), choice: 'openrouter::m' };
  assert.deepEqual(speechPatch(kok, f), { provider: 'openrouter', model: 'm' });
  assert.deepEqual(speechPatch(kok, { ...f, voice: ' Puck ' }), { provider: 'openrouter', model: 'm', voice: 'Puck' });
});
test('the sampler drops a late or superseded sample', async () => {
  const played = []; const player = { stop() {}, enqueue: b => played.push(b) };
  const waits = {};
  const s = createSampler({ player, fetchSample: v => new Promise(r => { waits[v] = r; }) });
  const a = s.play('a'); s.stop(); waits.a('A');
  assert.equal(await a, false);
  const b = s.play('b'); const c = s.play('c'); waits.c('C'); waits.b('B');
  assert.equal(await c, true); assert.equal(await b, false);
  assert.deepEqual(played, ['C']);
});

// -- final review: the alert keyed on the saved provider, the announcer, the error line, one download at a time --

test('the no-speaker alert follows the saved tool, never the pick, and never shows for Kokoro', () => {
  assert.equal(showsNoSpeakerAlert({ provider: 'kokoro', enabled: true, active: false }), false);
  assert.equal(showsNoSpeakerAlert({ provider: 'gone', enabled: true, active: false }), true);
  assert.equal(showsNoSpeakerAlert({ provider: 'gone', enabled: false, active: false }), false);
  assert.equal(showsNoSpeakerAlert({ provider: 'openrouter', enabled: true, active: true }), false);
  assert.equal(showsNoSpeakerAlert(null), false);
});

test('the announcer says each state once: no progress ticks while downloading', () => {
  const a = liveLine(local({ state: 'downloading', done: 1_000_000 }));
  assert.equal(a, liveLine(local({ state: 'downloading', done: 300_000_000 })));
  assert.equal(a, 'Downloading…');
  assert.equal(liveLine(local({ state: 'ready' })), 'Downloaded.');
  assert.equal(liveLine(local({ state: 'failed', error: 'disk' })), ERROR_LINES.disk);
  assert.equal(liveLine(local()), 'The voice is not downloaded yet (354 MB).');
});

test('the error line is cleared when the state or the picked voice changes, not on a progress tick', () => {
  const k = errorKey(local({ state: 'downloading', done: 1 }), 'af_heart');
  assert.equal(k, errorKey(local({ state: 'downloading', done: 2 }), 'af_heart'));
  assert.notEqual(k, errorKey(local({ state: 'ready' }), 'af_heart'));
  assert.notEqual(k, errorKey(local({ state: 'downloading' }), 'bf_emma'));
  assert.equal(errorKey(undefined, undefined), errorKey({}, undefined));
});

test('oneAtATime lets one call through until it ends', async () => {
  const guard = oneAtATime(f => f());
  let calls = 0; let release;
  const slow = () => { calls += 1; return new Promise(r => { release = r; }); };
  const first = guard(slow);
  assert.equal(await guard(slow), undefined);
  assert.equal(calls, 1);
  release('done');
  assert.equal(await first, 'done');
  await guard(() => { calls += 1; });
  assert.equal(calls, 2);
  await assert.rejects(guard(() => Promise.reject(new Error('x'))));
  await guard(() => { calls += 1; });
  assert.equal(calls, 3);  // a failed call frees the guard
});

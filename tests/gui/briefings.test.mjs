// The Briefings section's decisions (briefings.js).
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  scheduleLabel, overlap, validate, lastRunLine, readinessLine, hostedNote, defaultForm, canPlay, skippedLate, formBlocker, saveBlocked, ceilingLow, speakerChoices, loginTook, LOGIN_FAILED, KEEPER_LINUX, KEEPER_WAKE, keeperOf, offersSignIn, signInStart, keeperSentence, wakeWarning, toggleBody, readinessFix,
} from '../../src/gui/briefings.js';

const wk = { days: [0, 1, 2, 3, 4], time: '06:45' };
const we = { days: [5, 6], time: '08:30' };

test('scheduleLabel folds consecutive days into a range and writes 12-hour times', () => {
  assert.equal(scheduleLabel([wk, we]), 'Mon–Fri at 6:45 AM · Sat, Sun at 8:30 AM');
  assert.equal(scheduleLabel([{ days: [4, 0, 2], time: '13:05' }]), 'Mon, Wed, Fri at 1:05 PM');
  assert.equal(scheduleLabel([{ days: [0, 1, 2], time: '00:10' }]), 'Mon–Wed at 12:10 AM');
  assert.equal(scheduleLabel([{ days: [0, 1, 2, 3, 4, 5, 6], time: '12:00' }]), 'Mon–Sun at 12:00 PM');
  assert.equal(scheduleLabel([]), '');
});

test('overlap is true when two lines share a day', () => {
  assert.equal(overlap([wk, we]), false);
  assert.equal(overlap([wk, { days: [0, 6], time: '08:00' }]), true);
  assert.equal(overlap([{ days: [1, 1], time: '08:00' }]), true);
});

test('validate returns the route sentences', () => {
  const ok = { enabled: true, slots: [wk, we], speakers: null, volume: 25 };
  assert.equal(validate(ok, 60), null);
  assert.equal(validate({ ...ok, slots: [] }, 60), 'Add at least one day and time.');
  assert.equal(validate({ ...ok, slots: Array(8).fill(0).map((_, i) => ({ days: [i % 7], time: '06:00' })) }, 60), 'Use at most 7 lines.');
  assert.equal(validate({ ...ok, slots: [{ days: [], time: '06:45' }] }, 60), 'Pick at least one day for each line.');
  assert.equal(validate({ ...ok, slots: [{ days: [0], time: '' }] }, 60), 'Enter each time as HH:MM, from 00:00 to 23:59.');
  assert.equal(validate({ ...ok, slots: [{ days: [0], time: '24:00' }] }, 60), 'Enter each time as HH:MM, from 00:00 to 23:59.');
  assert.equal(validate({ ...ok, slots: [wk, { days: [4], time: '07:00' }] }, 60), 'Each day can have only one time.');
  assert.equal(validate({ ...ok, volume: 0 }, 60), 'Pick a volume from 1 to 60.');
  assert.equal(validate({ ...ok, volume: 61 }, 60), 'Pick a volume from 1 to 60.');
  assert.equal(validate({ ...ok, speakers: [] }, 60), 'Pick from 1 to 32 speakers, or use every speaker.');
  assert.equal(validate({ ...ok, speakers: Array(33).fill(0).map((_, i) => `S${i}`) }, 60), 'Pick from 1 to 32 speakers, or use every speaker.');
  assert.equal(validate({ ...ok, speakers: ['Den', 'den'] }, 60), 'Each speaker can be listed only once.');
  assert.equal(validate({ ...ok, speakers: ['Den'] }, 60), null);
});

test('lastRunLine covers played, partial, phone, late, skipped and failed', () => {
  const run = o => ({ at: '2026-11-24T06:45:03', slot: '2026-11-24T06:45:00', outcome: 'played', played: 5, failed: {}, phone_paused: [], reason: '', ...o });
  assert.equal(lastRunLine(run({}), null), 'played on 5 speakers');
  assert.equal(lastRunLine(run({ played: 1 }), null), 'played on 1 speaker');
  assert.equal(lastRunLine(run({ played: 4, failed: { Garage: 'x' } }), null), 'played on 4 speakers; Garage did not answer');
  assert.equal(lastRunLine(run({ played: 3, failed: { Garage: 'x', Den: 'y' } }), null), 'played on 3 speakers; Garage and Den did not answer');
  assert.equal(lastRunLine(run({ phone_paused: ['Garage'] }), null),
    'played on 5 speakers; Garage was playing from a phone: press play there to resume.');
  assert.equal(lastRunLine(run({ outcome: 'skipped_late', played: 0, reason: 'late' }), null), 'skipped: ZenBot was not running at 6:45');
  assert.equal(lastRunLine(run({ outcome: 'skipped_late', slot: null, reason: 'late' }), null), 'skipped: ZenBot was not running');
  assert.equal(lastRunLine(run({ outcome: 'skipped', reason: 'sonos_off' }), null), 'skipped: Sonos is off');
  assert.equal(lastRunLine(run({ outcome: 'skipped', reason: 'no_speakers' }), null), 'skipped: no Sonos speaker was found');
  assert.equal(lastRunLine(run({ outcome: 'skipped', reason: 'no_voice' }), null), 'skipped: no voice is set up');
  assert.equal(lastRunLine(run({ outcome: 'skipped', reason: 'ceiling_zero' }), null), 'skipped: your Sonos volume limit is 0');
  assert.equal(lastRunLine(run({ outcome: 'skipped', reason: 'odd' }), null), 'skipped');
  assert.equal(lastRunLine(run({ outcome: 'failed', played: 0, reason: 'The voice could not be made.' }), null), 'failed: The voice could not be made.');
  assert.equal(lastRunLine(null, null), '');
  assert.equal(lastRunLine(null, { at: 'x', outcome: 'skipped', reason: 'sonos_off' }), 'skipped: Sonos is off');
});

test('skippedLate is true only for a skipped_late last run', () => {
  assert.equal(skippedLate({ outcome: 'skipped_late' }), true);
  assert.equal(skippedLate({ outcome: 'played' }), false);
  assert.equal(skippedLate(null), false);
});

test('readinessLine gives the route sentences', () => {
  assert.equal(readinessLine(null), '');
  assert.equal(readinessLine('sonos_off'), 'Sonos is off. Switch it on in Settings > Connected apps.');
  assert.equal(readinessLine('no_speakers'), 'No Sonos speaker was found.');
  assert.equal(readinessLine('no_voice'), 'Set up a voice in Settings > Models > Jobs > Read aloud first.');
  assert.equal(readinessLine('ceiling_zero'), 'Your Sonos volume limit is 0.');
  assert.equal(readinessLine('new_code'), 'The briefing is not ready.');
});

test('hostedNote is null for Kokoro and none, else the exact sentence', () => {
  assert.equal(hostedNote(null), null);
  assert.equal(hostedNote('kokoro'), null);
  assert.equal(hostedNote('openrouter'),
    'Your briefing, including calendar titles and birthday names, is sent to openrouter to be spoken. Kokoro keeps it on this computer.');
});

test('defaultForm is weekdays at 06:45, every speaker, volume min(25, ceiling)', () => {
  assert.deepEqual(defaultForm(60), { enabled: true, slots: [{ days: [0, 1, 2, 3, 4], time: '06:45' }], speakers: null, volume: 25 });
  assert.equal(defaultForm(10).volume, 10);
});

test('canPlay needs idle and ready', () => {
  assert.equal(canPlay({ state: 'idle', ready: true }), true);
  assert.equal(canPlay({ state: 'playing', ready: true }), false);
  assert.equal(canPlay({ state: 'idle', ready: false }), false);
});

test('the Linux sentence is verbatim', () => {
  assert.equal(KEEPER_LINUX, 'Keep this computer from sleeping overnight, or the briefing will be skipped.');
});

test('formBlocker: the top-level reason, and a ceiling below 1 is a blocker of its own', () => {
  assert.equal(formBlocker({ ready: true, reason: null, ceiling: 60 }), null);
  assert.equal(formBlocker({ ready: false, reason: 'no_voice', ceiling: 60 }), 'Set up a voice in Settings > Models > Jobs > Read aloud first.');
  assert.equal(formBlocker({ ready: false, reason: 'ceiling_zero', ceiling: 0 }), 'Your Sonos volume limit is 0.');
  assert.equal(formBlocker({ ready: true, reason: null, ceiling: 0 }), 'Your Sonos volume limit is 0.');
  assert.equal(formBlocker(null), null);
});

test('saveBlocked only for a ceiling below 1, and never for a switched-off briefing', () => {
  const on = { enabled: true }, off = { enabled: false };
  assert.equal(saveBlocked({ ready: false, reason: 'no_voice', ceiling: 60 }, on), false);
  assert.equal(saveBlocked({ ready: false, reason: 'ceiling_zero', ceiling: 0 }, on), true);
  assert.equal(saveBlocked({ ready: true, reason: null, ceiling: 0 }, on), true);
  assert.equal(saveBlocked(null, on), false);
  assert.equal(saveBlocked({ ready: false, reason: 'ceiling_zero', ceiling: 0 }, off), false);
  assert.equal(validate({ ...off, slots: [{ days: [0], time: '06:45' }], speakers: null, volume: 50 }, 0), null);
  assert.equal(ceilingLow({ ready: true, reason: null, ceiling: 0 }), true);
  assert.equal(ceilingLow({ ready: true, reason: null, ceiling: 5 }), false);
});

test('speakerChoices keeps a saved speaker that no longer answers, so it can be unticked', () => {
  assert.deepEqual(speakerChoices(['Den', 'Kitchen'], ['Garage', 'Den']), [
    { name: 'Den', missing: false }, { name: 'Kitchen', missing: false }, { name: 'Garage', missing: true }]);
  assert.deepEqual(speakerChoices([], ['Garage', 'Garage']), [{ name: 'Garage', missing: true }]);
  assert.deepEqual(speakerChoices(['Den'], null), [{ name: 'Den', missing: false }]);
  assert.deepEqual(speakerChoices(undefined, [7, 'Den']), [{ name: 'Den', missing: true }]);
});

test('the keeper is the preload API or null', () => {
  assert.equal(keeperOf({}), null);
  assert.equal(keeperOf(undefined), null);
  const k = { get() {}, setLogin() {}, refresh() {} };
  assert.equal(keeperOf({ openzenbot: { briefingKeeper: k } }), k);
});

test('the sign-in box shows only in the installed app', () => {
  assert.equal(offersSignIn({ managed: true, platform: 'win32' }), true);
  assert.equal(offersSignIn({ managed: false, platform: 'win32' }), false);
  assert.equal(offersSignIn(null), false);
  assert.equal(signInStart({ managed: true, login: false }, false), true);
  assert.equal(signInStart({ managed: true, login: false }, true), false);
  assert.equal(signInStart({ managed: true, login: true }, true), true);
});

test('the keeper sentence follows the platform', () => {
  assert.equal(keeperSentence({ platform: 'linux' }), KEEPER_LINUX);
  assert.equal(keeperSentence({ platform: 'win32' }), KEEPER_WAKE);
  assert.equal(keeperSentence({ platform: 'darwin' }), '');
  assert.equal(keeperSentence(null), '');
});

test('the wake warning is the keeper sentence when set', () => {
  assert.equal(wakeWarning({ wake_error: 'Windows would not set a wake-up time for the briefing.' }), 'Windows would not set a wake-up time for the briefing.');
  assert.equal(wakeWarning({ wake_error: null }), '');
  assert.equal(wakeWarning(null), '');
});

test('validate holds an enabled form to the Sonos limit and a switched-off one to 1..100', () => {
  const ok = { enabled: true, slots: [wk], speakers: null, volume: 25 };
  assert.equal(validate({ ...ok, volume: 50 }, 30), 'Pick a volume from 1 to 30.');
  assert.equal(validate({ ...ok, enabled: false, volume: 50 }, 30), null);
  assert.equal(validate({ ...ok, enabled: false, volume: 50 }, 0), null);
  assert.equal(validate({ ...ok, enabled: false, volume: 0 }, 30), 'Pick a volume from 1 to 100.');
  assert.equal(validate({ ...ok, enabled: false, volume: 101 }, 30), 'Pick a volume from 1 to 100.');
  assert.equal(validate({ ...ok, volume: 0 }, 0), 'Your Sonos volume limit is 0.');
  assert.equal(validate({ ...ok, volume: 25 }, 0), 'Your Sonos volume limit is 0.');
  for (const v of [0, 25]) assert.ok(!String(validate({ ...ok, volume: v }, 0)).includes('1 to 0'));
});

test('toggleBody sends the saved volume to switch off and min(volume, limit) to switch on', () => {
  const b = { enabled: true, slots: [wk], speakers: ['Den'], volume: 50, id: 'x', next_run: 'y' };
  assert.deepEqual(toggleBody(b, false, 30), { enabled: false, slots: [wk], speakers: ['Den'], volume: 50 });
  assert.deepEqual(toggleBody({ ...b, enabled: false }, true, 30), { enabled: true, slots: [wk], speakers: ['Den'], volume: 30 });
  assert.deepEqual(toggleBody({ ...b, enabled: false }, true, 60), { enabled: true, slots: [wk], speakers: ['Den'], volume: 50 });
  assert.equal(toggleBody(b, false, 0).volume, 50);
  // switched on under a zero limit the harness answers 'Your Sonos volume limit is 0.'
  assert.equal(validate(toggleBody({ ...b, enabled: false }, true, 0), 0), 'Your Sonos volume limit is 0.');
});

test('loginTook: the keeper must answer the state asked for', () => {
  assert.equal(loginTook(true, { managed: true, login: true }), true);
  assert.equal(loginTook(true, { managed: true, login: false }), false);
  assert.equal(loginTook(false, { managed: true, login: false }), true);
  assert.equal(loginTook(true, undefined), true);  // an older desktop app answered nothing
  assert.match(LOGIN_FAILED, /^The briefing is saved, but /);
});

test('the no-voice fix opens the Read aloud card on Settings > Models > Jobs', async () => {
  const { anchorId, tabOf, toolAnchor } = await import('../../src/gui/settings-index.js');
  const fix = readinessFix('no_voice');
  assert.equal(fix.panel, 'models');
  assert.equal(anchorId(fix.panel, fix.title), toolAnchor('speech'));
  assert.equal(tabOf(fix.panel, anchorId(fix.panel, fix.title)), 'jobs');
  for (const r of ['sonos_off', 'no_speakers', 'ceiling_zero']) assert.deepEqual(readinessFix(r), { panel: 'speakers', title: 'Sonos' });
});

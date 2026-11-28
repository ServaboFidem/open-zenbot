// Telnyx's decisions for the settings section.
import test from 'node:test';
import assert from 'node:assert/strict';
import { ACCOUNT_POINTER, ERRORS, LEADS, REGISTER_URL, TELNYX_LINE, TEXTS_POINTER, canTest, createReads, errorLine, leadLabel, needsAccount, statusLine } from '../../src/gui/telnyx.js';

const ready = {
  key_set: true, key_hint: '6789', number: '+15550001111', last_sent_at: null, last_error: '', sent_today: 0,
  skipped_today: 0, texts: { valid: true, before: true, rundown: false, lead_minutes: 30, evening: '20:00', phone_hint: '4567' },
};

test('the status line says what is off, what is set up and when it last sent', () => {
  assert.equal(statusLine(null), '');
  assert.equal(statusLine({ ...ready, key_set: false, number: null }), 'Save a Telnyx key to start.');
  assert.equal(statusLine({ ...ready, number: null }), 'Choose the number ZenBot sends from.');
  assert.equal(statusLine({ ...ready, texts: null }), 'Set your phone number and which texts to send.');
  assert.equal(statusLine({ ...ready, texts: { ...ready.texts, before: false } }), 'Texts are off.');
  assert.equal(statusLine(ready), 'Texts go to the phone ending 4567. Nothing sent yet.');
  const sent = statusLine({ ...ready, last_sent_at: Date.UTC(2026, 10, 23, 15, 0) / 1000 });
  assert.match(sent, /^Texts go to the phone ending 4567\. Last text sent .+\.$/);
});

test('each error kind has its own sentence, and a capped day says how many were skipped', () => {
  for (const kind of ['network', 'key', 'forbidden', 'refused', 'invalid', 'provider', 'uncertain', 'interrupted']) {
    assert.ok(ERRORS[kind]);
    assert.ok(statusLine({ ...ready, last_error: kind }).endsWith(ERRORS[kind]));
  }
  assert.ok(!/key/i.test(ERRORS.forbidden)); // a 403 is the account's limit, not the key
  assert.ok(statusLine({ ...ready, last_error: 'odd' }).endsWith(ERRORS.provider));
  assert.ok(statusLine({ ...ready, sent_today: 30, skipped_today: 4 }).endsWith('30 texts were sent today; 4 more were skipped.'));
});

test('a test text needs a key, a number and a phone', () => {
  assert.equal(canTest(ready), true);
  assert.equal(canTest({ ...ready, number: null }), false);
  assert.equal(canTest({ ...ready, texts: null }), false);
  assert.equal(canTest({ ...ready, texts: { ...ready.texts, phone_hint: '' } }), false);
  assert.equal(canTest({ ...ready, key_set: false }), false);
  assert.equal(canTest(null), false);
});

test('the fixed copy, the leads and the guide address', () => {
  assert.deepEqual(LEADS, [10, 15, 30, 60, 120]);
  assert.deepEqual(LEADS.map(leadLabel), ['10 minutes', '15 minutes', '30 minutes', '1 hour', '2 hours']);
  assert.match(TELNYX_LINE, /at most 30 texts a day/);
  assert.match(REGISTER_URL, /^https:\/\/support\.telnyx\.com\//);
  assert.equal(errorLine({ message: 'Telnyx is busy. Try again in a moment.' }), 'Telnyx is busy. Try again in a moment.');
  assert.equal(errorLine({ message: '{"detail":1}' }), 'Something went wrong. Try again.');
});

test('a read that started before a key or number change is dropped', () => {
  const reads = createReads();
  const onMount = reads.start();
  assert.equal(reads.current(onMount), true);
  const save = reads.change();  // a key save starts while the first status read is still out
  assert.equal(reads.current(onMount), false);
  assert.equal(reads.current(save), true);
  const test = reads.start();  // a test text changes neither, so it drops nothing
  assert.equal(reads.current(save), true);
  assert.equal(reads.current(test), true);
});

test('Text reminders asks for the account only once the status says it is missing', () => {
  assert.equal(needsAccount(null), false);
  assert.equal(needsAccount({ key_set: false }), true);
  assert.equal(needsAccount({ key_set: true, number: '' }), true);
  assert.equal(needsAccount({ key_set: true, number: '+15550001111' }), false);
  assert.equal(ACCOUNT_POINTER, 'Texts need a Telnyx key and the number ZenBot sends from. Set them up in Settings > Connected apps > Telnyx.');
  assert.equal(TEXTS_POINTER, 'Your phone number and which texts you get are in Settings > Notifications > Text reminders.');
});

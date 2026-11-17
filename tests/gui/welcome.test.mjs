import test from 'node:test';
import assert from 'node:assert/strict';
import { showWelcome, saveError } from '../../src/gui/welcome.js';

test('the screen shows only when the harness is up and has no OpenRouter key', () => {
  assert.equal(showWelcome({ online: true, settings: { openrouter_key: false }, dismissed: false }), true);
  assert.equal(showWelcome({ online: true, settings: { openrouter_key: true }, dismissed: false }), false);
  assert.equal(showWelcome({ online: false, settings: { openrouter_key: false }, dismissed: false }), false);
  assert.equal(showWelcome({ online: true, settings: null, dismissed: false }), false);
  assert.equal(showWelcome({ online: true, settings: {}, dismissed: false }), false);          // an older harness: no field, no screen
  assert.equal(showWelcome({ online: true, settings: { openrouter_key: false }, dismissed: true }), false);
});

test('saveError shows the harness message for a refused key and for offline', () => {
  assert.equal(saveError({ status: 422, message: 'OpenRouter did not accept that key.' }), 'OpenRouter did not accept that key.');
  assert.equal(saveError({ status: 422, message: 'key is empty' }), 'key is empty');
  const off = 'Could not reach OpenRouter. Check your connection and try again.';
  assert.equal(saveError({ status: 503, message: off }), off);
});

test('saveError falls back for anything else', () => {
  const fallback = 'Something went wrong saving the key.';
  assert.equal(saveError({ status: 500, message: 'boom' }), fallback);
  assert.equal(saveError({ status: 0, message: 'The harness did not answer.' }), fallback);
  assert.equal(saveError({ status: 422, message: '' }), fallback);
  assert.equal(saveError(null), fallback);
  assert.equal(saveError(new Error('x')), fallback);
});

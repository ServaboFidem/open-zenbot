// Settings > Tracing's forms: the secret never round-trips, a blank one is left out of the save.
import test from 'node:test';
import assert from 'node:assert/strict';
import { langfuseBody, langfuseDirty, langfuseForm, secretHint, sentryDirty, sentryForm, sourceLabel } from '../../src/gui/tracing.js';

const settings = {
  sentry: { dsn: 'https://k@glitch/3', source: '.env', live: true },
  langfuse: { public_key: 'pk-1', base_url: 'https://lf', environment: '', source: 'saved', secret_key_hint: '4f2a', live: true },
};

test('the forms start from the settings, with an empty secret', () => {
  assert.deepEqual(sentryForm(settings), { dsn: 'https://k@glitch/3' });
  assert.deepEqual(langfuseForm(settings), { public_key: 'pk-1', secret_key: '', base_url: 'https://lf', environment: '' });
  assert.equal(secretHint(settings.langfuse), 'Secret key set · ends in …4f2a');
  assert.equal(secretHint({}), 'No secret key set');
});

test('a blank secret is not sent, a typed one is', () => {
  assert.deepEqual(langfuseBody({ public_key: ' pk-1 ', secret_key: '  ', base_url: 'https://lf', environment: 'production' }),
    { public_key: 'pk-1', base_url: 'https://lf', environment: 'production' });
  assert.equal(langfuseBody({ public_key: 'pk', secret_key: 'sk-new' }).secret_key, 'sk-new');
});

test('dirty tracks edits, and a typed secret is an edit', () => {
  assert.equal(sentryDirty(settings, sentryForm(settings)), false);
  assert.equal(sentryDirty(settings, { dsn: '' }), true);
  const f = langfuseForm(settings);
  assert.equal(langfuseDirty(settings, f), false);
  assert.equal(langfuseDirty(settings, { ...f, secret_key: 'sk-x' }), true);
});

test('source labels', () => {
  assert.equal(sourceLabel(settings.sentry), 'From .env');
  assert.equal(sourceLabel({ dsn: '', source: 'saved' }), 'Turned off here');
  assert.equal(sourceLabel({ source: 'off' }), 'Off');
});

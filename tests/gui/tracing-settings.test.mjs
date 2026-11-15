// The desktop app reads Settings > Tracing's saved DSN at launch: it wins over .env, '' saved is off,
// and a missing or broken file leaves .env in charge.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { applySavedDsn, savedDsn } = require('../../src/electron/tracing-settings.cjs');

// The vault is <tmp>/vault; settings live beside it in <tmp>/vault-settings (old: <tmp>/vault/settings).
const vault = (body, where = 'beside') => {
  const root = path.join(mkdtempSync(path.join(tmpdir(), 'zen-')), 'vault');
  mkdirSync(root);
  if (body !== undefined) {
    const dir = where === 'beside' ? `${root}-settings` : path.join(root, 'settings');
    mkdirSync(dir);
    writeFileSync(path.join(dir, 'tracing.json'), body, 'utf8');
  }
  return root;
};

test('settings are read from vault-settings beside the vault, then from an older <vault>/settings', () => {
  const env = { ZEN_VAULT: vault(JSON.stringify({ sentry: { dsn: 'https://k@old/9' } }), 'inside') };
  assert.equal(savedDsn(env), 'https://k@old/9');
  assert.equal(savedDsn({ ZEN_VAULT: vault(JSON.stringify({ sentry: { dsn: 'https://k@new/8' } })) }), 'https://k@new/8');
});

test('a saved DSN wins over .env', () => {
  const env = { ZEN_VAULT: vault(JSON.stringify({ sentry: { dsn: 'https://k@saved/2' } })), ZEN_GLITCHTIP_DSN: 'https://k@env/1' };
  assert.equal(applySavedDsn(env), 'saved');
  assert.equal(env.ZEN_GLITCHTIP_DSN, 'https://k@saved/2');
});

test('an empty saved DSN turns Sentry off', () => {
  const env = { ZEN_VAULT: vault(JSON.stringify({ sentry: { dsn: '' } })), ZEN_GLITCHTIP_DSN: 'https://k@env/1' };
  assert.equal(applySavedDsn(env), 'saved');
  assert.equal(env.ZEN_GLITCHTIP_DSN, '');
});

test('no file, no sentry section, or a broken file leaves .env in charge', () => {
  for (const body of [undefined, JSON.stringify({ langfuse: {} }), '{broken']) {
    const env = { ZEN_VAULT: vault(body), ZEN_GLITCHTIP_DSN: 'https://k@env/1' };
    assert.equal(savedDsn(env), null);
    assert.equal(applySavedDsn(env), '.env');
    assert.equal(env.ZEN_GLITCHTIP_DSN, 'https://k@env/1');
  }
  assert.equal(applySavedDsn({ ZEN_VAULT: vault(undefined) }), 'off');
});

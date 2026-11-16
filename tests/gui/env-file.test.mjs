// The desktop app reads the repo's .env at start, like the harness: what is unset comes from the file,
// what the shell set wins, and only names are reported (the values are keys).
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { loadEnvFile } = require('../../src/electron/env-file.cjs');

const envFile = text => {
  const file = path.join(mkdtempSync(path.join(tmpdir(), 'zen-env-')), '.env');
  writeFileSync(file, text, 'utf8');
  return file;
};

test('the .env sets what is unset and never overrides the shell', () => {
  const file = envFile('# a comment\nZEN_GLITCHTIP_DSN=https://k@glitch.example/1\nZEN_HARNESS_URL=http://127.0.0.1:9000\nQUOTED="two words"\n# COMMENTED=no\n');
  const env = { ZEN_HARNESS_URL: 'http://127.0.0.1:8765' };
  const loaded = loadEnvFile(file, env);
  assert.equal(env.ZEN_GLITCHTIP_DSN, 'https://k@glitch.example/1');
  assert.equal(env.ZEN_HARNESS_URL, 'http://127.0.0.1:8765');
  assert.equal(env.QUOTED, 'two words');
  assert.ok(!('COMMENTED' in env));
  assert.deepEqual(loaded.sort(), ['QUOTED', 'ZEN_GLITCHTIP_DSN']);
});

test('a missing .env is fine', () => {
  assert.deepEqual(loadEnvFile(path.join(tmpdir(), 'zen-no-such-dir', '.env'), {}), []);
});

test('the main process loads its .env (the repo\'s, or <userData>/.env when managed) before it reads any other setting', () => {
  const main = readFileSync(new URL('../../src/electron/main.cjs', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
  const load = main.indexOf("loadEnvFile(managed ? paths.envFile : path.join(__dirname, '..', '..', '.env'))");
  assert.ok(load > 0, 'main.cjs loads <repo>/.env, or <userData>/.env when managed');
  // only the two that decide which .env and where the bundled files are come first (so neither can be set in a .env)
  assert.deepEqual([...new Set(main.slice(0, load).match(/process\.env\.[A-Z_]+/g))], ['process.env.ZEN_MANAGED', 'process.env.ZEN_RESOURCES']);
  assert.doesNotMatch(main.slice(load, load + 400), /process\.env\[|values|\$\{v/, 'only names are logged');
});

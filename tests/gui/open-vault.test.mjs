// Browse your vault: the main process opens the vault's root in the OS file manager. The page sends no path.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { openVault } = require('../../src/electron/open-vault.cjs');

const shellStub = (answer = '') => { const opened = []; return { opened, openPath: async p => { opened.push(p); return answer; } }; };

test('opens the folder ZEN_VAULT names and answers nothing', async () => {
  const root = path.join(mkdtempSync(path.join(tmpdir(), 'zen-')), 'vault');
  mkdirSync(root);
  const shell = shellStub();
  assert.equal(await openVault({ env: { ZEN_VAULT: root }, shell }), '');
  assert.deepEqual(shell.opened, [root]);
});

test('a vault that is missing, or is a file, is not opened', async () => {
  const dir = mkdtempSync(path.join(tmpdir(), 'zen-'));
  const file = path.join(dir, 'vault');
  const shell = shellStub();
  assert.equal(await openVault({ env: { ZEN_VAULT: path.join(dir, 'none') }, shell }), 'Your vault folder was not found.');
  writeFileSync(file, 'x');
  assert.equal(await openVault({ env: { ZEN_VAULT: file }, shell }), 'Your vault folder was not found.');
  assert.deepEqual(shell.opened, []);
});

test('a file manager that fails gives a sentence without the path', async () => {
  const root = mkdtempSync(path.join(tmpdir(), 'zen-'));
  assert.equal(await openVault({ env: { ZEN_VAULT: root }, shell: shellStub(`Failed to open ${root}`) }), 'Your vault could not be opened.');
});

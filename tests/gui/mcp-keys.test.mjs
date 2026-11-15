// The MCP servers page's key fields: a key never round-trips, a blank field is left out of the save.
import test from 'node:test';
import assert from 'node:assert/strict';
import { keyHint, keysBody, keysDirty, keysForm, missingKeys } from '../../src/gui/mcp-keys.js';

const keys = [
  { name: 'PEXELS_API_KEY', source: 'saved', hint: '4f2a' },
  { name: 'TOOLSMONK_API_KEY', source: '.env', hint: 'set' },
  { name: 'EXA_API_KEY', source: '', hint: '' },
];

test('each key starts as an empty field', () => {
  assert.deepEqual(keysForm(keys), { PEXELS_API_KEY: '', TOOLSMONK_API_KEY: '', EXA_API_KEY: '' });
  assert.deepEqual(keysForm(undefined), {});
});

test('the hint says where a key comes from and only how it ends', () => {
  assert.match(keyHint(keys[0]), /^Saved here · ends in …4f2a\./);
  assert.match(keyHint(keys[1]), /^From \.env\. /);
  assert.match(keyHint(keys[2]), /^Not set\./);
});

test('a save sends only the typed keys, trimmed', () => {
  const form = { PEXELS_API_KEY: '  pk-new  ', TOOLSMONK_API_KEY: '', EXA_API_KEY: '   ' };
  assert.deepEqual(keysBody(form), { keys: { PEXELS_API_KEY: 'pk-new' } });
  assert.equal(keysDirty(form), true);
  assert.equal(keysDirty(keysForm(keys)), false);
});

test('missing keys are named', () => {
  assert.deepEqual(missingKeys(keys), ['EXA_API_KEY']);
  assert.deepEqual(missingKeys(undefined), []);
});

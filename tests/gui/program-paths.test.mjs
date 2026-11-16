import test from 'node:test';
import assert from 'node:assert/strict';
import { EXAMPLE } from '../../src/gui/program-paths.js';

test('each Windows path example is a real backslash path with no control characters', () => {
  for (const [key, text] of Object.entries(EXAMPLE)) {
    assert.match(text, /C:\\[A-Za-z]/, key);
    assert.doesNotMatch(text, /[\u0000-\u001f]/, key);
  }
  assert.ok(EXAMPLE['claude-code'].includes('C:\\Users\\you\\.local\\bin\\claude.exe'));
  assert.ok(EXAMPLE.codex.includes('\\npm\\codex.cmd'));
});

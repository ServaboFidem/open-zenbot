// A framed bot page may only navigate within this machine (the harness and the loopback memory dashboard).
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const { allowFrameNavigation } = createRequire(import.meta.url)('../../src/electron/frame-nav.cjs');

test('a framed page may only move within this machine', () => {
  assert.equal(allowFrameNavigation('http://127.0.0.1:8765/conversations/c/attachments/a?studio=1'), true);
  assert.equal(allowFrameNavigation('http://localhost:9999/'), true);
  assert.equal(allowFrameNavigation('http://[::1]:8765/'), true);
  for (const u of ['https://example.com/?d=secret', 'http://127.0.0.1.evil.test/', 'http://localhost.evil.test/', 'file:///C:/x', 'javascript:1', 'not a url'])
    assert.equal(allowFrameNavigation(u), false, u);
});

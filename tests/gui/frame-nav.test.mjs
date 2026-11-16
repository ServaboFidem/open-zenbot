// A framed bot page may only navigate to the harness's own origin or within this machine (the loopback memory dashboard).
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const { allowFrameNavigation } = createRequire(import.meta.url)('../../src/electron/frame-nav.cjs');
const LOCAL = 'http://127.0.0.1:8765';

test('a framed page may only move within this machine', () => {
  assert.equal(allowFrameNavigation('http://127.0.0.1:8765/conversations/c/attachments/a?studio=1', LOCAL), true);
  assert.equal(allowFrameNavigation('http://localhost:9999/', LOCAL), true);
  assert.equal(allowFrameNavigation('http://[::1]:8765/', LOCAL), true);
  for (const u of ['https://example.com/?d=secret', 'http://127.0.0.1.evil.test/', 'http://localhost.evil.test/', 'file:///C:/x', 'javascript:1', 'not a url'])
    assert.equal(allowFrameNavigation(u, LOCAL), false, u);
});

test('a harness off this machine is allowed for its own origin only', () => {
  const harness = 'https://zen.example.net:9443';
  assert.equal(allowFrameNavigation('https://zen.example.net:9443/conversations/c/attachments/a', harness), true);
  assert.equal(allowFrameNavigation('https://zen.example.net:9443/', `${harness}/`), true);
  for (const u of ['https://zen.example.net.evil.test:9443/', 'https://evil-zen.example.net:9443/', 'https://zen.example.net:9444/',
                   'https://zen.example.net/', 'http://zen.example.net:9443/'])
    assert.equal(allowFrameNavigation(u, harness), false, u);
  assert.equal(allowFrameNavigation('http://127.0.0.1:8765/', harness), true);  // loopback stays allowed
});

test('a harness address that does not parse allows nothing', () => {
  for (const bad of ['not a url', '', undefined, null])
    assert.equal(allowFrameNavigation('http://127.0.0.1:8765/', bad), false, String(bad));
});

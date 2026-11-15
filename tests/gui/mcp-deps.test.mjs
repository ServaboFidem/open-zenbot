// The MCP servers page's requirement cards: what is unmet, in plain words, and where to download by hand.
import test from 'node:test';
import assert from 'node:assert/strict';
import { unmetDeps, depProblem, manualLink, recheckNotice } from '../../src/gui/mcp-deps.js';

test('unmetDeps keeps only deps that are not ok', () => {
  assert.deepEqual(unmetDeps(undefined), []);
  assert.deepEqual(unmetDeps([{ bin: 'a', ok: true }, { bin: 'b', ok: false }]).map(d => d.bin), ['b']);
});

test('depProblem words each problem', () => {
  assert.equal(depProblem({ problem: 'missing' }), 'not installed');
  assert.equal(depProblem({ problem: 'too_old', version: '1.2', min_version: '2.0' }), 'found 1.2, needs 2.0');
  assert.equal(depProblem({ problem: 'too_old', found_path: '/usr/bin/node' }), 'an older version is installed (node)');
  assert.equal(depProblem({ problem: 'too_old', found_path: 'C:\\x\\node.exe' }), 'an older version is installed (node.exe)');
  assert.equal(depProblem({ problem: 'not_on_path' }), 'installed, but not on PATH');
  assert.equal(depProblem({ problem: 'probe_failed' }), 'found, but its version check failed');
});

test('manualLink pulls the first URL out of the manual note', () => {
  assert.equal(manualLink({ manual: '' }), null);
  assert.equal(manualLink({}), null);
  assert.deepEqual(manualLink({ manual: 'Get Node from https://nodejs.org/en/download' }), { href: 'https://nodejs.org/en/download', text: 'Get Node from' });
  assert.deepEqual(manualLink({ manual: 'https://nodejs.org' }), { href: 'https://nodejs.org', text: 'Download page' });
});

test('recheckNotice words each outcome', () => {
  assert.equal(recheckNotice('magick', { state: 'connected' }), 'magick is connected');
  assert.equal(recheckNotice('magick', { state: 'needs_install' }), 'magick still needs install');
  assert.equal(recheckNotice('magick', { state: 'failed' }), 'magick failed to start');
  assert.equal(recheckNotice('magick', { state: 'failed', error: 'boom' }), 'magick failed to start: boom');
  assert.equal(recheckNotice('magick', undefined), 'magick failed to start');
});

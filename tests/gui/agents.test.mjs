import test from 'node:test';
import assert from 'node:assert/strict';
import { agentStatus } from '../../src/gui/agents.js';

const base = { id: 'claude-code', name: 'Claude Code', path: '/bin/claude', version: '2.1.325', auth: 'subscription', confinable: true, runnable: true, usable: true, reason: '', sign_in: 'claude' };

test('usable with a subscription is success', () => {
  assert.deepEqual(agentStatus(base), { text: '2.1.325 · signed in with your subscription', tone: 'success' });
});
test('usable with an API key warns that runs bill credits', () => {
  const s = agentStatus({ ...base, auth: 'api_key' });
  assert.equal(s.tone, 'warning');
  assert.match(s.text, /bill API credits/);
});
test('not signed in names the sign-in command', () => {
  const s = agentStatus({ ...base, auth: 'none', usable: false, reason: 'Not signed in.' });
  assert.equal(s.tone, 'warning');
  assert.match(s.text, /claude/);
});
test('too old to confine is danger', () => {
  const s = agentStatus({ ...base, confinable: false, usable: false, reason: 'Claude Code is too old to keep inside one folder.' });
  assert.equal(s.tone, 'danger');
});
test('not found is a warning', () => {
  assert.deepEqual(agentStatus({ ...base, path: '', version: '', runnable: false, usable: false, auth: 'unknown' }), { text: 'Not found', tone: 'warning' });
});
test('found but not runnable shows the reason', () => {
  assert.deepEqual(agentStatus({ ...base, runnable: false, usable: false, reason: 'It did not answer when run.' }), { text: 'It did not answer when run.', tone: 'warning' });
});

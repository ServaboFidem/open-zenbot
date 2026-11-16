import test from 'node:test';
import assert from 'node:assert/strict';
import { agentStatus } from '../../src/gui/agents.js';

const base = { id: 'claude-code', name: 'Claude Code', path: '/bin/claude', version: '2.1.325', auth: 'subscription', confinable: true, runnable: true, usable: true, reason: '', sign_in: 'claude' };

test('usable with a subscription is success', () => {
  assert.deepEqual(agentStatus(base), { text: '2.1.325 · signed in with your subscription', tone: 'success', detail: '' });
});
test('usable with an API key warns that runs bill credits', () => {
  const s = agentStatus({ ...base, auth: 'api_key' });
  assert.equal(s.tone, 'warning');
  assert.match(s.text, /bill API credits/);
});
test('not signed in has a short badge and the command in the detail', () => {
  const s = agentStatus({ ...base, auth: 'none', usable: false, reason: 'Not signed in.' });
  assert.equal(s.tone, 'warning');
  assert.equal(s.text, 'Not signed in');
  assert.match(s.detail, /claude/);
});
test('too old to confine is danger with the reason as detail', () => {
  const s = agentStatus({ ...base, confinable: false, usable: false, reason: 'Claude Code is too old to keep inside one folder.' });
  assert.deepEqual(s, { text: 'Too old', tone: 'danger', detail: 'Claude Code is too old to keep inside one folder.' });
});
test('not found is a warning', () => {
  const s = agentStatus({ ...base, path: '', version: '', runnable: false, usable: false, auth: 'unknown', reason: 'Claude Code is not installed.' });
  assert.deepEqual(s, { text: 'Not found', tone: 'warning', detail: 'Claude Code is not installed.' });
});
test('found but not runnable keeps the badge short and the reason in the detail', () => {
  assert.deepEqual(agentStatus({ ...base, runnable: false, usable: false, reason: 'It did not answer when run.' }), { text: 'Not ready', tone: 'warning', detail: 'It did not answer when run.' });
});

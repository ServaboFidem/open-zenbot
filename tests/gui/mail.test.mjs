// The AgentMail page's and settings section's decisions, without the page.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BAD_NAME, errorLine, senderName, showsNav, timeLabel, usernameError } from '../../src/gui/mail.js';

test('showsNav only when the harness says enabled', () => {
  assert.equal(showsNav({ enabled: true }), true);
  assert.equal(showsNav({ enabled: false, key_set: true }), false);
  assert.equal(showsNav({ enabled: 'yes' }), false);
  assert.equal(showsNav(null), false);
  assert.equal(showsNav(undefined), false);
});

test('senderName takes the display name, else the address', () => {
  assert.equal(senderName('Ann Lee <ann@example.com>'), 'Ann Lee');
  assert.equal(senderName('"Ann" <a@x>'), 'Ann');
  assert.equal(senderName('<a@x>'), 'a@x');
  assert.equal(senderName('a@x'), 'a@x');
  assert.equal(senderName('  '), '');
  assert.equal(senderName(['x']), '');
  assert.equal(senderName(undefined), '');
});

test('timeLabel: a clock time today, Yesterday, else a short date', () => {
  const now = new Date(2026, 10, 21, 15, 0);
  const today = new Date(2026, 10, 21, 9, 30);
  assert.equal(timeLabel(today.toISOString(), now), today.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
  assert.equal(timeLabel(new Date(2026, 10, 20, 23, 0).toISOString(), now), 'Yesterday');
  const march = new Date(2026, 2, 2, 12, 0);
  assert.equal(timeLabel(march.toISOString(), now), march.toLocaleDateString([], { month: 'short', day: 'numeric' }));
  const lastYear = new Date(2025, 11, 31, 12, 0);
  assert.equal(timeLabel(lastYear.toISOString(), now), lastYear.toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }));
  assert.equal(timeLabel('nope', now), '');
  assert.equal(timeLabel('', now), '');
  assert.equal(timeLabel(undefined, now), '');
});

test('timeLabel: Yesterday across a month boundary', () => {
  assert.equal(timeLabel(new Date(2026, 10, 30, 22, 0).toISOString(), new Date(2026, 11, 1, 8, 0)), 'Yesterday');
});

test('usernameError mirrors the harness rule', () => {
  assert.equal(usernameError('zen'), '');
  assert.equal(usernameError(' ZenBot.Inbox '), '');
  assert.equal(usernameError('a'.repeat(64)), '');
  for (const bad of ['', '-zen', '.zen', 'a'.repeat(65), 'zen bot', 'zen@x', 'zén']) assert.equal(usernameError(bad), BAD_NAME, bad);
  assert.equal(BAD_NAME, 'A name is 1 to 64 lowercase letters, digits, dots, dashes or underscores, starting with a letter or digit.');
});

test('errorLine uses the harness sentence, never raw JSON', () => {
  assert.equal(errorLine({ status: 409, message: 'That address is taken. Try another name.' }), 'That address is taken. Try another name.');
  assert.equal(errorLine({ status: 0, message: 'The harness at http://127.0.0.1:8765 did not answer.' }), 'The harness at http://127.0.0.1:8765 did not answer.');
  assert.equal(errorLine({ status: 422, message: '[{"loc":["body","key"]}]' }), 'Something went wrong. Try again.');
  assert.equal(errorLine({ status: 500, message: '' }), 'Something went wrong. Try again.');
  assert.equal(errorLine(null), 'Something went wrong. Try again.');
});

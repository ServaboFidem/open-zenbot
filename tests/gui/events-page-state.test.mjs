// The Events page's pure decisions: which links it opens, and the status line under the month.
import test from 'node:test';
import assert from 'node:assert/strict';
import { OFFLINE, isGoogleLink, statusLine } from '../../src/gui/events/page-state.js';

const NY = 'America/New_York';
const NOW = new Date('2026-11-17T20:00:00Z');   // 15:00 in New York
const status = (state, sync = {}, account = 'you@example.com') => ({
  google: { state, account }, sync: { running: false, last: '', error: '', lost_edits: 0, ...sync },
});
const line = (s, opts = {}) => statusLine(s, { zone: NY, now: NOW, ...opts });

test('isGoogleLink takes https on google.com and its subdomains only', () => {
  assert.equal(isGoogleLink('https://www.google.com/calendar/event?eid=abc'), true);
  assert.equal(isGoogleLink('https://calendar.google.com/x'), true);
  assert.equal(isGoogleLink('https://google.com/'), true);
  for (const bad of ['http://www.google.com/calendar', 'https://evil.com/', 'https://google.com.evil.com/',
    'https://notgoogle.com/', 'javascript:alert(1)', 'https://www.google.com@evil.com/', '', null, undefined, 'not a url']) {
    assert.equal(isGoogleLink(bad), false, String(bad));
  }
});

test('before the first answer: nothing said, New event waits', () => {
  const l = line(null);
  assert.equal(l.text, ''); assert.equal(l.ready, false); assert.equal(l.refresh, false);
});
test('offline, or a first load that failed: the harness is offline', () => {
  for (const l of [line(status('connected'), { online: false }), line(null, { failed: true }), line(null, { online: false })]) {
    assert.equal(l.text, OFFLINE); assert.equal(l.ready, false); assert.equal(l.refresh, false); assert.equal(l.action, '');
  }
});
test('no credentials, or credentials without a sign-in: only on this computer', () => {
  for (const state of ['not_set_up', 'ready']) {
    const l = line(status(state));
    assert.equal(l.text, 'Only on this computer.');
    assert.deepEqual([l.action, l.actionLabel, l.link, l.refresh, l.ready], ['settings', 'Connect Google Calendar in Settings', true, false, true]);
  }
});
test('connected: synced with the time and account, or not synced yet', () => {
  const l = line(status('connected', { last: '2026-11-17T19:02:00Z' }));
  assert.equal(l.text, 'Synced 14:02 with you@example.com');
  assert.deepEqual([l.refresh, l.refreshDisabled, l.action, l.ready], [true, false, '', true]);
  assert.equal(line(status('connected', { last: '2026-11-16T19:02:00Z' })).text, 'Synced 16 Nov 14:02 with you@example.com');
  assert.equal(line(status('connected', { last: '2026-11-17T19:02:00Z' }, '')).text, 'Synced 14:02');
  assert.equal(line(status('connected')).text, 'Not synced yet');
});
test('connected with an error: events kept, Retry', () => {
  const l = line(status('connected', { last: '2026-11-17T19:02:00Z', error: 'network' }));
  assert.equal(l.text, 'Could not reach Google. Showing what was here at 14:02.');
  assert.deepEqual([l.action, l.actionLabel, l.link, l.refresh], ['retry', 'Retry', false, true]);
});
test('running, from the harness or from this page: Syncing…, Refresh disabled', () => {
  for (const l of [line(status('connected', { running: true })), line(status('connected', { error: 'network' }), { syncing: true })]) {
    assert.equal(l.text, 'Syncing…'); assert.equal(l.spinner, true); assert.equal(l.refreshDisabled, true); assert.equal(l.action, '');
  }
});
test('needs_reconnect: sign-in expired, Reconnect in Settings', () => {
  const l = line(status('needs_reconnect', { error: 'auth' }));
  assert.equal(l.text, 'Google sign-in has expired. Events already here are still shown.');
  assert.deepEqual([l.action, l.actionLabel, l.link, l.refresh, l.ready], ['settings', 'Reconnect in Settings', false, false, true]);
});
test('an unknown state says nothing and offers no Refresh', () => {
  const l = line(status('something_new', { error: 'x' }));
  assert.deepEqual([l.text, l.action, l.refresh, l.spinner, l.ready], ['', '', false, false, true]);
});

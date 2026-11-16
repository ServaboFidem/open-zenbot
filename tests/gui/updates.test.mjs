import test from 'node:test';
import assert from 'node:assert/strict';
import { noticeOf, statusLine, appVersion, updatesApi } from '../../src/gui/updates.js';

test('noticeOf is null unless there is something to tell', () => {
  assert.equal(noticeOf(null), null);
  assert.equal(noticeOf({ notify: false, latest: '0.2.0' }), null);
  assert.deepEqual(noticeOf({ notify: true, latest: '0.2.0', notes: 'n' }), { version: '0.2.0', notes: 'n' });
  assert.deepEqual(noticeOf({ notify: true, latest: '0.2.0', notes: null }), { version: '0.2.0', notes: '' });
});
test('statusLine covers latest, available, failed and never checked', () => {
  assert.equal(statusLine(null), 'Not checked yet.');
  assert.equal(statusLine({ checkedAt: null }), 'Not checked yet.');
  assert.equal(statusLine({ checkedAt: 1, newer: false, latest: '0.1.0', error: null }), 'You have the latest version.');
  assert.equal(statusLine({ checkedAt: 1, newer: true, latest: '0.2.0', error: null }), 'Version 0.2.0 is available.');
  assert.equal(statusLine({ checkedAt: 1, newer: false, error: 'offline' }), 'Could not check for updates.');
});
test('appVersion prefers the shell, and does not throw without the build', () => {
  assert.equal(appVersion({ openzenbot: { version: '1.2.3' } }), '1.2.3');
  assert.equal(appVersion({}), '');
  assert.equal(appVersion(undefined), '');
});
test('updatesApi is null in a browser', () => {
  assert.equal(updatesApi({}), null);
  assert.equal(updatesApi(undefined), null);
  const u = { get() {} };
  assert.equal(updatesApi({ openzenbot: { updates: u } }), u);
});

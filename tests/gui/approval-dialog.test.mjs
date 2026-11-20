import test from 'node:test';
import assert from 'node:assert/strict';
import { applyDecided, botLabel, countLine, keepDeferred, nextApproval, readOrder, waiting } from '../../src/gui/approval-dialog.js';

test('a list read that answers after a newer one is not used', () => {
  const reads = readOrder();
  const old = reads.start(), newer = reads.start();
  assert.equal(reads.accept(newer), true);
  assert.equal(reads.accept(old), false);  // it would drop a deferral or bring back a decided approval
  assert.equal(reads.accept(newer), false);  // an answer is used once
  assert.equal(reads.accept(reads.start()), true);
});

test('reads sent before a decision are not used after it', () => {
  const reads = readOrder();
  const before = reads.start();
  reads.drop();
  assert.equal(reads.accept(before), false);
  assert.equal(reads.accept(reads.start()), true);
});

test('reads answering in the order they were sent are all used', () => {
  const reads = readOrder();
  const one = reads.start(), two = reads.start();
  assert.equal(reads.accept(one), true);
  assert.equal(reads.accept(two), true);
});

test('a list read before a decision cannot bring the decided approval back as pending', () => {
  const stale = [{ id: 'one', state: 'pending' }, { id: 'two', state: 'pending' }];
  const now = applyDecided(stale, { one: 'approved' });
  assert.deepEqual(now.map(x => x.state), ['approved', 'pending']);
  assert.equal(stale[0].state, 'pending');  // the list given is not changed
  assert.equal(nextApproval(now, []).id, 'two');
});

test('applyDecided leaves a list alone when it has nothing to correct', () => {
  const list = [{ id: 'one', state: 'rejected' }, { id: 'two', state: 'pending' }];
  assert.equal(applyDecided(list, { one: 'approved' }), list);  // the harness's own answer stands
  assert.equal(applyDecided(list, {}), list);
  assert.deepEqual(applyDecided(undefined, undefined), []);
});

const a = (id, state = 'pending', created_at = '2026-11-20T10:00:00+00:00', bot_id = 'writer') => ({ id, state, created_at, bot_id });

test('the oldest pending approval shows first', () => {
  const list = [a('new', 'pending', '2026-11-20T10:05:00+00:00'), a('old', 'pending', '2026-11-20T10:01:00+00:00')];
  assert.equal(nextApproval(list, []).id, 'old');
  assert.deepEqual(waiting(list, []).map(x => x.id), ['old', 'new']);
});

test('a tie keeps list order', () => {
  assert.deepEqual(waiting([a('one'), a('two')], []).map(x => x.id), ['one', 'two']);
});

test('decided and deferred approvals do not show', () => {
  const list = [a('done', 'approved'), a('no', 'rejected'), a('later'), a('now')];
  assert.equal(nextApproval(list, ['later']).id, 'now');
  assert.equal(nextApproval([a('done', 'approved'), a('later')], ['later']), null);
});

test('nothing to show for an empty or missing list', () => {
  assert.equal(nextApproval([], []), null);
  assert.equal(nextApproval(undefined, undefined), null);
  assert.deepEqual(waiting(null, null), []);
});

test('a deferred id is forgotten once it is no longer pending', () => {
  assert.deepEqual(keepDeferred(['gone', 'done', 'still'], [a('done', 'approved'), a('still')]), ['still']);
});

test('keepDeferred answers the same array when nothing changed', () => {
  const deferred = ['still'];
  assert.equal(keepDeferred(deferred, [a('still')]), deferred);
});

test('the bot is named, by id when it is not a known bot, and never blank', () => {
  const bots = [{ id: 'writer', name: 'Writer' }];
  assert.equal(botLabel(a('x'), bots), 'Writer');
  assert.equal(botLabel(a('x', 'pending', '', 'ghost'), bots), 'ghost');
  assert.equal(botLabel(a('x', 'pending', '', ''), bots), 'A bot');
  assert.equal(botLabel(a('x'), undefined), 'writer');
});

test('the count line shows only when more than one waits', () => {
  assert.equal(countLine([a('one')], []), '');
  assert.equal(countLine([a('one'), a('two'), a('three')], ['three']), '1 of 2 waiting');
  assert.equal(countLine([], []), '');
});

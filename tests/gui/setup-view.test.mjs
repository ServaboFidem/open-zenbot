// The setup page's words and buttons, from the state the main process sends (src/gui/setup-view.js).
import test from 'node:test';
import assert from 'node:assert/strict';
import { viewOf } from '../../src/gui/setup-view.js';

const steps = statuses => ['disk', 'python', 'packages', 'node', 'stamp'].map((id, i) => ({ id, title: `Step ${id}`, status: statuses[i] }));
const running = { steps: steps(['done', 'running', 'pending', 'pending', 'pending']), line: 'Downloading cpython', detail: ['a', 'b'], error: null };
const failed = { steps: steps(['done', 'failed', 'pending', 'pending', 'pending']), line: '', detail: ['could not start uv'], error: 'Installing Python failed.' };

test('a newer release is offered before anything is downloaded', () => {
  const v = viewOf({ phase: 'update', update: { notify: true, latest: '0.2.0' } });
  assert.deepEqual(v.actions, ['get-update', 'continue']);
  assert.match(v.heading, /0\.2\.0/);
});

test('while the check runs there is nothing to press', () => {
  const v = viewOf({ phase: 'update', update: null });
  assert.deepEqual(v.actions, []);
  assert.match(v.heading, /newer version/i);
  assert.deepEqual(viewOf({ phase: 'update', update: { notify: false, latest: '0.1.0' } }).actions, []);
});

test("the offer shows the release notes as the details, and the version you have", () => {
  const v = viewOf({ phase: 'update', update: { notify: true, latest: '0.2.0', current: '0.1.0', notes: 'Faster setup.' } });
  assert.equal(v.detail, 'Faster setup.');
  assert.match(v.body, /0\.1\.0/);
});

test('setup shows each step with its status and says the download happens once', () => {
  const v = viewOf({ phase: 'setup', setup: running });
  assert.match(v.body, /once/i);
  assert.deepEqual(v.steps.map(s => [s.title, s.status]), running.steps.map(s => [s.title, s.status]));
  assert.deepEqual(v.steps.map(s => s.label), ['Done', 'In progress', 'Waiting', 'Waiting', 'Waiting']);
  assert.equal(v.line, 'Downloading cpython');
  assert.equal(v.detail, 'a\nb');
  assert.deepEqual(v.actions, ['details']);
});

test('a failed step shows its message with Retry and Show details', () => {
  const v = viewOf({ phase: 'setup', setup: failed });
  assert.deepEqual(v.actions, ['retry', 'details', 'open-logs']);
  assert.match(v.body, /Installing Python failed\./);
  assert.equal(v.steps[1].label, 'Failed');
  assert.equal(v.detail, 'could not start uv');
});

test('a skipped step says it was not needed', () => {
  const v = viewOf({ phase: 'setup', setup: { ...running, steps: steps(['done', 'done', 'done', 'skipped', 'running']) } });
  assert.equal(v.steps[3].label, 'Not needed');
});

test('starting says tools and memory are fetched on the first start', () => {
  const first = viewOf({ phase: 'starting', harness: { firstStart: true } });
  assert.match(first.body, /tools and memory/i);
  assert.match(first.body, /once/i);
  assert.deepEqual(first.actions, []);
  assert.doesNotMatch(viewOf({ phase: 'starting', harness: { firstStart: false } }).body, /tools and memory/i);
});

test('port in use and a stopped harness each have their own message and action', () => {
  assert.deepEqual(viewOf({ phase: 'failed', harness: { reason: 'exited' } }).actions, ['restart', 'open-logs']);
  assert.deepEqual(viewOf({ phase: 'failed', harness: { reason: 'port-in-use' } }).actions, ['open-logs']);
  const exited = viewOf({ phase: 'failed', harness: { reason: 'exited', detail: 'Traceback\nboom' } });
  const port = viewOf({ phase: 'failed', harness: { reason: 'port-in-use', detail: 'Port 8765 is in use by another program.' } });
  assert.notEqual(exited.heading, port.heading);
  assert.match(port.body, /8765/);
  assert.equal(exited.detail, 'Traceback\nboom');
});

test('a state it does not know shows a plain waiting page, never throws', () => {
  for (const s of [undefined, null, {}, { phase: 'nope' }, { phase: 'setup' }, { phase: 'failed' }]) {
    const v = viewOf(s);
    assert.equal(typeof v.heading, 'string');
    assert.ok(Array.isArray(v.steps) && Array.isArray(v.actions));
  }
});

test('an unexpected error says OpenZenBot could not start, with the log folder only', () => {
  const v = viewOf({ phase: 'failed', harness: { reason: 'error', detail: 'TypeError: x' } });
  assert.equal(v.heading, 'OpenZenBot could not start.');
  assert.deepEqual(v.actions, ['open-logs']);
  assert.equal(v.detail, 'TypeError: x');
});

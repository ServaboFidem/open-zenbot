import test from 'node:test';
import assert from 'node:assert/strict';
import { APP_STEPS, bringInList, defaultDestination, destinations, pushFailure, pushSentence, shownSections, signInChanged } from '../../src/gui/calendar-settings.js';

const st = (state, can_write = false) => ({ google: { state, can_write } });
const cal = (id, o = {}) => ({ id, name: id, source: 'google', writable: true, primary: false, ...o });

test('four steps, the last names the Desktop app type', () => {
  assert.equal(APP_STEPS.length, 4);
  assert.match(APP_STEPS[3], /"Desktop app"/);
});
test('calendars and push show only when connected', () => {
  for (const s of ['not_set_up', 'ready', 'needs_reconnect']) assert.deepEqual(shownSections(st(s)), { google: true, calendars: false, push: false }, s);
  assert.deepEqual(shownSections(st('connected')), { google: true, calendars: true, push: true });
  assert.equal(shownSections(null).calendars, false);
});
test('a sign-in is noticed by the state or the write permission changing', () => {
  assert.equal(signInChanged(st('ready'), st('ready')), false);
  assert.equal(signInChanged(st('ready'), st('connected')), true);
  assert.equal(signInChanged(st('connected', false), st('connected', true)), true);
  assert.equal(signInChanged(null, st('connected')), false);
});
test('only Google calendars are listed, and only writable ones are destinations', () => {
  const list = [cal('a'), cal('b', { writable: false }), { id: 'local', source: 'local', writable: true }, null];
  assert.deepEqual(bringInList(list).map(c => c.id), ['a', 'b']);
  assert.deepEqual(destinations(list).map(c => c.id), ['a']);
  assert.deepEqual(destinations(undefined), []);
});
test('the default destination is the saved one, else primary, else first, else empty', () => {
  const list = [cal('a'), cal('b', { primary: true }), cal('c', { writable: false })];
  assert.equal(defaultDestination(list, 'a'), 'a');
  assert.equal(defaultDestination(list, 'c'), 'b');
  assert.equal(defaultDestination([cal('a'), cal('d')], ''), 'a');
  assert.equal(defaultDestination([cal('c', { writable: false })], ''), '');
});
test('the push sentence names only the non-zero counts', () => {
  assert.equal(pushSentence({ add: 12, update: 3, remove: 1 }, 'Work'), '12 added, 3 updated, 1 removed on Google');
  assert.equal(pushSentence({ add: 2, update: 0, remove: 0 }, 'Work'), '2 added on Google');
  assert.equal(pushSentence({ add: 0, update: 0, remove: 0 }, 'Work'), 'Nothing to send yet. Events you make from now on will be added to Work.');
});
test('a 409 on switching push on asks for write access; anything else shows the sentence', () => {
  assert.deepEqual(pushFailure({ status: 409, message: 'Allow OpenZenBot to add events to Google first.' }), { kind: 'allow' });
  assert.deepEqual(pushFailure({ status: 422, message: 'Pick a Google calendar.' }), { kind: 'show', text: 'Pick a Google calendar.' });
  assert.deepEqual(pushFailure(new Error('')), { kind: 'show', text: 'Something went wrong.' });
  assert.deepEqual(pushFailure(null), { kind: 'show', text: 'Something went wrong.' });
});

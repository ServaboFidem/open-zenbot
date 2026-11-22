import test from 'node:test';
import assert from 'node:assert/strict';
import { EMPTY, HOLD, body, canChange, compareDefaults, count, cssValue, frameMessages, holdsSend, pictureProblem, saveLabel, setImage, setText, setTweak, slotFor, whyNot } from '../../src/gui/design-edits.js';

const slots = { version: 5, texts: [{ id: 'hero', text: 'Hello' }], images: [{ id: 'logo', name: 'logo.png', alt: 'Logo' }],
  tweaks: [{ var: '--accent', label: 'Accent', type: 'color', value: '#2f6feb' }, { var: '--gap', label: 'Gap', type: 'range', min: 0, max: 40, step: 1, unit: 'px', value: 8 }] };

test('slotFor finds a text or picture slot by element id, else null', () => {
  assert.deepEqual(slotFor(slots, { elementId: 'hero' }), { kind: 'text', id: 'hero', text: 'Hello' });
  assert.deepEqual(slotFor(slots, { elementId: 'logo' }), { kind: 'image', id: 'logo', name: 'logo.png', alt: 'Logo' });
  assert.equal(slotFor(slots, { elementId: 'nope' }), null);
  assert.equal(slotFor(slots, { elementId: '' }), null);
});
test('a value equal to the saved one removes the entry', () => {
  const p = setText(EMPTY, slots, 'hero', 'Hi');
  assert.equal(count(p), 1);
  assert.equal(count(setText(p, slots, 'hero', 'Hello')), 0);
  const t = setTweak(EMPTY, slots, '--gap', 12);
  assert.equal(count(t), 1);
  assert.equal(count(setTweak(t, slots, '--gap', 8)), 0);
  assert.equal(count(EMPTY), 0);
  assert.deepEqual(EMPTY, { texts: {}, images: {}, tweaks: {} });
});
test('saveLabel names the next version', () => assert.equal(saveLabel(EMPTY, 4), 'Save as Version 5'));
test('cssValue', () => {
  assert.equal(cssValue(slots.tweaks[0], '#c2410c'), '#c2410c');
  assert.equal(cssValue({ type: 'range', unit: 'px' }, 12), '12px');
});
test('frameMessages starts with reset and sends every tweak in one vars message', () => {
  let p = setText(EMPTY, slots, 'hero', 'Hi');
  p = setImage(p, 'logo', { name: 'a.png', dataUrl: 'data:image/png;base64,QUJD', alt: 'A' });
  p = setTweak(setTweak(p, slots, '--accent', '#c2410c'), slots, '--gap', 12);
  assert.deepEqual(frameMessages(p, slots), [{ zen: 'reset' }, { zen: 'text', elementId: 'hero', text: 'Hi' },
    { zen: 'image', elementId: 'logo', src: 'data:image/png;base64,QUJD', alt: 'A' }, { zen: 'vars', vars: { '--accent': '#c2410c', '--gap': '12px' } }]);
  assert.deepEqual(frameMessages(EMPTY, slots), [{ zen: 'reset' }]);
});
test('body splits a picture to bare base64 and never carries a dataUrl', () => {
  const p = setTweak(setImage(setText(EMPTY, slots, 'hero', 'Hi'), 'logo', { name: 'a.png', dataUrl: 'data:image/png;base64,QUJD' }), slots, '--gap', 12);
  const b = body(p, 4);
  assert.deepEqual(b, { version: 4, texts: { hero: 'Hi' }, images: { logo: { name: 'a.png', data: 'QUJD', alt: null } }, tweaks: { '--gap': 12 } });
  assert.equal(JSON.stringify(b).includes('dataUrl'), false);
});
test('canChange, whyNot and the send hold', () => {
  const ok = { onNewest: true, ready: true, disabled: false, comparing: false };
  assert.equal(canChange(ok), true);
  for (const k of ['onNewest', 'ready']) assert.equal(canChange({ ...ok, [k]: false }), false);
  assert.equal(canChange({ ...ok, disabled: true }), false);
  assert.equal(canChange({ ...ok, comparing: true }), false);
  assert.equal(whyNot({ ...ok, comparing: true }), 'Close Compare to change the design.');
  assert.equal(whyNot({ ...ok, disabled: true }), 'Wait for the task to finish before commenting.');
  assert.equal(whyNot({ ...ok, ready: false }), 'The design is still loading.');
  assert.match(whyNot({ ...ok, onNewest: false, newestLabel: 'Version 3' }), /Switch to Version 3/);
  assert.equal(whyNot(ok), '');
  assert.equal(holdsSend(EMPTY), false);
  assert.equal(holdsSend(setText(EMPTY, slots, 'hero', 'Hi')), true);
  assert.equal(HOLD, 'Save or discard your changes first.');
});
test('pictureProblem', () => {
  assert.equal(pictureProblem({ type: 'image/png', size: 1500000 }), '');
  assert.equal(pictureProblem({ type: 'image/gif', size: 10 }), '');
  assert.equal(pictureProblem({ type: 'image/png', size: 1500001 }), 'That picture is over 1,500,000 bytes.');
  assert.equal(pictureProblem({ type: 'image/svg+xml', size: 10 }), 'Use a PNG, JPEG, WebP or GIF picture.');
});
test('compareDefaults is previous and newest of the newest design', () => {
  const r = [{ attId: 'x1', design: 'other' }, { attId: 'a1', design: 'land' }, { attId: 'a2', design: 'land' }, { attId: 'a3', design: 'land' }];
  assert.deepEqual(compareDefaults(r), ['a2', 'a3']);
  assert.deepEqual(compareDefaults(r.slice(0, 1)), ['x1', 'x1']);
});

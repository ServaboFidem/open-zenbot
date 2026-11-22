import test from 'node:test';
import assert from 'node:assert/strict';
import { DESIGN_CHANGED, EMPTY, HOLD, WAIT_FOR_TASK, body, canChange, compareDefaults, count, cssValue, dropPending, dropStale, exportItems, frameMessages, holdsSend, pendingFor, pictureProblem, restorable, saveLabel, saveWhy, setImage, setText, setTweak, slotFor, slotsBehind, unsavedLabel, whyNot, withPending } from '../../src/gui/design-edits.js';

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
  assert.equal(whyNot({ ...ok, disabled: true }), 'Wait for the task to finish before changing the design.');
  assert.equal(whyNot({ ...ok, ready: false }), 'The design is still loading.');
  assert.equal(whyNot({ ...ok, ready: false, loaded: false }), 'The design is still loading.');
  // loaded with no bridge: a file over 5 MB is served without it, so changing never becomes possible
  assert.equal(whyNot({ ...ok, ready: false, loaded: true }), 'Changing is off for this file: it is too large.');
  assert.equal(whyNot({ ...ok, loaded: true }), '');
  assert.equal(whyNot({ ...ok, onNewest: false, newestLabel: 'Version 3' }), 'Changes go on the newest version. Switch to Version 3 to change it.');
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
test('a colour picked back to the saved one, in any case, removes the entry', () => {
  const upper = { ...slots, tweaks: [{ var: '--accent', label: 'Accent', type: 'color', value: '#2F6FEB' }] };
  const t = setTweak(EMPTY, upper, '--accent', '#c2410c');
  assert.equal(count(t), 1);
  assert.equal(count(setTweak(t, upper, '--accent', '#2f6feb')), 0);
  assert.equal(count(setTweak(t, slots, '--accent', '#2F6FEB')), 0);
});
test('restorable: an older version of the same file, outside Compare', () => {
  const v3 = { attId: 'c', design: 'site', name: 'index.html', version: 3 };
  const v1 = { attId: 'a', design: 'site', name: 'index.html', version: 1 };
  assert.equal(restorable(v1, v3, false), true);
  assert.equal(restorable(v3, v3, false), false);
  assert.equal(restorable(v1, v3, true), false);
  assert.equal(restorable({ ...v1, design: 'other' }, v3, false), false);
  // two files in one design: the details page's version 1 is not the index page's
  assert.equal(restorable({ attId: 'd', design: 'site', name: 'details.html', version: 1 }, v3, false), false);
});
test('slotsBehind: the slots name another version than the newest the pane holds', () => {
  const newest = { attId: 'c', version: 3 };
  assert.equal(slotsBehind({ version: 4 }, newest), true);
  assert.equal(slotsBehind({ version: 3 }, newest), false);
  assert.equal(slotsBehind(null, newest), false);
});

test('exportItems: HTML always, ZIP on the newest version only, PDF and PNG only with the desktop bridge', () => {
  const kinds = o => exportItems({ title: 'Bakery', ...o }).map(i => i.kind);
  assert.deepEqual(kinds({ isNewest: true, hasBridge: true }), ['html', 'zip', 'pdf', 'png']);
  assert.deepEqual(kinds({ isNewest: false, hasBridge: true }), ['html', 'pdf', 'png']);
  assert.deepEqual(kinds({ isNewest: true, hasBridge: false }), ['html', 'zip']);
  assert.deepEqual(kinds({ isNewest: false, hasBridge: false }), ['html']);
  assert.deepEqual(exportItems({ isNewest: true, hasBridge: true, title: 'Bakery' }).map(i => [i.label, i.name]),
    [['HTML', 'Bakery.html'], ['ZIP (page and pictures)', 'Bakery.zip'], ['PDF', 'Bakery'], ['PNG', 'Bakery']]);
});

test('exportItems: ZIP waits while a task runs, and an empty title names the files "design"', () => {
  const zip = disabled => exportItems({ isNewest: true, hasBridge: false, disabled, title: '' }).find(i => i.kind === 'zip');
  assert.deepEqual(zip(true), { kind: 'zip', label: 'ZIP (page and pictures)', name: 'design.zip', why: WAIT_FOR_TASK });
  assert.equal(WAIT_FOR_TASK, 'Wait for the task to finish.');
  assert.equal(zip(false).why, undefined);
  assert.equal(exportItems({ title: '  ' })[0].name, 'design.html');
});

test('exportItems: a title that is a file name loses its .html or .htm, in any case', () => {
  const names = title => exportItems({ isNewest: true, hasBridge: true, title }).map(i => i.name);
  assert.deepEqual(names('landing.html'), ['landing.html', 'landing.zip', 'landing', 'landing']);
  assert.deepEqual(names('Landing.HTM'), ['Landing.html', 'Landing.zip', 'Landing', 'Landing']);
  assert.deepEqual(names('Landing page'), ['Landing page.html', 'Landing page.zip', 'Landing page', 'Landing page']);
  assert.equal(exportItems({ title: '.html' })[0].name, 'design.html');
  assert.equal(exportItems({ title: 'notes.html.txt' })[0].name, 'notes.html.txt.html');
});

test('pending changes are kept per conversation, on the revision they were made on', () => {
  const a = setText(EMPTY, slots, 'hero', 'Hi'), b = setTweak(EMPTY, slots, '--gap', 12);
  let map = withPending({}, 'c1', 'att1', a);
  map = withPending(map, 'c2', 'att9', b);
  // switching away and back shows the same changes; another conversation has its own
  assert.equal(pendingFor(map, 'c1', 'att1'), a);
  assert.equal(pendingFor(map, 'c2', 'att9'), b);
  assert.equal(pendingFor(map, 'c3', 'att1'), EMPTY);
  assert.equal(pendingFor(map, '', 'att1'), EMPTY);
  assert.equal(holdsSend(pendingFor(map, 'c1', 'att1')), true);
  assert.equal(holdsSend(pendingFor(map, 'c3', 'att1')), false);
  // a new revision in that conversation ends them
  assert.equal(pendingFor(map, 'c1', 'att2'), EMPTY);
  const pruned = dropStale(map, 'c1', 'att2');
  assert.deepEqual(Object.keys(pruned), ['c2']);
  assert.equal(dropStale(map, 'c1', 'att1'), map);  // nothing stale: the same map
  assert.equal(dropStale(map, 'c3', 'att1'), map);
  // a discard or a save (no changes left) drops the entry; an unknown or empty id changes nothing
  assert.deepEqual(Object.keys(withPending(map, 'c1', 'att1', EMPTY)), ['c2']);
  assert.deepEqual(Object.keys(dropPending(map, 'c2')), ['c1']);
  assert.equal(dropPending(map, 'nope'), map);
  assert.equal(withPending(map, '', 'att1', a), map);
  assert.equal(pendingFor({}, 'toString', undefined), EMPTY);  // only the map's own entries
});

test('an empty update clears the changes made on its own revision, never those made since on a newer one', () => {
  const newer = setText(EMPTY, slots, 'hero', 'Made on the new version');
  const map = withPending({}, 'c1', 'att2', newer);
  // a save or restore answer that was sent from att1 lands after edits on att2: they stay
  assert.equal(withPending(map, 'c1', 'att1', EMPTY), map);
  assert.equal(pendingFor(withPending(map, 'c1', 'att1', EMPTY), 'c1', 'att2'), newer);
  // Save or Discard on the same revision still clears them
  assert.deepEqual(withPending(map, 'c1', 'att2', EMPTY), {});
  assert.deepEqual(withPending({}, 'c1', 'att1', EMPTY), {});
});

test('unsavedLabel names the count of waiting changes for the hidden pane', () => {
  assert.equal(unsavedLabel(1), '1 change not saved');
  assert.equal(unsavedLabel(3), '3 changes not saved');
  assert.equal(unsavedLabel(0), '');
});

test('saveWhy: Save waits for a task, and is refused while the slots name a newer version than the pane holds', () => {
  const newest = { attId: 'c', version: 3 };
  assert.equal(saveWhy({ disabled: false, slots: { version: 3 }, newest }), '');
  assert.equal(saveWhy({ disabled: true, slots: { version: 3 }, newest }), 'Wait for the task to finish before changing the design.');
  // a catch-up made version 4 after these changes were made on version 3: saving them on 4 would overwrite it unseen
  assert.equal(saveWhy({ disabled: false, slots: { version: 4 }, newest }), DESIGN_CHANGED);
  assert.equal(DESIGN_CHANGED, 'The design has changed. Reload and try again.');
  assert.equal(saveWhy({ disabled: false, slots: null, newest }), '');
});

// The design pane's pure half: which revisions to frame, which messages from the frame to trust, and
// which picks become comments on the task.
import test from 'node:test';
import assert from 'node:assert/strict';
import { acceptPick, acceptReady, addPick, commentsFor, designRevisions, MAX_PICKS, studioFrameUrl } from '../../src/gui/design.js';

const win = {};
const ok = { zen: 'pick', elementId: 'hero', selector: 'body > h1', tag: 'h1', text: 'Hi', rect: { x: 1, y: 2, w: 3, h: 4 } };
test('a pick is accepted only from the pane\'s own frame and in the exact shape', () => {
  assert.deepEqual(acceptPick({ source: win, data: ok }, win), { elementId: 'hero', selector: 'body > h1', tag: 'h1', text: 'Hi', rect: ok.rect });
  assert.equal(acceptPick({ source: {}, data: ok }, win), null);
  for (const bad of [{ ...ok, extra: 1 }, { ...ok, selector: 'x'.repeat(301) }, { ...ok, text: 'x'.repeat(161) },
    { ...ok, tag: '<img onerror=1>' }, { ...ok, selector: '' }, { ...ok, rect: { x: 1, y: 2, w: 3, h: NaN } },
    { ...ok, elementId: 7 }, null, 'pick'])
    assert.equal(acceptPick({ source: win, data: bad }, win), null);
});
test('a pick is refused when the frame is gone or the shape is near but not exact', () => {
  assert.equal(acceptPick({ source: null, data: ok }, null), null);
  const { text, ...noText } = ok;
  for (const bad of [noText, { ...ok, zen: 'ready' }, { ...ok, elementId: 'x'.repeat(121) }, { ...ok, tag: 'H1' },
    { ...ok, rect: { x: 1, y: 2, w: 3 } }, { ...ok, rect: { x: 1, y: 2, w: 3, h: 4, z: 5 } }, { ...ok, rect: { x: 1, y: 2, w: '3', h: 4 } },
    { ...ok, rect: { x: 1, y: 2, w: 3, h: Infinity } }, { ...ok, rect: null }, [ok]])
    assert.equal(acceptPick({ source: win, data: bad }, win), null);
  assert.deepEqual(acceptPick({ source: win, data: { ...ok, elementId: '', text: '' } }, win).elementId, '');
});
test('ready counts only from the pane\'s own frame and in its exact shape', () => {
  assert.equal(acceptReady({ source: win, data: { zen: 'ready' } }, win), true);
  assert.equal(acceptReady({ source: {}, data: { zen: 'ready' } }, win), false);
  assert.equal(acceptReady({ source: null, data: { zen: 'ready' } }, null), false);
  for (const bad of [{ zen: 'ready', x: 1 }, { zen: 'pick' }, 'ready', null, [{ zen: 'ready' }]])
    assert.equal(acceptReady({ source: win, data: bad }, win), false);
});
test('revisions come oldest first and skip plain attachments', () => {
  const ex = [{ attachments: [{ id: 'a', title: 'L', design: 'landing', version: 1 }, { id: 'p', title: 'doc' }] },
              { attachments: [{ id: 'b', title: 'L', design: 'landing', version: 2 }] }];
  assert.deepEqual(designRevisions(ex).map(r => r.attId), ['a', 'b']);
  assert.deepEqual(designRevisions(ex)[1], { attId: 'b', title: 'L', design: 'landing', version: 2 });
  assert.deepEqual(designRevisions([{ text: 'no attachments' }]), []);
});
test('the studio frame url adds studio=1', () => {
  assert.equal(studioFrameUrl('http://h/conversations/c/attachments/a'), 'http://h/conversations/c/attachments/a?studio=1');
  assert.equal(studioFrameUrl('http://h/x?k=1'), 'http://h/x?k=1&studio=1');
});
test('a pick joins with an empty note, and none past the twentieth', () => {
  const p = { elementId: 'hero', selector: 's', tag: 'h1', text: 'Hi', rect: ok.rect };
  assert.deepEqual(addPick([], p), [{ ...p, note: '' }]);
  const full = Array.from({ length: MAX_PICKS }, () => ({ ...p, note: '' }));
  assert.equal(MAX_PICKS, 20);
  assert.equal(addPick(full, p), full);
});
test('only picks with a note are sent', () => {
  const picks = [{ elementId: 'hero', selector: 's', tag: 'h1', text: 'Hi', note: ' bigger ' }, { elementId: '', selector: 't', tag: 'p', text: '', note: ' ' }];
  assert.deepEqual(commentsFor('b', picks), [{ attachment_id: 'b', element_id: 'hero', selector: 's', tag: 'h1', text: 'Hi', note: 'bigger' }]);
});

// The design pane's pure half: which revisions to frame, which messages from the frame to trust, and
// which picks become comments on the task.
import test from 'node:test';
import assert from 'node:assert/strict';
import { acceptPick, acceptReady, addPick, clearsComposer, commentsFor, designRevisions, MAX_PICKS, redoTarget, startedFrom, studioFrameUrl } from '../../src/gui/design.js';

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
test('an unchanged file attached again is one revision, under its latest attachment', () => {
  const ex = [{ attachments: [{ id: 'a', title: 'L', design: 'landing', version: 1 }] },
              { attachments: [{ id: 'b', title: 'L', design: 'landing', version: 2 }] },
              { attachments: [{ id: 'c', title: 'L', design: 'landing', version: 2 }, { id: 'p', title: 'P', design: 'pricing', version: 2 }] }];
  assert.deepEqual(designRevisions(ex).map(r => [r.design, r.version, r.attId]), [['landing', 1, 'a'], ['landing', 2, 'c'], ['pricing', 2, 'p']]);
});
test('two files in one design with the same version number are separate revisions', () => {
  const ex = [{ attachments: [{ id: 'a', title: 'Home', name: 'index.html', design: 'site', version: 1 }, { id: 'b', title: 'Details', name: 'details.html', design: 'site', version: 1 }] },
              { attachments: [{ id: 'c', title: 'Home', name: 'index.html', design: 'site', version: 1 }] }];
  assert.deepEqual(designRevisions(ex).map(r => r.attId), ['b', 'c']);
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
test('without a fallback, only picks with a note are sent', () => {
  const picks = [{ elementId: 'hero', selector: 's', tag: 'h1', text: 'Hi', note: ' bigger ' }, { elementId: '', selector: 't', tag: 'p', text: '', note: ' ' }];
  const want = [{ attachment_id: 'b', element_id: 'hero', selector: 's', tag: 'h1', text: 'Hi', note: 'bigger' }];
  assert.deepEqual(commentsFor('b', picks), want);
  assert.deepEqual(commentsFor('b', picks, {}), want);
  assert.deepEqual(commentsFor('b', picks, { fallback: '  ' }), want);
  assert.deepEqual(commentsFor('b', picks, { fallback: 3 }), want);
});
test('with a fallback, a pick without a note is sent with the fallback note', () => {
  const picks = [{ elementId: 'hero', selector: 's', tag: 'h1', text: 'Hi', note: ' bigger ' }, { elementId: '', selector: 't', tag: 'p', text: '', note: ' ' }, { elementId: 'cta', selector: 'u', tag: 'a', text: 'Go' }];
  assert.deepEqual(commentsFor('b', picks, { fallback: 'See my message.' }).map(c => c.note), ['bigger', 'See my message.', 'See my message.']);
  assert.deepEqual(commentsFor('b', [], { fallback: 'See my message.' }), []);
});

const att = (id, version) => ({ id, name: 'a.html', title: 'A', design: 'd', version });
const helperEx = (over = {}) => ({ session_id: 's2', bot: 'copywriter', outcome: 'done', set_aside: false, attachments: [att('a2', 2)], ...over });

test('redoTarget is the newest exchange when a helper ended it with a revision', () => {
  const first = { session_id: 's1', bot: '', outcome: 'done', attachments: [att('a1', 1)] };
  const ex = helperEx();
  assert.equal(redoTarget([first, ex]), ex);
  assert.equal(redoTarget([]), null);
  assert.equal(redoTarget(undefined), null);
  assert.equal(redoTarget([ex, first]), null);
  assert.equal(redoTarget([first, helperEx({ bot: '' })]), null);
  assert.equal(redoTarget([first, helperEx({ outcome: 'pending' })]), null);
  assert.equal(redoTarget([first, helperEx({ set_aside: true })]), null);
  assert.equal(redoTarget([first, helperEx({ attachments: [] })]), null);
});

test('startedFrom is the revision before the one the exchange attached', () => {
  const first = { session_id: 's1', attachments: [att('a1', 1)] };
  const ex = helperEx();
  assert.equal(startedFrom([first, ex], ex), 'a1');
  assert.equal(startedFrom([ex], ex), null);
  assert.equal(startedFrom([first, helperEx({ attachments: [] })], { attachments: [] }), null);
  const other = { session_id: 's0', attachments: [{ ...att('z1', 1), design: 'other' }] };
  assert.equal(startedFrom([other, ex], ex), null);
});

test('startedFrom follows the harness: the same file, the highest number below, the first attachment of it', () => {
  const file = (id, name, version) => ({ id, name, title: name, design: 'd', version });
  const exs = [
    { session_id: 's1', attachments: [file('a1', 'a.html', 1)] },
    { session_id: 's2', attachments: [file('b1', 'b.html', 1)] },
    { session_id: 's3', attachments: [file('a2', 'a.html', 2)] },
    { session_id: 's4', attachments: [file('b1again', 'b.html', 1)] },
  ];
  const ex = helperEx({ attachments: [file('b2', 'b.html', 2)] });
  assert.equal(startedFrom([...exs, ex], ex), 'b1');  // file B's version 1, not file A's version 2
  const onA = helperEx({ attachments: [file('a3', 'a.html', 3)] });
  assert.equal(startedFrom([...exs, onA], onA), 'a2');
});

test('a redo leaves the composer and the pending picks alone; a message from the composer clears them', () => {
  assert.equal(clearsComposer({}), true);
  assert.equal(clearsComposer({ conversation: true }), true);
  assert.equal(clearsComposer({ conversation: false }), false);
  assert.equal(clearsComposer({ redoOf: 's2' }), false);
});

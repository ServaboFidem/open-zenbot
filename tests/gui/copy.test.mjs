// Copy under a code block takes exactly the block's text (a prompt to paste elsewhere); Copy under a
// reply takes the reply as written. Copying never throws: a refused clipboard is reported, not raised.
import test from 'node:test';
import assert from 'node:assert/strict';
import { codeText, copyText, isProse } from '../../src/gui/copy.js';

const el = (tagName, children, properties = {}) => ({ type: 'element', tagName, properties, children });
const txt = value => ({ type: 'text', value });

test('a code block copies its text without the closing newline', () => {
  const pre = el('pre', [el('code', [txt('A misty mountain range at dawn,\nsoft light, 35mm\n')], { className: ['language-text'] })]);
  assert.equal(codeText(pre), 'A misty mountain range at dawn,\nsoft light, 35mm');
});

test('highlighted spans inside a block are joined back into the plain text', () => {
  const pre = el('pre', [el('code', [el('span', [txt('const')]), txt(' x = '), el('span', [txt('1')]), txt('\n')])]);
  assert.equal(codeText(pre), 'const x = 1');
});

test('inner blank lines and indentation are kept', () => {
  const pre = el('pre', [el('code', [txt('first\n\n  indented\n')])]);
  assert.equal(codeText(pre), 'first\n\n  indented');
});

test('copyText writes to the clipboard and says whether it worked', async () => {
  const written = [];
  assert.equal(await copyText('hello', { writeText: async t => { written.push(t); } }), true);
  assert.deepEqual(written, ['hello']);
  assert.equal(await copyText('hello', { writeText: async () => { throw new Error('denied'); } }), false);
  assert.equal(await copyText('hello', undefined), false);
});

test('a block with no language, or a plain-text one, is prose and wraps; real code keeps its lines', () => {
  const block = cls => el('pre', [el('code', [txt('x\n')], cls ? { className: [cls] } : {})]);
  assert.equal(isProse(block()), true);
  for (const lang of ['language-text', 'language-plaintext', 'language-txt', 'language-prompt', 'language-markdown']) assert.equal(isProse(block(lang)), true, lang);
  for (const lang of ['language-python', 'language-js', 'language-json']) assert.equal(isProse(block(lang)), false, lang);
});

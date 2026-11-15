// Each pdf.js document owns a Web Worker until it is destroyed, so a card must destroy its document
// whatever happens: after it has drawn, when drawing fails, and when the card left while it loaded.
import test from 'node:test';
import assert from 'node:assert/strict';
import { usingPdf } from '../../src/gui/pdf.js';

const fakeDoc = () => ({ destroyed: 0, numPages: 3, async destroy() { this.destroyed++; } });

test('the document is destroyed once its use is done, and the use returns', async () => {
  const doc = fakeDoc();
  assert.equal(await usingPdf(async () => doc, async d => d.numPages), 3);
  assert.equal(doc.destroyed, 1);
});

test('the document is destroyed when drawing fails', async () => {
  const doc = fakeDoc();
  await assert.rejects(usingPdf(async () => doc, async () => { throw new Error('no canvas'); }), /no canvas/);
  assert.equal(doc.destroyed, 1);
});

test('a card that left while the PDF loaded never draws, and its document is still destroyed', async () => {
  const doc = fakeDoc();
  let used = false;
  await usingPdf(async () => doc, async () => { used = true; }, () => false);
  assert.equal(used, false);
  assert.equal(doc.destroyed, 1);
});

test('a PDF that fails to load rejects with nothing to destroy', async () => {
  await assert.rejects(usingPdf(async () => { throw new Error('404'); }, async () => {}), /404/);
});

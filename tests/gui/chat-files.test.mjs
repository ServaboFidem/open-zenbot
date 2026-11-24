// Files added to a chat message: what the composer accepts before the harness sees them, the chips, when
// Send waits, and the line about a model that cannot see pictures.
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  MAX_FILES, MAX_PICTURES, PICTURE_UPLOAD_MAX, DOCUMENT_MAX, DOCUMENT_SUFFIXES, REFUSED, TOO_LARGE, TOO_MANY, TOO_MANY_PICTURES, WAIT_UPLOADS,
  precheck, addChips, chipUploaded, chipRefused, removeChip, afterSend, sendable, stillSendable, holdLine, blindLine, sizeLabel, toBase64, uploadName, sentFiles,
} from '../../src/gui/chat-files.js';

const png = (name = 'a.png', size = 1000) => ({ name, size, type: 'image/png' });
const pdf = (name = 'r.pdf', size = 1000) => ({ name, size, type: 'application/pdf' });

test('the limits and the document suffixes equal the harness', () => {
  assert.equal(MAX_FILES, 10); assert.equal(MAX_PICTURES, 4);
  assert.equal(PICTURE_UPLOAD_MAX, 50_000_000); assert.equal(DOCUMENT_MAX, 20_000_000);
  assert.deepEqual([...DOCUMENT_SUFFIXES].sort(), ['.csv', '.docx', '.epub', '.json', '.markdown', '.md', '.pdf', '.pptx', '.text', '.txt', '.xlsx']);
});

test('precheck: pictures by type, documents by suffix, sizes by kind', () => {
  assert.equal(precheck(png()), null);
  assert.equal(precheck({ name: 'IMG_0001.JPG', size: 4_000_000, type: 'image/jpeg' }), null);
  assert.equal(precheck({ name: 'a.webp', size: 10, type: 'image/webp' }), null);
  assert.equal(precheck({ name: 'cat.gif', size: 10, type: 'image/gif' }), null);  // the harness decides on animation
  assert.equal(precheck(pdf()), null);
  assert.equal(precheck({ name: 'Notes.MD', size: 10, type: '' }), null);
  assert.equal(precheck({ name: 'song.mp3', size: 10, type: 'audio/mpeg' }), REFUSED);
  assert.equal(precheck({ name: 'page.html', size: 10, type: 'text/html' }), REFUSED);
  assert.equal(precheck({ name: 'feed.xml', size: 10, type: 'text/xml' }), REFUSED);
  assert.equal(precheck({ name: 'logo.svg', size: 10, type: 'image/svg+xml' }), REFUSED);
  assert.equal(precheck({ name: 'noext', size: 10, type: '' }), REFUSED);
  assert.equal(precheck(png('big.png', 50_000_000)), null);
  assert.equal(precheck(png('big.png', 50_000_001)), TOO_LARGE);
  assert.equal(precheck(pdf('big.pdf', 20_000_000)), null);
  assert.equal(precheck(pdf('big.pdf', 20_000_001)), TOO_LARGE);
  assert.equal(precheck({ name: 'empty.txt', size: 0, type: 'text/plain' }), null);
});

test('addChips: one chip per file, uploading or refused with its sentence, past ten refused', () => {
  const one = addChips([], [png(), { name: 'song.mp3', size: 5, type: 'audio/mpeg' }]);
  assert.equal(one.length, 2);
  assert.equal(one[0].state, 'uploading'); assert.equal(one[0].name, 'a.png'); assert.equal(one[0].size, 1000);
  assert.equal(one[1].state, 'refused'); assert.equal(one[1].error, REFUSED);
  assert.notEqual(one[0].key, one[1].key);
  const ten = addChips([], Array.from({ length: 10 }, (_, i) => pdf(`f${i}.pdf`)));
  assert.ok(ten.every(c => c.state === 'uploading'));
  const eleven = addChips(ten, [pdf('f10.pdf')]);
  assert.equal(eleven.length, 11);
  assert.equal(eleven[10].state, 'refused'); assert.equal(eleven[10].error, TOO_MANY);
  // refused chips do not count toward the ten
  const withRefused = addChips(addChips([], Array.from({ length: 9 }, (_, i) => pdf(`f${i}.pdf`))), [{ name: 'x.mp3', size: 1, type: '' }, pdf('g.pdf')]);
  assert.equal(withRefused.at(-1).state, 'uploading');
  assert.equal(new Set(eleven.map(c => c.key)).size, 11);
});

test('a chip becomes ready with the harness answer, or refused with its sentence; removing one drops it', () => {
  let chips = addChips([], [png(), pdf()]);
  const [a, b] = chips.map(c => c.key);
  chips = chipUploaded(chips, a, { id: 'u1', name: 'a.webp', kind: 'image', size: 300 });
  chips = chipRefused(chips, b, 'This file is too large.');
  assert.deepEqual({ ...chips[0], key: undefined }, { key: undefined, name: 'a.png', size: 1000, state: 'ready', id: 'u1', kind: 'image' });
  assert.equal(chips[1].state, 'refused'); assert.equal(chips[1].error, 'This file is too large.');
  assert.equal(removeChip(chips, a).length, 1);
  assert.equal(chipUploaded(chips, 'gone', { id: 'x' }), chips);  // a chip removed while it uploaded stays removed
});

test('sendable: held while any chip uploads; ids of the ready ones; pictures counted', () => {
  let chips = addChips([], [png('1.png'), png('2.png'), pdf(), { name: 'x.mp3', size: 1, type: '' }]);
  assert.deepEqual(sendable([]), { held: false, ids: [], pictures: 0 });
  assert.equal(sendable(chips).held, true);
  assert.equal(holdLine(chips), WAIT_UPLOADS);
  chips = chipUploaded(chips, chips[0].key, { id: 'p1', kind: 'image' });
  chips = chipUploaded(chips, chips[1].key, { id: 'p2', kind: 'image' });
  assert.equal(sendable(chips).held, true);
  chips = chipUploaded(chips, chips[2].key, { id: 'd1', kind: 'document' });
  assert.deepEqual(sendable(chips), { held: false, ids: ['p1', 'p2', 'd1'], pictures: 2 });
  assert.equal(holdLine(chips), null);
});

test('holdLine: more than four pictures waits with the harness sentence', () => {
  let chips = addChips([], Array.from({ length: 5 }, (_, i) => png(`${i}.png`)));
  chips = chips.reduce((c, ch, i) => chipUploaded(c, ch.key, { id: `p${i}`, kind: 'image' }), chips);
  assert.equal(sendable(chips).pictures, 5);
  assert.equal(holdLine(chips), TOO_MANY_PICTURES);
  assert.equal(TOO_MANY_PICTURES, 'At most 4 pictures a message.');
});

test('afterSend drops the chips that went and the refused ones, keeps one added meanwhile', () => {
  let chips = addChips([], [png(), { name: 'x.mp3', size: 1, type: '' }]);
  chips = chipUploaded(chips, chips[0].key, { id: 'p1', kind: 'image' });
  chips = addChips(chips, [pdf()]);
  const left = afterSend(chips, ['p1']);
  assert.deepEqual(left.map(c => c.name), ['r.pdf']);
});

test('blindLine: only for a ready picture on a bot whose model is not known to see', () => {
  let chips = addChips([], [png(), pdf()]);
  const bot = { name: 'Writer', sees_images: false };
  assert.equal(blindLine(bot, chips), null);  // nothing ready yet
  chips = chipUploaded(chips, chips[1].key, { id: 'd1', kind: 'document' });
  assert.equal(blindLine(bot, chips), null);  // a document only
  chips = chipUploaded(chips, chips[0].key, { id: 'p1', kind: 'image' });
  assert.equal(blindLine(bot, chips), "Writer's model can't see pictures. It can still work with the file.");
  assert.equal(blindLine({ name: 'Writer', sees_images: null }, chips), "Writer's model can't see pictures. It can still work with the file.");
  assert.equal(blindLine({ name: 'Writer', sees_images: true }, chips), null);
  assert.equal(blindLine(null, chips), null);
});

test('sizeLabel, uploadName and toBase64', () => {
  assert.equal(sizeLabel(0), '0 bytes'); assert.equal(sizeLabel(1), '1 byte'); assert.equal(sizeLabel(900), '900 bytes');
  assert.equal(sizeLabel(2048), '2 KB'); assert.equal(sizeLabel(1_500_000), '1.4 MB');
  assert.equal(uploadName({ name: 'a.png', type: 'image/png' }), 'a.png');
  assert.equal(uploadName({ name: '', type: 'image/jpeg' }), 'pasted-picture.jpg');
  assert.equal(uploadName({ name: '', type: 'image/png' }), 'pasted-picture.png');
  const bytes = new Uint8Array(100_000).map((_, i) => i % 256);
  assert.equal(toBase64(bytes.buffer), Buffer.from(bytes).toString('base64'));
  assert.equal(toBase64(new ArrayBuffer(0)), '');
});

test('sentFiles: pictures as image cards, documents as files to save, from the upload route', () => {
  const url = id => `http://h/uploads/${id}`;
  const out = sentFiles([{ id: 'p1', name: 'cat.webp', kind: 'image' }, { id: 'd1', name: 'report.pdf', kind: 'document' }], url);
  assert.equal(out.length, 2);
  assert.equal(out[0].kind, 'image'); assert.equal(out[0].src, 'http://h/uploads/p1'); assert.equal(out[0].name, 'cat.webp');
  assert.equal(out[1].kind, 'file'); assert.equal(out[1].href, 'http://h/uploads/d1'); assert.equal(out[1].name, 'report.pdf');
  assert.deepEqual(sentFiles(undefined, url), []);
});

test('precheck: a picture with no type is known by its name, under the picture limit', () => {
  assert.equal(precheck({ name: 'shot.png', size: 30_000_000, type: '' }), null);
  assert.equal(precheck({ name: 'IMG.JPEG', size: 10, type: '' }), null);
  for (const name of ['a.jpg', 'a.webp', 'a.gif']) assert.equal(precheck({ name, size: 10, type: '' }), null);
  assert.equal(precheck({ name: 'shot.png', size: 50_000_001, type: '' }), TOO_LARGE);
  assert.equal(precheck({ name: 'setup.exe', size: 10, type: '' }), REFUSED);
  assert.equal(precheck({ name: 'logo.svg', size: 10, type: '' }), REFUSED);
});

test('stillSendable: of the ids captured, only those still ready go; one added meanwhile does not', () => {
  const ready = (key, id) => ({ key, name: key, size: 1, state: 'ready', id, kind: 'document' });
  const chips = [ready('a', 'id-a'), ready('c', 'id-c')];
  assert.deepEqual(stillSendable(['id-a', 'id-b'], chips), ['id-a']);  // id-b was removed during the await
  assert.deepEqual(stillSendable([], chips), []);  // id-c was added meanwhile: not part of this message
  assert.deepEqual(stillSendable(['id-a'], [{ key: 'a', state: 'uploading' }]), []);
  assert.deepEqual(stillSendable(undefined, chips), []);
});

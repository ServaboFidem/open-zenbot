// Files added to a chat message: what the composer takes before the harness sees them (the harness has the
// last word: it refuses animated pictures and checks the bytes), the chips under the message, when Send
// waits, and the line about a model that cannot see pictures. Pure: no React, no DOM.

export const MAX_FILES = 10;
export const MAX_PICTURES = 4;
export const PICTURE_UPLOAD_MAX = 50_000_000;
export const DOCUMENT_MAX = 20_000_000;
// equal to the harness's chat_files.DOCUMENT_SUFFIXES (pages such as .html and .xml are never taken)
export const DOCUMENT_SUFFIXES = ['.csv', '.docx', '.epub', '.json', '.markdown', '.md', '.pdf', '.pptx', '.text', '.txt', '.xlsx'];
export const PICTURE_TYPES = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp', 'image/gif': 'gif' };

export const REFUSED = "ZenBot can't work with this kind of file yet.";
export const TOO_LARGE = 'This file is too large.';
export const TOO_MANY = `At most ${MAX_FILES} files a message.`;
export const TOO_MANY_PICTURES = `At most ${MAX_PICTURES} pictures a message.`;
export const WAIT_UPLOADS = 'Wait for the files to finish uploading.';

const suffix = name => { const m = /\.[^./\\]+$/.exec(String(name || '')); return m ? m[0].toLowerCase() : ''; };
const isPicture = file => Object.hasOwn(PICTURE_TYPES, String(file?.type || '').toLowerCase());

/** The sentence a file is refused with before it is sent, or null when it may go to the harness. */
export function precheck(file) {
  if (isPicture(file)) return file.size > PICTURE_UPLOAD_MAX ? TOO_LARGE : null;
  if (DOCUMENT_SUFFIXES.includes(suffix(file?.name))) return file.size > DOCUMENT_MAX ? TOO_LARGE : null;
  return REFUSED;
}

/** The name a file is uploaded under: its own, or one from its type for a pasted picture that has none. */
export function uploadName(file) {
  const name = String(file?.name || '').trim();
  if (name) return name;
  return `pasted-picture.${PICTURE_TYPES[String(file?.type || '').toLowerCase()] || 'png'}`;
}

let seq = 0;
const counts = c => c.state !== 'refused';

/** The chips with one more per file: uploading, or refused with its sentence (past MAX_FILES too). */
export function addChips(chips, files) {
  const out = [...chips];
  for (const file of files) {
    const chip = { key: `f${++seq}`, name: uploadName(file), size: Number(file?.size) || 0 };
    const error = precheck(file) || (out.filter(counts).length >= MAX_FILES ? TOO_MANY : null);
    out.push(error ? { ...chip, state: 'refused', error } : { ...chip, state: 'uploading' });
  }
  return out;
}

const change = (chips, key, fn) => (chips.some(c => c.key === key) ? chips.map(c => (c.key === key ? fn(c) : c)) : chips);

/** The harness took the file: the chip is ready with its id and kind. A chip no longer there stays gone. */
export const chipUploaded = (chips, key, answer) => change(chips, key, c => ({ key: c.key, name: c.name, size: c.size, state: 'ready', id: answer.id, kind: answer.kind }));

/** The harness refused the file: the chip shows its sentence. */
export const chipRefused = (chips, key, error) => change(chips, key, c => ({ key: c.key, name: c.name, size: c.size, state: 'refused', error: error || REFUSED }));

export const removeChip = (chips, key) => chips.filter(c => c.key !== key);

/** Once the harness took the message: the chips that went with it, and the refused ones, are gone. */
export const afterSend = (chips, ids) => chips.filter(c => c.state !== 'refused' && !(c.state === 'ready' && ids.includes(c.id)));

/** What a send would carry: held while any chip uploads; the ready ids; how many are pictures. */
export function sendable(chips) {
  const ready = chips.filter(c => c.state === 'ready');
  return { held: chips.some(c => c.state === 'uploading'), ids: ready.map(c => c.id), pictures: ready.filter(c => c.kind === 'image').length };
}

/** Why Send waits for the files, or null. */
export function holdLine(chips) {
  const s = sendable(chips);
  if (s.held) return WAIT_UPLOADS;
  if (s.pictures > MAX_PICTURES) return TOO_MANY_PICTURES;
  return null;
}

/** The note under the composer when a picture is ready for a bot whose model is not known to see pictures. */
export function blindLine(bot, chips) {
  if (!bot || bot.sees_images === true) return null;
  if (!chips.some(c => c.state === 'ready' && c.kind === 'image')) return null;
  return `${bot.name}'s model can't see pictures. It can still work with the file.`;
}

export function sizeLabel(bytes) {
  const n = Number(bytes) || 0;
  if (n < 1024) return `${n} ${n === 1 ? 'byte' : 'bytes'}`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

/** Base64 of a file's bytes, in pieces so a large file never overflows the call stack. */
export function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let text = '';
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(text);
}

/** The files a sent message carried, as cards: pictures as images, documents as files to save. */
export function sentFiles(files, urlFor) {
  return (files || []).map(f => {
    const url = urlFor(f.id);
    const name = String(f.name || '');
    return { kind: f.kind === 'image' ? 'image' : 'file', src: url, href: url, name, caption: [{ text: name }], local: true };
  });
}

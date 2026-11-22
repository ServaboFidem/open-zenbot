// The design pane's unsaved changes ("pending"): text, pictures and tweaks the user has set directly. Pure
// decisions only; the pane holds the state, the frame shows it, and nothing is saved until the user saves.

export const EMPTY = { texts: {}, images: {}, tweaks: {} };
export const HOLD = 'Save or discard your changes first.';
export const MAX_PICTURE_BYTES = 1500000;
const PICTURE_TYPES = ['image/png', 'image/jpeg', 'image/webp', 'image/gif'];

// What the pick points at, as the harness's slots answer lists it, or null when it is no slot.
export function slotFor(slots, pick) {
  const id = pick?.elementId;
  if (!id || !slots) return null;
  const text = (slots.texts || []).find(t => t.id === id);
  if (text) return { kind: 'text', id, text: text.text };
  const image = (slots.images || []).find(i => i.id === id);
  if (image) return { kind: 'image', id, name: image.name, alt: image.alt };
  return null;
}

const without = (map, key) => { const { [key]: _, ...rest } = map; return rest; };

export function setText(pending, slots, id, text) {
  const saved = (slots?.texts || []).find(t => t.id === id)?.text;
  return { ...pending, texts: text === saved ? without(pending.texts, id) : { ...pending.texts, [id]: text } };
}

export function setImage(pending, id, { name, dataUrl, alt }) {
  return { ...pending, images: { ...pending.images, [id]: { name, dataUrl, alt: alt ?? null } } };
}

export function setTweak(pending, slots, name, value) {
  const saved = (slots?.tweaks || []).find(k => k.var === name)?.value;
  return { ...pending, tweaks: value === saved ? without(pending.tweaks, name) : { ...pending.tweaks, [name]: value } };
}

export const count = pending =>
  Object.keys(pending.texts).length + Object.keys(pending.images).length + Object.keys(pending.tweaks).length;

// `version` is the newest saved version; a save makes the next one.
export const saveLabel = (pending, version) => `Save as Version ${version + 1}`;

export const cssValue = (knob, value) => knob.type === 'range' ? `${value}${knob.unit || ''}` : String(value);

// The bridge messages that show the whole pending state, after a reset to the saved design.
export function frameMessages(pending, slots) {
  const out = [{ zen: 'reset' }];
  for (const [elementId, text] of Object.entries(pending.texts)) out.push({ zen: 'text', elementId, text });
  for (const [elementId, p] of Object.entries(pending.images)) out.push({ zen: 'image', elementId, src: p.dataUrl, alt: p.alt ?? '' });
  const vars = {};
  for (const [name, value] of Object.entries(pending.tweaks)) {
    const knob = (slots?.tweaks || []).find(k => k.var === name);
    if (knob) vars[name] = cssValue(knob, value);
  }
  if (Object.keys(vars).length) out.push({ zen: 'vars', vars });
  return out;
}

// The POST /changes body: a picture goes as bare base64, never as a data: URL.
export function body(pending, version) {
  const images = {};
  for (const [id, p] of Object.entries(pending.images))
    images[id] = { name: p.name, data: p.dataUrl.slice(p.dataUrl.indexOf(',') + 1), alt: p.alt ?? null };
  return { version, texts: { ...pending.texts }, images, tweaks: { ...pending.tweaks } };
}

export const canChange = ({ onNewest, ready, disabled, comparing }) => Boolean(onNewest && ready && !disabled && !comparing);

export function whyNot({ onNewest, ready, disabled, comparing, newestLabel = 'the newest version' }) {
  if (disabled) return 'Wait for the task to finish before commenting.';
  if (comparing) return 'Close Compare to change the design.';
  if (!onNewest) return `Comments go on the newest version. Switch to ${newestLabel} to comment.`;
  if (!ready) return 'The design is still loading.';
  return '';
}

export const holdsSend = pending => count(pending) > 0;

export function pictureProblem(file) {
  if (!file || !PICTURE_TYPES.includes(file.type)) return 'Use a PNG, JPEG, WebP or GIF picture.';
  if (file.size > MAX_PICTURE_BYTES) return 'That picture is over 1,500,000 bytes.';
  return '';
}

// Previous and newest revision of the newest design (the same one twice when there is only one).
export function compareDefaults(revisions) {
  const newest = revisions[revisions.length - 1];
  if (!newest) return [];
  const same = revisions.filter(r => r.design === newest.design);
  const previous = same[same.length - 2] || newest;
  return [previous.attId, newest.attId];
}

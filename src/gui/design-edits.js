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

// A colour equal to the saved one in any case (the colour input answers lower-case) is no change.
export function setTweak(pending, slots, name, value) {
  const knob = (slots?.tweaks || []).find(k => k.var === name);
  const colour = knob?.type === 'color' && typeof value === 'string' && typeof knob.value === 'string';
  const same = knob !== undefined && (colour ? value.toLowerCase() === knob.value.toLowerCase() : value === knob.value);
  return { ...pending, tweaks: same ? without(pending.tweaks, name) : { ...pending.tweaks, [name]: value } };
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

// `loaded` with no `ready`: the frame loaded but its bridge never answered, as for a file over 5 MB (served without it).
export function whyNot({ onNewest, ready, loaded, disabled, comparing, newestLabel = 'the newest version' }) {
  if (disabled) return 'Wait for the task to finish before changing the design.';
  if (comparing) return 'Close Compare to change the design.';
  if (!onNewest) return `Changes go on the newest version. Switch to ${newestLabel} to change it.`;
  if (!ready) return loaded ? 'Changing is off for this file: it is too large.' : 'The design is still loading.';
  return '';
}

export const holdsSend = pending => count(pending) > 0;

// The hidden pane's button that opens it again while changes wait; empty when none do.
export const unsavedLabel = n => n > 0 ? `${n} change${n === 1 ? '' : 's'} not saved` : '';

// Why Save is off, or '' when it may go. A slots answer for a newer version than the pane holds means the harness made a
// version (a catch-up) after these changes were made: saving them on it would overwrite that version unseen.
export const DESIGN_CHANGED = 'The design has changed. Reload and try again.';
export function saveWhy({ disabled, slots, newest }) {
  if (disabled) return 'Wait for the task to finish before changing the design.';
  if (slotsBehind(slots, newest)) return DESIGN_CHANGED;
  return '';
}

// Pending changes are kept per conversation (`map`: conversation id -> { pending, newest }), each with the attachment id
// of the newest revision they were made on. Leaving the conversation and coming back keeps them; a new revision there (a
// save, a restore, the Designer's) or a discard ends them.
const has = (map, convId) => !!convId && Object.prototype.hasOwnProperty.call(map, convId);
export function pendingFor(map, convId, newest) {
  const entry = has(map, convId) ? map[convId] : null;
  return entry && entry.newest === newest ? entry.pending : EMPTY;
}
export const dropPending = (map, convId) => has(map, convId) ? without(map, convId) : map;
export function withPending(map, convId, newest, pending) {
  if (!convId) return map;
  return count(pending) ? { ...map, [convId]: { pending, newest } } : dropPending(map, convId);
}
// The conversation's entry once its newest revision is no longer the one the changes were made on; else the same map.
export const dropStale = (map, convId, newest) => has(map, convId) && map[convId].newest !== newest ? dropPending(map, convId) : map;

export function pictureProblem(file) {
  if (!file || !PICTURE_TYPES.includes(file.type)) return 'Use a PNG, JPEG, WebP or GIF picture.';
  if (file.size > MAX_PICTURE_BYTES) return 'That picture is over 1,500,000 bytes.';
  return '';
}

// Restore acts on the newest revision's file, so it is offered only on an older version of that same file (design and
// file name: one design can hold two files), and not while comparing.
export const restorable = (shown, newest, comparing) =>
  !comparing && !!shown && !!newest && shown.attId !== newest.attId && shown.design === newest.design && shown.name === newest.name;

// The slots answer names another version than the newest the pane holds: the harness made one the pane has not seen.
export const slotsBehind = (slots, newest) => !!slots && !!newest && slots.version !== newest.version;

// Previous and newest revision of the newest design (the same one twice when there is only one).
export function compareDefaults(revisions) {
  const newest = revisions[revisions.length - 1];
  if (!newest) return [];
  const same = revisions.filter(r => r.design === newest.design);
  const previous = same[same.length - 2] || newest;
  return [previous.attId, newest.attId];
}

// The Export menu on the version shown: HTML always; ZIP (the working file and its pictures) only on the newest
// version, and not while a task runs (the harness answers 409 then); PDF and PNG only in the desktop app. `name` is the
// suggested file name (for PDF and PNG the main process adds the extension to the title); a title that is a file name
// loses its `.html` or `.htm` first, so no name ends in two extensions.
export const WAIT_FOR_TASK = 'Wait for the task to finish.';
export function exportItems({ isNewest, hasBridge, disabled, title }) {
  const base = String(title || '').trim().replace(/\.html?$/i, '').trim() || 'design';
  return [
    { kind: 'html', label: 'HTML', name: `${base}.html` },
    ...(isNewest ? [{ kind: 'zip', label: 'ZIP (page and pictures)', name: `${base}.zip`, ...(disabled ? { why: WAIT_FOR_TASK } : {}) }] : []),
    ...(hasBridge ? [{ kind: 'pdf', label: 'PDF', name: base }, { kind: 'png', label: 'PNG', name: base }] : []),
  ];
}

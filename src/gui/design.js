// The design pane's pure half. The framed design is made by a bot and is untrusted: acceptPick is the
// trust boundary, and a pick it lets through only ever becomes a chip the user sees and notes. Pure:
// no React, no DOM, so it loads under node --test.

const TAG = /^[a-z][a-z0-9-]{0,39}$/;
const PICK_KEYS = ['elementId', 'rect', 'selector', 'tag', 'text', 'zen'];
const RECT_KEYS = ['h', 'w', 'x', 'y'];

const isObj = v => !!v && typeof v === 'object' && !Array.isArray(v);
const sameKeys = (o, keys) => { const k = Object.keys(o).sort(); return k.length === keys.length && k.every((x, i) => x === keys[i]); };
const str = (v, min, max) => typeof v === 'string' && v.length >= min && v.length <= max;

// Every design revision in the conversation, oldest first; a plain attachment has no version. Attaching an
// unchanged file again gives the same copy: one entry per (design, file name, version), its latest attachment, in
// its place. Copies are numbered per file, so two files in one design can each have a version 1.
export function designRevisions(exchanges) {
  const byRev = new Map();
  for (const ex of exchanges || [])
    for (const a of ex.attachments || [])
      if (typeof a.version === 'number') {
        const key = JSON.stringify([a.design, a.name, a.version]);
        byRev.delete(key);  // re-inserted, so the latest attachment takes the newest place
        byRev.set(key, { attId: a.id, title: a.title, design: a.design, version: a.version });
      }
  return [...byRev.values()];
}

export const studioFrameUrl = src => `${src}${src.includes('?') ? '&' : '?'}studio=1`;

// A pick from the pane's own frame, in exactly the shape the bridge sends, or null.
export function acceptPick(event, frameWindow) {
  if (!event || !frameWindow || event.source !== frameWindow) return null;
  const d = event.data;
  if (!isObj(d) || !sameKeys(d, PICK_KEYS) || d.zen !== 'pick') return null;
  if (!str(d.elementId, 0, 120) || !str(d.selector, 1, 300) || !str(d.text, 0, 160) || typeof d.tag !== 'string' || !TAG.test(d.tag)) return null;
  const r = d.rect;
  if (!isObj(r) || !sameKeys(r, RECT_KEYS) || !RECT_KEYS.every(k => typeof r[k] === 'number' && Number.isFinite(r[k]))) return null;
  return { elementId: d.elementId, selector: d.selector, tag: d.tag, text: d.text, rect: { x: r.x, y: r.y, w: r.w, h: r.h } };
}

// The frame's "my script runs" signal, again only from the pane's own frame and in its exact shape.
export const acceptReady = (event, frameWindow) =>
  !!event && !!frameWindow && event.source === frameWindow && isObj(event.data) && sameKeys(event.data, ['zen']) && event.data.zen === 'ready';

// One message carries at most this many comments (the harness refuses more); later picks are ignored.
export const MAX_PICKS = 20;
export const addPick = (picks, pick) => picks.length >= MAX_PICKS ? picks : [...picks, { ...pick, note: '' }];

// The picks as the task's design_comments on that revision. A pick with no note takes `fallback` (when the
// user wrote a message, the note points at it) or, with no fallback, is left out.
export function commentsFor(attId, picks, { fallback } = {}) {
  const spare = typeof fallback === 'string' ? fallback.trim() : '';
  return (picks || [])
    .map(p => ({ attachment_id: attId, element_id: p.elementId, selector: p.selector, tag: p.tag, text: p.text, note: String(p.note || '').trim() || spare }))
    .filter(c => c.note);
}

// The exchange a helper answered that the user may send back to the owner: the newest one, a helper's, ended, not
// set aside, and it attached a design revision. Else null.
export function redoTarget(exchanges) {
  const ex = (exchanges || []).at(-1);
  if (!ex || !ex.bot || !ex.outcome || ex.outcome === 'pending' || ex.set_aside) return null;
  return designRevisions([ex]).length ? ex : null;
}

// The attachment id of the version a helper's change started from, by the harness's rule (`_redo_target`): of the
// same design and file name as the copy `exchange` attached (its first), the one with the highest version below it,
// taking the first attachment of that version; else null.
export function startedFrom(exchanges, exchange) {
  const mine = (exchange?.attachments || []).find(a => typeof a.version === 'number');
  if (!mine) return null;
  let best = null;
  for (const ex of exchanges || []) {
    if (ex === exchange) break;
    for (const a of ex.attachments || [])
      if (typeof a.version === 'number' && a.design === mine.design && a.name === mine.name && a.version < mine.version
          && (!best || a.version > best.version)) best = a;
  }
  return best?.id ?? null;
}

// Whether starting a task clears the composer and the pending picks: only a message the user wrote there. A routine
// joins no conversation, and a redo re-sends an earlier message, so both leave what the user is writing alone.
export const clearsComposer = ({ conversation = true, redoOf } = {}) => conversation && !redoOf;

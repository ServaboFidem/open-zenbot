// Question forms: a bot's reply may end with a fenced `question-form` block of JSON. The JSON is
// untrusted, so the parser returns a fresh object of known keys only, or null; it never throws.
// Pure helpers, so they test without a DOM.

export const TYPES = ['radio', 'checkbox', 'select', 'text', 'textarea', 'range', 'color'];
const CHOICE = new Set(['radio', 'checkbox', 'select']);
const MAX_TEXT = 20000;

// Bot-written labels and ids end up in the user's message, so a line break or control character
// (which could forge a line there) fails the whole form.
const CONTROL = /[\u0000-\u001f\u007f\u2028\u2029]/;
const str = (v, min, max) => typeof v === 'string' && v.length >= min && v.length <= max;
const plain = (v, min, max) => str(v, min, max) && !CONTROL.test(v);
const num = v => typeof v === 'number' && Number.isFinite(v);

function question(q) {
  if (!q || typeof q !== 'object' || Array.isArray(q)) return null;
  if (!plain(q.id, 1, 64) || !plain(q.label, 1, 200) || !TYPES.includes(q.type)) return null;
  const out = { id: q.id, label: q.label, type: q.type };
  if (CHOICE.has(q.type)) {
    if (!Array.isArray(q.options) || q.options.length < 2 || q.options.length > 12 || !q.options.every(o => plain(o, 1, 200))) return null;
    out.options = [...q.options];
  }
  if (q.type === 'range') {
    if (!num(q.min) || !num(q.max) || q.min >= q.max) return null;
    if (q.step !== undefined && !(num(q.step) && q.step > 0)) return null;
    out.min = q.min;
    out.max = q.max;
    if (q.step !== undefined) out.step = q.step;
  }
  if (q.allow_custom !== undefined) {
    if (typeof q.allow_custom !== 'boolean') return null;
    out.allow_custom = q.allow_custom;
  }
  // A default that does not fit is dropped, not fatal.
  const d = q.default;
  if (q.type === 'checkbox' ? Array.isArray(d) && d.every(x => typeof x === 'string')
    : q.type === 'range' ? num(d)
      : q.type === 'color' ? typeof d === 'string' && /^#[0-9a-fA-F]{6}$/.test(d)
        : typeof d === 'string') out.default = Array.isArray(d) ? [...d] : d;
  return out;
}

/** A valid form (a clean copy) from a block's text, or null. */
export function parseQuestionForm(text) {
  if (typeof text !== 'string' || text.length > MAX_TEXT) return null;
  let raw;
  try { raw = JSON.parse(text); } catch { return null; }
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  if (!plain(raw.id, 1, 64)) return null;
  if (raw.title !== undefined && !plain(raw.title, 0, 120)) return null;
  if (!Array.isArray(raw.questions) || raw.questions.length < 1 || raw.questions.length > 5) return null;
  const questions = raw.questions.map(question);
  if (questions.some(q => !q) || new Set(questions.map(q => q.id)).size !== questions.length) return null;
  const out = { id: raw.id };
  if (raw.title !== undefined) out.title = raw.title;
  out.questions = questions;
  return out;
}

/** Starting answers: each question's default, else empty. */
export function initialValues(form) {
  const v = {};
  for (const q of form.questions) v[q.id] = q.default ?? (q.type === 'checkbox' ? [] : q.type === 'range' ? q.min : '');
  return v;
}

const oneLine = s => String(s).replace(/\s*[\r\n\u2028\u2029]+\s*/g, ' ').trim();

/** The answers as the user's next message. `others` holds each question's custom "Other" text. */
export function answersMessage(form, values, others = {}) {
  const lines = form.questions.map(q => {
    const v = values?.[q.id];
    const custom = oneLine(others?.[q.id] ?? '');
    let a;
    if (q.type === 'checkbox') a = [...(Array.isArray(v) ? v : []).map(oneLine).filter(Boolean), ...(custom ? [custom] : [])].join(', ');
    else if (q.type === 'textarea') a = String(v ?? '').trim().split(/\r?\n/).join('\n  ');
    else a = custom || (v === undefined || v === null ? '' : oneLine(v));
    return `- ${oneLine(q.label)}: ${a || '(no answer)'}`;
  });
  return `Form answers (${oneLine(form.id)}):\n${lines.join('\n')}`;
}

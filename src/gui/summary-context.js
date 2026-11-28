// What the owner is told when the summary model's context is too small: the chat notice, the warning on
// the Summary model card and the dialog's facts and advice. Decisions only; the components draw them.
// status is GET /settings/models/summary/context.

export const NOTICE = "Earlier turns could not be summarised: the summary model's context is too small.";
export const NOTICE_LINK = 'What to change…';
export const CARD_LINK = 'Details…';
export const HOSTED_LINE = 'This summary model runs on a hosted provider; its context is not read.';
export const MORE_MEMORY = 'A larger context uses more memory.';

const num = n => Number(n).toLocaleString('en-US');
export const tokens = n => `${num(n)} tokens`;
const known = n => Number.isFinite(n) && n > 0;
const hosted = s => !s || !s.provider || s.server === 'hosted';

// The one line under the Summary model picker, or null.
export function cardLine(s) {
  if (hosted(s)) return null;
  if (s.warning === 'overrun') return 'Summary model: the last compaction did not fit its context.';
  if (s.warning === 'at_max') return `Summary model: loaded at ${num(s.loaded_context)} tokens, the most this model supports. Long tasks are summarised in many small pieces.`;
  if (s.warning === 'small') return `Summary model: loaded at ${num(s.loaded_context)} tokens. Long tasks are summarised in many small pieces.`;
  return null;
}

const altNote = a => `${a.enabled ? 'enabled' : 'enable it on the Providers tab first'}${a.loaded ? '' : ' (not loaded)'}`;

// The context the model was last seen at: loaded, else learned from a refusal, else the refused request's.
const seenContext = s => [s.loaded_context, s.learned_context, s.last_overrun?.n_ctx].find(known);

// True when the model cannot be given more: its maximum is no more than what it has seen; the recommended size
// stands in for the maximum only while the maximum is unknown.
function atMaximum(s) {
  const seen = seenContext(s);
  if (!known(seen)) return false;
  if (known(s.max_context)) return s.max_context <= seen;
  return known(s.recommended_context) && s.recommended_context <= seen;
}

// True when the model is loaded at or above the recommended size, so no LM Studio change is advised.
const loadedEnough = s => known(s.loaded_context) && known(s.recommended_context) && s.loaded_context >= s.recommended_context;

// The dialog's label/value rows, advice lines and the models that could replace this one.
// Shown as "28 Nov 03:11" (no comma). `timeZone` is for tests; the default is local time.
export const stamp = (at, timeZone) => {
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return '';
  const p = Object.fromEntries(new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone }).formatToParts(d).map(x => [x.type, x.value]));
  return `${p.day} ${p.month} ${p.hour}:${p.minute}`;
};

// `format` turns an ISO time into the text shown (tests pass their own; the default is local time).
export function advice(s, format = at => stamp(at)) {
  if (hosted(s)) return { facts: [], lines: [HOSTED_LINE], alternatives: [] };
  const o = s.last_overrun;
  const facts = [
    ['Context loaded', known(s.loaded_context) ? tokens(s.loaded_context) : (s.server === 'lmstudio' ? 'not loaded' : 'unknown')],
    ['Most it supports', known(s.max_context) ? tokens(s.max_context) : 'unknown'],
  ];
  if (o) {
    const when = o.at ? format(o.at) : '';
    facts.push(['Last refused', `${known(o.n_prompt_tokens) ? `a ${num(o.n_prompt_tokens)}-token request` : 'a request'}${when ? `, ${when},` : ''}${o.where === 'fold' ? ' in a conversation summary' : ' in a task'}`]);
  }
  if (known(s.learned_context) && !known(s.loaded_context)) facts.push(['Learned limit', tokens(s.learned_context)]);

  const lines = [];
  let alternatives = [];
  if (s.server === 'other') {
    if (o && known(o.n_prompt_tokens) && known(o.n_ctx)) lines.push(`The server refused a request of ${num(o.n_prompt_tokens)} tokens with a context of ${num(o.n_ctx)}. Give the model a larger context on that server, or pick another summary model.`);
    else if (known(s.learned_context)) lines.push(`The server's learned limit is ${num(s.learned_context)} tokens. Give the model a larger context on that server, or pick another summary model.`);
  } else if (atMaximum(s)) {
    lines.push(`This model cannot take more than ${num(s.max_context ?? s.recommended_context)} tokens, so no LM Studio setting will help.`, 'Pick a summary model that supports more.');
  } else if (!loadedEnough(s) && known(s.recommended_context) && (s.warning || o)) {
    lines.push(`In LM Studio, open the model's load settings, set Context Length to ${num(s.recommended_context)}, and load it again.`);
  }
  if (lines.length) {
    alternatives = (s.alternatives || []).map(a => ({ id: a.id, max: tokens(a.max_context), note: altNote(a) }));
    lines.push(MORE_MEMORY);
  }
  return { facts, lines, alternatives };
}

// Model providers and the models enabled on them, as Settings shows them. Pure: no React, no DOM.

export const KIND_LABELS = { text: 'Text', image: 'Image', decision: 'Decision', embedding: 'Embedding', tts: 'Text to speech',
  transcription: 'Speech to text', audio: 'Audio generation', video: 'Video', rerank: 'Rerank' };

// What a model is known to support, as short tags; a flag that is false or unknown (null) shows nothing.
const CAPABILITY_TAGS = [['image_input', 'Vision'], ['structured_output', 'JSON'], ['tools', 'Tools']];
export const capabilityTags = m => CAPABILITY_TAGS.filter(([flag]) => m[flag] === true).map(([, label]) => label);

// A picker's value for one provider's model. A provider id is a slug, so '::' never appears inside it.
export const choiceKey = (provider, model) => (provider && model ? `${provider}::${model}` : '');

/** { provider, model } from a picker's value; null for none. */
export function parseChoice(key) {
  const at = (key || '').indexOf('::');
  return at > 0 ? { provider: key.slice(0, at), model: key.slice(at + 2) } : null;
}

/** One line for a model in a list or picker: name, then what it costs and whether it reasons, when known. */
export function modelLabel(m) {
  const extra = [m.cost, m.reasoning ? 'reasoning' : ''].filter(Boolean);
  return extra.length ? `${m.name} — ${extra.join(' · ')}` : m.name;
}

/** Enabled models of one kind, grouped by provider, for a picker: [{ provider, name, models }]. `only` limits the providers. */
export function enabledByKind(providers, kind, only = null, needs = null) {
  return (providers || [])
    .filter(p => !only || only.includes(p.id))
    .map(p => ({ provider: p.id, name: p.name, models: (p.models || []).filter(m => (m.kinds || []).includes(kind) && (!needs || (m.supported_parameters || []).includes(needs))) }))
    .filter(g => g.models.length);
}

/** The provider list's models narrowed by a search, a kind, and enabled-only, enabled ones first. */
export function filterModels(models, { q = '', kind = '', enabledOnly = false, enabled = new Set(), showUnusable = false } = {}) {
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  const hit = m => words.every(w => m.id.toLowerCase().includes(w) || (m.name || '').toLowerCase().includes(w));
  return (models || [])
    .filter(m => (showUnusable || !m.unusable) && (!kind || (m.kinds || []).includes(kind)) && (!enabledOnly || enabled.has(m.id)) && hit(m))
    .sort((a, b) => Number(enabled.has(b.id)) - Number(enabled.has(a.id)));
}

/** Where a provider's key comes from, in words. */
export function keyLabel(p) {
  if (!p.key_set && p.no_key) return 'No key needed';
  if (!p.key_set) return p.key_source ? `No key: ${p.key_source} is not set` : 'No key';
  return p.key_source === 'stored' ? 'Key saved in providers.json' : `Key from ${p.key_source}`;
}

export const presetOf = (presets, id) => (presets || []).find(x => x.id === id) || null;

/** The add form after picking a preset: its name, kind, address and whether it needs a key; a key typed so far stays. */
export function fromPreset(form, preset) {
  if (!preset) return { ...form, preset: '' };
  return { ...form, preset: preset.id, name: preset.name, kind: preset.kind, base_url: preset.base_url, no_key: !!preset.no_key };
}

/** A chosen model's display name from its provider's enabled list, or its id when not listed. */
export function modelName(providers, choice) {
  if (!choice) return '';
  const m = (providers || []).find(p => p.id === choice.provider)?.models?.find(x => x.id === choice.model);
  return m?.name || choice.model;
}

/** The bot picker's first option: the harness's chat model, which a bot without its own model runs on. */
export function defaultModelLabel(providers, roles) {
  return roles?.chat ? `Harness default (${modelName(providers, roles.chat)})` : 'Harness default';
}

// Testing a model as it is enabled (a provider that lists ids only): what the progress modal shows.
export const PROBE_LABELS = { chat: 'Answers chat', tools: 'Calls tools', structured: 'Structured output', image: 'Image input', embeddings: 'Embeddings', decision: 'Letter decisions', typed: 'Typed decisions' };

export const probeStart = (model, steps) => ({
  model, total: steps.length, done: 0, end: null,
  steps: steps.map(step => ({ step, label: PROBE_LABELS[step] || step, status: 'pending' })),
});

// One event from the probe's stream; `end` with state 'lost' is the stream giving up (a harness restart).
export function probeReducer(st, e) {
  if (e.kind === 'step') {
    const steps = st.steps.map(s => (s.step === e.step ? { ...s, status: e.status, ...(e.detail ? { detail: e.detail } : {}) } : s));
    return { ...st, steps, done: steps.filter(s => ['pass', 'fail', 'skip'].includes(s.status)).length };
  }
  if (e.kind === 'end') {
    if (e.state === 'lost') return { ...st, end: { status: 'failed', error: 'The connection to the harness was lost.' } };
    const { kind, ...end } = e;
    return { ...st, end };
  }
  return st;
}


// The Harness settings form after the saved roles changed (`prev` and `next` are the form each would give): a field
// whose saved value moved takes the new one, every other field keeps what is in the form, saved or not. So a save
// from another section (the Dreaming model) does not throw away picks the user has not saved yet.
export const keepEdits = (form, prev, next) => Object.fromEntries(Object.keys(next).map(k => [k, next[k] !== prev?.[k] || !(k in form) ? next[k] : form[k]]));

// The providers a scorer's decision model may be on (GET /settings/models `scorer_providers`: scorer kind -> provider
// kinds), as provider ids for `enabledByKind`. A harness that does not say means OpenRouter, as before.
export const scorerProviderIds = (providers, roles, scorer) => {
  const kinds = roles?.scorer_providers?.[scorer] || ['openrouter'];
  return (providers || []).filter(p => kinds.includes(p.kind)).map(p => p.id);
};
// The mark a scorer needs on its model (GET /settings/models `scorer_needs`), left by the step of the model test
// that scorer relies on; null when it needs none.
export const scorerNeeds = (roles, scorer) => roles?.scorer_needs?.[scorer] || null;
const NEEDS_STEP = { letter_decisions: 'Letter decisions', typed_decisions: 'Typed decisions' };
// The line under the Decision model picker, and what an empty picker says is missing.
export const decisionHint = (roles, scorer) => {
  if ((roles?.scorer_providers?.[scorer] || ['openrouter']).includes('openrouter')) return { hint: "The scorer's model. Decision models come from OpenRouter.", where: ' on an OpenRouter provider' };
  const step = NEEDS_STEP[scorerNeeds(roles, scorer)] || 'Decisions';
  return { hint: `The scorer's model: a local decision model on an OpenAI-compatible server such as LM Studio. A model shows here once its test, run when you enable it, passes the ${step} step. Shell commands always wait for your approval with this scorer.`,
    where: ` that passed the ${step} step on an OpenAI-compatible provider` };
};

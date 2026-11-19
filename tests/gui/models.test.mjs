// Settings' model helpers: picker values, labels, grouping by kind, filtering a provider's list.
import test from 'node:test';
import assert from 'node:assert/strict';
import { KIND_LABELS, PROBE_LABELS, capabilityTags, probeReducer, probeStart, choiceKey, defaultModelLabel, enabledByKind, filterModels, keepEdits, keyLabel, modelLabel, parseChoice } from '../../src/gui/models.js';

const providers = [
  { id: 'openrouter', name: 'OpenRouter', kind: 'openrouter', models: [
    { id: 'meta/muse', name: 'Muse', kinds: ['text'], reasoning: true, cost: '$0.10 in · $0.20 out per 1M tokens' },
    { id: 'tencent/hy-image', name: 'HY Image', kinds: ['image'], cost: '$1.60 per 1M image tokens' },
  ] },
  { id: 'groq', name: 'Groq', kind: 'openai-compatible', models: [{ id: 'llama-3.3-70b', name: 'llama-3.3-70b', kinds: ['text'], guessed: true }] },
];

test('a choice round-trips through its picker value, model ids with slashes included', () => {
  assert.equal(choiceKey('openrouter', 'meta/muse'), 'openrouter::meta/muse');
  assert.deepEqual(parseChoice('openrouter::meta/muse'), { provider: 'openrouter', model: 'meta/muse' });
  assert.equal(parseChoice(''), null); assert.equal(choiceKey('', 'x'), '');
});

test('labels carry cost and reasoning when known', () => {
  assert.equal(modelLabel(providers[0].models[0]), 'Muse — $0.10 in · $0.20 out per 1M tokens · reasoning');
  assert.equal(modelLabel(providers[1].models[0]), 'llama-3.3-70b');
});

test('enabled models group by provider per kind, optionally limited to some providers', () => {
  assert.deepEqual(enabledByKind(providers, 'text').map(g => [g.provider, g.models.map(m => m.id)]), [['openrouter', ['meta/muse']], ['groq', ['llama-3.3-70b']]]);
  assert.deepEqual(enabledByKind(providers, 'image', ['groq']), []);
  assert.deepEqual(enabledByKind(null, 'text'), []);
});

test('filtering searches name and id, keeps a kind, and lists enabled models first', () => {
  const models = [{ id: 'a/one', name: 'Zed', kinds: ['text'] }, { id: 'b/two', name: 'Alpha image', kinds: ['image'] }, { id: 'c/three', name: 'Beta', kinds: ['text'] }];
  const enabled = new Set(['c/three']);
  assert.deepEqual(filterModels(models, { enabled }).map(m => m.id), ['c/three', 'a/one', 'b/two']);
  assert.deepEqual(filterModels(models, { kind: 'image' }).map(m => m.id), ['b/two']);
  assert.deepEqual(filterModels(models, { q: 'ONE' }).map(m => m.id), ['a/one']);
  assert.deepEqual(filterModels(models, { enabledOnly: true, enabled }).map(m => m.id), ['c/three']);
});

test('key labels say where a key comes from without showing it', () => {
  assert.equal(keyLabel({ key_set: true, key_source: 'stored' }), 'Key saved in providers.json');
  assert.equal(keyLabel({ key_set: true, key_source: 'env:GROQ_API_KEY' }), 'Key from env:GROQ_API_KEY');
  assert.equal(keyLabel({ key_set: false, key_source: 'env:GROQ_API_KEY' }), 'No key: env:GROQ_API_KEY is not set');
  assert.equal(keyLabel({ key_set: false, key_source: null }), 'No key');
});

test('a preset fills name, kind, address and key need, and keeps a key already typed', async () => {
  const { fromPreset, presetOf } = await import('../../src/gui/models.js');
  const presets = [{ id: 'ollama', name: 'Ollama', kind: 'openai-compatible', base_url: 'http://localhost:11434/v1', no_key: true }];
  const form = { preset: '', name: '', kind: 'openrouter', base_url: '', key: 'typed', no_key: false };
  assert.deepEqual(fromPreset(form, presetOf(presets, 'ollama')),
    { preset: 'ollama', name: 'Ollama', kind: 'openai-compatible', base_url: 'http://localhost:11434/v1', key: 'typed', no_key: true });
  assert.equal(fromPreset(form, presetOf(presets, '')).preset, '');
  assert.equal(keyLabel({ key_set: false, no_key: true }), 'No key needed');
});

test('defaultModelLabel names the chat model a bot falls back to', () => {
  const providers = [{ id: 'openrouter', models: [{ id: 'a/b', name: 'Model B' }] }];
  assert.equal(defaultModelLabel(providers, { chat: { provider: 'openrouter', model: 'a/b' } }), 'Harness default (Model B)');
  assert.equal(defaultModelLabel(providers, { chat: { provider: 'openrouter', model: 'x/y' } }), 'Harness default (x/y)');
  assert.equal(defaultModelLabel(null, null), 'Harness default');
});

test('capability tags show only what a model is known to support', () => {
  assert.deepEqual(capabilityTags({ image_input: true, structured_output: false, tools: true }), ['Vision', 'Tools']);
  assert.deepEqual(capabilityTags({ image_input: true, structured_output: true, tools: true }), ['Vision', 'JSON', 'Tools']);
  assert.deepEqual(capabilityTags({ kinds: ['text'], image_input: null }), []);
});

test('audio kinds are split three ways', () => {
  assert.equal(KIND_LABELS.tts, 'Text to speech');
  assert.equal(KIND_LABELS.transcription, 'Speech to text');
  assert.equal(KIND_LABELS.audio, 'Audio generation');
});

test('a probe moves its progress bar one finished step at a time', () => {
  let st = probeStart('meta/llama', ['chat', 'tools', 'structured', 'image']);
  assert.equal(st.total, 4); assert.equal(st.done, 0); assert.equal(st.end, null);
  assert.deepEqual(st.steps.map(s => s.status), ['pending', 'pending', 'pending', 'pending']);
  assert.equal(st.steps[0].label, PROBE_LABELS.chat);
  st = probeReducer(st, { kind: 'step', step: 'chat', status: 'running' });
  assert.equal(st.steps[0].status, 'running'); assert.equal(st.done, 0);
  st = probeReducer(st, { kind: 'step', step: 'chat', status: 'pass', detail: 'reasons' });
  assert.equal(st.steps[0].status, 'pass'); assert.equal(st.steps[0].detail, 'reasons'); assert.equal(st.done, 1);
  st = probeReducer(st, { kind: 'step', step: 'tools', status: 'fail' });
  assert.equal(st.done, 2);
  st = probeReducer(st, { kind: 'stream_error' });
  assert.equal(st.end, null);  // a reconnect in progress changes nothing
});

test('a probe ends with why, and a lost stream counts as failed', () => {
  const st = probeStart('m', ['chat']);
  assert.deepEqual(probeReducer(st, { kind: 'end', status: 'unavailable', reason: 'the provider is rate limiting requests' }).end,
    { status: 'unavailable', reason: 'the provider is rate limiting requests' });
  assert.deepEqual(probeReducer(st, { kind: 'end', state: 'lost' }).end, { status: 'failed', error: 'The connection to the harness was lost.' });
  assert.equal(probeReducer(st, { kind: 'end', status: 'done', provider: { id: 'p' } }).end.status, 'done');
});

test('models the key cannot use are hidden unless asked for', () => {
  const models = [{ id: 'a', name: 'A', kinds: ['text'] }, { id: 'b', name: 'B', kinds: ['text'], unusable: true }];
  assert.deepEqual(filterModels(models, {}).map(m => m.id), ['a']);
  assert.deepEqual(filterModels(models, { showUnusable: true }).map(m => m.id), ['a', 'b']);
});

test('a skipped test counts as finished on the progress bar', () => {
  let st = probeStart('m', ['chat', 'embeddings', 'tools']);
  st = probeReducer(st, { kind: 'step', step: 'chat', status: 'fail' });
  st = probeReducer(st, { kind: 'step', step: 'embeddings', status: 'pass' });
  st = probeReducer(st, { kind: 'step', step: 'tools', status: 'skip' });
  assert.equal(st.done, 3); assert.equal(st.steps[2].status, 'skip');
});

test('a save elsewhere keeps the picks not yet saved and takes what the save changed', () => {
  const saved = { chat: 'or::a', summary: 'or::b', scorer: 'x', dream: '' };
  const form = { ...saved, chat: 'or::c' };  // chat picked, not saved
  assert.deepEqual(keepEdits(form, saved, { ...saved, dream: 'local::q' }), { ...saved, chat: 'or::c', dream: 'local::q' });
  assert.deepEqual(keepEdits(form, saved, { ...saved, chat: 'or::d' }), { ...saved, chat: 'or::d' });  // the saved value moved: it wins
  assert.deepEqual(keepEdits({ chat: '', summary: '' }, { chat: '', summary: '' }, saved), saved);  // the first load, a new field included
});

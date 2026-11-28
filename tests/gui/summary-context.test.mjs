import test from 'node:test';
import assert from 'node:assert/strict';
import { NOTICE, NOTICE_LINK, cardLine, advice, tokens, stamp } from '../../src/gui/summary-context.js';

const fmt = at => (at === 'x' ? '' : '28 Nov 03:11');
const slim = {
  provider: { id: 'lm', name: 'LM Studio' }, model: 'slim-summary-tool', server: 'lmstudio',
  loaded_context: 4096, max_context: 4096, loaded: true, learned_context: null,
  last_overrun: { at: '2026-11-28T03:11:00', where: 'task', n_ctx: 4096, n_prompt_tokens: 5726 },
  recovered_since: null, warning: 'at_max', recommended_context: 4096,
  alternatives: [
    { id: 'summary-llm-qwen-2.5-7b', max_context: 40960, loaded: false, enabled: true },
    { id: 'prithivmlmods/llama-chat-summary-3.2-3b', max_context: 131072, loaded: false, enabled: false },
  ],
};

test('the notice and its link', () => {
  assert.equal(NOTICE, "Earlier turns could not be summarised: the summary model's context is too small.");
  assert.equal(NOTICE_LINK, 'What to change…');
});

test('numbers get thousands separators', () => { assert.equal(tokens(4096), '4,096 tokens'); });

test('cardLine for each warning, and none for no warning or a hosted model', () => {
  assert.equal(cardLine({ ...slim, warning: '' }), null);
  assert.equal(cardLine(null), null);
  assert.equal(cardLine({ ...slim, server: 'hosted', warning: 'overrun' }), null);
  assert.equal(cardLine(slim), 'Summary model: loaded at 4,096 tokens, the most this model supports. Long tasks are summarised in many small pieces.');
  assert.equal(cardLine({ ...slim, warning: 'small' }), 'Summary model: loaded at 4,096 tokens. Long tasks are summarised in many small pieces.');
  assert.equal(cardLine({ ...slim, warning: 'overrun' }), 'Summary model: the last compaction did not fit its context.');
});

test('advice for a model loaded at its maximum', () => {
  const a = advice(slim, fmt);
  assert.deepEqual(a.facts, [['Context loaded', '4,096 tokens'], ['Most it supports', '4,096 tokens'], ['Last refused', 'a 5,726-token request, 28 Nov 03:11, in a task']]);
  assert.deepEqual(a.lines.slice(0, 2), ['This model cannot take more than 4,096 tokens, so no LM Studio setting will help.', 'Pick a summary model that supports more.']);
  assert.equal(a.lines.at(-1), 'A larger context uses more memory.');
  assert.deepEqual(a.alternatives, [
    { id: 'summary-llm-qwen-2.5-7b', max: '40,960 tokens', note: 'enabled (not loaded)' },
    { id: 'prithivmlmods/llama-chat-summary-3.2-3b', max: '131,072 tokens', note: 'enable it on the Providers tab first (not loaded)' },
  ]);
});

test('advice for a model loaded below its maximum', () => {
  const a = advice({ ...slim, loaded_context: 4096, max_context: 40960, recommended_context: 16384, warning: 'small', alternatives: [],
    last_overrun: { at: 'x', where: 'fold', n_ctx: 4096, n_prompt_tokens: null } }, fmt);
  assert.equal(a.facts[2][1], 'a request in a conversation summary');
  assert.ok(a.lines.includes("In LM Studio, open the model's load settings, set Context Length to 16,384, and load it again."));
  assert.equal(a.lines.at(-1), 'A larger context uses more memory.');
});

test('advice for another server shows the learned limit when nothing is loaded', () => {
  const a = advice({ ...slim, server: 'other', loaded_context: null, max_context: null, loaded: false, learned_context: 8192, alternatives: [] }, fmt);
  assert.deepEqual(a.facts, [['Context loaded', 'unknown'], ['Most it supports', 'unknown'], ['Last refused', 'a 5,726-token request, 28 Nov 03:11, in a task'], ['Learned limit', '8,192 tokens']]);
  assert.ok(a.lines.includes('The server refused a request of 5,726 tokens with a context of 4,096. Give the model a larger context on that server, or pick another summary model.'));
});

test('advice for a hosted model says only that it is not read', () => {
  const a = advice({ ...slim, server: 'hosted' });
  assert.deepEqual(a, { facts: [], lines: ['This summary model runs on a hosted provider; its context is not read.'], alternatives: [] });
  assert.equal(advice({ provider: null, server: 'lmstudio' }).lines.length, 1);
});

test('a model not loaded but known to be at its maximum is not told to raise the context', () => {
  const a = advice({ ...slim, loaded_context: null, loaded: false, max_context: 4096, learned_context: 4096, recommended_context: 4096 }, fmt);
  assert.equal(a.facts[0][1], 'not loaded');
  assert.deepEqual(a.lines.slice(0, 2), ['This model cannot take more than 4,096 tokens, so no LM Studio setting will help.', 'Pick a summary model that supports more.']);
  assert.ok(!a.lines.some(l => l.includes('set Context Length')));
});

test('the last-refused row carries a date, and none when the time is unreadable', () => {
  assert.equal(advice(slim, () => '28 Nov 03:11').facts[2][1], 'a 5,726-token request, 28 Nov 03:11, in a task');
  assert.equal(advice({ ...slim, last_overrun: { ...slim.last_overrun, at: 'garbage' } }).facts[2][1], 'a 5,726-token request in a task');
});

const noMore = a => !a.lines.some(l => l.includes('cannot take more') || l.includes('set Context Length'));

test('a model loaded above the recommended size and below its maximum gets no change advice', () => {
  const a = advice({ ...slim, loaded_context: 16384, max_context: 40960, recommended_context: 16384, warning: null, last_overrun: null, alternatives: [] }, fmt);
  assert.ok(noMore(a));
  assert.deepEqual(a.lines, []);
  assert.equal(a.facts[0][1], '16,384 tokens');
  assert.equal(a.facts[1][1], '40,960 tokens');
});

test('an old overrun at a smaller context gives no advice once the model is loaded larger', () => {
  const a = advice({ ...slim, loaded_context: 32768, max_context: 131072, recommended_context: 16384, warning: null,
    last_overrun: { at: '2026-11-28T03:11:00', where: 'task', n_ctx: 4096, n_prompt_tokens: 5726 }, alternatives: [] }, fmt);
  assert.ok(noMore(a));
  assert.equal(a.facts.length, 3);
});

test('the default time format has no comma', () => {
  assert.equal(stamp('2026-11-28T03:11:00Z', 'UTC'), '28 Nov 03:11');
  assert.match(stamp('2026-11-28T03:11:00'), /^\d{1,2} [A-Z][a-z]{2} \d{2}:\d{2}$/);
  assert.equal(stamp('garbage'), '');
});

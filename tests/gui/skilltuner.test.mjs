import test from 'node:test';
import assert from 'node:assert/strict';
import { attachPlan, doneLine, initialPicks, offeredBots, statusLine, tunerApi } from '../../src/gui/skilltuner.js';
import { SECTIONS, searchSettings } from '../../src/gui/settings-index.js';

test('tunerApi paths and bodies; every POST sends {}', async () => {
  const calls = [];
  const api = tunerApi((m, p, b) => { calls.push([m, p, b]); return Promise.resolve({}); });
  await api.status(); await api.saveSettings({ enabled: true }); await api.suggestions(); await api.suggestion('a/b');
  await api.approve('x'); await api.reject('x'); await api.remove('pdf tool'); await api.discovered();
  assert.deepEqual(calls, [
    ['GET', '/skilltuner/status', undefined],
    ['PUT', '/skilltuner/settings', { enabled: true }],
    ['GET', '/skilltuner/suggestions', undefined],
    ['GET', '/skilltuner/suggestions/a%2Fb', undefined],
    ['POST', '/skilltuner/suggestions/x/approve', {}],
    ['POST', '/skilltuner/suggestions/x/reject', {}],
    ['POST', '/skilltuner/skills/pdf%20tool/remove', {}],
    ['GET', '/skilltuner/discovered', undefined],
  ]);
});

test('statusLine', () => {
  assert.equal(statusLine({ enabled: false }), 'SkillTuner is off.');
  assert.equal(statusLine({ enabled: true, ready: false, why_not: 'Choose a decision model first.' }), 'Choose a decision model first.');
  for (const e of ['network', 'refused', 'provider', 'source_changed']) assert.equal(statusLine({ enabled: true, ready: true, error: e }), 'agentskills.finance could not be reached. Trying again tomorrow.');
  for (const e of ['unreachable', 'model', 'model_provider']) assert.equal(statusLine({ enabled: true, ready: true, error: e }), "SkillTuner's model could not be reached. Trying again tomorrow.");
  assert.equal(statusLine({ enabled: true, ready: true, error: 'decider' }), 'The decision model could not be reached. Trying again tomorrow.');
  assert.equal(statusLine({ enabled: true, ready: true, error: 'store' }), 'A suggestion could not be saved. Trying again tomorrow.');
  const at = new Date(2026, 10, 19, 3, 12).toISOString();
  const day = new Date(at).toLocaleDateString('en-US', { day: 'numeric', month: 'short' });
  assert.equal(statusLine({ enabled: true, ready: true, last_pass_at: at, pending: 2, spent_today: 7 }), `Last looked ${day}. 2 suggestions waiting. 7 of 40 requests used today.`);
  assert.equal(statusLine({ enabled: true, ready: true, last_pass_at: null, pending: 0, spent_today: 0 }), 'Has not looked yet. 0 suggestions waiting. 0 of 40 requests used today.');
  assert.match(statusLine({ enabled: true, ready: true, last_pass_at: at, pending: 1, spent_today: 1 }), /1 suggestion waiting\./);
});

const bots = [{ id: 'a', name: 'A' }, { id: 'b', name: 'B' }, { id: 'n', narrates: true }, { id: 't', tunes_skills: true }, { id: 'c', closed: true }, { id: 's', studio: true }];
test('offeredBots drops the four kinds', () => assert.deepEqual(offeredBots(bots).map(b => b.id), ['a', 'b']));
test('initialPicks are the fit winners among offered bots', () => {
  assert.deepEqual([...initialPicks(bots, { bot_ids: ['b', 'n', 'zzz'] })], ['b']);
  assert.equal(initialPicks(bots, null).size, 0);
});
test('attachPlan confirms one proposed bot, updates the rest', () => {
  const bs = [{ id: 'a', skills: ['x'] }, { id: 'b', skills: [] }, { id: 'c', skills: ['y'] }];
  const fit = { id: 'f', skill: 'new', bot_ids: ['a', 'b'] };
  assert.deepEqual(attachPlan(fit, new Set(['a']), bs), { confirm: ['a'], update: [] });
  assert.deepEqual(attachPlan(fit, new Set(['a', 'b', 'c']), bs), { confirm: ['a'], update: [{ id: 'b', skills: ['new'] }, { id: 'c', skills: ['y', 'new'] }] });
  assert.deepEqual(attachPlan(null, new Set(['c']), bs, 'new'), { confirm: [], update: [{ id: 'c', skills: ['y', 'new'] }] });
  assert.deepEqual(attachPlan(fit, new Set(), bs), { confirm: [], update: [] });
});
test('doneLine', () => {
  assert.equal(doneLine([]), 'Not added to any bot yet.');
  assert.equal(doneLine(['A']), 'Added to A.');
  assert.equal(doneLine(['A', 'B']), 'Added to A and B.');
  assert.equal(doneLine(['A', 'B', 'C']), 'Added to A, B and C.');
});
test('the search finds SkillTuner and the suggestions section', () => {
  assert.ok(SECTIONS.some(s => s.panel === 'harness' && s.title === 'SkillTuner'));
  assert.ok(SECTIONS.some(s => s.panel === 'skills' && s.title === 'Suggested by SkillTuner'));
  assert.ok(searchSettings('SkillTuner').length > 0);
  assert.ok(searchSettings('Suggested by SkillTuner').some(m => m.panel === 'skills'));
});

import { createLatest, nextSteps, sameSlugs } from '../../src/gui/skilltuner.js';
test('createLatest drops answers older than the newest request or action', () => {
  const l = createLatest();
  const a = l.start(), b = l.start();
  assert.equal(l.fresh(a), false); assert.equal(l.fresh(b), true);
  l.bump();
  assert.equal(l.fresh(b), false);
  assert.equal(l.fresh(l.start()), true);
});
test('sameSlugs', () => { assert.ok(sameSlugs(['a'], ['a'])); assert.ok(!sameSlugs(['a'], ['b'])); assert.ok(!sameSlugs([], ['a'])); });
test('nextSteps skips what an earlier try did and never confirms twice', () => {
  const bs = [{ id: 'a', skills: [] }, { id: 'b', skills: [] }, { id: 'c', skills: ['y'] }];
  const fit = { id: 'f', skill: 'new', bot_ids: ['a', 'b'] };
  const picks = new Set(['a', 'b', 'c']);
  assert.deepEqual(nextSteps(fit, picks, bs, 'new'), { confirm: ['a'], update: [{ id: 'b', skills: ['new'] }, { id: 'c', skills: ['y', 'new'] }] });
  assert.deepEqual(nextSteps(fit, picks, bs, 'new', { confirmed: ['a'], updated: [] }), { confirm: [], update: [{ id: 'b', skills: ['new'] }, { id: 'c', skills: ['y', 'new'] }] });
  assert.deepEqual(nextSteps(fit, picks, bs, 'new', { confirmed: ['a'], updated: ['b'] }), { confirm: [], update: [{ id: 'c', skills: ['y', 'new'] }] });
  assert.deepEqual(nextSteps(fit, new Set(['b']), bs, 'new', { confirmed: ['a'], updated: [] }), { confirm: [], update: [{ id: 'b', skills: ['new'] }] });
});

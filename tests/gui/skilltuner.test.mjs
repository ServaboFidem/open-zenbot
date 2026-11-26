import test from 'node:test';
import assert from 'node:assert/strict';
import { LEFT_OUT, LICENCE_CHOICES, PRIVACY, attachPlan, canAdd, cardSource, casemarkLine, doneLine, initialPicks, offeredBots, repoErrorLine, repoLine, statusLine, tunerApi } from '../../src/gui/skilltuner.js';
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

import { attachChosen, createQueue } from '../../src/gui/skilltuner.js';
// A harness stand-in: PATCH /bots/{id} replaces the skills list, as the real route does; every call yields first.
function fakeHarness(bots) {
  const store = new Map(bots.map(b => [b.id, { ...b, skills: [...b.skills] }]));
  const tick = () => new Promise(r => setTimeout(r, 0));
  const calls = [];
  return {
    store, calls, failPatch: 0,
    async bots() { calls.push('bots'); await tick(); return [...store.values()].map(b => ({ ...b, skills: [...b.skills] })); },
    async patchBot(id, body) {
      calls.push(`patch:${id}`); await tick();
      if (this.failPatch > 0) { this.failPatch -= 1; throw new Error('offline'); }
      store.get(id).skills = [...body.skills];
    },
    async confirmFit(fitId, id) { calls.push(`confirm:${id}`); await tick(); const b = store.get(id); if (!b.skills.includes(fitId)) b.skills.push(fitId); },
    async denyFit() { calls.push('deny'); },
  };
}
const chosen = (ref, picks, fit = null) => ({ skill: { ref }, fit, picks: new Set(picks) });

test('createQueue runs one action at a time and goes on after a failure', async () => {
  const q = createQueue(), seen = [];
  const step = (name, fail) => async () => { seen.push(`start ${name}`); await new Promise(r => setTimeout(r, 5)); seen.push(`end ${name}`); if (fail) throw new Error(name); return name; };
  const a = q.run(step('a', true)), b = q.run(step('b'));
  await assert.rejects(a, /a/);
  assert.equal(await b, 'b');
  assert.deepEqual(seen, ['start a', 'end a', 'start b', 'end b']);
});

test('two Done actions on one bot started together keep both skills', async () => {
  const api = fakeHarness([{ id: 'a', name: 'Alpha', skills: ['old'] }]);
  const q = createQueue();
  const one = q.run(() => attachChosen(api, chosen('x', ['a']), { confirmed: [], updated: [] }));
  const two = q.run(() => attachChosen(api, chosen('y', ['a']), { confirmed: [], updated: [] }));
  assert.deepEqual(await one, ['Alpha']); assert.deepEqual(await two, ['Alpha']);
  assert.deepEqual(api.store.get('a').skills, ['old', 'x', 'y']);
});

test('Done reads the stored skills, not a list the page holds, and a retry does too', async () => {
  const api = fakeHarness([{ id: 'a', name: 'Alpha', skills: [] }, { id: 'b', name: 'Beta', skills: [] }]);
  api.failPatch = 1;
  const progress = { confirmed: [], updated: [] };
  const item = chosen('x', ['a', 'b']);
  await assert.rejects(attachChosen(api, item, progress), /offline/);
  assert.deepEqual(progress, { confirmed: [], updated: [] });
  api.store.get('a').skills.push('z');  // added elsewhere before the retry
  assert.deepEqual(await attachChosen(api, item, progress), ['Alpha', 'Beta']);
  assert.deepEqual(api.store.get('a').skills, ['z', 'x']);
  assert.deepEqual(api.store.get('b').skills, ['x']);
});

test('Done confirms the fit once and denies a fit no bot took', async () => {
  const api = fakeHarness([{ id: 'a', name: 'Alpha', skills: [] }, { id: 'b', name: 'Beta', skills: [] }]);
  const fit = { id: 'x', skill: 'x', bot_ids: ['a'] };
  assert.deepEqual(await attachChosen(api, chosen('x', ['a', 'b'], fit), { confirmed: [], updated: [] }), ['Alpha', 'Beta']);
  assert.deepEqual(api.calls, ['bots', 'confirm:a', 'patch:b']);
  const api2 = fakeHarness([{ id: 'b', name: 'Beta', skills: [] }]);
  await attachChosen(api2, chosen('x', ['b'], { id: 'x', skill: 'x', bot_ids: ['a'] }), { confirmed: [], updated: [] });
  assert.deepEqual(api2.calls, ['bots', 'patch:b', 'deny']);
});

test('tunerApi: the source routes, every POST with a body', async () => {
  const calls = [];
  const api = tunerApi((m, p, b) => { calls.push([m, p, b]); return Promise.resolve({}); });
  await api.sources(); await api.checkRepo('https://github.com/o/r');
  await api.addRepo({ address: 'a', licence: 'MIT', index: '' }); await api.setRepoLicence('ab/c', 'MIT');
  await api.readRepo('r1'); await api.removeRepo('r1');
  assert.deepEqual(calls, [
    ['GET', '/skilltuner/sources', undefined],
    ['POST', '/skilltuner/repos/check', { address: 'https://github.com/o/r' }],
    ['POST', '/skilltuner/repos', { address: 'a', licence: 'MIT', index: '' }],
    ['PUT', '/skilltuner/repos/ab%2Fc', { licence: 'MIT' }],
    ['POST', '/skilltuner/repos/r1/read', {}],
    ['DELETE', '/skilltuner/repos/r1', undefined],
  ]);
});

test('LICENCE_CHOICES: the five and Dont use', () => {
  assert.deepEqual(LICENCE_CHOICES.map(c => c.value), ['Apache-2.0', 'MIT', 'BSD-2-Clause', 'BSD-3-Clause', 'CC-BY-4.0', '']);
  assert.equal(LICENCE_CHOICES.at(-1).label, "Don't use");
});

test('repoLine', () => {
  const base = { skills: 3046, index: 'skills_index.json', one_by_one: false, licence: 'MIT', last_read_at: '2026-11-26T10:00:00Z', reading: false, error: '' };
  assert.equal(repoLine(base), '3,046 skills from skills_index.json · MIT · read 26 Nov');
  assert.equal(repoLine({ ...base, skills: 20, index: '', one_by_one: true, licence: 'Apache-2.0', reading: true }), '20 skills, read one by one · Apache-2.0 · Reading…');
  assert.equal(repoLine({ ...base, skills: 1, licence: '' }), '1 skill from skills_index.json · no licence · read 26 Nov');
  assert.equal(repoLine({ skills: 0, licence: 'MIT', last_read_at: null, reading: false, error: '' }), 'MIT · Not read yet');
  assert.equal(repoLine({ skills: 0, licence: 'MIT', last_read_at: null, reading: true, error: '' }), 'MIT · Reading…');
});

test('repoErrorLine', () => {
  assert.equal(repoErrorLine('not_found'), 'GitHub has no such public repository.');
  assert.equal(repoErrorLine('source_changed'), 'The repository moved or changed. Remove it and add it again.');
  assert.equal(repoErrorLine('no_index'), 'This repository has no skill index and too many skills to read one by one.');
  assert.equal(repoErrorLine('too_large'), 'This repository is too large to read.');
  for (const k of ['network', 'refused']) assert.equal(repoErrorLine(k), 'GitHub could not be reached. Trying again tomorrow.');
  assert.equal(repoErrorLine('budget'), "Today's GitHub reads are used up.");
  assert.equal(repoErrorLine('whatever'), 'This repository could not be read.');
  assert.equal(repoErrorLine(''), '');
});

test('casemarkLine and cardSource', () => {
  assert.equal(casemarkLine({ spent_today: 12, budget: 40 }), 'built in · 12 of 40 requests today');
  assert.equal(cardSource({ source: 'github.com/o/r', license: 'MIT' }), 'From github.com/o/r · MIT');
  assert.equal(cardSource({ license: 'Apache-2.0' }), 'From agentskills.finance · Apache-2.0');
  assert.match(LEFT_OUT, /scripts/);
});

test('canAdd needs a checked address and a licence choice', () => {
  assert.equal(canAdd({ address: 'x', checked: 'x', licence: 'MIT' }), true);
  assert.equal(canAdd({ address: 'x', checked: 'x', licence: '' }), true);
  assert.equal(canAdd({ address: ' x ', checked: 'x', licence: 'MIT' }), true);
  assert.equal(canAdd({ address: 'x', checked: '', licence: 'MIT' }), false);
  assert.equal(canAdd({ address: 'y', checked: 'x', licence: 'MIT' }), false);
  assert.equal(canAdd({ address: 'x', checked: 'x', licence: null }), false);
  assert.equal(canAdd({ address: 'x', checked: 'x', licence: 'GPL' }), false);
});

test('statusLine names the service that failed', () => {
  const on = { enabled: true, ready: true };
  for (const e of ['network', 'refused', 'provider']) assert.equal(statusLine({ ...on, error: e, error_source: 'github' }), 'GitHub could not be reached. Trying again tomorrow.');
  assert.equal(statusLine({ ...on, error: 'budget', error_source: 'github' }), "Today's GitHub reads are used up. Trying again tomorrow.");
  assert.equal(statusLine({ ...on, error: 'network', error_source: 'casemark' }), 'agentskills.finance could not be reached. Trying again tomorrow.');
  assert.equal(statusLine({ ...on, error: 'network' }), 'agentskills.finance could not be reached. Trying again tomorrow.');
});

test('PRIVACY says repositories are searched on this computer', () => {
  assert.match(PRIVACY, /GitHub repositories/);
  assert.match(PRIVACY, /on this computer/);
});

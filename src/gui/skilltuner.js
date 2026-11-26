// SkillTuner in the GUI: the requests and the decisions, no React, so they are tested without the page
// (tests/gui/skilltuner.test.mjs). The harness checks everything again. Text from a suggestion is untrusted: the
// components show it as plain text only.

const enc = encodeURIComponent;
export const BUDGET = 40;
const NET = new Set(['network', 'refused', 'provider', 'source_changed']);
const MODEL = new Set(['unreachable', 'model', 'model_provider']);

// `fetchJson(method, path, body)` is createApi's `call`. Every POST carries a JSON body.
export function tunerApi(fetchJson) {
  return {
    status: () => fetchJson('GET', '/skilltuner/status'),
    saveSettings: ({ enabled }) => fetchJson('PUT', '/skilltuner/settings', { enabled }),
    suggestions: () => fetchJson('GET', '/skilltuner/suggestions'),
    suggestion: id => fetchJson('GET', `/skilltuner/suggestions/${enc(id)}`),
    approve: id => fetchJson('POST', `/skilltuner/suggestions/${enc(id)}/approve`, {}),
    reject: id => fetchJson('POST', `/skilltuner/suggestions/${enc(id)}/reject`, {}),
    remove: slug => fetchJson('POST', `/skilltuner/skills/${enc(slug)}/remove`, {}),
    discovered: () => fetchJson('GET', '/skilltuner/discovered'),
  };
}

export function statusLine(status) {
  const s = status || {};
  if (!s.enabled) return 'SkillTuner is off.';
  if (!s.ready) return s.why_not || 'SkillTuner is not ready.';
  if (NET.has(s.error)) return 'agentskills.finance could not be reached. Trying again tomorrow.';
  if (MODEL.has(s.error)) return "SkillTuner's model could not be reached. Trying again tomorrow.";
  if (s.error === 'decider') return 'The decision model could not be reached. Trying again tomorrow.';
  if (s.error === 'store') return 'A suggestion could not be saved. Trying again tomorrow.';
  const d = s.last_pass_at ? new Date(s.last_pass_at) : null;
  const when = d && !Number.isNaN(d.getTime()) ? `Last looked ${d.toLocaleDateString('en-US', { day: 'numeric', month: 'short' })}.` : 'Has not looked yet.';
  const n = s.pending || 0;
  return `${when} ${n} ${n === 1 ? 'suggestion' : 'suggestions'} waiting. ${s.spent_today || 0} of ${s.budget || BUDGET} requests used today.`;
}

// The bots a skill may be given to: not the Narrator, SkillTuner, a closed bot or a studio bot.
export const offeredBots = bots => (bots || []).filter(b => !b.narrates && !b.tunes_skills && !b.closed && !b.studio);

export function initialPicks(bots, fit) {
  const offered = new Set(offeredBots(bots).map(b => b.id));
  return new Set((fit?.bot_ids || []).filter(id => offered.has(id)));
}

// What ticking does. A fit can be confirmed once (for one bot), so the first ticked bot it proposed is confirmed
// and every other ticked bot gets the skill through a bot update with its whole skills list.
export function attachPlan(fit, picks, bots, slug) {
  const skill = fit?.skill || slug;
  const proposed = new Set(fit?.bot_ids || []);
  const ticked = (bots || []).filter(b => picks.has(b.id));
  const first = fit ? ticked.find(b => proposed.has(b.id)) : null;
  const update = ticked.filter(b => b !== first && !(b.skills || []).includes(skill))
    .map(b => ({ id: b.id, skills: [...(b.skills || []), skill] }));
  return { confirm: first ? [first.id] : [], update };
}

export function doneLine(names) {
  if (!names?.length) return 'Not added to any bot yet.';
  if (names.length === 1) return `Added to ${names[0]}.`;
  return `Added to ${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}.`;
}

// Under the switch (also in the search index).
export const PRIVACY = "SkillTuner reads what you asked for while the computer dreams, and searches agentskills.finance for a skill that fits. The words of a need are sent to that site and to the decision model you chose. Nothing is installed until you approve it.";

// A read answers only if nothing newer was started since: `start()` gives a token, `fresh(token)` says whether it
// is still the newest, and `bump()` (an action that changes the data) makes every read in flight stale.
export function createLatest() {
  let n = 0;
  return { start: () => ++n, fresh: t => t === n, bump: () => { n += 1; } };
}

export const sameSlugs = (a, b) => a.length === b.length && a.every((x, i) => x === b[i]);

// One action at a time (Approve, Reject, Done): each starts once the one before it has settled, failed or not.
export function createQueue() {
  let last = Promise.resolve();
  return {
    run(fn) {
      const p = last.then(() => fn());
      last = p.catch(() => {});
      return p;
    },
  };
}

// Done for one approved skill: confirm the fit's bot, add the skill to each other ticked bot, deny an unused fit.
// The bots' skills are read from the harness first, never taken from a list the page holds: PATCH /bots/{id}
// replaces the whole list, so a stale one would drop a skill an earlier Done added. `progress`
// ({confirmed, updated}) is filled as each step lands, so a retry skips those; on a failure it throws with
// `progress` as far as it got. Answers the names of the bots that now have the skill.
export async function attachChosen(api, item, progress) {
  const bots = await api.bots();
  const steps = nextSteps(item.fit, item.picks, bots, item.skill.ref, progress);
  for (const id of steps.confirm) { await api.confirmFit(item.fit.id, id); progress.confirmed.push(id); }
  for (const u of steps.update) { await api.patchBot(u.id, { skills: u.skills }); progress.updated.push(u.id); }
  if (item.fit && !progress.confirmed.length) await api.denyFit(item.fit.id).catch(() => {});
  return [...progress.confirmed, ...progress.updated].map(id => bots.find(b => b.id === id)?.name || id);
}

// What is still to do for Done, given what an earlier try already did (`done`: {confirmed: [], updated: []}).
// Once the fit was confirmed it cannot be again, so every other ticked bot goes through an update.
export function nextSteps(fit, picks, bots, slug, done = {}) {
  const confirmed = done.confirmed || [], updated = done.updated || [];
  const plan = attachPlan(confirmed.length ? { ...fit, bot_ids: [] } : fit, picks, bots, slug);
  return {
    confirm: plan.confirm.filter(id => !confirmed.includes(id)),
    update: plan.update.filter(u => !updated.includes(u.id) && !confirmed.includes(u.id)),
  };
}

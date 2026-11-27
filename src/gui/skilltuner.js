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
    sources: () => fetchJson('GET', '/skilltuner/sources'),
    checkRepo: address => fetchJson('POST', '/skilltuner/repos/check', { address }),
    addRepo: ({ address, licence, index }) => fetchJson('POST', '/skilltuner/repos', { address, licence, index }),
    setRepoLicence: (id, licence) => fetchJson('PUT', `/skilltuner/repos/${enc(id)}`, { licence }),
    readRepo: id => fetchJson('POST', `/skilltuner/repos/${enc(id)}/read`, {}),
    removeRepo: id => fetchJson('DELETE', `/skilltuner/repos/${enc(id)}`),
  };
}

export function statusLine(status) {
  const s = status || {};
  if (!s.enabled) return 'SkillTuner is off.';
  if (!s.ready) return s.why_not || 'SkillTuner is not ready.';
  if (s.error_source === 'github' && (s.error === 'budget' || NET.has(s.error))) {
    return `${s.error === 'budget' ? "Today's GitHub reads are used up." : 'GitHub could not be reached.'} Trying again tomorrow.`;
  }
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
export const PRIVACY = "SkillTuner reads what you asked for while the computer dreams, and looks for a skill that fits in agentskills.finance and in the GitHub repositories you add. Repositories are searched on this computer: only agentskills.finance and the decision model you chose receive the words of a need. Nothing is installed until you approve it.";

// A suggestion that came with files other than instructions and references.
export const LEFT_OUT = 'This skill came with scripts or other files that were not brought over.';

export const LICENCE_CHOICES = [
  ...['Apache-2.0', 'MIT', 'BSD-2-Clause', 'BSD-3-Clause', 'CC-BY-4.0'].map(v => ({ value: v, label: v })),
  { value: '', label: "Don't use" },
];

const REPO_ERRORS = {
  not_found: 'GitHub has no such public repository.',
  source_changed: 'The repository moved or changed. Remove it and add it again.',
  no_index: 'This repository has no skill index and too many skills to read one by one.',
  too_large: 'This repository is too large to read.',
  network: 'GitHub could not be reached. Trying again tomorrow.',
  refused: 'GitHub could not be reached. Trying again tomorrow.',
  budget: "Today's GitHub reads are used up.",
};
export const repoErrorLine = kind => (!kind ? '' : REPO_ERRORS[kind] || 'This repository could not be read.');

// "3,046 skills from skills_index.json · MIT · read 26 Nov". The date is the UTC day of the read.
export function repoLine(repo) {
  const r = repo || {};
  const n = r.skills || 0;
  const d = r.last_read_at ? new Date(r.last_read_at) : null;
  const read = !!d && !Number.isNaN(d.getTime());
  const parts = [];
  if (n > 0) parts.push(`${n.toLocaleString('en-US')} ${n === 1 ? 'skill' : 'skills'}${r.index ? ` from ${r.index}` : ', read one by one'}`);
  else if (read) parts.push('0 skills'); // a read that found none is shown, so the owner sees it
  parts.push(r.licence || 'no licence');
  if (r.reading) parts.push('Reading…');
  else if (read) parts.push(`read ${d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' })}`);
  else if (!r.error) parts.push('Not read yet');
  return parts.join(' · ');
}

export const casemarkLine = src => `built in · ${src?.spent_today || 0} of ${src?.budget || BUDGET} requests today`;

// The suggestion card's source: an older suggestion has none and came from agentskills.finance.
export const cardSource = s => `From ${s?.source || 'agentskills.finance'} · ${s?.license}`;

// Add is offered once this address was checked (and not edited since) and a licence choice is on the form.
// licence is null until the check answers; '' is a choice (Don't use).
export const canAdd = form => {
  const f = form || {};
  const a = String(f.address || '').trim();
  return !!a && f.checked === a && LICENCE_CHOICES.some(c => c.value === f.licence);
};

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

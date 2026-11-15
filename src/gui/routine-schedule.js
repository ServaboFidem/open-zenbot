// Pure helpers for the routine schedule editor and the Routines list.
export const defaultSchedule = () => ({ kind: 'weekly', days: [4], time: '09:00', hours: 24 });

export function validateSchedule(s) {
  if (s.kind === 'interval') return Number.isInteger(Number(s.hours)) && Number(s.hours) >= 1 && Number(s.hours) <= 168 ? '' : 'Enter a whole number of hours, 1 to 168.';
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(s.time || '')) return 'Choose a valid time.';
  if (s.kind === 'weekly' && !(s.days || []).length) return 'Choose at least one day.';
  return '';
}

// A routine pins one of its bot's attached skills: the dialog offers only those.
export const botSkills = (botId, bots, skills) => {
  const attached = bots.find(b => b.id === botId)?.skills || [];
  return skills.filter(s => attached.includes(s.ref));
};

export const toggleDay = (days, d) => (days.includes(d) ? days.filter(x => x !== d) : [...days, d].sort((a, b) => a - b));

const OUTCOMES = { started: 'running', ok: 'finished', failed: 'failed', skipped_overlap: 'skipped, the last run was still running', skipped_missed: 'skipped, missed while the app was closed' };
// `last_run` and `last_skip` carry `reason` when they failed: "failed: <reason>"
export const describeOutcome = (outcome, reason = '') => {
  const word = OUTCOMES[outcome] || outcome || '';
  return outcome === 'failed' && reason ? `${word}: ${reason}` : word;
};

export const formatWhen = iso => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
};

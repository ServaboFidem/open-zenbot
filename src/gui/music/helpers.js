// Pure helpers for the Music views: schedule words, dates, card labels.
export const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];  // routine days: 0 = Monday

export function formatTime(time) {
  const [h, m] = String(time || '09:00').split(':');
  return `${Number(h) % 12 || 12}:${m || '00'} ${Number(h) >= 12 ? 'PM' : 'AM'}`;
}

// The digest form's day/time, read from the managed routine's schedule (null when there is no routine).
export function draftFromRoutine(routine) {
  const s = routine?.schedule;
  if (!s) return { kind: 'weekly', day: 4, time: '09:00', hours: 0 };
  return { kind: s.kind, day: s.days?.[0] ?? 4, time: s.time || '09:00', hours: s.hours || 0 };
}

// The schedule to save: only day and time change; a daily or interval routine keeps its own kind.
export function scheduleFromDraft(routine, draft) {
  const s = routine.schedule;
  if (s.kind === 'weekly') return { ...s, days: [draft.day], time: draft.time };
  if (s.kind === 'daily') return { ...s, time: draft.time };
  return s;
}

export function scheduleLabel(routine) {
  const s = routine?.schedule;
  if (!s) return '';
  if (s.kind === 'interval') return `Every ${s.hours} hours`;
  if (s.kind === 'daily') return `Daily at ${formatTime(s.time)}`;
  return `${(s.days || []).map(d => DAYS[d]).join(', ')} at ${formatTime(s.time)}`;
}

export function whenLabel(iso) {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(+d) ? d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : '';
}

// "13 Nov" from a date string like 2026-11-13.
export function dayLabel(date) {
  const d = date ? new Date(`${String(date).slice(0, 10)}T00:00:00`) : null;
  return d && !Number.isNaN(+d) ? d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : '';
}

export const digestTitle = digest => (digest?.kind === 'on_demand' ? 'On request' : `Week of ${dayLabel(digest?.week_of)}`);

// Era mix is 0 (classics) to 1 (new) in settings, shown as a percentage of newer picks.
export const toPercent = v => Math.round((Number(v) || 0) * 100);
export const fromPercent = v => Math.round(Number(v)) / 100;

export const REACTION_LABELS = { wanted: 'Added to wantlist', passed: 'Passed', owned: 'Marked as owned', listened: 'Opened for listening' };

export const searchUrl = card => `https://music.youtube.com/search?q=${encodeURIComponent(`${card.artist} ${card.album}`)}`;
export const listenUrl = card => card?.links?.youtube_music || searchUrl(card);

// A card with its reaction or wantlist state changed, in a list of digests.
export const withCard = (digests, card) => digests.map(d => (d.cards.some(c => c.id === card.id) ? { ...d, cards: d.cards.map(c => (c.id === card.id ? card : c)) } : d));

// Scan progress as a 0-1 number and which of the three steps are done.
export function scanProgress(scan) {
  if (!scan) return { value: 0, reading: false, mapping: false, finding: false };
  if (scan.state === 'done') return { value: 1, reading: true, mapping: true, finding: true };
  if (scan.state !== 'running') return { value: 0, reading: false, mapping: false, finding: false };
  if (scan.stage === 'mapping') return { value: 0.7, reading: true, mapping: false, finding: false };
  const value = scan.total ? Math.min(0.6, 0.6 * (scan.done / scan.total)) : 0.05;
  return { value, reading: false, mapping: false, finding: false };
}

// The in-app arrival notice: only an unread weekly digest counts (an on-demand result never does).
// The words for n albums; the desktop notice (src/electron/arrival.cjs arrivalText) says the same.
export const arrivalText = n => (n === 1 ? "This week's album is in" : `This week's ${n} albums are in`);

export function weeklyArrival(digest) {
  if (!digest || digest.kind !== 'weekly' || digest.read_at) return null;
  return { id: digest.id, count: (digest.cards || []).length };
}

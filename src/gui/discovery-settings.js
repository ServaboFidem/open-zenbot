// Settings > Calendar > Event discovery: the draft the form edits and the rules for saving it. Pure, so it is tested
// without the page (tests/gui/discovery-settings.test.mjs). The harness checks everything again on save.

export const URL_HINT = 'Open eventbrite.com, search your city, and copy the address of the results page.';
export const MAX_INTERESTS = 2000;

const FIELDS = ['enabled', 'city', 'state', 'source_url', 'interests'];

// What the form edits, from GET /discover/status. `country` is always US and `place` is the harness's, so neither is here.
export function draftFrom(status) {
  const s = status?.settings || {};
  return {
    enabled: !!s.enabled, city: s.city || '', state: s.state || '', source_url: s.source_url || '',
    categories: Array.isArray(s.categories) ? [...s.categories] : [], interests: s.interests || '',
  };
}

export function changed(draft, status) {
  const was = draftFrom(status);
  if (FIELDS.some(k => draft[k] !== was[k])) return true;
  const a = [...draft.categories].sort(), b = [...was.categories].sort();
  return a.length !== b.length || a.some((t, i) => t !== b[i]);
}

export function canSave(draft) {
  if (draft.enabled && !draft.source_url.trim()) return { ok: false, why: 'Paste the address of your city’s Eventbrite page first.' };
  if (draft.interests.length > MAX_INTERESTS) return { ok: false, why: 'Keep your interests under 2,000 characters.' };
  return { ok: true, why: '' };
}

export function toggleCategory(tags, tag, on) {
  const rest = tags.filter(t => t !== tag);
  return on ? [...rest, tag] : rest;
}

// The body of PUT /discover/settings. The harness reads the place from the address.
export const bodyFrom = draft => ({ ...draft, country: 'US', city: draft.city.trim(), state: draft.state.trim(), source_url: draft.source_url.trim() });

// The weekly run's next time, read-only here: its day and time are changed on the Routines page.
export function nextRunLine(routine) {
  if (!routine || !routine.enabled || !routine.next_run) return '';
  const at = new Date(routine.next_run);
  if (Number.isNaN(at.getTime())) return '';
  return `Next weekly run: ${at.toLocaleString(undefined, { weekday: 'long', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}.`;
}

// Settings > Calendar > Event discovery: the draft the form edits and the rules for saving it. Pure, so it is tested
// without the page (tests/gui/discovery-settings.test.mjs). The harness checks everything again on save.

export const URL_HINT = 'Open eventbrite.com, search your city, and copy the address of the results page.';
export const MAX_INTERESTS = 2000;
export const KEY_HINT = 'Make a free account at findlocal.community, then copy a key from Dashboard > Keys.';
export const FINDLOCAL_FAILED = 'The last FindLocal read did not finish.';
const PROVIDER_SENTENCE = 'FindLocal sent an answer OpenZenBot could not use.';
export const FINDLOCAL_ERRORS = {
  key: 'FindLocal did not accept the key. Check it here.',
  refused: 'FindLocal is turning requests away for now. The free plan allows 100 requests a month.',
  network: 'Could not reach FindLocal.',
  source_changed: PROVIDER_SENTENCE,
  provider: PROVIDER_SENTENCE,
};

const FIELDS = ['enabled', 'city', 'state', 'source_url', 'interests', 'findlocal_city'];

// What the form edits, from GET /discover/status. `country` is always US and `place` is the harness's, so neither is here.
export function draftFrom(status) {
  const s = status?.settings || {};
  return {
    enabled: !!s.enabled, city: s.city || '', state: s.state || '', source_url: s.source_url || '',
    categories: Array.isArray(s.categories) ? [...s.categories] : [], interests: s.interests || '',
    findlocal_city: status?.findlocal?.city || '',
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
// `zone` is the browser's own time zone (the page reads it); it is an argument so this module needs no browser.
export const bodyFrom = (draft, zone) => ({ ...draft, country: 'US', city: draft.city.trim(), state: draft.state.trim(), source_url: draft.source_url.trim(), timezone: zone });

// The weekly run's next time, read-only here: its day and time are changed on the Routines page.
export function nextRunLine(routine) {
  if (!routine || !routine.enabled || !routine.next_run) return '';
  const at = new Date(routine.next_run);
  if (Number.isNaN(at.getTime())) return '';
  return `Next weekly run: ${at.toLocaleString(undefined, { weekday: 'long', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}.`;
}

// "columbia-mo" -> "Columbia Mo"
export const cityName = slug => String(slug).split('-').filter(Boolean).map(w => w[0].toUpperCase() + w.slice(1)).join(' ');

export function cityOptions(status) {
  const slugs = Array.isArray(status?.findlocal?.cities) ? status.findlocal.cities : [];
  return slugs.map(slug => ({ slug, name: cityName(slug) })).sort((a, b) => a.name.localeCompare(b.name));
}

export function zoneLine(status) {
  const z = status?.findlocal?.timezone;
  return z ? `Event times are read as ${z}.` : '';
}

// The last FindLocal read, or why it failed. Nothing until a city and a key are both set.
export function findLocalLine(status) {
  const f = status?.findlocal;
  if (!f || !f.city || !f.key_set) return '';
  if (f.last_error) return FINDLOCAL_ERRORS[f.last_error] || FINDLOCAL_FAILED;
  const at = f.last_refresh ? new Date(f.last_refresh) : null;
  if (!at || Number.isNaN(at.getTime())) return 'Not read yet. The next run reads it.';
  return `Last read: ${at.toLocaleString(undefined, { weekday: 'long', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}.`;
}

// Enter in the key field saves the key, never the settings form (they are saved apart). Returns whether it was
// handled. `state` is { key, busy }, as the Save key button sees them.
export function keyFieldEnter(event, state, saveKey) {
  if (event?.key !== 'Enter' || event.nativeEvent?.isComposing) return false;
  event.preventDefault();
  if (!state.busy && String(state.key ?? '').trim()) saveKey();
  return true;
}

// Which field a refusal of PUT /discover/settings shows under: the harness's two FindLocal city sentences under
// the FindLocal city, everything else under the Eventbrite address.
const FINDLOCAL_CITY_REFUSALS = ['Pick a FindLocal city from the list.', 'The time zone is missing. Pick the FindLocal city again.'];
export const refusalField = text => (FINDLOCAL_CITY_REFUSALS.includes(text) ? 'findlocal_city' : 'url');

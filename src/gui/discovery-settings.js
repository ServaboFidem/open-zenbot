// Settings > Calendar > Event discovery: the draft the form edits and the rules for saving it. Pure, so it is tested
// without the page (tests/gui/discovery-settings.test.mjs). The harness checks everything again on save.

export const URL_HINT = 'Open eventbrite.com, search your city, and copy the address of the results page.';
export const MAX_INTERESTS = 2000;
export const KEY_HINT = 'Make a free account at findlocal.community, then copy a key from Dashboard > Keys.';
export const FINDLOCAL_FAILED = 'The last FindLocal read did not finish.';

// What Settings and the Events page say when the Facebook helper cannot see
// Facebook anymore. One plain sentence, shared by the settings search index
// and the visible settings block, so both name the same state.
export const FACEBOOK_LOGIN_LOSS = 'If Facebook signed you out, the Facebook tab says the helper is not ready. Sign back in to Facebook in this browser and the helper picks up on its next check.';

const PROVIDER_SENTENCE = 'FindLocal sent an answer OpenZenBot could not use.';
export const FINDLOCAL_ERRORS = {
  key: 'FindLocal did not accept the key. Check it here.',
  refused: 'FindLocal is turning requests away for now. The free plan allows 100 requests a month.',
  network: 'Could not reach FindLocal.',
  source_changed: PROVIDER_SENTENCE,
  provider: PROVIDER_SENTENCE,
};

export const ZONE_HINT = 'FindLocal gives times with no zone, so they are read in this one. It starts as your computer’s; change it if the city is somewhere else. For example America/Chicago.';
export const ZONE_NEEDED = 'Enter the time zone of your FindLocal city, like America/Chicago.';

const FIELDS = ['enabled', 'city', 'state', 'source_url', 'interests', 'findlocal_city', 'timezone'];

// Whether `zone` is a time zone name this computer knows (Intl refuses any other).
export function validZone(zone) {
  if (typeof zone !== 'string' || !zone.trim()) return false;
  try { new Intl.DateTimeFormat('en-US', { timeZone: zone.trim() }); return true; } catch { return false; }
}

// The draft's FindLocal city and zone after a pick. A new city starts with the computer's zone (`zone`), a first
// guess the user can correct; the same city keeps what is there; no city has no zone.
export function pickCity(draft, slug, zone) {
  if (!slug) return { findlocal_city: '', timezone: '' };
  if (slug === draft.findlocal_city && draft.timezone) return { findlocal_city: slug, timezone: draft.timezone };
  return { findlocal_city: slug, timezone: zone || '' };
}

// What the form edits, from GET /discover/status. `country` is always US and `place` is the harness's, so neither is here.
export function draftFrom(status) {
  const s = status?.settings || {};
  return {
    enabled: !!s.enabled, city: s.city || '', state: s.state || '', source_url: s.source_url || '',
    categories: Array.isArray(s.categories) ? [...s.categories] : [], interests: s.interests || '',
    findlocal_city: status?.findlocal?.city || '',
    timezone: status?.findlocal?.timezone || '',   // the FindLocal city's zone, which the user may correct
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
  if (draft.findlocal_city && !validZone(draft.timezone)) return { ok: false, why: ZONE_NEEDED };
  return { ok: true, why: '' };
}

export function toggleCategory(tags, tag, on) {
  const rest = tags.filter(t => t !== tag);
  return on ? [...rest, tag] : rest;
}

// The body of PUT /discover/settings. The harness reads the place from the address.
// The zone is the draft's own (what the user sees and may correct); `zone`, the browser's time zone, is used only
// when the draft has none. It is an argument so this module needs no browser.
export const bodyFrom = (draft, zone) => ({ ...draft, country: 'US', city: draft.city.trim(), state: draft.state.trim(), source_url: draft.source_url.trim(), timezone: (draft.timezone || '').trim() || zone });

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

// Pure decisions for the Discover page: what it shows, how a card's time reads, when it asks "Did you
// sign up?", and the instants sent when a card is added to the calendar. No React here, so tests/gui can
// check them.
import { addDays, instantToParts, dayTitle, localZone, wallToInstant } from './dates.js';
import { OFFLINE } from './page-state.js';

// How long the page waits for the scout before giving up: the same 15 minutes as DIG_LIMIT_MS in
// src/gui/music/MusicArea.jsx (which a node test cannot import).
export const LOOK_LIMIT_MS = 15 * 60 * 1000;

const HOUR_MS = 3600000;
const toIso = ms => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');

// `zone` when Intl knows it, else the computer's: a card with no zone or a made-up one must not throw.
function usableZone(zone) {
  if (!zone) return localZone();
  try { new Intl.DateTimeFormat('en-US', { timeZone: zone }); return zone; } catch { return localZone(); }
}

const minutesOf = parts => Number(parts.time.slice(0, 2)) * 60 + Number(parts.time.slice(3));

// The instant of a wall time. wallToInstant puts a time inside a daylight-saving gap an hour late (02:30
// becomes 03:30); the first real minute after the gap is what the listing's start means, so step back to
// the minute the clock jumped to.
function instantOf(key, time, zone) {
  const iso = wallToInstant(key, time, zone);
  const want = parseInt(time.slice(0, 2), 10) * 60 + parseInt(time.slice(3), 10);
  const there = instantToParts(iso, zone);
  if (there.key === key && minutesOf(there) === want) return iso;   // an ordinary time
  let t = Date.parse(iso);
  for (let i = 0; i < 180; i++) {
    const before = instantToParts(toIso(t - 60000), zone);
    const now = instantToParts(toIso(t), zone);
    const gap = (minutesOf(now) - minutesOf(before) + 1440) % 1440;
    if (gap !== 1) return toIso(t);   // the clock jumped here: the first real minute
    t -= 60000;
  }
  return iso;
}

// What POST /discover/cards/{id}/add takes. A timed card: instants in the card's own zone, the end an
// hour after the start when the listing gives none. An all-day card: the start date and the day after the
// last day.
export function addTimes(card) {
  if (!card.start_time) {
    return { start: card.start_date, end: addDays(card.end_date || card.start_date, 1) };
  }
  const zone = usableZone(card.timezone);
  const start = instantOf(card.start_date, card.start_time, zone);
  if (card.end_date && card.end_time) return { start, end: instantOf(card.end_date, card.end_time, zone) };
  return { start, end: toIso(Date.parse(start) + HOUR_MS) };
}

const dayName = key => dayTitle(key, { weekday: 'short', day: 'numeric', month: 'short' });

function zoneName(card, zone) {
  try {
    const own = usableZone(card.timezone);
    if (!card.timezone || own !== card.timezone || own === zone) return '';
    const at = new Date(Date.parse(instantOf(card.start_date, card.start_time, own)));
    const part = new Intl.DateTimeFormat('en-US', { timeZone: own, timeZoneName: 'short' }).formatToParts(at).find(p => p.type === 'timeZoneName');
    return part ? part.value : own;
  } catch { return ''; }
}

// The card's time as the event's own wall clock; the zone is named when it differs from the computer's.
export function whenLabel(card, zone = localZone()) {
  if (!card.start_time) {
    const last = card.end_date || card.start_date;
    return last > card.start_date ? `${dayName(card.start_date)} – ${dayName(last)}, all day` : `${dayName(card.start_date)}, all day`;
  }
  const name = zoneName(card, zone);
  const tail = name ? ` ${name}` : '';
  const head = `${dayName(card.start_date)}, ${card.start_time}`;
  if (!card.end_time) return `${head}${tail}`;
  const end = card.end_date || card.start_date;
  if (end === card.start_date) return `${head} – ${card.end_time}${tail}`;
  return `${head} – ${dayName(end)}, ${card.end_time}${tail}`;
}

export const whereLabel = card => [card.venue, card.city].filter(Boolean).join(', ');

export const shownCards = digest => (digest?.cards || []).filter(c => c.state !== 'dismissed');

// What the Discover tab shows. opts: online (the app's harness check), loaded (the first status and
// digest have answered), looking (a run this page started). action: '' | 'retry' | 'settings' | 'run'.
export function viewState(status, digest, { online = true, loaded = true, looking = false } = {}) {
  if (!online) return { kind: 'offline', text: OFFLINE, action: 'retry' };
  if (!loaded || !status) return { kind: 'loading', text: 'Loading…', action: '' };
  if (!status.set_up) return { kind: 'not_set_up', text: 'Set up event discovery to see events near you.', action: 'settings' };
  if (looking || status.running) return { kind: 'looking', text: 'Looking for events…', action: '' };
  if (!digest || !(digest.cards || []).length) return { kind: 'empty', text: 'No events yet.', action: 'run' };
  if (!shownCards(digest).length) return { kind: 'digest', text: 'You have dealt with every event in this digest.', action: '' };
  return { kind: 'digest', text: '', action: '' };
}

const REFRESH_ERRORS = {
  network: 'Could not reach Eventbrite.',
  refused: 'Eventbrite is turning requests away for now.',
  source_changed: 'Eventbrite’s page has changed and OpenZenBot cannot read it yet.',
  provider: 'Eventbrite sent an answer OpenZenBot could not use.',
};
const REFRESH_FAILED = 'The last refresh did not finish.';   // a kind this page does not know

// What a failed refresh says, '' when the last one was clean. With events kept from an earlier read it says so.
export function refreshNotice(status) {
  const error = status?.refresh?.error;
  if (!error) return '';
  const why = Object.hasOwn(REFRESH_ERRORS, error) ? REFRESH_ERRORS[error] : REFRESH_FAILED;
  return status.refresh.pool > 0 ? `${why} Showing what was read.` : why;
}

// "Did you sign up?": the card whose Eventbrite link was followed is remembered (pending); the next focus
// asks about it once. event: { type: 'opened', cardId } | { type: 'focus' } | { type: 'answered' }.
// Returns { pending, ask }: ask is the card id to ask about, or null.
export function signupStep(pending, event) {
  switch (event?.type) {
    case 'opened': return { pending: event.cardId, ask: null };
    case 'focus': return { pending: null, ask: pending || null };
    case 'answered': return { pending: null, ask: null };
    default: return { pending: pending ?? null, ask: null };
  }
}

// Pure decisions for the Discover page: what it shows, how a card's time reads, when it asks "Did you
// sign up?", and the instants sent when a card is added to the calendar. No React here, so tests/gui can
// check them.
import { addDays, instantToParts, dayTitle, localZone, wallToInstant } from './dates.js';
import { OFFLINE } from './page-state.js';
import { FINDLOCAL_ERRORS, FINDLOCAL_FAILED } from '../discovery-settings.js';

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
// A zone the listing gave and Intl accepts. A timed card without one is not added (the harness refuses it too).
function knownZone(zone) {
  if (typeof zone !== 'string' || !zone) return false;
  try { new Intl.DateTimeFormat('en-US', { timeZone: zone }); return true; } catch { return false; }
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
  // Up to 180 minutes back: no gap is longer. If it ever runs out, the late instant wallToInstant gave is
  // returned unchanged (an hour late, still on the right day), never an error.
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

// Card text is written by strangers, so every field is checked before it reaches the date helpers (which
// throw on a date they cannot read). A date is usable when it is a real calendar day, a time when it is HH:MM.
const isDay = v => {
  if (typeof v !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(v)) return false;
  const [y, m, d] = v.split('-').map(Number);
  const t = new Date(Date.UTC(y, m - 1, d));
  return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d;
};
const isClock = v => typeof v === 'string' && /^([01]\d|2[0-3]):[0-5]\d$/.test(v);
const absent = v => v === '' || v === null || v === undefined;

// What POST /discover/cards/{id}/add takes, or null when the card cannot be added: its start date is not
// a real date, it has a start time that is not a time, or it is timed and its zone is missing or unknown. A timed card gives instants in the card's own
// zone; an end that is unusable or not after the start counts as no listed end, so the end is an hour after
// the start. An all-day card gives the start date and the day after the last day (the start date's own
// next day when the end is unusable or before the start).
export function addTimes(card) {
  try {
    const c = card || {};
    if (!isDay(c.start_date)) return null;
    if (absent(c.start_time)) {
      const last = isDay(c.end_date) && c.end_date >= c.start_date ? c.end_date : c.start_date;
      return { start: c.start_date, end: addDays(last, 1) };
    }
    if (!isClock(c.start_time)) return null;
    if (!knownZone(c.timezone)) return null;
    const zone = c.timezone;
    const start = instantOf(c.start_date, c.start_time, zone);
    const hourLater = toIso(Date.parse(start) + HOUR_MS);
    if (isDay(c.end_date) && isClock(c.end_time)) {   // the harness takes a listed end only with both
      const end = instantOf(c.end_date, c.end_time, zone);
      if (Date.parse(end) > Date.parse(start)) return { start, end };
    }
    return { start, end: hourLater };
  } catch { return null; }
}

export const canAdd = card => addTimes(card) !== null;

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
// Never throws: no usable start date says so, and a start time that is not a time leaves the day alone.
export function whenLabel(card, zone = localZone()) {
  try {
    const c = card || {};
    if (!isDay(c.start_date)) return 'Date not given';
    const lastDay = isDay(c.end_date) && c.end_date >= c.start_date ? c.end_date : c.start_date;
    if (absent(c.start_time)) {
      return lastDay > c.start_date ? `${dayName(c.start_date)} – ${dayName(lastDay)}, all day` : `${dayName(c.start_date)}, all day`;
    }
    if (!isClock(c.start_time)) return dayName(c.start_date);
    const name = zoneName(c, zone);
    const tail = name ? ` ${name}` : '';
    const head = `${dayName(c.start_date)}, ${c.start_time}`;
    if (!isClock(c.end_time)) return `${head}${tail}`;
    if (lastDay === c.start_date) return `${head} – ${c.end_time}${tail}`;
    return `${head} – ${dayName(lastDay)}, ${c.end_time}${tail}`;
  } catch { return 'Date not given'; }
}

export const whereLabel = card => [card?.venue, card?.city].filter(v => typeof v === 'string' && v).join(', ');

export const shownCards = digest => (Array.isArray(digest?.cards) ? digest.cards : []).filter(c => c && c.state !== 'dismissed');

// FindLocal's required credit: a fixed link, never built from data.
export const CREDIT = { text: 'Event data by FindLocal', href: 'https://findlocal.community' };
export const needsCredit = digest => shownCards(digest).some(c => c.source === 'findlocal');

// The one place that decides a card's outside link (its text comes from a public listing). An Eventbrite
// card (source missing or 'eventbrite') links only to an Eventbrite page; a FindLocal card to any https
// address; any other source, or anything that is not such a string, gets no link.
export function cardLink(card) {
  const none = { href: '', label: '' };
  const url = card?.url;
  if (typeof url !== 'string') return none;
  const source = card.source ?? 'eventbrite';
  if (source === 'eventbrite') return url.startsWith('https://www.eventbrite.com/') ? { href: url, label: 'View on Eventbrite' } : none;
  if (source === 'findlocal') return url.startsWith('https://') ? { href: url, label: 'View event page' } : none;
  return none;
}

// What the Discover tab shows. opts: online (the app's harness check), loaded (the first status and
// digest have answered), looking (a run this page started), arrived (a digest came in while the run went). action: '' | 'retry' | 'settings' | 'run'.
export function viewState(status, digest, { online = true, loaded = true, looking = false, arrived = false } = {}) {
  if (!online) return { kind: 'offline', text: OFFLINE, action: 'retry' };
  if (!loaded || !status) return { kind: 'loading', text: 'Loading…', action: '' };
  if (!status.set_up) return { kind: 'not_set_up', text: 'Set up event discovery to see events near you.', action: 'settings' };
  const hasCards = !!digest && Array.isArray(digest.cards) && digest.cards.length > 0;
  // a digest that came in during this run shows, whatever the status still says
  if ((looking || status.running) && !(arrived && hasCards)) return { kind: 'looking', text: 'Looking for events…', action: '' };
  if (!digest || !Array.isArray(digest.cards) || !digest.cards.length) return { kind: 'empty', text: 'No events yet.', action: 'run' };
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
// Settings' sentences, but on this tab the key is checked in Settings, not "here".
const FINDLOCAL_TAB_ERRORS = { ...FINDLOCAL_ERRORS, key: 'FindLocal did not accept the key. Check it in Settings.' };

export function refreshNotice(status) {
  const error = status?.refresh?.error;
  const flError = status?.findlocal?.last_error;
  if (!error && !flError) return '';
  const why = error
    ? (Object.hasOwn(REFRESH_ERRORS, error) ? REFRESH_ERRORS[error] : REFRESH_FAILED)
    : (Object.hasOwn(FINDLOCAL_TAB_ERRORS, flError) ? FINDLOCAL_TAB_ERRORS[flError] : FINDLOCAL_FAILED);
  return status.refresh?.pool > 0 ? `${why} Showing what was read.` : why;
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

// How long a run can go unseen as running before "the scout stopped" is believed: the harness may not show
// `running` the instant run() returns.
export const RUN_APPEAR_MS = 20000;

// One look at a run this page is waiting for. in: since and now (ms), known (digest ids from before the run),
// digests (the list just loaded, or null when the load failed), running (the status's flag), seenRunning (a
// look has already seen it running), mine (this page started the run). Returns { done, notice, seenRunning,
// arrived }; notice is '' when not done; arrived says a digest not in known is in.
// A run this page started is done on its first new digest, or when the scout stops, or at the time limit.
// A run that is not its own (the weekly one, or one from before the page was opened) has `known` seeded with
// the digests on hand and is done only when the run is seen stopped or time is up: it never stops looking
// while the last status says running. A load that failed gives digests null: it can still run out of time,
// but never decides the scout stopped.
export function lookStep({ since, now, known = [], digests = null, running = false, seenRunning = false, mine = true }) {
  const loaded = Array.isArray(digests);
  const arrived = loaded && digests.some(d => !known.includes(d.id));
  if (arrived && mine) return { done: true, notice: 'New events are in.', seenRunning, arrived };
  if (now - since > LOOK_LIMIT_MS) return { done: true, notice: 'The scout has not finished. Check back later.', seenRunning, arrived };
  const seen = seenRunning || (loaded && !!running);
  if (loaded && !running && (!mine || seen || now - since >= RUN_APPEAR_MS)) {
    return { done: true, notice: arrived ? '' : 'The scout finished without a digest.', seenRunning: seen, arrived };
  }
  return { done: false, notice: '', seenRunning: seen, arrived };
}

// The card the "Did you sign up?" dialog is about: only one on screen now (`shown`, the cards drawn) that is
// still new. null otherwise.
export function askable(cardId, shown) {
  if (!cardId || !Array.isArray(shown)) return null;
  const card = shown.find(c => c && c.id === cardId);
  return card && card.state === 'new' ? card : null;
}

// Whether "Find events now" is offered at the top: set up, nothing running, and the status known.
export const canRun = (status, { online = true, looking = false } = {}) => !!(online && status && status.set_up && !looking && !status.running);

// Whether the first load has settled for viewState: a load that failed before any status is the error view's.
export const firstLoadDone = ({ loaded = false, status = null, loadError = '' } = {}) => !!loaded && !(!status && !!loadError);

// Pure decisions for the All Events grid: filtering, sorting, how a row reads, when Refresh is offered, and
// what the tab shows. No DOM and no fetch here, so tests/gui can check them. `now` is milliseconds since the
// epoch (Date.now()), as in lookStep.

export const EMPTY_FILTERS = { text: '', category: '', source: '', from: '', to: '' };

export const COLUMNS = [
  { key: 'date', label: 'Date' },
  { key: 'time', label: 'Time' },
  { key: 'name', label: 'Event' },
  { key: 'venue', label: 'Venue' },
  { key: 'category', label: 'Category' },
  { key: 'source', label: 'Source' },
];
export const DEFAULT_SORT = { key: 'date', dir: 'asc' };
export const SOURCE_NAMES = { eventbrite: 'Eventbrite', findlocal: 'FindLocal' };
export const MARKS = {
  recommended: { glyph: '★', label: 'Recommended' },
  added: { glyph: '✓', label: 'On your calendar' },
};
export const READING = 'Reading events… this takes a couple of minutes.';
export const NO_EVENTS = 'No events yet.';
export const NO_MATCH = 'No events match.';
export const PAGE = 200;

const HOUR_MS = 3600000;
const REFRESH_AFTER_MS = 12 * HOUR_MS;   // Eventbrite is read again no sooner than the harness's own 12 hours
const NEAR_END_PX = 600;

const str = v => (typeof v === 'string' ? v : '');
const list = v => (Array.isArray(v) ? v.filter(e => e && typeof e === 'object') : []);
const names = e => (Array.isArray(e?.category_names) ? e.category_names.filter(n => typeof n === 'string' && n) : []);

// The rows that pass every filter that is set ("and"). Dates are inclusive, against the start date.
export function filterEvents(events, filters) {
  const f = { ...EMPTY_FILTERS, ...(filters || {}) };
  const text = str(f.text).trim().toLowerCase();
  return list(events).filter(e => {
    if (text && ![e.name, e.venue, e.city].some(v => str(v).toLowerCase().includes(text))) return false;
    if (f.category && !names(e).includes(f.category)) return false;
    if (f.source && e.source !== f.source) return false;
    if (f.from && !(str(e.start_date) >= f.from)) return false;
    if (f.to && !(str(e.start_date) <= f.to)) return false;
    return true;
  });
}

// A header click: the same column flips, a new one starts ascending.
export const nextSort = (sort, key) => (sort?.key === key ? { key, dir: sort.dir === 'asc' ? 'desc' : 'asc' } : { key, dir: 'asc' });

const text = (a, b) => str(a).localeCompare(str(b), undefined, { sensitivity: 'base' });
const tie = (a, b) => str(a.start_date).localeCompare(str(b.start_date)) || str(a.start_time).localeCompare(str(b.start_time)) || text(a.name, b.name);
const sortText = {
  date: e => str(e.start_date),
  time: e => str(e.start_time),   // '' (all day) sorts first
  name: e => str(e.name),
  venue: e => str(e.venue),
  category: e => names(e)[0] || '',
  source: e => str(e.source),
};

// A new array in the sort's order. An empty venue or category is last whichever way it goes; ties fall back to
// date, time (all day first), name.
export function sortEvents(events, sort) {
  const { key, dir } = { ...DEFAULT_SORT, ...(sort || {}) };
  const get = sortText[key] || sortText.date;
  const sign = dir === 'desc' ? -1 : 1;
  const blankLast = key === 'venue' || key === 'category';
  return list(events).slice().sort((a, b) => {
    const x = get(a), y = get(b);
    if (blankLast && (x === '') !== (y === '')) return x === '' ? 1 : -1;
    const c = key === 'date' || key === 'time' ? x.localeCompare(y) : text(x, y);
    return c ? c * sign : tie(a, b);
  });
}

export const categoryOptions = events => [...new Set(list(events).flatMap(names))].sort(text);
export const sourcesPresent = events => [...new Set(list(events).map(e => e.source).filter(s => typeof s === 'string' && s))].sort();
export const showSourceFilter = events => sourcesPresent(events).length === 2;

const DAY = /^(\d{4})-(\d{2})-(\d{2})$/;
const CLOCK = /^([01]\d|2[0-3]):([0-5]\d)$/;
// "2026-11-21" as "Sat, Nov 21": the listing's own day, formatted at UTC so no zone moves it.
function dayText(key) {
  const m = DAY.exec(str(key));
  if (!m) return '';
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const at = new Date(Date.UTC(y, mo - 1, d));
  if (at.getUTCFullYear() !== y || at.getUTCMonth() !== mo - 1 || at.getUTCDate() !== d) return '';
  return new Intl.DateTimeFormat('en-US', { timeZone: 'UTC', weekday: 'short', month: 'short', day: 'numeric' }).format(at);
}
function clockText(t) {
  const m = CLOCK.exec(str(t));
  if (!m) return '';
  const h = Number(m[1]);
  return `${h % 12 || 12}:${m[2]} ${h < 12 ? 'AM' : 'PM'}`;
}

// A row's cells as strings. Times are the listing's own wall clock, so `zone` changes nothing (kept so the
// page can pass one). The venue gets the city after it unless the city is empty or already in the venue.
export function cells(event, zone) {
  const e = event || {};
  const venue = str(e.venue), city = str(e.city);
  const where = venue && city && !venue.toLowerCase().includes(city.toLowerCase()) ? `${venue}, ${city}` : (venue || city);
  const source = str(e.source);
  return {
    date: dayText(e.start_date),
    time: e.start_time === '' || e.start_time === undefined || e.start_time === null ? (event ? 'All day' : '') : clockText(e.start_time),
    name: str(e.name),
    venue: where,
    category: names(e)[0] || '',
    source: Object.hasOwn(SOURCE_NAMES, source) ? SOURCE_NAMES[source] : source,
  };
}

// "612 of 1,640 events", "1,640 events", "1 event".
export function countLabel(shown, total) {
  const n = v => Number(v).toLocaleString('en-US');
  const noun = total === 1 ? 'event' : 'events';
  return shown === total ? `${n(total)} ${noun}` : `${n(shown)} of ${n(total)} ${noun}`;
}

// How long ago the pool was read, from `read_at` (a UTC instant) and now (ms).
export function readLabel(readAt, now) {
  const at = Date.parse(str(readAt));
  if (!str(readAt) || Number.isNaN(at)) return 'Not read yet';
  const ms = now - at;
  const plural = (n, unit) => `Read ${n} ${unit}${n === 1 ? '' : 's'} ago`;
  if (!(ms >= 60000)) return 'Read just now';
  if (ms < HOUR_MS) return plural(Math.floor(ms / 60000), 'minute');
  if (ms < 24 * HOUR_MS) return plural(Math.floor(ms / HOUR_MS), 'hour');
  return plural(Math.floor(ms / (24 * HOUR_MS)), 'day');
}

// Whether Refresh is offered: set up, online, nothing refreshing, and Eventbrite's last read none or 12 hours old.
export function canRefresh(status, now, { online = true } = {}) {
  if (!online || !status || !status.set_up || status.refreshing) return false;
  const last = str(status.refresh?.last);
  if (!last) return true;
  const at = Date.parse(last);
  return Number.isNaN(at) || now - at >= REFRESH_AFTER_MS;
}

// What the tab shows. loaded: the list has answered; shown: the rows left after the filters.
export function gridState({ online = true, loaded = false, error = '', status = null, events = [], shown = [] } = {}) {
  if (!online) return 'offline';
  if (error) return 'error';
  if (!loaded || !status) return 'loading';
  if (!status.set_up) return 'setup';
  if (!list(events).length) return 'empty';
  if (!list(shown).length) return 'nomatch';
  return 'ready';
}

// More rows are drawn as the user nears the end.
export const visibleCount = (current, total) => Math.min(total, current + PAGE);
export const nearEnd = ({ scrollTop = 0, clientHeight = 0, scrollHeight = 0 } = {}) => scrollHeight - scrollTop - clientHeight <= NEAR_END_PX;

// The list with one event's state replaced (a new list; the other rows are shared).
export const withState = (events, id, state) => list(events).map(e => (e.id === id ? { ...e, state } : e));

// A key on a row: where focus goes, or open it; null for any other key.
export function rowKeyStep(key, index, count) {
  const last = Math.max(0, count - 1);
  switch (key) {
    case 'ArrowDown': return { index: Math.min(last, index + 1) };
    case 'ArrowUp': return { index: Math.max(0, index - 1) };
    case 'Home': return { index: 0 };
    case 'End': return { index: last };
    case 'Enter': case ' ': return { open: true };
    default: return null;
  }
}

// FindLocal asks for a credit wherever its data shows.
export const panelCredit = event => event?.source === 'findlocal';
export const gridCredit = events => list(events).some(panelCredit);

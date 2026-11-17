// Pure date helpers for the Events page. A day is a key "YYYY-MM-DD"; keys move by integer
// arithmetic on UTC date parts, never by parsing a bare date string (which JS reads as UTC and
// shifts west of it). A zone is an IANA name; Intl does every conversion.
const localZone = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
const pad = (n, w = 2) => String(n).padStart(w, '0');
const DAY_MS = 86400000;

export const dayKey = (year, month, day) => `${pad(year, 4)}-${pad(month)}-${pad(day)}`;

const keyParts = key => key.split('-').map(Number);
const keyUtc = key => { const [y, m, d] = keyParts(key); return Date.UTC(y, m - 1, d); };
const utcKey = ms => { const d = new Date(ms); return dayKey(d.getUTCFullYear(), d.getUTCMonth() + 1, d.getUTCDate()); };

export const addDays = (key, n) => utcKey(keyUtc(key) + n * DAY_MS);

const formatters = new Map();
function formatter(zone) {
  if (!formatters.has(zone)) {
    formatters.set(zone, new Intl.DateTimeFormat('en-US', {
      timeZone: zone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit',
    }));
  }
  return formatters.get(zone);
}

// The wall clock in `zone` at instant `ms`, as the UTC millisecond count of the same wall fields.
function wallMs(ms, zone) {
  const p = {};
  for (const { type, value } of formatter(zone).formatToParts(new Date(ms))) p[type] = Number(value);
  return Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
}

export function todayKey(now = new Date(), zone = localZone()) {
  return utcKey(wallMs(now.getTime(), zone));
}

export function monthGrid(year, month, weekStart = 1) {
  const first = Date.UTC(year, month - 1, 1);
  const lead = (new Date(first).getUTCDay() - weekStart + 7) % 7;
  const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const rows = Math.ceil((lead + days) / 7);
  const grid = [];
  for (let r = 0; r < rows; r++) {
    grid.push(Array.from({ length: 7 }, (_, c) => utcKey(first + (r * 7 + c - lead) * DAY_MS)));
  }
  return grid;
}

export function gridRange(grid) {
  const last = grid[grid.length - 1];
  return { start: grid[0][0], end: addDays(last[last.length - 1], 1) };
}

const toIso = ms => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');

export function instantToParts(iso, zone = localZone()) {
  const w = new Date(wallMs(Date.parse(iso), zone));
  return { key: utcKey(w.getTime()), time: `${pad(w.getUTCHours())}:${pad(w.getUTCMinutes())}` };
}

export function wallToInstant(key, time, zone = localZone()) {
  const [h, mi] = time.split(':').map(Number);
  const wall = keyUtc(key) + (h * 60 + mi) * 60000;
  const offsetAt = ms => wallMs(ms, zone) - ms;
  const before = offsetAt(wall - DAY_MS);
  const after = offsetAt(wall + DAY_MS);
  const fits = [before, after].map(o => wall - o).filter(t => wallMs(t, zone) === wall);
  // earlier of two (ambiguous), the only one, or (a gap) the offset from before it, an hour later
  return toIso(fits.length ? Math.min(...fits) : wall - before);
}

export function eventDays(event, zone = localZone()) {
  let first, last;
  if (event.all_day) {
    first = event.start; last = addDays(event.end, -1);
    if (last < first) last = first;
  } else {
    first = instantToParts(event.start, zone).key;
    const end = Date.parse(event.end);
    last = end > Date.parse(event.start) ? instantToParts(toIso(end - 1000), zone).key : first;
  }
  const days = [];
  for (let k = first; k <= last; k = addDays(k, 1)) days.push(k);
  return days;
}

export function eventsByDay(events, zone = localZone()) {
  const map = new Map();
  for (const e of events) {
    for (const k of eventDays(e, zone)) {
      if (!map.has(k)) map.set(k, []);
      map.get(k).push(e);
    }
  }
  for (const list of map.values()) {
    list.sort((a, b) => (b.all_day ? 1 : 0) - (a.all_day ? 1 : 0) || String(a.start).localeCompare(String(b.start)));
  }
  return map;
}

export function timeLabel(event, zone = localZone(), key) {
  if (event.all_day) return 'All day';
  const s = instantToParts(event.start, zone);
  if (event.end === event.start) return s.time;
  const e = instantToParts(event.end, zone);
  const days = eventDays(event, zone);
  if (key && days.length > 1) {
    if (key === days[0]) return `${s.time} →`;
    if (key === days[days.length - 1]) return `→ ${e.time}`;
    return 'All day';
  }
  return `${s.time} – ${e.time}`;
}

export function moveKey(key, keyName, weekStart = 1) {
  switch (keyName) {
    case 'ArrowLeft': return addDays(key, -1);
    case 'ArrowRight': return addDays(key, 1);
    case 'ArrowUp': return addDays(key, -7);
    case 'ArrowDown': return addDays(key, 7);
    case 'Home': case 'End': {
      const lead = (new Date(keyUtc(key)).getUTCDay() - weekStart + 7) % 7;
      return addDays(key, keyName === 'Home' ? -lead : 6 - lead);
    }
    default: return key;
  }
}

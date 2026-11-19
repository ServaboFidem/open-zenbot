// Settings > Harness settings > Dreaming: the requests and the decisions, no React, so they are tested without the
// page (tests/gui/dream.test.mjs). The harness checks everything again on save.

export const MIN_MINUTES = 1;
export const MAX_MINUTES = 120;
export const NO_REPORT = 'Dreaming starts on its own only in the desktop app. Use Dream now here.';
const UNREACHABLE = 'The dreaming model could not be reached. Trying again in 10 minutes.';
const MODEL_ANSWER = 'The dreaming model gave an answer that could not be used.';

const enc = encodeURIComponent;

// `fetchJson(method, path, body)` is createApi's `call`. Every POST carries a JSON body.
export function dreamApi(fetchJson) {
  return {
    status: () => fetchJson('GET', '/dream/status'),
    saveSettings: ({ enabled, idle_minutes }) => fetchJson('PUT', '/dream/settings', { enabled, idle_minutes }),
    runNow: () => fetchJson('POST', '/dream/run', {}),
    facts: () => fetchJson('GET', '/dream/facts'),
    forget: id => fetchJson('DELETE', `/memory/${enc(id)}`),
  };
}

// A dreaming model is chosen: only then may dreaming be switched on.
export const canEnable = status => !!status?.model;
export const canRunNow = (status, online) => !!status?.ready && !!online && !status?.dreaming;
export const validMinutes = v => {
  if (typeof v === 'string' && !/^\s*\d+\s*$/.test(v)) return false;
  const n = typeof v === 'string' ? Number(v) : v;
  return Number.isInteger(n) && n >= MIN_MINUTES && n <= MAX_MINUTES;
};

const two = n => String(n).padStart(2, '0');
// "today 03:12", or "18 Nov 03:12" on another day, in this computer's time.
function when(iso, now) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return 'never';
  const n = now instanceof Date ? now : new Date(now ?? Date.now());
  const time = `${two(d.getHours())}:${two(d.getMinutes())}`;
  if (d.toDateString() === n.toDateString()) return `today ${time}`;
  return `${d.toLocaleDateString('en-US', { day: 'numeric', month: 'short' })} ${time}`;
}

export function statusLine(status, now = new Date()) {
  const s = status || {};
  const waiting = s.waiting || 0, learned = s.learned || 0;
  const line = `Waiting: ${waiting} ${waiting === 1 ? 'conversation' : 'conversations'} · Learned so far: ${learned} ${learned === 1 ? 'fact' : 'facts'} · Last dream: ${s.last_dream_at ? when(s.last_dream_at, now) : 'never'}`;
  return s.dreaming ? `Dreaming now · ${line}` : line;
}

export function errorLine(status) {
  if (status?.error === 'unreachable') return UNREACHABLE;
  if (status?.error === 'model') return MODEL_ANSWER;
  return '';
}

// The desktop app reports idle time; with none arriving and dreaming on, it will not start by itself.
export const needsDesktopNote = status => !!status?.enabled && status?.idle_reported === false;

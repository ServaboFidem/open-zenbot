// Pure decisions for the Events page: which Google links it shows, and what the status line under the
// month says. No React here, so tests/gui can check them.
import { dayTitle, instantToParts, todayKey } from './dates.js';

export const OFFLINE = 'The harness is offline.';

// A pulled event's link opens outside the app, so only https on google.com or a subdomain of it.
export function isGoogleLink(link) {
  try {
    const u = new URL(String(link || ''));
    return u.protocol === 'https:' && (u.hostname === 'google.com' || u.hostname.endsWith('.google.com'));
  } catch { return false; }
}

// A sync time: the time alone when it is today, else the day and the time.
export function syncTime(iso, zone, now = new Date()) {
  const { key, time } = instantToParts(iso, zone);
  return key === todayKey(now, zone) ? time : `${dayTitle(key, { day: 'numeric', month: 'short' })} ${time}`;
}

// What a failed sync says, by the error kind in status.sync.error. Only `network` means Google was not reached.
const SYNC_ERRORS = {
  network: 'Could not reach Google.',
  auth: 'Google did not accept the sign-in.',
  forbidden: 'Google refused access to a calendar.',
  rate: 'Google is limiting requests for now.',
  provider: 'Google sent an answer OpenZenBot could not use.',
  internal: 'The sync stopped on an error in OpenZenBot.',
};
const SYNC_FAILED = 'The sync did not finish.';   // a kind this page does not know

export function lostEditNotice(count) {
  return count === 1 ? 'An event you edited was deleted on Google, so your edit was lost.'
    : `${count} events you edited were deleted on Google, so your edits were lost.`;
}

// What the page does about status.sync.lost_edits in a status it has just loaded, whoever ran the sync: null, or
// { notice (text to show, '' when this count was shown already), ack (the count to acknowledge to the harness) }.
// seen: shown (a count already shown whose acknowledgement failed), acking (an acknowledgement is on its way).
export function lostEditStep(status, { shown = 0, acking = false } = {}) {
  const count = status?.sync?.lost_edits;
  if (!Number.isInteger(count) || count <= 0 || acking) return null;
  return { notice: count > shown ? lostEditNotice(count - shown) : '', ack: count };
}

// The status line from GET /calendar/status (null before the first answer).
// opts: online (the app's harness check), failed (the last load failed), syncing (a sync this page started),
// zone, now. Returns { text, action: '' | 'settings' | 'retry', actionLabel, link (action drawn as a link),
// spinner, refresh (Refresh shows), refreshDisabled, ready (the calendar has loaded: New event works) }.
export function statusLine(status, { online = true, failed = false, syncing = false, zone, now = new Date() } = {}) {
  const line = { text: '', action: '', actionLabel: '', link: false, spinner: false, refresh: false, refreshDisabled: false, ready: false };
  if (!online || (!status && failed)) return { ...line, text: OFFLINE };
  if (!status) return line;   // the first load has not answered yet
  const ready = { ...line, ready: true };
  const state = status.google?.state;
  const sync = status.sync || {};
  if (state === 'not_set_up' || state === 'ready') {
    return { ...ready, text: 'Only on this computer.', action: 'settings', actionLabel: 'Connect Google Calendar in Settings', link: true };
  }
  if (state === 'needs_reconnect') {
    return { ...ready, text: 'Google sign-in has expired. Events already here are still shown.', action: 'settings', actionLabel: 'Reconnect in Settings' };
  }
  if (state !== 'connected') return ready;   // a state this page does not know: say nothing rather than guess
  const running = syncing || !!sync.running;
  const connected = { ...ready, refresh: true, refreshDisabled: running };
  if (running) return { ...connected, text: 'Syncing…', spinner: true };
  if (!sync.error) {
    const account = status.google.account ? ` with ${status.google.account}` : '';
    return { ...connected, text: sync.last ? `Synced ${syncTime(sync.last, zone, now)}${account}` : 'Not synced yet' };
  }
  const why = Object.hasOwn(SYNC_ERRORS, sync.error) ? SYNC_ERRORS[sync.error] : SYNC_FAILED;
  return { ...connected, action: 'retry', actionLabel: 'Retry',
    text: sync.last ? `${why} Showing what was here at ${syncTime(sync.last, zone, now)}.` : `${why} Showing what was here.` };
}

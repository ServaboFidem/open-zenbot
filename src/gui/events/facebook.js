// The Facebook tab's decisions: what the lists route answered, shaped for FacebookPanels. No React, no requests.
import { dayName } from './discover.js';

export const POLL_MS = 30000;   // the helper reports on its own rhythm, so the open tab looks again this often
export const LISTS_FAILED = 'The Facebook lists could not be loaded.';
export const EMPTY = { invites: [], approvals: [], birthdays: [] };

// The harness keeps the last error as a kind ('provider') or as the reason a report was refused (a shape's
// name, never listing text). The tab says one of two sentences, never the raw value.
const NOT_SAVED = 'The last Facebook report could not be saved.';
const REFUSED = 'The last Facebook report was refused.';

const list = v => (Array.isArray(v) ? v.filter(x => x && typeof x.id === 'string' && x.id) : []);

// The harness's intake status as HelperStatus reads it: { lastRead, error, ready, refreshing }.
// raw: the status the lists route answered, or null before the first answer. loading: a load is out.
// failed: this page's own load failed, so its sentence stands in for the harness's.
// Before the first answer the line says it is reading (while a load is out) or that the helper is not ready.
export function helperStatus(raw, { loading = false, failed = '' } = {}) {
  const s = raw || {};
  const error = failed || (s.error ? (s.error === 'provider' ? NOT_SAVED : REFUSED) : '');
  return {
    lastRead: typeof s.last_read === 'string' ? s.last_read : '',
    error,
    ready: raw ? !!s.ready : !!loading,
    refreshing: !raw && !!loading,
  };
}

// The three lists from GET /intake/facebook/lists. A birthday's `date` becomes the day as the cards write it.
export function shapeLists(answer) {
  const a = answer || {};
  return {
    invites: list(a.invites),
    approvals: list(a.approvals),
    birthdays: list(a.birthdays).map(p => ({ ...p, date: dayName(p.start_date) })),
  };
}

// The lists after a panel reported a change (onChanged: the item with its new state), before the next load
// answers: an added row is marked, a dismissed approval leaves.
export function withChange(lists, item) {
  if (!item || !item.id) return lists;
  const mark = rows => rows.map(r => (r.id === item.id ? { ...r, state: item.state } : r));
  return {
    invites: mark(lists.invites),
    approvals: item.state === 'dismissed' ? lists.approvals.filter(r => r.id !== item.id) : mark(lists.approvals),
    birthdays: lists.birthdays,
  };
}

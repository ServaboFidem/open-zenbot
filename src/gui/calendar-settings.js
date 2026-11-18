// The Calendar settings panel's pure parts: the steps for making a Google app, which sections show, what the
// push confirmation says and which calendars may be chosen. Pure, so it is tested without the page
// (tests/gui/calendar-settings.test.mjs).

export const APP_STEPS = [
  'Open console.cloud.google.com and create a project.',
  'Enable the Google Calendar API for it.',
  'Set up the consent screen as External and add yourself as a test user, or publish it. An app left in "Testing" signs you out about every 7 days; publishing it for your own use needs no review by Google.',
  'Create an OAuth client of type "Desktop app" and paste its client ID and client secret here.',
];

export const POLL_MS = 2000;
export const POLL_LIMIT_MS = 10 * 60 * 1000;

// Which sections follow "Your Google app" for a status from GET /calendar/status.
export function shownSections(status) {
  const state = status?.google?.state;
  return { google: true, calendars: state === 'connected', push: state === 'connected' };
}

// The sign-in finishes in the browser; the page notices by the state or the write permission changing.
export function signInChanged(before, after) {
  return !!before && !!after && (before.google.state !== after.google.state || before.google.can_write !== after.google.can_write);
}

const googleCalendars = list => (Array.isArray(list) ? list : []).filter(c => c && c.source === 'google');
export const bringInList = list => googleCalendars(list);
export const destinations = list => googleCalendars(list).filter(c => c.writable);

// The calendar a new push would go to: the saved one when still offered, else the primary, else the first.
export function defaultDestination(list, saved) {
  const options = destinations(list);
  return (options.find(c => c.id === saved) || options.find(c => c.primary) || options[0] || {}).id || '';
}

// "12 added, 3 updated, 1 removed on Google", from the harness's {add, update, remove}.
export function pushSentence(counts, calendarName) {
  const parts = [[counts?.add, 'added'], [counts?.update, 'updated'], [counts?.remove, 'removed']]
    .filter(([n]) => n > 0).map(([n, word]) => `${n} ${word}`);
  if (!parts.length) return `Nothing to send yet. Events you make from now on will be added to ${calendarName}.`;
  return `${parts.join(', ')} on Google`;
}

// What a failed PUT /calendar/push (turning push on) does: a 409 means the write permission is missing, so ask for
// it; anything else shows the harness's sentence as plain text.
export function pushFailure(err) {
  if (err && err.status === 409) return { kind: 'allow' };
  return { kind: 'show', text: (err && err.message) || 'Something went wrong.' };
}

// Facebook panels: invites, approval cards, birthdays, and helper status.
// The panels are presentational (props in, existing discover routes out), so these
// tests read FacebookPanels.jsx as text for structure and prove the shared add and
// status behavior through the helpers the panels reuse.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { addTimes, canAdd, whenLabel, whereLabel } from '../../src/gui/events/discover.js';
import { readLabel } from '../../src/gui/events/all-events.js';

const NO_DATE = 'This listing has no usable date.';

const src = readFileSync(new URL('../../src/gui/events/FacebookPanels.jsx', import.meta.url), 'utf8');

const invite = (over = {}) => ({
  id: 'fb1', name: 'Marta’s Birthday Bash', start_date: '2026-11-22', start_time: '19:00',
  end_date: '2026-11-22', end_time: '22:00', timezone: 'America/New_York',
  venue: 'The Hall', city: 'Dublin', state: 'new', ...over,
});
const approval = (over = {}) => ({
  id: 'ap1', name: 'Team Dinner', why: 'Your friend invited you.', start_date: '2026-11-23',
  start_time: '18:00', end_date: '2026-11-23', end_time: '20:00', timezone: 'America/New_York',
  venue: '', city: '', state: 'new', ...over,
});

// --- panel: the dashboard lists invites, approval cards, and birthdays ---

test('panel exports the dashboard and one panel per list', () => {
  for (const name of ['FacebookPanels', 'InvitesPanel', 'ApprovalsPanel', 'BirthdaysPanel', 'HelperStatus', 'InviteRow', 'ApprovalRow', 'BirthdayRow']) {
    assert.ok(src.includes(`export function ${name}`) || src.includes(`export default function ${name}`), name);
  }
});

test('panel draws the three sections with headings', () => {
  assert.ok(src.includes('<h3>Invites</h3>'));
  assert.ok(src.includes('<h3>Waiting for approval</h3>'));
  assert.ok(src.includes('<h3>Birthdays</h3>'));
  assert.ok(src.includes('<h2>Facebook</h2>'));
});

test('panel renders invites, approvals, and birthdays from props only', () => {
  assert.ok(src.includes('invites'), 'invites prop');
  assert.ok(src.includes('approvals'), 'approvals prop');
  assert.ok(src.includes('birthdays'), 'birthdays prop');
  assert.ok(!src.includes('fetch('), 'no fetch: reads come through props');
  assert.ok(!src.includes('dangerouslySetInnerHTML'), 'listings drawn as text');
});

test('panel empty states name each list', () => {
  assert.ok(src.includes('No Facebook invites.'));
  assert.ok(src.includes('Nothing waiting for approval.'));
  assert.ok(src.includes('No birthdays this week.'));
});

test('birthday nudge shows the verbatim phrase across the nudge', () => {
  assert.ok(src.includes('both chat and feed'), 'verbatim phrase');
  assert.ok(src.includes('BIRTHDAY_NUDGE'), 'one shared nudge');
  const line = src.split('\n').find(l => l.includes('BIRTHDAY_NUDGE ='));
  assert.ok(line && line.includes('both chat and feed'));
});

// --- status: freshness and errors from props ---

test('status reads last read, error, and ready through props', () => {
  assert.ok(src.includes('lastRead'), 'freshness');
  assert.ok(src.includes('status.error') || src.includes('s.error'), 'error');
  assert.ok(src.includes('ready'), 'ready flag');
  assert.ok(src.includes('refreshing'), 'refreshing flag');
  assert.ok(src.includes('HelperStatus'), 'status line component');
});

test('status freshness reuses the All Events read label', () => {
  assert.ok(src.includes('readLabel('), 'same freshness words as All Events');
  const at = '2026-11-18T12:00:00Z';
  const t = Date.parse(at);
  assert.equal(readLabel('', t), 'Not read yet');
  assert.equal(readLabel(at, t), 'Read just now');
  assert.equal(readLabel(at, t + 3600000), 'Read 1 hour ago');
});

test('status offline and not-ready sentences exist', () => {
  assert.ok(src.includes('The harness is offline.'));
  assert.ok(src.includes('The Facebook helper is not ready yet.'));
  assert.ok(src.includes("role={isError ? 'alert' : 'status'}") || src.includes('role='), 'error is an alert, freshness is a status');
});

// --- add: same behavior as today, through the existing routes ---

test('add uses the existing pool add route only', () => {
  assert.ok(!src.includes('discover.add('), 'a Facebook row has no card: the card add route would answer 404');
  assert.equal(src.split('discover.addEvent(').length - 1, 2, 'pool add route for invites and approvals');
  assert.ok(!src.includes('/facebook'), 'no new add path');
  assert.ok(!src.includes('discover.run('), 'panels do not start runs');
  assert.ok(!src.includes('discover.refresh('), 'panels do not refresh sources');
});

test('add sends the listing’s own instants and refuses an unusable date like today', () => {
  assert.ok(src.includes('addTimes('), 'instants from the listing');
  assert.deepEqual(addTimes(invite()), { start: '2026-11-23T00:00:00Z', end: '2026-11-23T03:00:00Z' });
  assert.equal(addTimes(invite({ start_date: 'soon' })), null);
  assert.equal(canAdd(invite()), true);
  assert.equal(canAdd(invite({ start_date: 'soon' })), false);
});

test('add shows On your calendar once added and the no-date note when not addable', () => {
  assert.ok(src.includes('On your calendar'), 'added mark like EventCard');
  assert.ok(src.includes('NO_DATE'), 'same no-date sentence as today');
  assert.equal(NO_DATE, 'This listing has no usable date.');
  assert.ok(src.includes('Adding…'), 'busy label while the add is in flight');
});

test('invite and approval rows time and place read as the cards write them', () => {
  assert.ok(src.includes('whenLabel('), 'same time words');
  assert.ok(src.includes('whereLabel('), 'same place words');
  assert.equal(whenLabel(invite(), 'America/New_York'), 'Sun 22 Nov, 19:00 – 22:00');
  assert.equal(whereLabel(invite()), 'The Hall, Dublin');
});

// --- dismiss: same behavior as today, through the existing route ---

test('dismiss uses the Facebook dismiss route', () => {
  assert.ok(!src.includes('discover.dismiss('), 'a Facebook row has no card to dismiss');
  assert.ok(src.includes('discover.facebookDismiss('), 'the Facebook dismiss route for approvals');
  assert.ok(src.includes('Dismiss'), 'dismiss button on approvals only');
  assert.ok(src.includes("state: 'dismissed'"), 'dismissed mark kept for the mount');
});

test('dismissed approvals leave the list like dismissed cards do', () => {
  const dismissed = { ...approval(), state: 'dismissed' };
  assert.equal(canAdd(dismissed), true, 'dismissal does not change addability');
  assert.ok(src.includes('!added && <Button'), 'dismiss offered only while not added');
});

// --- invariant: props in, existing routes out; no paths or keys ---

test('invariant: the GUI writes only through the pool add and Facebook dismiss routes', () => {
  const writes = ['discover.addEvent(', 'discover.facebookDismiss('];
  for (const w of writes) assert.ok(src.includes(w), w);
  assert.ok(!src.includes('fetch('), 'no direct network write');
  assert.ok(!src.includes('localStorage'), 'no stored write');
});

test('invariant: the panels never show a disk path or a key', () => {
  assert.ok(!src.includes('/vault'), 'no vault path');
  assert.ok(!src.includes('settings_dir'), 'no settings folder');
  assert.ok(!src.toLowerCase().includes('api_key'), 'no key');
  assert.ok(!src.toLowerCase().includes('secret'), 'no secret');
  assert.ok(!src.includes('Z-'), 'no internal id prefix shown');
});

test('invariant: no News Feed view and no mount edits in this slice', () => {
  assert.ok(!src.toLowerCase().includes('news feed'), 'no News Feed view');
  assert.ok(!src.includes('EventsArea'), 'mount lives in the mount slice');
  assert.ok(!src.includes('TabList') && !src.includes('<Tab'), 'no tab mount here');
});

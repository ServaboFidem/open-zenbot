// Settings > Notifications: what the page's sources hold, since its components have no DOM tests. The decisions are
// tested in settings-index.test.mjs (tabs, search) and telnyx.test.mjs (Text reminders' account line).
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = name => readFileSync(new URL(`../../src/gui/${name}`, import.meta.url), 'utf8');
const app = read('app.jsx');
// the text of one `{settingsTab === '<id>' && ...}` branch, up to the next one
const branch = id => { const at = app.indexOf(`{settingsTab === '${id}'`); assert.ok(at >= 0, id); const end = app.indexOf('{settingsTab ===', at + 1); return app.slice(at, end < 0 ? undefined : end); };
const routines = () => { const at = app.indexOf("{page === 'Routines'"); return app.slice(at, app.indexOf("{page === 'Approvals'", at)); };

test('the spoken briefing is on Settings > Notifications, and its fixes still open their settings', () => {
  assert.equal(app.split('<BriefingsSection').length - 1, 1);
  const n = branch('notifications');
  assert.ok(n.includes('<BriefingsSection'));
  assert.ok(n.includes("openSettings(panel, title ? anchorId(panel, title) : '')"));
  assert.ok(n.includes('<MorningSettings') && n.includes('<TextReminders'));
});
test('Routines points to the briefing instead of holding it', () => {
  assert.ok(!routines().includes('<BriefingsSection'));
  assert.ok(routines().includes('Spoken briefings are in Settings &gt; Notifications.'));
});
test("Calendar holds Google only, and the Today card's weather link opens Notifications", () => {
  assert.ok(branch('calendar').includes('<CalendarSettings') && !branch('calendar').includes('<MorningSettings'));
  assert.ok(app.includes("openSettings('notifications', anchorId('notifications', 'Weather'))"));
});
test('the texts are on Text reminders, and Connected apps keeps the Telnyx account', () => {
  const texts = read('TextReminders.jsx'), telnyx = read('TelnyxSettings.jsx');
  for (const s of ['function TextsForm', 'Set your phone number in the desktop app.', 'Send a test text', 'ACCOUNT_POINTER']) assert.ok(texts.includes(s), s);
  for (const s of ['TextsForm', 'Send a test text']) assert.ok(!telnyx.includes(s), s);
  assert.ok(telnyx.includes('TEXTS_POINTER') && telnyx.includes('The number ZenBot sends from'));
});
test('the daily email still says it is set in the desktop app when there is no bridge', () => {
  assert.ok(read('MorningSettings.jsx').includes('Manage the daily email recipient and delivery time in the desktop app.'));
});

// gui-mount: the Facebook tab is mounted, settings search finds the new
// sections, and login loss shows as one plain sentence in Settings and status.
// EventsArea and the panels stay props-only here: data wiring is api-mount's.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { SECTIONS, anchorId, searchSettings } from '../../src/gui/settings-index.js';
import { FACEBOOK_LOGIN_LOSS } from '../../src/gui/discovery-settings.js';

const gui = id => readFileSync(new URL(`../../src/gui/${id}`, import.meta.url), 'utf8');
const events = id => readFileSync(new URL(`../../src/gui/events/${id}`, import.meta.url), 'utf8');

// --- mount: the Facebook tab renders the panels slice's dashboard ---

test('EventsArea mounts FacebookPanels on its own reachable tab', () => {
  const src = events('EventsArea.jsx');
  assert.ok(src.includes(`from './FacebookPanels.jsx'`), 'imports the panels slice');
  assert.ok(src.includes('<Tab value="facebook">Facebook</Tab>'), 'tab beside the existing views');
  assert.ok(src.includes(`tab === 'facebook' && <FacebookPanels`), 'renders the dashboard on that tab');
  assert.ok(src.includes('api={api}') && src.includes('online={online}') && src.includes('notify={notify}'),
    'passes what the area owns through');
});

test('the mount adds no data fetching and no new add path', () => {
  const src = events('EventsArea.jsx');
  assert.ok(!src.includes('fetch('), 'no fetch: reads stay in the panels props / api-mount');
  assert.ok(!src.includes('addEvent') && !src.includes('.add(') && !src.includes('.dismiss('),
    'no add path: writes stay on the existing card and pool routes');
});

// --- search-index: the new sections are anchored and findable ---

test('Facebook helper and sign-in are anchored calendar sections', () => {
  for (const title of ['Facebook helper', 'Facebook sign-in']) {
    const s = SECTIONS.find(x => x.panel === 'calendar' && x.title === title);
    assert.ok(s, title);
    assert.equal(s.anchor, anchorId('calendar', title), `${title} uses its anchored id`);
  }
});

test('search finds the new sections by their own words', () => {
  assert.ok(searchSettings('facebook').some(m => m.panel === 'calendar' && m.section === 'Facebook helper'), 'facebook');
  assert.ok(searchSettings('facebook helper').some(m => m.section === 'Facebook helper'), 'facebook helper');
  assert.ok(searchSettings('signed out').some(m => m.section === 'Facebook sign-in'), 'signed out');
  assert.ok(searchSettings('login').some(m => m.section === 'Facebook sign-in'), 'login');
  assert.ok(searchSettings('nudge').some(m => m.section === 'Facebook helper'), 'birthday nudge');
});

// --- login loss: one plain sentence in Settings and in helper status ---

test('login loss is one plain sentence, shared by Settings and the index', () => {
  assert.equal(FACEBOOK_LOGIN_LOSS,
    'If Facebook signed you out, the Facebook tab says the helper is not ready. Sign back in to Facebook in this browser and the helper picks up on its next check.');
  const signin = SECTIONS.find(x => x.panel === 'calendar' && x.title === 'Facebook sign-in');
  assert.ok(signin.words.includes(FACEBOOK_LOGIN_LOSS), 'indexed under Facebook sign-in');
  const settings = gui('DiscoverySettings.jsx');
  assert.ok(settings.includes(`anchorId('calendar', 'Facebook helper')`), 'helper heading exists');
  assert.ok(settings.includes(`anchorId('calendar', 'Facebook sign-in')`), 'sign-in heading exists');
  assert.ok(settings.includes('FACEBOOK_LOGIN_LOSS'), 'Settings shows the same sentence, not a copy');
});

test('helper status shows login loss plainly through the panels slice', () => {
  const src = events('FacebookPanels.jsx');
  assert.ok(src.includes('The Facebook helper is not ready yet.'), 'not-ready sentence stays the status words');
  assert.ok(src.includes('status.error'), 'a reported error (login loss included) is shown as text');
  assert.ok(!src.includes('settings_dir'), 'no settings folder in reach');
});

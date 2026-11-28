import test from 'node:test';
import assert from 'node:assert/strict';
import { PANELS, SECTIONS, anchorId, liveSections, providerAnchor, searchSettings, tabOf, toolAnchor } from '../../src/gui/settings-index.js';

test('panels are the thirteen, in order', () => {
  assert.deepEqual(PANELS.map(p => p.id), ['models', 'mcps', 'skills', 'programs', 'folders', 'calendar', 'tracing', 'ledger', 'memory', 'notifications', 'apps', 'speakers', 'about']);
  for (const p of PANELS) assert.ok(p.name && p.description);
});
test('every section belongs to a panel and its anchor is anchorId', () => {
  const ids = new Set(PANELS.map(p => p.id));
  for (const s of SECTIONS) { assert.ok(ids.has(s.panel), s.title); assert.ok(s.anchor === '' || s.anchor === anchorId(s.panel, s.title), s.title); }
  for (const id of ids) assert.ok(SECTIONS.some(s => s.panel === id), `${id} has a section`);
});
test('anchorId slugs the title', () => assert.equal(anchorId('tracing', 'Sentry (GlitchTip)'), 'settings-tracing-sentry-glitchtip'));
test('finds body text on a panel and names its section', () => {
  const [m] = searchSettings('dsn');
  assert.equal(m.panel, 'tracing'); assert.equal(m.panelName, 'Tracing');
  assert.equal(m.section, 'Sentry (GlitchTip)'); assert.equal(m.anchor, 'settings-tracing-sentry-glitchtip');
  assert.match(m.text, /dsn/i);
});
test('every word must match, in any case and order', () => {
  assert.ok(searchSettings('KEY langfuse secret').some(m => m.section === 'Langfuse'));
  assert.deepEqual(searchSettings('langfuse zebra'), []);
});
test('a panel or section name ranks above body text', () => {
  const r = searchSettings('tracing');
  assert.equal(r[0].panel, 'tracing');
  const body = r.findIndex(m => m.panel !== 'tracing');
  assert.ok(body === -1 || body > 0);
});
test('desktop app finds the calendar panel', () => assert.ok(searchSettings('desktop app').some(m => m.panel === 'calendar')));
test('claude and magick find the Programs panel', () => {
  assert.ok(searchSettings('claude').some(m => m.panel === 'programs'));
  assert.ok(searchSettings('imagemagick').some(m => m.panel === 'programs'));
});
test('no section is left on a removed panel', () => {
  for (const s of SECTIONS) assert.ok(!['harness', 'providers', 'tools', 'agents'].includes(s.panel), s.title);
});
test('empty and blank queries give nothing', () => { assert.deepEqual(searchSettings(''), []); assert.deepEqual(searchSettings('   '), []); });
test('regex characters are plain text', () => { for (const q of ['c++', '(', '.*', '[', '\\']) assert.ok(Array.isArray(searchSettings(q))); assert.deepEqual(searchSettings('.*'), []); });
test('live names are searched and bad entries skipped', () => {
  const extra = [{ panel: 'models', anchor: '', title: 'Acme Router', words: ['acme-large'] }, { panel: 'models', anchor: '', title: undefined, words: null }, null];
  const r = searchSettings('acme', extra);
  assert.equal(r.length, 1); assert.equal(r[0].section, 'Acme Router'); assert.equal(r[0].panel, 'models');
});
test('a section matching on several words is listed once', () => {
  const r = searchSettings('langfuse');
  assert.equal(r.filter(m => m.anchor === 'settings-tracing-langfuse').length, 1);
});
test('at most 20 results', () => assert.ok(searchSettings('e').length <= 20));

import { readFileSync, readdirSync } from 'node:fs';
test('every indexed section has its id on the page', () => {
  const dir = new URL('../../src/gui/', import.meta.url);
  const src = readdirSync(dir).filter(f => f.endsWith('.jsx')).map(f => readFileSync(new URL(f, dir), 'utf8')).join('\n');
  for (const s of SECTIONS.filter(s => s.anchor)) assert.ok(src.includes(`anchorId('${s.panel}', ${JSON.stringify(s.title).replace(/^"|"$/g, "'")})`), `${s.panel}: ${s.title}`);
});

test('a section is not matched by words that belong to its panel or to a sibling', () => {
  assert.ok(!searchSettings('sentry').some(m => m.anchor === 'settings-tracing-langfuse'));
  assert.ok(!searchSettings('langfuse').some(m => m.anchor === 'settings-tracing-sentry-glitchtip'));
  assert.ok(!searchSettings('name').some(m => m.section === 'Rebuild memory'));
});
test('the section that holds the word comes first', () => assert.equal(searchSettings('license')[0].section, 'Open source'));
test('a live entry is matched by its own words only', () => {
  const extra = [{ panel: 'models', anchor: '', title: 'Acme Router', words: ['acme-large'] }];
  assert.ok(!searchSettings('key', extra).some(m => m.section === 'Acme Router'));
  assert.ok(!searchSettings('model', extra).some(m => m.section === 'Acme Router'));
});
test('a panel and its section of the same name are one result', () => {
  assert.equal(searchSettings('claude')[0].anchor, anchorId('programs', 'Coding agents'));
  assert.equal(searchSettings('tracing').filter(m => m.section === 'Tracing').length, 1);
});
test('the ledger is a settings panel, found by name and by its kinds filter', () => {
  assert.equal(searchSettings('ledger')[0].panel, 'ledger');
  assert.ok(searchSettings('decider scores').some(m => m.panel === 'ledger'));
});
test('a panel is found by its description', () => assert.equal(searchSettings('outside services')[0].panel, 'apps'));
test('Sonos is on the Speakers page, above Wi-Fi speakers, and the connected-apps mockup is gone', () => {
  assert.equal(PANELS.find(p => p.id === 'speakers').name, 'Speakers');
  const sonos = searchSettings('Sonos')[0];
  assert.equal(sonos.panel, 'speakers'); assert.equal(sonos.anchor, anchorId('speakers', 'Sonos'));
  assert.equal(searchSettings('volume limit')[0]?.panel, 'speakers');
  const onSpeakers = SECTIONS.filter(s => s.panel === 'speakers').map(s => s.title);
  assert.deepEqual(onSpeakers.slice(0, 2), ['Sonos', 'Wi-Fi speakers']);
  assert.deepEqual(SECTIONS.filter(s => s.panel === 'apps').map(s => s.title), ['AgentMail', 'Telnyx']);
  assert.deepEqual(searchSettings('mockup'), []);
});
test('Notifications holds the briefing, the morning digest and the texts, each on its tab', () => {
  assert.deepEqual(SECTIONS.filter(s => s.panel === 'notifications').map(s => [s.title, s.tab]),
    [['Spoken briefing', 'briefing'], ['Weather', 'digest'], ['Daily email', 'digest'], ['Text reminders', 'texts']]);
  assert.deepEqual(SECTIONS.filter(s => s.panel === 'calendar').map(s => s.title),
    ['Your Google app', 'Google Calendar', 'Calendars to bring in', 'Send events to Google', 'Event discovery', 'Facebook helper', 'Facebook sign-in']);
  assert.equal(tabOf('notifications', ''), 'briefing');
  assert.equal(tabOf('notifications', anchorId('notifications', 'Weather')), 'digest');
  assert.equal(tabOf('notifications', anchorId('notifications', 'Text reminders')), 'texts');
  for (const [q, tab, title] of [['joke', 'briefing', 'Spoken briefing'], ['temperature unit', 'digest', 'Weather'],
    ['delivery time', 'digest', 'Daily email'], ['evening rundown', 'texts', 'Text reminders']]) {
    const hit = searchSettings(q)[0];
    assert.deepEqual([hit.panel, hit.tab, hit.section], ['notifications', tab, title], q);
  }
  assert.equal(searchSettings('sending number')[0].panel, 'apps');
});
test('the matched text holds a word that was typed', () => { for (const m of searchSettings('secret langfuse')) assert.match(m.text, /secret|langfuse/i); });

test('a title hit in a later panel comes before a body hit in an earlier one', () => {
  const r = searchSettings('memory');
  assert.equal(r[0].panel, 'memory');
  const title = r.findIndex(m => m.section === 'Rebuild memory'), body = r.findIndex(m => m.panel === 'about');
  assert.ok(title >= 0 && body >= 0 && title < body);
});
test("the page's own sentences are searchable", () => {
  assert.ok(searchSettings('through the files tool').some(m => m.panel === 'folders'));
  assert.ok(searchSettings('models the harness may use').some(m => m.panel === 'models' && m.tab === 'providers'));
});
test('live sections carry the id of their card', () => {
  const live = liveSections([{ id: 'acme', name: 'Acme Router', models: [{ id: 'acme-large', name: 'Acme Large' }] }], [{ id: 'image', name: 'Image generation', summary: 'Makes pictures.' }]);
  assert.deepEqual(live, [
    { panel: 'models', anchor: providerAnchor('acme'), title: 'Acme Router', words: ['acme-large', 'Acme Large'], tab: 'providers' },
    { panel: 'models', anchor: toolAnchor('image'), title: 'Image generation', words: ['Makes pictures.'], tab: 'jobs' },
  ]);
  assert.equal(providerAnchor('acme'), 'settings-models-provider-acme');
  assert.equal(toolAnchor('image'), 'settings-models-tool-image');
  assert.equal(searchSettings('acme', live)[0].anchor, providerAnchor('acme'));
  assert.equal(searchSettings('acme', live)[0].tab, 'providers');
  assert.equal(searchSettings('makes pictures', live)[0].tab, 'jobs');
});
test('live sections survive nothing loaded and odd entries', () => {
  assert.deepEqual(liveSections(null, null), []);
  assert.deepEqual(liveSections([null, { id: 'x' }, { id: 'y', name: 'Y' }], undefined), [{ panel: 'models', anchor: providerAnchor('y'), title: 'Y', words: [], tab: 'providers' }]);
});

test('MCP servers and skills are settings panels, found by name and by their cards', () => {
  assert.equal(searchSettings('mcp servers')[0].panel, 'mcps');
  assert.equal(searchSettings('skills')[0].panel, 'skills');
  const live = liveSections(null, null, { pexels: { use: 'Free stock photos.', tools: ['photos_search'] }, odd: null }, [
    { ref: 'research-brief', name: 'Research brief', kind: 'skill', use: 'Dig into a topic.' }, { ref: 'mcp:pexels', name: 'pexels', kind: 'mcp' }, null]);
  assert.deepEqual(live, [
    { panel: 'mcps', anchor: 'settings-mcps-pexels', title: 'pexels', words: ['Free stock photos.', 'photos_search'] },
    { panel: 'skills', anchor: 'settings-skills-research-brief', title: 'Research brief', words: ['Dig into a topic.'] },
  ]);
  assert.equal(searchSettings('photos_search', live)[0].anchor, 'settings-mcps-pexels');
  assert.equal(searchSettings('research brief', live)[0].anchor, 'settings-skills-research-brief');
});
test('update finds the About panel\'s Version and updates section', () => {
  const m = searchSettings('update').find(x => x.panel === 'about');
  assert.ok(m); assert.equal(m.section, 'Version and updates'); assert.equal(m.anchor, 'settings-about-version-and-updates');
});

test('FindLocal is found under the calendar panel\'s Event discovery section', () => {
  for (const q of ['findlocal', 'findlocal key', 'findlocal city']) {
    assert.ok(searchSettings(q).some(m => m.panel === 'calendar' && m.section === 'Event discovery'), q);
  }
});

test('Event discovery is found by Discover, Your Recommendations and All Events', () => {
  const s = SECTIONS.find(x => x.panel === 'calendar' && x.title === 'Event discovery');
  for (const w of ['Discover', 'Your Recommendations', 'All Events']) assert.ok(s.words.includes(w), w);
  for (const q of ['your recommendations', 'all events']) assert.ok(searchSettings(q).some(m => m.section === 'Event discovery'), q);
});

test('Dreaming is found under Memory by its words', () => {
  for (const q of ['dreaming', 'idle', 'local model', 'learn']) assert.ok(searchSettings(q).some(m => m.panel === 'memory' && m.section === 'Dreaming'), q);
  assert.equal(searchSettings('dreaming')[0].anchor, anchorId('memory', 'Dreaming'));
});
test('the Read aloud card is found by its local voice words', () => {
  const summary = 'Reads bot replies aloud when the speaker button in a chat is on. The Narrator bot rewrites each reply for the ear first, then a voice speaks it: a speech model on a provider, or Kokoro on this computer.';
  const live = liveSections(null, [{ id: 'speech', name: 'Read aloud', summary }]);
  assert.equal(searchSettings('kokoro', live)[0].anchor, toolAnchor('speech'));
  assert.equal(searchSettings('kokoro', live)[0].tab, 'jobs');
  assert.ok(searchSettings('on this computer', live).some(m => m.anchor === toolAnchor('speech')));
});

test('the Settings search finds Skill discovery on the Skills page, SkillTuner tab', () => {
  for (const q of ['Skill discovery', 'GitHub', 'repository']) assert.ok(searchSettings(q).some(m => m.panel === 'skills' && m.anchor === anchorId('skills', 'Skill discovery') && m.tab === 'tuner'), q);
});
test('SkillTuner and its sources are found on the Skills page, on the SkillTuner tab', () => {
  assert.equal(PANELS.some(p => p.id === 'discovery'), false);
  assert.equal(SECTIONS.some(s => s.panel !== 'skills' && s.title === 'SkillTuner'), false);
  for (const q of ['SkillTuner', 'GitHub repository', 'agentskills.finance', 'Suggested by SkillTuner']) {
    const hit = searchSettings(q)[0];
    assert.equal(hit.panel, 'skills', q);
    assert.equal(hit.tab, 'tuner', q);
  }
  assert.equal(searchSettings('Bots with this skill')[0].tab, 'main');
  assert.equal(tabOf('skills', anchorId('skills', 'SkillTuner')), 'tuner');
  assert.equal(tabOf('skills', anchorId('skills', 'Skill discovery')), 'tuner');
  assert.equal(tabOf('skills', anchorId('skills', 'Suggested by SkillTuner')), 'tuner');
  assert.equal(tabOf('skills', ''), 'main');
  assert.equal(tabOf('skills', anchorId('skills', 'some-skill-ref')), 'main');
  assert.equal(tabOf('memory', anchorId('skills', 'SkillTuner')), 'main');
});

test('the Skills page opens on the SkillTuner tab while suggestions wait, unless a section says otherwise', () => {
  assert.equal(tabOf('skills', '', 2), 'tuner');
  assert.equal(tabOf('skills', '', 0), 'main');
  assert.equal(tabOf('skills', anchorId('skills', 'some-skill-ref'), 2), 'main');
  assert.equal(tabOf('skills', anchorId('skills', 'Suggested by SkillTuner'), 0), 'tuner');
  assert.equal(tabOf('memory', '', 2), 'main');
});

test('the Models page opens on Jobs unless a section on Providers was asked for', () => {
  assert.equal(tabOf('models', ''), 'jobs');
  assert.equal(tabOf('models', '', 3), 'jobs');
  assert.equal(tabOf('models', anchorId('models', 'Models the harness runs on')), 'jobs');
  assert.equal(tabOf('models', toolAnchor('speech')), 'jobs');
  assert.equal(tabOf('models', anchorId('models', 'Model providers')), 'providers');
  assert.equal(tabOf('models', anchorId('models', 'Add a provider')), 'providers');
  assert.equal(tabOf('models', providerAnchor('openrouter')), 'providers');
  assert.equal(tabOf('programs', ''), 'main');
  assert.equal(searchSettings('add a provider')[0].tab, 'providers');
  assert.equal(searchSettings('decider scorer')[0].tab, 'jobs');
  for (const m of searchSettings('e')) assert.ok(typeof m.tab === 'string' && m.tab, m.section);
});
test('the harness status line is on About', () => {
  assert.ok(searchSettings('harness address').some(m => m.panel === 'about' && m.anchor === anchorId('about', 'Harness')));
});

test('no message, comment, prompt or setup doc names a Settings page that is gone', () => {
  const repo = new URL('../../', import.meta.url);
  const gone = [/Harness Tools/, /Harness settings/, /Model providers page/, /Settings (>|&gt;) Model providers/, /Settings (>|&gt;) Coding agents/, /Connected apps (>|&gt;) Sonos/, /Settings (>|&gt;) Wi-Fi speakers/, /Switch it on in Settings (>|&gt;) Connected apps/,
    /Settings (>|&gt;) Calendar (>|&gt;) Morning digest/, /Morning digest settings/, /top of the Routines page/, /while the Routines page is open/,
    /recipient in Settings (>|&gt;|→) Calendar/, /the Routines section, the form/,
    /Routines\*\* page and choose \*\*Add a morning briefing/];
  const walk = (dir, ext) => readdirSync(dir, { withFileTypes: true }).flatMap(e => e.isDirectory() ? walk(new URL(`${e.name}/`, dir), ext) : ext.test(e.name) ? [new URL(e.name, dir)] : []);
  const files = [
    ...walk(new URL('src/', repo), /\.(py|js|jsx|cjs)$/), ...walk(new URL('bot-prompts/', repo), /\.md$/), ...walk(new URL('skills/', repo), /\.md$/),
    new URL('README.md', repo), new URL('.env.example', repo), new URL('AGENTS.md', repo),
  ];
  for (const f of files) {
    const text = readFileSync(f, 'utf8');
    for (const g of gone) assert.ok(!g.test(text), `${f.href.slice(repo.href.length)} names ${g.source}`);
  }
});

import test from 'node:test';
import assert from 'node:assert/strict';
import { PANELS, SECTIONS, anchorId, liveSections, searchSettings } from '../../src/gui/settings-index.js';

test('panels are the twelve, in order', () => {
  assert.deepEqual(PANELS.map(p => p.id), ['harness', 'providers', 'tools', 'mcps', 'skills', 'agents', 'folders', 'calendar', 'tracing', 'memory', 'apps', 'about']);
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
test('claude finds the coding agents panel', () => assert.ok(searchSettings('claude').some(m => m.panel === 'agents')));
test('empty and blank queries give nothing', () => { assert.deepEqual(searchSettings(''), []); assert.deepEqual(searchSettings('   '), []); });
test('regex characters are plain text', () => { for (const q of ['c++', '(', '.*', '[', '\\']) assert.ok(Array.isArray(searchSettings(q))); assert.deepEqual(searchSettings('.*'), []); });
test('live names are searched and bad entries skipped', () => {
  const extra = [{ panel: 'providers', anchor: '', title: 'Acme Router', words: ['acme-large'] }, { panel: 'providers', anchor: '', title: undefined, words: null }, null];
  const r = searchSettings('acme', extra);
  assert.equal(r.length, 1); assert.equal(r[0].section, 'Acme Router'); assert.equal(r[0].panel, 'providers');
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
  const extra = [{ panel: 'providers', anchor: '', title: 'Acme Router', words: ['acme-large'] }];
  assert.ok(!searchSettings('key', extra).some(m => m.section === 'Acme Router'));
  assert.ok(!searchSettings('model', extra).some(m => m.section === 'Acme Router'));
});
test('a panel and its section of the same name are one result', () => {
  assert.equal(searchSettings('claude').filter(m => m.panel === 'agents').length, 1);
  assert.equal(searchSettings('claude')[0].anchor, 'settings-agents-coding-agents');
  assert.equal(searchSettings('tracing').filter(m => m.section === 'Tracing').length, 1);
});
test('a panel is found by its description', () => assert.equal(searchSettings('mockup')[0].panel, 'apps'));
test('the matched text holds a word that was typed', () => { for (const m of searchSettings('secret langfuse')) assert.match(m.text, /secret|langfuse/i); });

test('a title hit in a later panel comes before a body hit in an earlier one', () => {
  const r = searchSettings('memory');
  assert.equal(r[0].panel, 'memory');
  const title = r.findIndex(m => m.section === 'Rebuild memory'), body = r.findIndex(m => m.panel === 'harness');
  assert.ok(title >= 0 && body >= 0 && title < body);
});
test("the page's own sentences are searchable", () => {
  assert.ok(searchSettings('through the files tool').some(m => m.panel === 'folders'));
  assert.ok(searchSettings('models the harness may use').some(m => m.panel === 'providers'));
});
test('live sections carry the id of their card', () => {
  const live = liveSections([{ id: 'acme', name: 'Acme Router', models: [{ id: 'acme-large', name: 'Acme Large' }] }], [{ id: 'image', name: 'Image generation', summary: 'Makes pictures.' }]);
  assert.deepEqual(live, [
    { panel: 'providers', anchor: 'settings-providers-acme', title: 'Acme Router', words: ['acme-large', 'Acme Large'] },
    { panel: 'tools', anchor: 'settings-tools-image', title: 'Image generation', words: ['Makes pictures.'] },
  ]);
  assert.equal(searchSettings('acme', live)[0].anchor, 'settings-providers-acme');
});
test('live sections survive nothing loaded and odd entries', () => {
  assert.deepEqual(liveSections(null, null), []);
  assert.deepEqual(liveSections([null, { id: 'x' }, { id: 'y', name: 'Y' }], undefined), [{ panel: 'providers', anchor: 'settings-providers-y', title: 'Y', words: [] }]);
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

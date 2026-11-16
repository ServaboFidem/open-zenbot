import test from 'node:test';
import assert from 'node:assert/strict';
import { PANELS, SECTIONS, anchorId, searchSettings } from '../../src/gui/settings-index.js';

test('panels are the nine, in order', () => {
  assert.deepEqual(PANELS.map(p => p.id), ['harness', 'providers', 'tools', 'agents', 'folders', 'tracing', 'memory', 'apps', 'about']);
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

// The recall adapter turns harness memory into the knowledge panel's graph
// contract (nodes, edges, sources) and answers questions through /memory/recall.
import test from 'node:test';
import assert from 'node:assert/strict';
import { recallAdapter, layout } from '../../src/gui/knowledge-adapter.js';

const facts = [
  { id: 'f1', text: 'Alex prefers uv for Python packaging.', namespace: 'shared', tier: 'user_pref', bot_id: '', entities: ['uv'], source: '', at: '2026-11-06T00:00:00+00:00' },
  { id: 'f2', text: 'The GUI is React and talks to the harness over HTTP.', namespace: 'shared', tier: 'project', bot_id: '', entities: ['react', 'harness'], source: 'vault:docs/brief.md', at: '2026-11-06T00:01:00+00:00' },
  { id: 'f3', text: 'Staging database is called pelican.', namespace: 'bot:alpha', tier: 'private', bot_id: 'alpha', entities: ['pelican'], source: '', at: '2026-11-06T00:02:00+00:00' },
];
const fakeApi = {
  calls: [],
  async memory() { this.calls.push('memory'); return facts; },
  async recall(botId, task) { this.calls.push(`recall ${botId} ${task}`); return { facts: [facts[0].text], band: { scores: { f1: 0.9, f2: 0.3 }, winners: ['f1'], top: 0.9, floor: 0.8, capped: false, outcome: 'act' }, fallback: false, sources: { shared: 2 }, blocked: [] }; },
};

test('load maps facts and entities to nodes, edges, and sources with a layout', async () => {
  const a = recallAdapter(fakeApi);
  const g = await a.load();
  const ids = g.nodes.map(n => n.id);
  assert.ok(ids.includes('f1') && ids.includes('entity:uv') && ids.includes('entity:harness'));
  const uv = g.nodes.find(n => n.id === 'entity:uv');
  assert.equal(uv.type, 'Entity'); assert.equal(uv.dataset, 'shared'); assert.deepEqual(uv.sourceIds, ['f1']);
  const f2 = g.nodes.find(n => n.id === 'f2');
  assert.equal(f2.type, 'Project fact'); assert.equal(f2.properties.Source, 'vault:docs/brief.md'); assert.equal(f2.summary, facts[1].text);
  const f3 = g.nodes.find(n => n.id === 'f3');
  assert.equal(f3.dataset, 'bot:alpha'); assert.equal(f3.type, 'Private fact');
  assert.deepEqual(g.edges.filter(e => e.from === 'f2').map(e => e.to).sort(), ['entity:harness', 'entity:react']);
  assert.equal(g.edges[0].label, 'mentions');
  assert.equal(g.sources.find(s => s.id === 'f2').excerpt, facts[1].text);
  for (const n of g.nodes) { assert.ok(n.x >= 0 && n.x <= 1000 && n.y >= 0 && n.y <= 680, `${n.id} in bounds`); }
});

test('layout is deterministic and keeps nodes apart', () => {
  const nodes = Array.from({ length: 9 }, (_, i) => ({ id: `n${i}`, type: i < 4 ? 'Entity' : 'Project fact' }));
  const a = layout(nodes.map(n => ({ ...n }))), b = layout(nodes.map(n => ({ ...n })));
  assert.deepEqual(a.map(n => [n.x, n.y]), b.map(n => [n.x, n.y]));
  for (let i = 0; i < a.length; i++) for (let j = i + 1; j < a.length; j++) assert.ok(Math.hypot(a[i].x - a[j].x, a[i].y - a[j].y) > 60);
});

test('search recalls through the harness and maps winners to fact nodes with confidence', async () => {
  const api = { ...fakeApi, calls: [], async memory(ns) { this.calls.push(`memory ${ns}`); return facts.filter(f => f.namespace === ns); } };
  const a = recallAdapter(api);
  const r = await a.search('which tool for python packages', { botId: 'alpha' });
  assert.equal(api.calls.at(-1), 'recall alpha which tool for python packages');
  assert.deepEqual(r.matches.map(m => m.id), ['f1']);
  assert.equal(r.matches[0].confidence, 0.9);
  assert.deepEqual(r.relationships.map(e => e.to), ['entity:uv']);
  assert.equal(r.sources[0].id, 'f1');
  assert.match(r.summary, /1 fact/);
  assert.equal(r.band.top, 0.9);
});

test('search before load still answers by loading first', async () => {
  const a = recallAdapter(fakeApi);
  const r = await a.search('anything', { botId: 'alpha' });
  assert.equal(r.matches.length, 1);
});

test('load reads the shared and the chosen bot private namespace', async () => {
  const api = { calls: [], async memory(ns) { this.calls.push(ns); return facts.filter(f => f.namespace === ns); }, async recall() { return { facts: [], band: null, fallback: false, sources: {}, blocked: [] }; } };
  const a = recallAdapter(api);
  const g = await a.load({ botId: 'alpha' });
  assert.deepEqual(api.calls, ['shared', 'bot:alpha']);
  assert.ok(g.nodes.some(n => n.id === 'f3'));
});

// -- the Knowledge page is Hindsight's dashboard plus a recall box ----

test('the adapter no longer opens a facts view', () => {
  assert.equal(recallAdapter({}).open, undefined);
});

test('ui reports the control plane through /knowledge/ui', async () => {
  const status = { url: 'http://127.0.0.1:9999', state: 'ready', reason: '' };
  const api = { async knowledgeUi() { return status; } };
  assert.deepEqual(await recallAdapter(api).ui(), status);
});

test('the store-graph helpers are gone', async () => {
  const mod = await import('../../src/gui/knowledge-adapter.js');
  assert.equal(mod.ontologyLayout, undefined); assert.equal(mod.graphFromOntology, undefined);
  const a = recallAdapter({});
  assert.equal(a.stores, undefined); assert.equal(a.loadGraph, undefined);
});

test('search still shows a recalled fact that has no node in the loaded graph', async () => {
  const api = { async memory() { return [facts[0]]; }, async recall() { return { facts: ['cache-only fact about pelican'], band: { scores: {}, winners: [], top: 1, floor: 0.9, capped: false, outcome: 'act' }, fallback: false, sources: { cache: 1 }, blocked: [] }; } };
  const r = await recallAdapter(api).search('pelican', { botId: 'alpha' });
  assert.equal(r.matches.length, 1);
  assert.equal(r.matches[0].text, 'cache-only fact about pelican');
  assert.match(r.summary, /1 fact recalled/);
});

test('the Knowledge page shows only the dashboard: no Facts view, map or list', async () => {
  const { readFileSync } = await import('node:fs');
  const src = readFileSync(new URL('../../src/gui/KnowledgePanel.jsx', import.meta.url), 'utf8');
  assert.match(src, /<MemoryDashboard ui=\{ui\}\/>/);
  assert.doesNotMatch(src, /'Facts'|\['Map', 'List'\]|<svg className="knowledge-graph"|filterKnowledge/);
  assert.doesNotMatch(src, /type-\$\{|isOwner/, 'the store-graph colours and owner ring stay gone');
});

test('search reloads the facts when recall switches to another bot', async () => {
  const api = { calls: [], async memory(ns) { this.calls.push(ns); return facts.filter(f => f.namespace === ns); }, async recall() { return { facts: [], band: null, fallback: false, sources: {}, blocked: [] }; } };
  const a = recallAdapter(api);
  await a.search('q', { botId: 'alpha' });
  await a.search('q', { botId: 'alpha' });
  await a.search('q', { botId: 'beta' });
  assert.deepEqual(api.calls, ['shared', 'bot:alpha', 'shared', 'bot:beta']);
});

test('only the default control-plane port is framed; any other opens in the browser', async () => {
  const { frameable } = await import('../../src/gui/knowledge-adapter.js');
  assert.equal(frameable('http://127.0.0.1:9999'), true);
  assert.equal(frameable('http://localhost:9999/'), true);
  assert.equal(frameable('http://127.0.0.1:19999'), false);  // ZEN_HINDSIGHT_UI_PORT: the CSP would block the frame
  assert.equal(frameable('http://10.0.0.5:9999'), false);
  assert.equal(frameable(''), false);
  assert.equal(frameable('not a url'), false);
  const { readFileSync } = await import('node:fs');
  const csp = readFileSync(new URL('../../src/gui/index.html', import.meta.url), 'utf8');
  // the harness origins follow it (bot-made diagrams, sandboxed); the dashboard's only port is still 9999
  assert.match(csp, /frame-src http:\/\/127\.0\.0\.1:9999 http:\/\/localhost:9999[ ;]/, 'frameable() follows the CSP');
  assert.doesNotMatch(csp, /frame-src[^;]*:19999/);
  const panel = readFileSync(new URL('../../src/gui/KnowledgePanel.jsx', import.meta.url), 'utf8');
  assert.match(panel, /ui\.state === 'ready' && frameable\(ui\.url\)/);
});

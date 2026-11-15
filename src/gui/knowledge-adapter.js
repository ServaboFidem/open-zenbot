// Recall adapter: the knowledge panel's graph contract, fed by the harness.
// Nodes are facts and the entities they name; edges say which fact
// mentions which entity; sources are the facts themselves (the vault
// pointer is a property). Questions go through /memory/recall, so what
// the panel shows is what a bot would be offered, band and all.
const TIER_LABEL = { user_pref: 'User preference', project: 'Project fact', private: 'Private fact', session: 'Session fact' };
const W = 1000, H = 680, CX = 500, CY = 340;

export function layout(nodes) {
  // Deterministic rings: entities outside, facts inside, both sorted by id.
  const rings = [
    { items: nodes.filter(n => n.type === 'Entity').sort((a, b) => a.id.localeCompare(b.id)), r: 270 },
    { items: nodes.filter(n => n.type !== 'Entity').sort((a, b) => a.id.localeCompare(b.id)), r: 140 },
  ];
  for (const { items, r } of rings) {
    const radius = items.length === 1 ? 0 : Math.max(r, items.length * 16);
    items.forEach((n, i) => {
      const angle = (2 * Math.PI * i) / Math.max(items.length, 1) - Math.PI / 2;
      n.x = Math.round(Math.min(W - 60, Math.max(60, CX + Math.cos(angle) * Math.min(radius, 420))));
      n.y = Math.round(Math.min(H - 60, Math.max(60, CY + Math.sin(angle) * Math.min(radius, 270))));
    });
  }
  return nodes;
}

export function factNode(f) {
  return {
    id: f.id, label: f.text.length > 42 ? f.text.slice(0, 40) + '…' : f.text, type: TIER_LABEL[f.tier] || f.tier, dataset: f.namespace,
    summary: f.text, properties: { Tier: f.tier, Bot: f.bot_id || 'shared', Source: f.source || 'typed in', Remembered: f.at },
    sourceIds: [f.id], text: f.text, entities: f.entities || [],
  };
}

export function graphFromFacts(facts) {
  const nodes = facts.map(factNode);
  const entities = new Map();
  for (const f of facts) for (const e of f.entities || []) {
    const id = `entity:${e}`;
    const n = entities.get(id) || { id, label: e, type: 'Entity', dataset: f.namespace, summary: '', properties: {}, sourceIds: [], namespaces: new Set() };
    n.sourceIds.push(f.id); n.namespaces.add(f.namespace);
    entities.set(id, n);
  }
  for (const n of entities.values()) {
    n.summary = `Named in ${n.sourceIds.length} ${n.sourceIds.length === 1 ? 'fact' : 'facts'}.`;
    n.properties = { Facts: String(n.sourceIds.length), Namespaces: [...n.namespaces].join(', ') };
    n.dataset = n.namespaces.size === 1 ? [...n.namespaces][0] : 'shared';
    delete n.namespaces;
    nodes.push(n);
  }
  const edges = facts.flatMap(f => (f.entities || []).map((e, i) => ({ id: `${f.id}-${i}`, from: f.id, to: `entity:${e}`, label: 'mentions', sourceId: f.id })));
  return { nodes: layout(nodes), edges, sources: facts.map(factSource) };
}

function factSource(f) {
  return { id: f.id, title: `${TIER_LABEL[f.tier] || f.tier} · ${f.namespace}`, location: f.source || `remembered ${f.at}`, excerpt: f.text, dataset: f.namespace };
}

// The app's Content-Security-Policy (src/gui/index.html) lets a frame load only the control plane's default
// address, port 9999. On any other port (ZEN_HINDSIGHT_UI_PORT) the frame would be blocked: open it in the browser.
export const FRAME_PORT = '9999';

export function frameable(url) {
  try {
    const u = new URL(url);
    return u.protocol === 'http:' && ['127.0.0.1', 'localhost'].includes(u.hostname) && u.port === FRAME_PORT;
  } catch {
    return false;
  }
}

export function recallAdapter(api) {
  let graph = null, loadedFor = null;  // the facts search maps recalled text onto (ids, tiers, namespaces)
  return {
    label: 'Harness memory',
    // shared memory plus the chosen bot's private namespace: what that bot would be offered
    async load({ botId = '' } = {}) {
      const shared = await api.memory('shared');
      const priv = botId ? await api.memory(`bot:${botId}`) : [];
      graph = graphFromFacts([...shared, ...priv]);
      loadedFor = botId;
      return graph;
    },
    // the memory store's dashboard: { url, state, reason }
    ui() { return api.knowledgeUi(); },
    async search(question, { botId = '' } = {}) {
      if (!graph || loadedFor !== botId) await this.load({ botId });
      const r = await api.recall(botId, question);
      const byText = new Map(graph.nodes.filter(n => n.text).map(n => [n.text, n]));
      const scores = r.band?.scores || {};
      // a recalled fact with no node (the session cache, or a namespace not loaded) still shows, as a plain fact
      const matches = (r.facts || []).map((t, i) => byText.get(t) || { id: `recalled:${i}`, label: t.length > 40 ? t.slice(0, 39) + '…' : t, text: t, summary: t, type: 'Recalled fact', dataset: 'recall', properties: {}, sourceIds: [], x: 0, y: 0 })
        .map(n => ({ ...n, confidence: scores[n.id] ?? null }));
      const ids = new Set(matches.map(m => m.id));
      const relationships = graph.edges.filter(e => ids.has(e.from) || ids.has(e.to));
      const sources = graph.sources.filter(s => ids.has(s.id));
      const blocked = (r.blocked || []).length;
      const band = r.band;
      const summary = matches.length
        ? `${matches.length} ${matches.length === 1 ? 'fact' : 'facts'} recalled${band ? ` in the winning band (top ${band.top.toFixed(2)}, floor ${band.floor.toFixed(2)})` : ''}${r.fallback ? '; scorer unavailable, retrieval order used' : ''}${blocked ? `; ${blocked} blocked by the exclusion filter` : ''}.`
        : `Nothing recalled for that${blocked ? `; ${blocked} blocked by the exclusion filter` : ''}. Remember a fact or ingest a document first.`;
      return { question, matches, relationships, sources, summary, band, fallback: !!r.fallback, blocked: r.blocked || [] };
    },
  };
}

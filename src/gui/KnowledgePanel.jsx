import React, { useEffect, useRef, useState } from 'react';
import { Badge, Button, Field, Input, Spinner } from '@fluentui/react-components';
import { Search24Regular } from '@fluentui/react-icons';
import { frameable } from './knowledge-adapter';
import './knowledge.css';

const POLL_MS = 3000;  // how often the starting dashboard is asked whether it is up
const NO_UI = { url: '', state: 'off', reason: '' };

// The Knowledge page is Hindsight's own dashboard (its entities, links and observations over the whole bank),
// plus a recall box that asks the harness what a bot would be offered.
// adapter: { label, ui() -> {url, state, reason}, search(question, {botId}) -> {question, matches, sources, summary} }
export default function KnowledgePanel({ adapter, botId = '', bots = [], refreshKey = 0 }) {
  const [ui, setUi] = useState(null);  // the dashboard's { url, state, reason }; null until asked
  const [question, setQuestion] = useState('');
  const [asBot, setAsBot] = useState(botId);
  const [result, setResult] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const request = useRef(0);
  useEffect(() => () => { request.current += 1; }, []);
  useEffect(() => { if (botId && !asBot) setAsBot(botId); }, [botId]);
  // the dashboard's state, asked when the page is shown, and again every few seconds while it is starting
  const uiState = ui?.state;
  useEffect(() => {
    let live = true, timer = null;
    const ask = () => adapter.ui().then(r => { if (live) setUi(r); }).catch(e => { if (live) setUi({ ...NO_UI, state: 'unavailable', reason: e?.message || 'The memory dashboard could not be reached.' }); });
    ask();  // every time the page is entered, so a transient error does not stick
    if (uiState === 'starting') timer = setInterval(ask, POLL_MS);
    return () => { live = false; if (timer) clearInterval(timer); };
  }, [adapter, refreshKey, uiState]);
  async function ask(value = question) {
    const q = value.trim();
    if (!q || busy) return;
    setQuestion(q); setBusy(true); setError(''); setResult(null);
    const version = ++request.current;
    try {
      const next = await adapter.search(q, { botId: asBot });
      if (version === request.current) setResult(next);
    } catch (e) { if (version === request.current) setError(e?.message || 'Recall could not finish. Try again.'); }
    finally { if (version === request.current) setBusy(false); }
  }
  return <div className="knowledge-panel">
    <section className="page-heading od-row-top"><div className="od-fill"><div className="eyebrow">MEMORY, MADE EXPLORABLE</div><h1>Your knowledge, connected.</h1><p>Follow the relationships. Inspect the evidence. Find what your bots remember.</p></div><Badge appearance="outline" color="brand">{adapter.label}</Badge></section>
    <div className="knowledge-layout dashboard">
      <section className="knowledge-explorer" aria-label="Memory dashboard"><MemoryDashboard ui={ui}/></section>
    </div>
    <section className="knowledge-query" aria-labelledby="knowledge-query-title"><div className="od-row"><div className="od-fill"><h2 id="knowledge-query-title">Ask your knowledge</h2><p>Recall runs as a bot would see it: cache, then private, then shared, banded by the decider.</p></div><Badge appearance="outline">Harness recall</Badge></div><div className="knowledge-query-input"><Field label="Your question"><Input value={question} maxLength={500} onChange={(e, v) => setQuestion(v.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); ask(); } }} placeholder="What do we know about the launch?"/></Field>{bots.length > 0 && <Field label="Recall as"><select value={asBot} onChange={e => setAsBot(e.target.value)}>{bots.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field>}<Button appearance="primary" disabled={!question.trim() || busy} onClick={() => ask()} icon={<Search24Regular className="od-icon"/>}>{busy ? 'Recalling…' : 'Recall'}</Button></div>
      <div aria-live="polite" aria-busy={busy}>{busy && <div className="od-row"><Spinner size="tiny"/><p>Recalling through the harness…</p></div>}{error && <p className="form-error" role="alert">{error}</p>}{result && <div className="knowledge-results"><h3>Results for “{result.question}”</h3><p>{result.summary}</p>{result.matches.length > 0 && <><h3>Recalled facts</h3><div className="knowledge-result-sources">{result.matches.map(m => <article key={m.id} className="knowledge-source"><h4>{m.type}{m.confidence != null && <> <Badge appearance="tint" size="small">{m.confidence.toFixed(2)}</Badge></>}</h4><p className="knowledge-source-name">{m.dataset}</p><blockquote>{m.text}</blockquote></article>)}</div></>}</div>}</div>
    </section>
  </div>;
}

// Hindsight's control plane, in a frame. The address is only ever the one the harness reports.
function MemoryDashboard({ ui }) {
  if (!ui) return <div className="od-row"><Spinner size="tiny"/><p>Checking the memory dashboard…</p></div>;
  const link = ui.url && ui.state === 'ready' ? <a href={ui.url} target="_blank" rel="noreferrer">Open in browser</a> : null;
  if (ui.state === 'ready' && frameable(ui.url)) return <div className="knowledge-dashboard"><iframe className="knowledge-frame" src={ui.url} title="Hindsight memory dashboard"/><p className="knowledge-graph-hint">{link}</p></div>;
  if (ui.state === 'ready') return <div className="knowledge-dashboard-note od-stack" role="status"><p>The memory dashboard runs on a port the app cannot frame, so it opens in your browser.</p>{link}</div>;
  if (ui.state === 'starting') return <div className="knowledge-dashboard-note od-row" role="status"><Spinner size="tiny"/><p>The memory dashboard is starting…</p></div>;
  return <div className="knowledge-dashboard-note od-stack" role="status"><p>{ui.reason || 'The memory dashboard is off.'}</p>{link}</div>;
}

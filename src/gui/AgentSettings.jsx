import React, { useEffect, useState } from 'react';
import { Badge, Button, Field, Input, Spinner, Switch } from '@fluentui/react-components';
import { ProgramPathSection } from './ProgramPathSettings';
import { anchorId } from './settings-index';
import { agentStatus } from './agents';

// Settings > Programs > Coding agents: which of Claude Code and Codex bots may hand work to, kept in
// vault-settings/agents.json. Each program's path is the ProgramPathSection under its card. ctx: { api, online, guard, notify }
function AgentCard({ ctx, a, onChange, reload }) {
  const { api, online, guard, notify } = ctx;
  const [model, setModel] = useState(a.model || '');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { setModel(a.model || ''); }, [a.model]);
  const st = agentStatus(a);
  const asks = a.id === 'claude-code';
  async function save(body, done) {
    setBusy(true); setError('');
    try { onChange(await api.setAgent(a.id, body)); if (done) notify(done); }
    catch (e) { setError(e?.message || 'Something went wrong.'); }
    finally { setBusy(false); }
  }
  const dirty = model.trim() !== (a.model || '');
  return <div className="resource-row editor-form">
    <div className="od-row-top tool-heading"><div className="od-field od-fill"><h3>{a.name}</h3></div><Badge appearance="tint" color={st.tone}>{st.text}</Badge></div>
    {st.detail && <p className="field-hint">{st.detail}</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
    <Switch checked={a.enabled} disabled={!online || busy || (!a.usable && !a.enabled)} label={`Bots may hand work to ${a.name}`} onChange={(e, d) => save({ enabled: d.checked })}/>
    {asks ? <Switch checked={a.ask} disabled={!online || busy} label="Ask me before each run" onChange={(e, d) => save({ ask: d.checked })}/>
      : <p className="field-hint">Always asks: Codex can read files outside the job folder.</p>}
    <form className="od-row" onSubmit={e => { e.preventDefault(); save({ model: model.trim() }, `${a.name}: model saved`); }}>
      <div className="od-fill"><Field label="Model" hint="Leave empty to use the program's own default.">
        <Input value={model} placeholder="Default" onChange={(e, d) => setModel(d.value)} autoComplete="off" spellCheck={false}/>
      </Field></div>
      <Button appearance="primary" type="submit" disabled={!online || busy || !dirty}>Save</Button>
    </form>
    <p className="field-hint">Runs use your {a.vendor} sign-in and send the job folder's contents to {a.vendor}.</p>
    <ProgramPathSection ctx={ctx} programKey={a.id} onSaved={reload}/>
  </div>;
}

export function CodingAgentsSection({ ctx }) {
  const { api, online, guard } = ctx;
  const [agents, setAgents] = useState(null);
  const [failed, setFailed] = useState(false);
  const load = () => { setFailed(false); return guard(() => api.agents(), { quiet: true }).then(r => r ? setAgents(r.agents) : setFailed(true)); };
  useEffect(() => { if (online) load(); }, [online]);
  if (!online) return <div className="resource-row"><p>The harness is offline.</p></div>;
  if (!agents && failed) return <div className="resource-row od-row"><p className="od-fill">Could not check for coding agents.</p><Button appearance="outline" onClick={load}>Retry</Button></div>;
  if (!agents) return <div className="resource-row"><Spinner size="tiny" label="Checking coding agents…"/></div>;
  const put = next => setAgents(list => list.map(x => x.id === next.id ? next : x));
  return <>
    <div className="resource-row intro-row" id={anchorId('programs', 'Coding agents')}><div className="od-field"><h3>Coding agents</h3><p>Let bots hand a coding job to a program on this computer. The program works in one folder you allow, and you see what it changed.</p></div></div>
    {agents.map(a => <AgentCard key={a.id} ctx={ctx} a={a} onChange={put} reload={load}/>)}
  </>;
}

// Settings > Programs: the programs on this computer the harness runs. The coding agents, then ImageMagick's path.
export function ProgramsPage({ ctx }) {
  return <>
    <CodingAgentsSection ctx={ctx}/>
    <ProgramPathSection ctx={ctx} programKey="imagemagick"/>
  </>;
}

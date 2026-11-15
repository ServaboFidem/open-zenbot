import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Badge, Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Field, Input, ProgressBar, Spinner } from '@fluentui/react-components';
import { KIND_LABELS, capabilityTags, choiceKey, probeReducer, probeStart, enabledByKind, filterModels, fromPreset, keyLabel, modelLabel, parseChoice, presetOf } from './models.js';

const SHOWN = 150;  // rows a model list renders at once; a search narrows the rest
const BLANK = { preset: '', name: '', kind: 'openai-compatible', base_url: '', keyMode: 'paste', key: '', key_ref: '', no_key: false };

// Settings > Model providers: add providers (OpenRouter, or any OpenAI-compatible API), keep their keys,
// and choose which of each provider's models the harness may use.
// ctx: { api, online, guard, notify, log }; providers: GET /settings/providers; reload(): fetch it again
export function ModelProvidersTab({ ctx, providers, kinds, presets = [], reload }) {
  const { api, online, guard, notify, log } = ctx;
  const [open, setOpen] = useState(null);       // the provider whose models are showing
  const [adding, setAdding] = useState(null);   // the add form, or null
  const [keyFor, setKeyFor] = useState(null);   // { id, keyMode, key, key_ref } while changing a key
  if (providers === null) return <div className="resource-row"><p>{online ? 'Loading model providers…' : 'The harness is offline.'}</p></div>;

  async function add(e) {
    e.preventDefault();
    const f = adding;
    const body = { name: f.name.trim(), kind: f.kind, base_url: f.kind === 'openrouter' ? null : f.base_url.trim(),
      key: f.keyMode === 'paste' ? f.key.trim() || null : null, key_ref: f.keyMode === 'ref' ? f.key_ref.trim() || null : null, no_key: f.no_key };
    const p = await guard(() => api.addProvider(body));
    if (!p) return;
    setAdding(null); await reload(); setOpen(p.id);
    notify(`Added ${p.name}. Choose its models below.`); log('Provider added', `${p.name} · ${p.kind}`);
  }
  async function saveKey(e) {
    e.preventDefault();
    const f = keyFor;
    const p = await guard(() => api.updateProvider(f.id, f.keyMode === 'paste' ? { key: f.key.trim() } : { key_ref: f.key_ref.trim() }));
    if (p) { setKeyFor(null); await reload(); notify(`Key saved for ${p.name}`); }
  }
  async function setNoKey(p, noKey) {
    const r = await guard(() => api.updateProvider(p.id, { no_key: noKey }));
    if (r) { await reload(); notify(noKey ? `${p.name} runs without a key` : `${p.name} needs a key`); log('Provider changed', `${p.name} · ${noKey ? 'no key needed' : 'key needed'}`); }
  }
  async function remove(p) {
    if (await guard(() => api.deleteProvider(p.id)) === undefined) return;
    if (open === p.id) setOpen(null);
    await reload(); notify(`Removed ${p.name}`); log('Provider removed', p.name);
  }

  return <>
    <div className="resource-row"><h3>Model providers</h3><p>Each provider's key lives in the harness's providers.json, in your vault, readable by you only. Enable the models the harness may use; the Harness settings and Harness Tools tabs pick from them.</p></div>
    {providers.map(p => <div className="resource-row od-stack provider-row" key={p.id}>
      <div className="od-row-top provider-heading">
        <div className="od-field od-fill">
          <h3>{p.name} <Badge appearance="outline" size="small">{p.kind}</Badge></h3>
          <p>{p.base_url}</p>
          <p className="quiet-line"><Badge appearance="tint" size="small" color={p.usable ? 'success' : 'warning'}>{keyLabel(p)}</Badge> · {p.models.length} {p.models.length === 1 ? 'model' : 'models'} enabled{p.in_use?.length ? ` · used by ${p.in_use.join(', ')}` : ''}</p>
          {p.kind !== 'openrouter' && <label className="od-row no-key"><input type="checkbox" checked={!!p.no_key} disabled={!online} onChange={e => setNoKey(p, e.target.checked)}/> This server needs no API key</label>}
        </div>
        <div className="od-row provider-actions">
          <Button appearance={open === p.id ? 'primary' : 'outline'} onClick={() => setOpen(open === p.id ? null : p.id)}>{open === p.id ? 'Close models' : 'Choose models'}</Button>
          <Button appearance="subtle" disabled={!online} onClick={() => setKeyFor(keyFor?.id === p.id ? null : { id: p.id, keyMode: 'paste', key: '', key_ref: '' })}>Change key</Button>
          <Button appearance="subtle" disabled={!online || p.in_use?.length > 0} title={p.in_use?.length ? `In use by ${p.in_use.join(', ')}` : ''} onClick={() => remove(p)}>Remove</Button>
        </div>
      </div>
      {keyFor?.id === p.id && <KeyForm form={keyFor} setForm={setKeyFor} onSubmit={saveKey} onCancel={() => setKeyFor(null)} submit="Save key"/>}
      {open === p.id && <ModelBrowser ctx={ctx} provider={p} reload={reload}/>}
    </div>)}
    {adding ? <form className="resource-row editor-form" onSubmit={add}>
      <h3>Add a provider</h3>
      <Field label="Preconfigured" hint={presetOf(presets, adding.preset)?.note || 'Pick one to fill in its name and address, or set up any OpenAI-compatible API yourself.'}>
        <select aria-label="Preconfigured provider" value={adding.preset} onChange={e => setAdding(f => fromPreset(f, presetOf(presets, e.target.value)))}>
          <option value="">Custom</option>
          {presets.some(x => !x.local) && <optgroup label="Hosted">{presets.filter(x => !x.local).map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</optgroup>}
          {presets.some(x => x.local) && <optgroup label="Runs on your machine">{presets.filter(x => x.local).map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</optgroup>}
        </select>
      </Field>
      <Field label="Name" required><Input required maxLength={80} value={adding.name} onChange={(e, d) => setAdding(f => ({ ...f, name: d.value }))} placeholder="Groq"/></Field>
      <Field label="Kind"><select aria-label="Provider kind" value={adding.kind} onChange={e => setAdding(f => ({ ...f, kind: e.target.value }))}>{(kinds || []).map(k => <option key={k} value={k}>{k === 'openai-compatible' ? 'OpenAI-compatible API' : 'OpenRouter'}</option>)}</select></Field>
      {adding.kind !== 'openrouter' && <Field label="Base URL" required hint="Where the API's /chat/completions and /models live, e.g. https://api.groq.com/openai/v1. Plain http works for this machine, your local network or a Tailscale peer (gpu-box.tailnet.ts.net, 100.x.y.z)."><Input required value={adding.base_url} onChange={(e, d) => setAdding(f => ({ ...f, base_url: d.value }))} placeholder="https://api.groq.com/openai/v1"/></Field>}
      {adding.kind !== 'openrouter' && <label className="od-row no-key"><input type="checkbox" checked={adding.no_key} onChange={e => setAdding(f => ({ ...f, no_key: e.target.checked }))}/> This server needs no API key</label>}
      {!adding.no_key && <KeyForm form={adding} setForm={setAdding} embedded/>}
      <div className="od-row"><div className="od-fill"/><Button appearance="secondary" onClick={() => setAdding(null)}>Cancel</Button><Button appearance="primary" type="submit" disabled={!online || !adding.name.trim()}>Add provider</Button></div>
    </form> : <div className="resource-row"><Button appearance="outline" disabled={!online} onClick={() => setAdding({ ...BLANK })}>Add a provider</Button></div>}
  </>;
}

// A pasted key (saved in providers.json) or a reference to one kept elsewhere (env:NAME, vault:NAME).
function KeyForm({ form, setForm, onSubmit, onCancel, submit, embedded }) {
  const body = <>
    <Field label="API key">
      <div className="od-row key-mode" role="radiogroup" aria-label="Where the key comes from">
        <label><input type="radio" checked={form.keyMode === 'paste'} onChange={() => setForm(f => ({ ...f, keyMode: 'paste' }))}/> Paste the key</label>
        <label><input type="radio" checked={form.keyMode === 'ref'} onChange={() => setForm(f => ({ ...f, keyMode: 'ref' }))}/> Read it from the environment</label>
      </div>
    </Field>
    {form.keyMode === 'paste'
      ? <Field hint="Saved in providers.json in your vault (owner-only). Never sent back to this app."><Input type="password" autoComplete="off" aria-label="API key" value={form.key} onChange={(e, d) => setForm(f => ({ ...f, key: d.value }))}/></Field>
      : <Field hint="A reference like env:GROQ_API_KEY: the value stays in .env or your shell."><Input aria-label="Key reference" value={form.key_ref} onChange={(e, d) => setForm(f => ({ ...f, key_ref: d.value }))} placeholder="env:GROQ_API_KEY"/></Field>}
  </>;
  if (embedded) return body;
  const ready = form.keyMode === 'paste' ? form.key.trim() : form.key_ref.trim();
  return <form className="editor-form key-form" onSubmit={onSubmit}>{body}<div className="od-row"><div className="od-fill"/><Button appearance="secondary" onClick={onCancel}>Cancel</Button><Button appearance="primary" type="submit" disabled={!ready}>{submit}</Button></div></form>;
}

// One provider's whole model list: search, filter by kind, tick models on and off, save.
function ModelBrowser({ ctx, provider, reload }) {
  const { api, online, guard, notify, log } = ctx;
  const [models, setModels] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [q, setQ] = useState('');
  const [kind, setKind] = useState('');
  const [enabledOnly, setEnabledOnly] = useState(false);
  const [showUnusable, setShowUnusable] = useState(false);  // models a test found this key can't run
  const saved = useMemo(() => new Set(provider.models.map(m => m.id)), [provider.models]);
  const [picked, setPicked] = useState(saved);
  useEffect(() => setPicked(new Set(saved)), [saved]);

  async function load(refresh = false) {
    setBusy(true); setError('');
    try { const r = await api.providerModels(provider.id, refresh); setModels(r.models); }
    catch (e) { setError(e?.message || 'The model list could not be read.'); }
    finally { setBusy(false); }
  }
  useEffect(() => { load(); }, [provider.id]);

  const kinds = useMemo(() => [...new Set((models || []).flatMap(m => m.kinds))].sort(), [models]);
  const shown = useMemo(() => filterModels(models, { q, kind, enabledOnly, enabled: picked, showUnusable }), [models, q, kind, enabledOnly, picked, showUnusable]);
  const unusable = (models || []).filter(m => m.unusable).length;
  const changed = picked.size !== saved.size || [...picked].some(id => !saved.has(id));
  // A provider that lists ids only: each model is tested as it is enabled, one at a time, with no batch Save.
  const probes = provider.kind !== 'openrouter';
  const [probe, setProbe] = useState(null);  // probeStart(...) plus the job's id, while the modal is open
  const closeStream = useRef(null);
  useEffect(() => () => closeStream.current?.(), []);
  useEffect(() => {
    const end = probe?.end;
    if (!end) return;
    closeStream.current = null;
    if (end.status === 'done') {
      setProbe(null);
      Promise.all([reload(), load()]).then(() => {  // the list rows too: they carry the tags the probe earned
        notify(`${probe.model} enabled on ${provider.name}`); log('Model tested and enabled', `${provider.name}: ${probe.model}`); });
    } else if (end.status === 'stopped') setProbe(null);
    else if (end.unusable) load();  // the model this key can't run drops out of the list behind the dialog
  }, [probe?.end]);

  async function startProbe(id) {
    const r = await guard(() => api.startProbe(provider.id, id));
    if (!r) return;
    setProbe({ ...probeStart(id, r.steps), id: r.probe_id });
    closeStream.current = api.probeEvents(provider.id, r.probe_id, e => setProbe(st => (st && st.id === r.probe_id ? probeReducer(st, e) : st)));
  }
  async function disable(id) {
    const p = await guard(() => api.setProviderModels(provider.id, [...saved].filter(x => x !== id)));
    if (p) { await reload(); notify(`${id} disabled on ${p.name}`); log('Model disabled', `${p.name}: ${id}`); }
  }
  const toggle = id => {
    if (probes) { if (!probe) (saved.has(id) ? disable(id) : startProbe(id)); return; }
    setPicked(s => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n; });
  };

  async function save() {
    const p = await guard(() => api.setProviderModels(provider.id, [...picked]));
    if (p) { await reload(); notify(`${p.models.length} ${p.models.length === 1 ? 'model' : 'models'} enabled on ${p.name}`); log('Models enabled', `${p.name}: ${p.models.length}`); }
  }

  return <div className="model-browser od-stack">
    <div className="od-row model-toolbar">
      <Input className="od-fill" value={q} onChange={(e, d) => setQ(d.value)} placeholder="Search models by name or id" aria-label={`Search ${provider.name} models`}/>
      <select value={kind} onChange={e => setKind(e.target.value)} aria-label="Kind">{[['', 'All kinds'], ...kinds.map(k => [k, KIND_LABELS[k] || k])].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      <label className="od-row enabled-only"><input type="checkbox" checked={enabledOnly} onChange={e => setEnabledOnly(e.target.checked)}/> Enabled only</label>
      {unusable > 0 && <label className="od-row enabled-only" title="Models a test found this key can't run"><input type="checkbox" checked={showUnusable} onChange={e => setShowUnusable(e.target.checked)}/> Show unusable ({unusable})</label>}
      <Button appearance="subtle" disabled={busy || !online} onClick={() => load(true)}>Refresh</Button>
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}
    {busy && !models && <div className="od-row"><Spinner size="tiny"/><span>Reading {provider.name}'s models…</span></div>}
    {models && <>
      <p className="quiet-line">{picked.size} enabled · {models.length - unusable} offered{unusable ? ` · ${unusable} hidden because this key can't run them` : ''}{probes ? ' · this provider lists ids only, so each model is tested as you enable it, one at a time' : ''}</p>
      <ul className="model-list" aria-label={`${provider.name} models`}>
        {shown.slice(0, SHOWN).map(m => <li key={m.id} className={picked.has(m.id) ? 'enabled' : ''}>
          <label className="od-row-top">
            <input type="checkbox" checked={picked.has(m.id)} disabled={probes && (!online || !!probe)} onChange={() => toggle(m.id)}/>
            <span className="od-field od-fill">
              <span className="model-name">{m.name}</span>
              <span className="model-id">{m.id}</span>
            </span>
            <span className="model-facts">
              {m.unusable && <Badge appearance="tint" color="danger" size="small">Not available to this key</Badge>}
              {m.kinds.map(k => <Badge key={k} appearance="outline" size="small">{KIND_LABELS[k] || k}{m.guessed ? '?' : ''}</Badge>)}
              {m.kinds.includes('text') && m.reasoning === true && <Badge appearance="tint" color="brand" size="small">Reasoning</Badge>}
              {m.kinds.includes('text') && m.reasoning === false && <Badge appearance="tint" color="informative" size="small">No reasoning</Badge>}
              {capabilityTags(m).map(t => <Badge key={t} appearance="tint" color="success" size="small">{t}</Badge>)}
              <span className="model-cost">{m.cost || 'cost not reported'}</span>
            </span>
          </label>
        </li>)}
      </ul>
      {shown.length > SHOWN && <p className="quiet-line">Showing {SHOWN} of {shown.length}. Search or pick a kind to narrow the list.</p>}
      {!shown.length && <p className="quiet-line">No models match.</p>}
      {!probes && <div className="od-row model-save"><div className="od-fill"/>{changed && <Button appearance="subtle" onClick={() => setPicked(new Set(saved))}>Discard</Button>}<Button appearance="primary" disabled={!online || !changed} onClick={save}>Save enabled models</Button></div>}
    </>}
    {probe && <ProbeDialog probe={probe} onCancel={() => guard(() => api.stopProbe(provider.id, probe.id))} onClose={() => setProbe(null)}/>}
  </div>;
}

// Settings > Harness settings: which enabled model the harness runs on for each job.
// roles: GET /settings/models
export function HarnessSettingsTab({ ctx, providers, roles, onSaved, status, openProviders }) {
  const { api, online, guard, notify, log } = ctx;
  const initial = r => ({ chat: choiceKey(r?.chat?.provider, r?.chat?.model), summary: choiceKey(r?.summary?.provider, r?.summary?.model),
    decision: choiceKey(r?.decision?.provider, r?.decision?.model), scorer: r?.scorer || '' });
  const [form, setForm] = useState(initial(roles));
  useEffect(() => setForm(initial(roles)), [roles]);
  if (!roles || !providers) return <>{status}<div className="resource-row"><p>{online ? 'Loading…' : 'The harness is offline.'}</p></div></>;
  const dirty = JSON.stringify(form) !== JSON.stringify(initial(roles));

  async function save(e) {
    e.preventDefault();
    const r = await guard(() => api.setModelRoles({ chat: parseChoice(form.chat), summary: parseChoice(form.summary),
      decision: form.scorer ? parseChoice(form.decision) : null, scorer: form.scorer }));
    if (r) { onSaved(r); notify('Harness settings saved'); log('Harness settings saved', `chat ${r.chat?.model} · summary ${r.summary?.model}${r.scorer ? ` · decision ${r.decision?.model}` : ''}`); }
  }
  const picker = (role, hint, disabled = false) => {
    const kind = roles.roles[role];
    const groups = enabledByKind(providers, kind, role === 'decision' ? providers.filter(p => p.kind === 'openrouter').map(p => p.id) : null);
    const current = parseChoice(form[role]);
    const missing = current && !groups.some(g => g.provider === current.provider && g.models.some(m => m.id === current.model));
    return <Field label={roles.labels[role]} hint={groups.length ? hint : undefined}>
      {groups.length ? <select aria-label={roles.labels[role]} value={form[role]} disabled={disabled || !online} onChange={e => setForm(f => ({ ...f, [role]: e.target.value }))}>
        {(!form[role] || missing) && <option value={form[role]}>{missing ? `${current.model} (not enabled)` : 'Pick a model'}</option>}
        {groups.map(g => <optgroup key={g.provider} label={g.name}>{g.models.map(m => <option key={m.id} value={choiceKey(g.provider, m.id)}>{modelLabel(m)}</option>)}</optgroup>)}
      </select> : <p className="field-hint">No {KIND_LABELS[kind]?.toLowerCase() || kind} models enabled{role === 'decision' ? ' on an OpenRouter provider' : ''}. <button type="button" className="text-link" onClick={openProviders}>Enable one on the Model providers tab</button>.</p>}
    </Field>;
  };
  return <>
    {status}
    <form className="resource-row editor-form" onSubmit={save}>
      <h3>Models the harness runs on</h3>
      <p className="field-hint">Choose from the models enabled on the Model providers tab. Saving switches the harness over at once; running tasks finish on the model they started with.</p>
      {picker('chat', "Runs your bots' tasks.")}
      {picker('summary', 'Compacts long tasks, folds old conversation turns, and summarizes imported documents.')}
      <Field label="Decider scorer" hint="Chooses skills and MCP servers per task, and watches long tasks. Without one, every skill and tool is offered.">
        <select aria-label="Decider scorer" value={form.scorer} disabled={!online} onChange={e => setForm(f => ({ ...f, scorer: e.target.value }))}><option value="">None</option>{roles.scorer_kinds.map(k => <option key={k} value={k}>{k}</option>)}</select>
      </Field>
      {form.scorer && picker('decision', "The scorer's model. Decision models come from OpenRouter.")}
      <div className="od-row"><div className="od-fill"/>{dirty && <Button appearance="subtle" onClick={() => setForm(initial(roles))}>Discard</Button>}<Button appearance="primary" type="submit" disabled={!online || !dirty || !form.chat || !form.summary || (form.scorer && !form.decision)}>Save</Button></div>
    </form>
  </>;
}


// The modal while a model is tested; then why it could not be enabled, if it could not.
function ProbeDialog({ probe, onCancel, onClose }) {
  const end = probe.end;
  const MARK = { pending: '○', running: '●', pass: '✓', fail: '✗', skip: '–' };
  if (end?.status === 'unavailable') {
    return <Dialog open modalType="alert"><DialogSurface className="edit-dialog"><DialogBody>
      <DialogTitle>{probe.model} could not be enabled right now</DialogTitle>
      <DialogContent><p>It could not be enabled because {end.reason}. Try again later.</p></DialogContent>
      <DialogActions><Button appearance="primary" onClick={onClose}>OK</Button></DialogActions>
    </DialogBody></DialogSurface></Dialog>;
  }
  const failed = end?.status === 'failed';
  return <Dialog open modalType="alert"><DialogSurface className="edit-dialog"><DialogBody>
    <DialogTitle>{failed ? `${probe.model} could not be enabled` : `Testing ${probe.model}`}</DialogTitle>
    <DialogContent className="od-stack">
      <ProgressBar value={probe.total ? probe.done : undefined} max={probe.total || 1} thickness="large" aria-label={`Tested ${probe.done} of ${probe.total}`}/>
      <p className="quiet-line">{probe.total ? `${probe.done} of ${probe.total}` : 'Enabling…'}</p>
      <ul className="probe-steps" aria-label="Tests">{probe.steps.map(s => <li key={s.step} className={`probe-${s.status}`}>
        <span aria-hidden="true">{MARK[s.status]}</span> {s.label}{s.status === 'running' ? '…' : ''}{s.detail ? ` (${s.detail})` : ''}{s.status === 'fail' ? ': no' : ''}{s.status === 'skip' ? ': skipped (no chat)' : ''}
      </li>)}</ul>
      {failed && <p className="form-error" role="alert">{end.error}</p>}
    </DialogContent>
    <DialogActions>{failed ? <Button appearance="primary" onClick={onClose}>Close</Button> : <Button appearance="secondary" onClick={onCancel}>Cancel</Button>}</DialogActions>
  </DialogBody></DialogSurface></Dialog>;
}

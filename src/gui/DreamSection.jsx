import React, { useEffect, useState } from 'react';
import { Button, Field, Input } from '@fluentui/react-components';
import { anchorId } from './settings-index';
import { KIND_LABELS, choiceKey, enabledByKind, modelLabel, parseChoice } from './models.js';
import { MAX_MINUTES, MIN_MINUTES, PRIVACY, canEnable, canRunNow, errorLine, needsDesktopNote, statusLine, validMinutes } from './dream.js';

const POLL_MS = 5000;

// Settings > Harness settings > Dreaming: the switch, the Dreaming model, the idle time, Dream now and what was
// learned. ctx: { api, online, guard, notify }; providers and roles as the Harness tab has them; onSaved(roles).
export function DreamSection({ ctx, providers, roles, onSaved, openProviders }) {
  const { api, online, guard, notify } = ctx;
  const [status, setStatus] = useState(null);
  const [facts, setFacts] = useState(null);
  const [minutes, setMinutes] = useState('5');
  const [error, setError] = useState('');

  const take = s => { setStatus(s); setMinutes(m => (document.activeElement?.id === 'dream-minutes' ? m : String(s.idle_minutes))); };
  const loadStatus = () => guard(() => api.dream.status(), { quiet: true }).then(s => { if (s) take(s); return s; });
  const loadFacts = () => guard(() => api.dream.facts(), { quiet: true }).then(f => { if (Array.isArray(f)) setFacts(f); });
  useEffect(() => { if (online) { loadStatus(); loadFacts(); } }, [online]);
  useEffect(() => {
    if (!online || !status?.dreaming) return undefined;
    const t = setInterval(loadStatus, POLL_MS);
    return () => { clearInterval(t); loadFacts(); };
  }, [online, status?.dreaming]);

  async function run(fn, done) {
    setError('');
    try { const s = await fn(); if (s) take(s); if (done) notify(done); return true; }
    catch (e) { setError(e?.message || 'Something went wrong.'); return false; }
  }
  const save = (enabled, idle_minutes) => run(() => api.dream.saveSettings({ enabled, idle_minutes }), enabled ? 'Dreaming is on' : 'Dreaming is off');
  async function choose(value) {
    setError('');
    const r = await guard(() => api.setModelRoles(value ? { dream: parseChoice(value) } : { clear_dream: true }));
    if (!r) return;
    onSaved(r); await loadStatus();
    notify(value ? 'Dreaming model saved' : 'Dreaming model cleared');
  }
  async function forget(f) {
    if (await guard(() => api.dream.forget(f.id)) === undefined) return;
    setFacts(list => (list || []).filter(x => x.id !== f.id)); loadStatus();
  }
  function onMinutesBlur() {
    if (!status) return;
    if (!validMinutes(minutes)) { setMinutes(String(status.idle_minutes)); return; }
    if (Number(minutes) !== status.idle_minutes) save(status.enabled, Number(minutes));
  }

  const kind = roles?.roles?.dream || 'text';
  const groups = enabledByKind(providers, kind);
  const current = status?.model ? choiceKey(status.model.provider, status.model.model) : '';
  const missing = status?.model && !groups.some(g => g.provider === status.model.provider && g.models.some(m => m.id === status.model.model));
  const problem = status ? errorLine(status) : '';

  return <div className="resource-row editor-form od-stack">
    <h3 id={anchorId('harness', 'Dreaming')}>Dreaming</h3>
    {!status
      ? <p className="field-hint">{online ? 'Loading…' : 'The harness is offline.'}</p>
      : <>
        <label className="od-row"><input type="checkbox" checked={status.enabled} disabled={!online || (!status.enabled && !canEnable(status))} onChange={e => save(e.target.checked, validMinutes(minutes) ? Number(minutes) : status.idle_minutes)}/> Learn from my conversations while the computer is idle</label>
        <p className="field-hint">{PRIVACY}</p>
        <Field label="Dreaming model">
          {groups.length || status.model
            ? <select aria-label="Dreaming model" value={current} disabled={!online} onChange={e => choose(e.target.value)}>
              <option value="">None</option>
              {missing && <option value={current}>{status.model.model} (not enabled)</option>}
              {groups.map(g => <optgroup key={g.provider} label={g.name}>{g.models.map(m => <option key={m.id} value={choiceKey(g.provider, m.id)}>{modelLabel(m)}</option>)}</optgroup>)}
            </select>
            : <p className="field-hint">No {KIND_LABELS[kind]?.toLowerCase() || kind} models enabled. <button type="button" className="text-link" onClick={openProviders}>Enable one on the Model providers page</button>.</p>}
        </Field>
        <div className="od-row">
          <label className="od-row">Start after <Input id="dream-minutes" type="number" min={MIN_MINUTES} max={MAX_MINUTES} aria-label="Start after, in minutes idle" value={minutes} disabled={!online}
            onChange={(e, d) => setMinutes(d.value)} onBlur={onMinutesBlur}/> minutes idle</label>
          <div className="od-fill"/>
          <Button appearance="outline" disabled={!canRunNow(status, online)} onClick={() => run(() => api.dream.runNow())}>Dream now</Button>
        </div>
        {needsDesktopNote(status) && <p className="field-hint">Dreaming starts on its own only in the desktop app. Use Dream now here.</p>}
        <p className="quiet-line" aria-live="polite">{statusLine(status)}</p>
        {(problem || error) && <p className="form-error" role="alert">{error || problem}</p>}
        <h4>What was learned</h4>
        {facts === null ? null : facts.length === 0 ? <p className="quiet-line">Nothing yet.</p>
          : <ul className="model-list" aria-label="What was learned">{facts.map(f => <li key={f.id}><div className="od-row-top">
            <span className="od-field od-fill"><span className="model-name">{f.text}</span>{f.title && <span className="model-id">{f.title}</span>}</span>
            <Button appearance="subtle" disabled={!online} onClick={() => forget(f)}>Forget</Button>
          </div></li>)}</ul>}
      </>}
  </div>;
}

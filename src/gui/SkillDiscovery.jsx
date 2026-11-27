import React, { useEffect, useRef, useState } from 'react';
import { Button, Input, Spinner } from '@fluentui/react-components';
import { anchorId } from './settings-index';
import { LICENCE_CHOICES, canAdd, casemarkLine, createLatest, createQueue, repoErrorLine, repoLine } from './skilltuner.js';

const POLL_MS = 15000;
const READING_MS = 3000;
const EMPTY = { address: '', checked: '', licence: null, github: '', index: '' };

// Settings > Skill discovery: where SkillTuner looks for skills. agentskills.finance is built in; the owner adds
// public GitHub repositories. Addresses, labels and errors come from the harness (and GitHub) and are shown as plain
// text children, never as HTML or Markdown. ctx: { api, online, notify }.
export function SkillDiscovery({ ctx }) {
  const { api, online, notify } = ctx;
  const tuner = api.tuner;
  const [sources, setSources] = useState(null);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');
  const [rowError, setRowError] = useState({});
  const [busy, setBusy] = useState(() => new Set());
  const [form, setForm] = useState(EMPTY);
  const [formError, setFormError] = useState('');
  const [working, setWorking] = useState('');  // 'check' or 'add'
  const latest = useRef(createLatest()).current;  // only the newest read is used
  const queue = useRef(createQueue()).current;    // Read again, Remove, a licence save and Add never overlap
  const reading = !!sources?.repos?.some(r => r.reading);

  const load = () => {
    const t = latest.start();
    tuner.sources().then(s => { if (latest.fresh(t)) { setSources(s); setError(''); } }).catch(e => { if (latest.fresh(t)) setError(e?.message || 'Could not read the sources.'); });
    tuner.status().then(s => { if (latest.fresh(t)) setStatus(s); }).catch(() => {});
  };
  useEffect(() => {
    if (!online) return undefined;
    load();
    const t = setInterval(load, reading ? READING_MS : POLL_MS);
    return () => clearInterval(t);
  }, [online, reading]);  // eslint-disable-line react-hooks/exhaustive-deps

  const mark = (id, on) => setBusy(b => { const n = new Set(b); if (on) n.add(id); else n.delete(id); return n; });
  // The button is off from the click until its own action ends.
  function act(id, fn, done) {
    setRowError(x => ({ ...x, [id]: '' }));
    mark(id, true);
    return queue.run(async () => {
      latest.bump();
      try { await fn(); latest.bump(); if (done) notify(done); }
      catch (e) { setRowError(x => ({ ...x, [id]: e?.message || 'Something went wrong.' })); }
      finally { load(); }
    }).finally(() => mark(id, false));
  }
  const readAgain = r => act(r.id, () => tuner.readRepo(r.id), 'Reading the repository');
  const remove = r => {
    if (!window.confirm('Remove this repository? Skills already suggested from it stay.')) return;
    act(r.id, () => tuner.removeRepo(r.id), 'Repository removed');
  };
  const setLicence = (r, licence) => act(r.id, () => tuner.setRepoLicence(r.id, licence), 'Licence saved');

  const address = form.address.trim();
  async function check() {
    setFormError(''); setWorking('check');
    try {
      const r = await tuner.checkRepo(address);
      setForm(f => ({ ...f, checked: address, licence: r?.licence || '', github: r?.github_licence || '' }));
    } catch (e) { setForm(f => ({ ...f, checked: '', licence: null, github: '' })); setFormError(e?.message || 'Could not check the address.'); }
    finally { setWorking(''); }
  }
  async function add() {
    setFormError(''); setWorking('add'); latest.bump();
    try {
      await queue.run(() => tuner.addRepo({ address, licence: form.licence, index: form.index.trim() }));
      setForm(EMPTY);
      notify('Repository added');
    } catch (e) { setFormError(e?.message || 'Could not add the repository.'); }
    finally { setWorking(''); load(); }
  }

  const repos = sources?.repos || [];
  return <section className="resource-row editor-form" id={anchorId('discovery', 'Skill discovery')}>
    <h2>Skill discovery</h2>
    <p className="field-hint">SkillTuner looks in these places for skills that fit what you keep asking for. Repositories are searched on this computer; only agentskills.finance receives a need's words.</p>
    {!online && <p className="field-hint">Start the harness to see the sources.</p>}
    {online && !sources && !error && <Spinner size="tiny" label="Loading sources…"/>}
    {sources && <div className="od-stack">
      <div className="od-row"><span className="model-name od-fill">{sources.casemark?.label || 'agentskills.finance'}</span><span className="field-hint">{casemarkLine(sources.casemark)}</span></div>
      {repos.map(r => <div className="od-stack" key={r.id}>
        <div className="od-row-top">
          <div className="od-field od-fill">
            <span className="model-name">{r.label || r.address}{r.folder ? ` /${r.folder}` : ''}</span>
            <span className="field-hint">{repoLine(r)}</span>
            {r.error && !r.reading && <span className="form-error" role="status">{repoErrorLine(r.error)}</span>}
          </div>
          <select aria-label={`Licence for skills without their own, ${r.label || r.address}`} value={r.licence || ''} disabled={!online || busy.has(r.id)} onChange={e => setLicence(r, e.target.value)}>
            {LICENCE_CHOICES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
          </select>
          <Button appearance="outline" disabled={!online || r.reading || busy.has(r.id)} onClick={() => readAgain(r)}>Read again</Button>
          <Button appearance="subtle" disabled={!online || busy.has(r.id)} onClick={() => remove(r)}>Remove</Button>
        </div>
        {rowError[r.id] && <p className="form-error" role="alert">{rowError[r.id]}</p>}
      </div>)}
    </div>}
    <div className="od-stack">
      <h3>Add a repository</h3>
      <div className="od-row">
        <Input className="od-fill" aria-label="GitHub repository address" placeholder="https://github.com/owner/repo/tree/main/skills" value={form.address} disabled={!online || working !== ''}
          onChange={(e, v) => setForm(f => ({ ...f, address: v.value }))}/>
        <Button appearance="outline" disabled={!online || !address || working !== ''} onClick={check}>{working === 'check' ? 'Checking…' : 'Check'}</Button>
      </div>
      {form.licence !== null && form.checked === address && <label className="od-row">Licence for skills without their own
        <select aria-label="Licence for skills without their own" value={form.licence} onChange={e => setForm(f => ({ ...f, licence: e.target.value }))}>
          {LICENCE_CHOICES.map(c => <option key={c.value} value={c.value}>{c.label}</option>)}
        </select>
        <span className="field-hint">{form.github ? `(GitHub says: ${form.github})` : '(GitHub names no licence)'}</span></label>}
      <div className="od-row">
        <Input className="od-fill" aria-label="Advanced: index file path" placeholder="Advanced: index file path (optional)" value={form.index} disabled={!online || working !== ''}
          onChange={(e, v) => setForm(f => ({ ...f, index: v.value }))}/>
        <Button appearance="primary" disabled={!online || working !== '' || !canAdd({ ...form, address })} onClick={add}>{working === 'add' ? 'Adding…' : 'Add'}</Button>
      </div>
      {formError && <p className="form-error" role="alert">{formError}</p>}
    </div>
    {status && !status.enabled && <p className="field-hint">SkillTuner is off. Switch it on under Dreaming.</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </section>;
}

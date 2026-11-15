import React, { useState } from 'react';
import { Button, Input, ProgressBar } from '@fluentui/react-components';
import { Checkmark24Regular, Clock24Regular } from '@fluentui/react-icons';
import { connectionNote } from '../music-api.js';
import { DigestFields, GenrePicker } from './fields.jsx';
import { DAYS, formatTime, fromPercent, scanProgress, scheduleFromDraft, toPercent } from './helpers.js';

const STEPS = ['Your collection', 'Your focus', 'Your digest', 'Ready to explore'];
const Icon = ({ as: Component }) => <Component className="od-icon" aria-hidden="true"/>;

// First-run and "Connection & setup". The token is typed here, sent once (test, then save) and the field is cleared.
// props: status, music (api), api (routines), names, refreshStatus, onFinish(), onCancel()
export default function MusicSetup({ status, music, api, names, refreshStatus, onFinish, onCancel }) {
  const s = status.settings;
  const [step, setStep] = useState(0);
  const [draft, setDraft] = useState({ username: s.username || '', focus: [...s.focus], count: s.count, era: toPercent(s.era_mix), obscurity: toPercent(s.obscurity), day: 4, time: '09:00' });
  const [token, setToken] = useState('');
  const [found, setFound] = useState(null);  // the connect result when the test passed
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const saved = status.connection.token;
  const scan = status.scan;
  const progress = scanProgress(scan);
  const tokenSaved = saved;

  async function run(fn) {
    setBusy(true); setError('');
    try { return await fn(); } catch (e) { setError(e.message || 'Something went wrong.'); return undefined; } finally { setBusy(false); }
  }
  const test = () => run(async () => {
    setFound(null);
    if (!draft.username.trim()) { setError('Enter a Discogs username to continue.'); return; }
    const r = await music.connect(draft.username.trim(), token.trim() || undefined);
    if (r.state === 'ok') setFound(r); else setError(connectionNote(r.state) || 'Could not connect to Discogs.');
  });
  const next0 = () => run(async () => {
    if (token.trim()) { await music.saveToken(token.trim()); await refreshStatus(); }
    setToken('');  // the token is not kept after it is sent
    setStep(1);
  });
  const startScan = () => run(async () => {
    const st = await music.settings({ enabled: true, username: draft.username.trim(), focus: draft.focus, count: draft.count, era_mix: fromPercent(draft.era), obscurity: fromPercent(draft.obscurity) });
    if (st.routine_id) {
      const routine = (await api.routines()).find(r => r.id === st.routine_id);
      if (routine) await api.saveRoutine({ ...routine, schedule: scheduleFromDraft(routine, draft) });
    }
    await music.scan();
    setStep(3);
    await refreshStatus();
  });
  const retryScan = () => run(async () => { await music.scan(); await refreshStatus(); });
  const set = patch => setDraft(d => ({ ...d, ...patch }));

  return <div className="setup-shell">
    <div className="page-heading"><div><div className="eyebrow">Music librarian</div><h1>A good place to start.</h1><p className="description">Connect your shelf. Tell us where to dig.</p></div>{onCancel && <div className="actions"><Button appearance="subtle" onClick={onCancel}>Back to Music</Button></div>}</div>
    <ol className="setup-steps">{STEPS.map((label, i) => <li className={`setup-step ${i === step ? 'current' : i < step ? 'complete' : ''}`} key={label} aria-current={i === step ? 'step' : undefined}><span className="step-number">{i < step ? '✓' : i + 1}</span><span>{label}</span></li>)}</ol>
    <section className="setup-panel">
      {step === 0 && <>
        <div><h2>Bring your record collection.</h2><p className="muted small">A public Discogs collection only needs a username.</p></div>
        <div className="field"><label htmlFor="discogs-username">Discogs username</label><Input id="discogs-username" value={draft.username} onChange={(_, d) => { set({ username: d.value }); setFound(null); setError(''); }} onKeyDown={e => { if (e.key === 'Enter') test(); }} aria-invalid={!!error} aria-describedby={error ? 'discogs-error' : undefined}/></div>
        <div className="field"><label htmlFor="discogs-token">Personal access token {saved ? <span className="muted">(saved ••••{status.connection.token_hint}; leave empty to keep it)</span> : <span className="muted">(needed for weekly picks)</span>}</label><Input id="discogs-token" type="password" value={token} onChange={(_, d) => { setToken(d.value); setFound(null); setError(''); }} autoComplete="off"/><span className="hint">Weekly picks are checked against your collection through Discogs, and adding to your wantlist needs it too. The map works without one. <a href="https://www.discogs.com/settings/developers" target="_blank" rel="noopener noreferrer">Get a token in Discogs ↗</a></span></div>
        {error && <p id="discogs-error" className="field-error" role="alert">{error}</p>}
        {found && <div className="inline-status" role="status"><Icon as={Checkmark24Regular}/><span>Found {found.records?.toLocaleString?.() ?? 'your'} records{found.digest_needs_token ? ' · weekly picks need a token' : ''}</span></div>}
        <div className="setup-actions"><Button onClick={test} disabled={busy}>{busy ? 'Checking…' : 'Test connection'}</Button><Button appearance="primary" disabled={!found || busy} onClick={next0}>Continue</Button></div>
      </>}
      {step === 1 && <>
        <div><h2>What should we dig for?</h2><p className="muted small">Start with the genres that matter to you right now.</p></div>
        <GenrePicker focus={draft.focus} setFocus={focus => set({ focus })} names={names}/>
        {!names.length && <p className="small muted">The genre atlas downloads the first time Music is switched on. Continue; you can pick focus genres in Settings once it is ready.</p>}
        {names.length > 0 && draft.focus.length === 0 && <p className="field-error">Choose at least one focus genre to continue.</p>}
        <div className="setup-actions"><Button onClick={() => setStep(0)}>Back</Button><Button appearance="primary" disabled={names.length > 0 && !draft.focus.length} onClick={() => setStep(2)}>Continue</Button></div>
      </>}
      {step === 2 && <>
        <div><h2>Make it a weekly ritual.</h2><p className="muted small">A short, considered list. Ready when you are.</p></div>
        <DigestFields value={draft} onChange={setDraft} schedule={tokenSaved ? { kind: 'weekly' } : null}/>
        {!tokenSaved && <p className="small muted">The weekly digest starts once a Discogs token is saved. Everything else works now.</p>}
        {error && <p className="field-error" role="alert">{error}</p>}
        <div className="setup-actions"><Button onClick={() => setStep(1)}>Back</Button><Button appearance="primary" disabled={busy || (tokenSaved && !draft.time)} onClick={startScan}>{busy ? 'Starting…' : 'Start collection scan'}</Button></div>
      </>}
      {step === 3 && <>
        <div><h2>{scan.state === 'done' ? 'Your shelf, mapped.' : scan.state === 'failed' ? 'The scan did not finish.' : 'Getting to know your shelf.'}</h2><p className="muted small">{scan.state === 'done' ? `${scan.mapped.toLocaleString()} records mapped. A world of places to go next.` : scan.state === 'failed' ? (connectionNote(scan.error) || 'Something stopped the scan. Try again.') : status.atlas.state === 'downloading' ? 'Downloading the genre atlas (one time), then reading your collection.' : 'A first scan takes about 30 seconds per 1,000 records.'}</p></div>
        <ProgressBar value={progress.value} aria-label="Collection scan progress"/>
        <div className="scan-steps" aria-live="polite">
          <div><Icon as={progress.reading ? Checkmark24Regular : Clock24Regular}/><span>Read Discogs collection {progress.reading ? '· done' : ''}</span></div>
          <div><Icon as={progress.mapping ? Checkmark24Regular : Clock24Regular}/><span>Match musical styles to the atlas {progress.mapping ? '· done' : ''}</span></div>
          <div><Icon as={progress.finding ? Checkmark24Regular : Clock24Regular}/><span>Find nearby genres to explore {progress.finding ? '· done' : ''}</span></div>
        </div>
        {scan.state === 'done' && <div className="ownership">{tokenSaved ? `Your first digest: ${DAYS[draft.day]} at ${formatTime(draft.time)} · ${draft.count} albums` : 'Paste your Discogs token to get weekly picks'}</div>}
        {error && <p className="field-error" role="alert">{error}</p>}
        <div className="setup-actions"><span className="small muted">{scan.state === 'done' ? 'You can change your preferences anytime.' : scan.state === 'failed' ? '' : 'You can leave this page; the scan keeps going.'}</span>{scan.state === 'failed' ? <Button appearance="primary" disabled={busy} onClick={retryScan}>Try again</Button> : <Button appearance="primary" disabled={scan.state !== 'done'} onClick={onFinish}>Explore my taste map</Button>}</div>
      </>}
    </section>
  </div>;
}

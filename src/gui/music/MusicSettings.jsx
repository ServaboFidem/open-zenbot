import React, { useEffect, useRef, useState } from 'react';
import { Badge, Button, Input, ProgressBar, Switch } from '@fluentui/react-components';
import { connectionNote } from '../music-api.js';
import { DigestFields, GenrePicker } from './fields.jsx';
import { draftFromRoutine, fromPercent, scanProgress, scheduleFromDraft, scheduleLabel, toPercent, whenLabel } from './helpers.js';

const stateBadge = state => state === 'ok' ? ['success', 'Collection available'] : state === 'unset' ? ['informative', 'Not connected'] : ['danger', 'Needs attention'];

// Music settings: connection, focus, the digest (the managed routine's day, time and switch).
// props: status, music (api), api (routines), routine (the managed routine or null), names, refreshStatus, reloadRoutine, notify, startSetup
export default function MusicSettings({ status, music, api, routine, names, refreshStatus, reloadRoutine, notify, startSetup }) {
  const s = status.settings;
  const sig = JSON.stringify([s, routine && [routine.enabled, routine.schedule]]);
  const fresh = () => ({ focus: [...s.focus], count: s.count, era: toPercent(s.era_mix), obscurity: toPercent(s.obscurity), notify: s.notify, enabled: routine ? routine.enabled : false, ...draftFromRoutine(routine) });
  const [draft, setDraft] = useState(fresh);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState('');
  const [token, setToken] = useState('');
  const [tokenError, setTokenError] = useState('');
  const tokenRef = useRef(null);
  useEffect(() => { setDraft(fresh()); setError(''); }, [sig]);  // the saved values changed: show them
  const c = status.connection;
  const [badge, badgeText] = stateBadge(c.state);
  const scan = status.scan;
  const scanning = scan.state === 'running';
  const progress = scanProgress(scan);
  const rejected = c.state === 'token_invalid';
  const set = patch => setDraft(d => ({ ...d, ...patch }));

  async function guard(name, fn) {
    setBusy(name);
    try { await fn(); return true; } catch (e) { return e.message || 'Something went wrong.'; } finally { setBusy(''); }
  }
  async function save() {
    if (names.length && !draft.focus.length) { setError('Choose at least one focus genre.'); return; }
    if (routine && routine.schedule.kind !== 'interval' && !draft.time) { setError('Choose a valid digest time.'); return; }
    setError('');
    const r = await guard('save', async () => {
      await music.settings({ focus: draft.focus, count: draft.count, era_mix: fromPercent(draft.era), obscurity: fromPercent(draft.obscurity), notify: draft.notify });
      if (routine) await api.saveRoutine({ ...routine, enabled: draft.enabled, schedule: scheduleFromDraft(routine, draft) });
      await Promise.all([refreshStatus(), reloadRoutine()]);
    });
    if (r === true) notify('Music preferences saved.'); else setError(r);
  }
  async function saveToken() {
    if (!token.trim()) return;
    setTokenError('');
    const r = await guard('token', async () => { await music.saveToken(token.trim()); await refreshStatus(); await reloadRoutine(); });
    setToken('');  // sent; not kept
    if (r === true) notify('Discogs token saved.'); else setTokenError(r);
  }
  async function removeToken() {
    const r = await guard('token', async () => { await music.deleteToken(); await refreshStatus(); await reloadRoutine(); });
    if (r === true) notify('Discogs token removed. The weekly digest is off until you add one.'); else setTokenError(r);
  }
  async function rescan() {
    const r = await guard('scan', async () => { await music.scan(); await refreshStatus(); });
    if (r !== true) setError(r);
  }
  async function digestBackOn() {
    const r = await guard('digest', async () => { await music.settings({ digest: true }); await Promise.all([refreshStatus(), reloadRoutine()]); });
    if (r === true) notify('Weekly digest is back on.'); else setError(r);
  }
  async function toggleMusic(on) {
    const r = await guard('enabled', async () => { await music.settings({ enabled: on }); await Promise.all([refreshStatus(), reloadRoutine()]); });
    if (r !== true) setError(r);
  }

  return <div className="settings-layout"><div className="settings-form">
    <section className="settings-panel">
      <div><h2>Your collection</h2><p>Discogs · {c.username || 'no username yet'}{scan.mapped ? ` · ${scan.mapped.toLocaleString()} records mapped` : ''}</p></div>
      <div className="od-cluster"><Badge appearance="tint" color={badge}>{badgeText}</Badge><Button onClick={startSetup}>Connection &amp; setup</Button><Button appearance="subtle" disabled={!c.username || scanning || busy === 'scan'} onClick={rescan}>{scanning ? 'Scanning…' : 'Scan collection'}</Button></div>
      {scanning && <ProgressBar value={progress.value} aria-label="Collection scan progress"/>}
      {scan.state === 'failed' && <p className="field-error" role="alert">The last scan did not finish. {connectionNote(scan.error)}</p>}
      {c.state !== 'ok' && c.state !== 'unset' && !rejected && <p className="field-error" role="alert">{connectionNote(c.state)}</p>}
      {rejected && <div className="notice" role="alert"><div><strong>Reconnect Discogs</strong><p>Discogs rejected the saved token. Paste a fresh one below to get picks and wantlist adds working again.</p></div><Button appearance="primary" onClick={() => tokenRef.current?.focus()}>Paste a new token</Button></div>}
      <div className="field"><label htmlFor="settings-token">Discogs personal access token {c.token ? <span className="muted">(saved ••••{c.token_hint})</span> : <span className="muted">(none saved)</span>}</label>
        <div className="od-row"><Input id="settings-token" ref={tokenRef} type="password" autoComplete="off" value={token} onChange={(_, d) => setToken(d.value)} placeholder={c.token ? 'Paste a new token to replace it' : 'Paste your token'} className="od-fill"/><Button disabled={!token.trim() || busy === 'token'} onClick={saveToken}>{c.token ? 'Replace token' : 'Save token'}</Button>{c.token && <Button appearance="subtle" disabled={busy === 'token'} onClick={removeToken}>Remove token</Button>}</div>
        <span className="hint">Needed for weekly picks and wantlist adds. Kept in an owner-only file, never shown again. <a href="https://www.discogs.com/settings/developers" target="_blank" rel="noopener noreferrer">Get a token in Discogs ↗</a></span>
        {tokenError && <span className="field-error" role="alert">{tokenError}</span>}</div>
      <Switch checked={status.enabled} disabled={busy === 'enabled'} onChange={(_, d) => toggleMusic(d.checked)} label="Music librarian on"/>
    </section>
    <section className="settings-panel"><div><h2>Where to dig</h2><p>Only your focus list guides the weekly digest. The map still shows your whole collection.</p></div><GenrePicker focus={draft.focus} setFocus={focus => set({ focus })} names={names}/></section>
    <section className="settings-panel">
      <div><h2>Your weekly digest</h2><p>These preferences apply to future digests.{routine ? ` Now: ${scheduleLabel(routine)}${routine.enabled && routine.next_run ? `, next ${whenLabel(routine.next_run)}` : ''}.` : ''}</p></div>
      {status.digest === 'needs_token' && <p className="small muted">The weekly digest needs a Discogs token. Add one above and it starts.</p>}
      {routine?.error && <p className="field-error" role="alert">The digest&rsquo;s schedule is not valid: {routine.error}. Fix it on the Routines page.</p>}
      {!routine && status.digest === 'off' && <div className="od-cluster"><span className="small muted">The weekly digest routine was deleted.</span><Button appearance="primary" disabled={busy === 'digest'} onClick={digestBackOn}>Turn the weekly digest back on</Button></div>}
      <DigestFields value={draft} onChange={setDraft} schedule={routine?.schedule || null}/>
      {routine && <Switch checked={draft.enabled} onChange={(_, d) => set({ enabled: d.checked })} label="Run the digest on schedule"/>}
      <Switch checked={draft.notify} onChange={(_, d) => set({ notify: d.checked })} label="Desktop arrival notices"/>
      {error && <p className="field-error" role="alert">{error}</p>}
      <div className="save-row"><Button appearance="primary" disabled={busy === 'save'} onClick={save}>{busy === 'save' ? 'Saving…' : 'Save preferences'}</Button><Button onClick={() => { setDraft(fresh()); setError(''); notify('Unsaved changes discarded.'); }}>Discard changes</Button></div>
    </section>
  </div>
  <aside className="help-panel"><h3>A librarian who learns.</h3><p>Want it, pass, or already own it: each reaction gives next week&rsquo;s picks a little more direction.</p>{status.atlas.licenses?.length > 0 && <details className="licenses"><summary>Genre atlas licenses</summary>{status.atlas.licenses.map((l, i) => <p className="tiny" key={i}>{l}</p>)}</details>}</aside></div>;
}

import React, { useEffect, useRef, useState } from 'react';
import { anchorId } from './settings-index';
import { PRIVACY, createLatest, statusLine } from './skilltuner.js';

const POLL_MS = 15000;

// Settings > Skills > SkillTuner: the switch and a status line. SkillTuner runs only inside dreaming's passes, so it
// reads dreaming's own status too: while dreaming is off the switch cannot be turned on, and a line says where to turn
// dreaming on (openDreaming opens Settings > Memory > Dreaming). ctx: { api, online, notify }.
export function SkillTunerSection({ ctx, openDreaming }) {
  const { api, online, notify } = ctx;
  const [status, setStatus] = useState(null);
  const [dreamingOn, setDreamingOn] = useState(null);  // null until dreaming's status is read
  const [error, setError] = useState('');
  const latest = useRef(createLatest()).current;  // a read that started before the newest one or a save is dropped
  const load = () => {
    const t = latest.start();
    api.tuner.status().then(s => { if (latest.fresh(t)) setStatus(s); }).catch(() => {});
    api.dream.status().then(d => setDreamingOn(!!d?.enabled)).catch(() => {});
  };
  useEffect(() => {
    if (!online) return undefined;
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [online]);  // eslint-disable-line react-hooks/exhaustive-deps
  async function toggle(enabled) {
    setError('');
    latest.bump();
    try { const s = await api.tuner.saveSettings({ enabled }); latest.bump(); setStatus(s); notify(enabled ? 'SkillTuner is on' : 'SkillTuner is off'); }
    catch (e) { setError(e?.message || 'Something went wrong.'); }
  }
  return <section className="resource-row editor-form">
    <h2 id={anchorId('skills', 'SkillTuner')}>SkillTuner</h2>
    {!status ? <p className="field-hint">{online ? 'Loading…' : 'The harness is offline.'}</p> : <>
      <label className="od-row"><input type="checkbox" checked={!!status.enabled} disabled={!online || (!status.enabled && dreamingOn !== true)} onChange={e => toggle(e.target.checked)}/> Find skills that match what I ask for while the computer dreams</label>
      {dreamingOn === false && <p className="field-hint">Dreaming is off. SkillTuner runs only while ZenBot dreams. Turn it on under <button type="button" className="text-link" onClick={openDreaming}>Memory &gt; Dreaming</button>.</p>}
      <p className="field-hint">{PRIVACY}</p>
      <p className="quiet-line" aria-live="polite">{statusLine(status)}</p>
      {error && <p className="form-error" role="alert">{error}</p>}
    </>}
  </section>;
}

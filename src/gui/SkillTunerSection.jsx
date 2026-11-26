import React, { useEffect, useRef, useState } from 'react';
import { anchorId } from './settings-index';
import { PRIVACY, createLatest, statusLine } from './skilltuner.js';

const POLL_MS = 15000;

// Settings > Harness settings > Dreaming > SkillTuner: the switch and a status line. ctx: { api, online, notify };
// dreamingOn says whether dreaming itself is on (SkillTuner runs inside its passes).
export function SkillTunerSection({ ctx, dreamingOn }) {
  const { api, online, notify } = ctx;
  const [status, setStatus] = useState(null);
  const [error, setError] = useState('');
  const latest = useRef(createLatest()).current;  // a read that started before the newest one or a save is dropped
  const load = () => { const t = latest.start(); api.tuner.status().then(s => { if (latest.fresh(t)) setStatus(s); }).catch(() => {}); };
  useEffect(() => {
    if (!online) return undefined;
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [online]);
  async function toggle(enabled) {
    setError('');
    latest.bump();
    try { const s = await api.tuner.saveSettings({ enabled }); latest.bump(); setStatus(s); notify(enabled ? 'SkillTuner is on' : 'SkillTuner is off'); }
    catch (e) { setError(e?.message || 'Something went wrong.'); }
  }
  return <div className="od-stack">
    <h4 id={anchorId('harness', 'SkillTuner')}>SkillTuner</h4>
    {!status ? <p className="field-hint">{online ? 'Loading…' : 'The harness is offline.'}</p> : <>
      <label className="od-row"><input type="checkbox" checked={!!status.enabled} disabled={!online || (!status.enabled && !dreamingOn)} onChange={e => toggle(e.target.checked)}/> Find finance skills that match what I ask for while the computer dreams</label>
      <p className="field-hint">{PRIVACY}</p>
      <p className="quiet-line" aria-live="polite">{statusLine(status)}</p>
      {error && <p className="form-error" role="alert">{error}</p>}
    </>}
  </div>;
}

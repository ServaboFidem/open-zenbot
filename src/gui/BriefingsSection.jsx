import React, { useEffect, useRef, useState } from 'react';
import { Badge, Button, Switch } from '@fluentui/react-components';
import { Add24Regular, Play24Regular, WeatherSunny24Regular } from '@fluentui/react-icons';
import BriefingDialog from './BriefingDialog.jsx';
import { POLL_MS, SECTION_LINE, canPlay, keeperOf, lastRunLine, readinessFix, readinessLine, scheduleLabel, skippedLate, wakeWarning } from './briefings.js';
import { formatWhen } from './routine-schedule.js';

// The Briefings section at the top of the Routines page: ZenBot speaking on the Sonos speakers. The status is read
// again every 15 s while the page is open, as the routines are. Speaker names come from Sonos: plain text only.
// Props: { api, online, notify, openSettings(panel, title) }
export default function BriefingsSection({ api, online, notify, openSettings }) {
  const [status, setStatus] = useState(null);   // GET /briefings
  const [weatherSet, setWeatherSet] = useState(null);
  const [dialog, setDialog] = useState(null);   // { briefing } to edit, {} to add
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const seq = useRef(0);
  const keeper = keeperOf(typeof window === 'undefined' ? null : window);  // the desktop app's, or null
  const [keeperState, setKeeperState] = useState(null);  // keeper.get(): { managed, platform, login, wake_error }

  async function readKeeper() {
    if (!keeper) return;
    try { setKeeperState(await keeper.get()); } catch { /* keep what was shown */ }
  }
  // the main process matches its wake tasks to the harness's briefings again, then says how that went
  async function syncKeeper() {
    if (!keeper) return;
    try { await keeper.refresh(); } catch { /* the briefing is saved either way */ }
    await readKeeper();
  }

  async function refresh() {
    const mine = ++seq.current;
    try { const s = await api.briefings.list(); if (mine === seq.current) setStatus(s); } catch { /* keep what was shown */ }
  }
  useEffect(() => {
    if (!online) return undefined;
    refresh();
    const t = setInterval(refresh, POLL_MS);
    return () => clearInterval(t);
  }, [online]);  // eslint-disable-line react-hooks/exhaustive-deps
  async function readWeather() {
    try { const s = await api.morning.settings(); setWeatherSet(!!s?.location); } catch { setWeatherSet(null); }
  }
  useEffect(() => { if (online) readWeather(); }, [online]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { readKeeper(); }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  const morning = status?.briefings?.find(b => b.kind === 'morning') || null;
  const reason = status?.reason || null;  // the top level holds even before a briefing exists

  async function guarded(fn) {
    setBusy(true); setError('');
    try { await fn(); await refresh(); await syncKeeper(); return true; } catch (e) { setError(e?.message || 'That did not work.'); return false; } finally { setBusy(false); }
  }
  async function save(body, login) {
    await api.briefings.save('morning', body);
    if (login !== null && keeper?.setLogin) { try { await keeper.setLogin(login); } catch { /* the briefing is saved */ } }
    setDialog(null); notify('Morning briefing saved'); await refresh(); await syncKeeper();
  }
  const toggle = (b, enabled) => guarded(() => api.briefings.save('morning', { enabled, slots: b.slots, speakers: b.speakers, volume: b.volume }));
  const remove = () => { if (window.confirm('Delete the morning briefing?')) guarded(async () => { await api.briefings.remove('morning'); notify('Morning briefing deleted'); }); };
  const play = () => guarded(async () => { await api.briefings.play('morning'); notify('Playing the morning briefing'); });

  const lastLine = morning ? lastRunLine(morning.last_run, morning.last_skip) : '';
  const lastAt = morning?.last_run?.at || morning?.last_skip?.at;
  const notReady = morning && !morning.ready;
  const fix = readinessFix(morning?.reason);
  return <section className="briefings-section" aria-label="Briefings">
    <div className="section-heading od-row"><h2>Briefings</h2></div>
    <div className="od-row">
      <p className="od-fill">{SECTION_LINE}</p>
      {!morning && <Button appearance="primary" icon={<Add24Regular/>} disabled={!online || !status} onClick={() => setDialog({})}>Add a morning briefing</Button>}
    </div>
    {error && <div className="form-error" role="alert">{error}</div>}
    {status?.error && <div className="form-error" role="alert">{status.error}</div>}
    {morning && <div className="list-panel"><article className="resource-row od-row-top">
      <span className="resource-icon"><WeatherSunny24Regular/></span>
      <div className="od-field od-fill">
        <h3>Morning briefing {morning.state !== 'idle' && <Badge appearance="tint">{morning.state === 'building' ? 'Getting ready' : 'Playing'}</Badge>}</h3>
        <p>{scheduleLabel(morning.slots)} · {morning.speakers ? morning.speakers.join(', ') : 'Every speaker'} · Volume {morning.volume}</p>
        <span>Next: {morning.enabled ? formatWhen(morning.next_run) || 'none' : 'paused'} · Last: {lastLine ? `${lastAt ? `${formatWhen(lastAt)}, ` : ''}${lastLine}` : 'never'}</span>
        {skippedLate(morning.last_run) && <span className="form-error" role="status">ZenBot was not running or the computer was asleep at the briefing time.</span>}
        {wakeWarning(keeperState) && <span className="form-error" role="status">{wakeWarning(keeperState)}</span>}
        {notReady && <span className="form-error" role="status">{readinessLine(morning.reason)}{fix && <> <button type="button" className="text-link" onClick={() => openSettings(fix.panel, fix.title)}>Open settings</button></>}</span>}
      </div>
      <div className="routine-controls">
        <Switch checked={morning.enabled} label={morning.enabled ? 'On' : 'Off'} disabled={busy} onChange={(e, v) => toggle(morning, v.checked)}/>
        <Button appearance="outline" onClick={() => setDialog({ briefing: morning })}>Edit</Button>
        <Button appearance="outline" disabled={busy} onClick={remove}>Delete</Button>
        <Button appearance="primary" icon={<Play24Regular/>} disabled={!online || busy || !canPlay(morning)} title={notReady ? readinessLine(morning.reason) : ''} onClick={play}>Play now</Button>
      </div>
    </article></div>}
    {dialog && <BriefingDialog briefing={dialog.briefing || null} status={status} ready={!reason} reason={reason} weatherSet={weatherSet}
      keeper={keeperState} onSave={save} onClose={() => setDialog(null)} openSettings={(p, t) => { setDialog(null); openSettings(p, t); }}/>}
  </section>;
}

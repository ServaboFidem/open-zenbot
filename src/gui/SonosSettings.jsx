import React, { useEffect, useState } from 'react';
import { Button, Field, Input, Spinner, Switch } from '@fluentui/react-components';
import { anchorId } from './settings-index';
import { errorLine } from './mail.js';
import { HELP_LINE, POLL_MS, canLookAgain, shouldPoll, statusLine, volumeError } from './sonos.js';

// Settings > Connected apps > Sonos: whether ZenBot looks for Sonos speakers on this network, what it found, Look
// again (which also scans the network, slower), and the highest volume ZenBot may set. Room names come from the
// speakers and are shown as plain text.
// Props: { api, online, notify }
export function SonosSettings({ api, online, notify }) {
  const sonos = api.sonos;
  const [status, setStatus] = useState(null);
  const [volume, setVolume] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  async function load() {
    try {
      const st = await sonos.status();
      setStatus(st);
      setVolume(v => (v === '' ? String(st.max_volume) : v));
      setError('');
    } catch (e) { setError(errorLine(e)); }
  }
  useEffect(() => { if (online) load(); }, [online]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {  // while a look is under way, read the status again until it ends
    if (!online || !shouldPoll(status)) return undefined;
    const id = setTimeout(load, POLL_MS);
    return () => clearTimeout(id);
  }, [online, status]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function run(label, call, done) {
    setBusy(label); setError('');
    try {
      const st = await call();
      setStatus(st);
      if (done) notify(done);
      return st;
    } catch (e) { setError(errorLine(e)); return null; }
    finally { setBusy(''); }
  }
  const toggle = on => run('switch', () => sonos.saveSettings(on, status.max_volume), on ? 'Sonos on' : 'Sonos off');
  const lookAgain = () => run('look', () => sonos.lookAgain());
  async function saveVolume(e) {
    e.preventDefault();
    const st = await run('volume', () => sonos.saveSettings(status.enabled, Number(volume.trim())), 'Volume limit saved');
    if (st) setVolume(String(st.max_volume));
  }

  const off = !online;
  const volError = volume.trim() ? volumeError(volume) : '';
  const lastLook = status?.last_look_at ? new Date(status.last_look_at) : null;

  return <section className="resource-row editor-form" id={anchorId('apps', 'Sonos')}>
    <h2>Sonos</h2>
    <p className="field-hint">{HELP_LINE}</p>
    {off && <p className="field-hint">Start the harness to find your Sonos speakers.</p>}
    {online && !status && !error && <Spinner size="tiny" label="Loading Sonos…"/>}
    {status && <>
      <Switch checked={status.enabled} disabled={off || busy !== ''} label="Look for Sonos speakers and let ZenBot play music"
        onChange={(e, d) => toggle(d.checked)}/>
      <p role="status">{statusLine(status)}</p>
      {status.enabled && lastLook && !Number.isNaN(lastLook.getTime()) &&
        <p className="field-hint">Last looked {lastLook.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}.</p>}
      <div className="od-row">
        <Button appearance="outline" disabled={busy !== '' || !canLookAgain(status, online)} onClick={lookAgain}>
          {busy === 'look' ? 'Starting…' : 'Look again'}</Button>
        <span className="field-hint">Also searches the whole network, which takes longer.</span>
      </div>
      <form className="od-row" onSubmit={saveVolume}>
        <Field label="Highest volume ZenBot may set" hint="0 to 100. A louder speaker is turned down to this before ZenBot plays."
          validationMessage={volError || undefined}>
          <Input type="number" min={0} max={100} step={1} value={volume} disabled={off || busy !== ''}
            onChange={(e, d) => setVolume(d.value)}/>
        </Field>
        <Button appearance="primary" type="submit"
          disabled={off || busy !== '' || !volume.trim() || !!volError || Number(volume) === status.max_volume}>
          {busy === 'volume' ? 'Saving…' : 'Save'}</Button>
      </form>
    </>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </section>;
}

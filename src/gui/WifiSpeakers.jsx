import React, { useEffect, useRef, useState } from 'react';
import { Button, Radio, RadioGroup, Spinner, Switch } from '@fluentui/react-components';
import { anchorId } from './settings-index';
import { errorLine } from './mail.js';
import { freshRead } from './sonos.js';
import { HELP_LINE, NETWORK_LINE, PROTOCOLS, PROTOCOL_NAMES, canLookAgain, defaultLine, lastLook, nextRead, protocolLine, speakerDetail } from './wifi-speakers.js';

// Settings > Wi-Fi speakers: a switch per speaker type (AirPlay, Chromecast, DLNA; each off until turned on, since
// it listens on the home network), the speakers each found, and the one default speaker, picked with a radio and
// saved at once. Names come from the devices and are shown as plain text.
// Props: { api, online, notify }
export function WifiSpeakers({ api, online, notify }) {
  const speakers = api.speakers;
  const [status, setStatus] = useState(null);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [fails, setFails] = useState(0);  // failed status reads in a row
  const saves = useRef(0);  // saves finished; a status read that was out across one is stale

  async function load() {
    const at = saves.current;
    try {
      const st = await speakers.status();
      if (!freshRead(at, saves.current)) return;
      setStatus(st); setError(''); setFails(0);
    } catch (e) { setError(errorLine(e)); setFails(n => n + 1); }
  }
  useEffect(() => { if (online) load(); }, [online]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {  // while a look is under way read the status again until it ends; retry a failed first read
    const wait = online ? nextRead(status, fails > 0) : null;
    if (wait === null) return undefined;
    const id = setTimeout(load, wait);
    return () => clearTimeout(id);
  }, [online, status, fails]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function run(label, call, done) {
    setBusy(label); setError('');
    try {
      const st = await call();
      saves.current += 1;
      setStatus(st);
      if (done) notify(done);
    } catch (e) { setError(errorLine(e)); }
    finally { setBusy(''); }
  }
  const toggle = (p, on) => run(`switch-${p}`, () => speakers.saveSwitches({ [p]: on }), `${PROTOCOL_NAMES[p]} ${on ? 'on' : 'off'}`);
  const pick = key => run('default', () => speakers.setDefault(key), key ? 'Default speaker saved' : 'Default speaker cleared');
  const lookAgain = () => run('look', () => speakers.lookAgain());

  const off = !online;
  const looked = status ? lastLook(status) : '';
  const lookedAt = looked ? new Date(looked) : null;
  const chosen = status?.default?.key || '';

  return <section className="resource-row editor-form" id={anchorId('speakers', 'Wi-Fi speakers')}>
    <h2>Wi-Fi speakers</h2>
    <p className="field-hint">{HELP_LINE}</p>
    <p className="field-hint">{NETWORK_LINE}</p>
    {off && <p className="field-hint">Start the harness to find your speakers.</p>}
    {online && !status && !error && <Spinner size="tiny" label="Loading speakers…"/>}
    {status && <>
      <p role="status">{defaultLine(status)}</p>
      <div className="od-row">
        <Button appearance="outline" disabled={busy !== '' || !canLookAgain(status, online)} onClick={lookAgain}>
          {busy === 'look' ? 'Starting…' : 'Look again'}</Button>
        {chosen && <Button appearance="subtle" disabled={off || busy !== ''} onClick={() => pick(null)}>No default</Button>}
        {lookedAt && !Number.isNaN(lookedAt.getTime()) &&
          <span className="field-hint">Last looked {lookedAt.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}.</span>}
      </div>
      {PROTOCOLS.map(p => {
        const info = status.protocols?.[p];
        if (!info) return null;
        const name = PROTOCOL_NAMES[p];
        const line = protocolLine(info, name);
        return <div className="od-stack" key={p}>
          <div className="od-row">
            <h3 className="od-fill">{name}</h3>
            <Switch checked={info.on} disabled={off || busy !== ''} label={info.on ? 'On' : 'Off'} aria-label={`Look for ${name} speakers`}
              onChange={(e, d) => toggle(p, d.checked)}/>
          </div>
          {line && <p className="field-hint">{line}</p>}
          {info.on && info.speakers?.length > 0 &&
            <RadioGroup aria-label={`${name} speakers`} value={chosen} disabled={off || busy !== ''} onChange={(e, d) => pick(d.value)}>
              {info.speakers.map(s => <Radio key={s.key} value={s.key} label={<>
                {s.name || 'Unnamed speaker'}
                {speakerDetail(s) && <span className="field-hint"> {speakerDetail(s)}</span>}
                {s.key === chosen && <span className="field-hint"> (default)</span>}
              </>}/>)}
            </RadioGroup>}
        </div>;
      })}
    </>}
    {error && <p className="form-error" role="alert">{error}</p>}
  </section>;
}

import React, { useEffect, useRef, useState } from 'react';
import { Button, Field } from '@fluentui/react-components';
import { createPlayer } from './speech.js';
import { NOTE, REMOVE_ASK, canSample, createSampler, errorKey, liveLine, localAction, localLine, oneAtATime, voiceFor, voiceGroups } from './speech-settings.js';

// The Kokoro part of the Read aloud card: the download status and its button, the voice picker, Play a sample, the note.
// The status object each call answers goes up through onStatus, which replaces the tool's entry.
// Props: { api, tool, voice, online, onVoice, onStatus, notify }
export function ReadAloudLocal({ api, tool, voice, online, onVoice, onStatus, notify }) {
  const local = tool.local || {};
  const [busy, setBusy] = useState('');  // 'download' | 'remove' | 'sample' while a call is out
  const [error, setError] = useState('');
  const player = useRef(null);
  if (!player.current) player.current = createPlayer({ Audio: window.Audio, URL: window.URL });
  const sampler = useRef(null);
  if (!sampler.current) sampler.current = createSampler({ fetchSample: v => api.speechSample(v), player: player.current });
  const downloadOnce = useRef(null);
  if (!downloadOnce.current) downloadOnce.current = oneAtATime(f => f());  // a fast double click sends one request
  useEffect(() => () => sampler.current.stop(), []);
  const key = errorKey(local, voice);
  useEffect(() => { setError(''); }, [key]);  // a new state or voice makes the old error line stale

  async function run(label, call, done) {
    setBusy(label); setError('');
    try {
      const st = await call();
      if (st) onStatus(st);
      if (done) notify(done);
    } catch (e) { setError(e && e.message ? e.message : 'That did not work. Try again.'); }
    finally { setBusy(''); }
  }
  async function sample() {
    setBusy('sample'); setError('');
    try {
      await sampler.current.play(voiceFor(local, voice), () => setError('The sample could not be played.'));
    } catch (e) { setError(e && e.message ? e.message : 'The sample could not be made.'); }
    finally { setBusy(''); }
  }
  function remove() {
    if (!window.confirm(REMOVE_ASK)) return;
    run('remove', () => api.speechRemoveModel(), 'Downloaded voice removed');
  }
  const download = () => downloadOnce.current(() => run('download', () => api.speechDownload()));
  const action = localAction(local);
  const lineError = local.state === 'failed';
  return <>
    <div className="od-row">
      <p className={lineError ? 'form-error' : 'field-hint'}>{localLine(local)}</p>
      <span className="sr-only" role="status">{liveLine(local)}</span>
      <div className="od-fill"/>
      {(action === 'download' || action === 'retry') && <Button type="button" appearance="primary" disabled={!online || !!busy} onClick={download}>{action === 'retry' ? 'Try again' : 'Download'}</Button>}
      {action === 'remove' && <Button type="button" appearance="subtle" disabled={!online || !!busy} onClick={remove}>Remove</Button>}
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}
    <Field label="Voice">
      {control => <div className="od-row">
        <select {...control} value={voiceFor(local, voice)} disabled={!online} onChange={e => onVoice(e.target.value)}>
          {voiceGroups(local.voices).map(g => <optgroup key={g.label} label={g.label}>{g.voices.map(v => <option key={v.name} value={v.name}>{v.label}</option>)}</optgroup>)}
        </select>
        <Button type="button" appearance="secondary" disabled={!online || !canSample(local, busy === 'sample')} onClick={sample}>Play a sample</Button>
      </div>}
    </Field>
    <p className="field-hint">{NOTE}</p>
  </>;
}

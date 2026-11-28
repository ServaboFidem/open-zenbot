import React, { useEffect, useRef, useState } from 'react';
import { Button, Field, Input, Radio, RadioGroup, Spinner } from '@fluentui/react-components';
import { anchorId } from './settings-index';
import { REGISTER_LINE, REGISTER_URL, TELNYX_LINE, TEXTS_POINTER, createReads, errorLine } from './telnyx.js';
import './mail.css';

// Settings > Connected apps > Telnyx: the account. The API key (saved by the harness, never sent back; only its last
// four characters are shown) and the number ZenBot sends from. Where texts go and when, the test text and how sending
// is going are on Settings > Notifications > Text reminders (TextReminders.jsx).
// Props: { api, online, notify, openSettings(panel, anchor) }
export function TelnyxSettings({ api, online, notify, openSettings }) {
  const telnyx = api.telnyx;
  const [status, setStatus] = useState(null);
  const [numbers, setNumbers] = useState(null);
  const [key, setKey] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState({ at: '', text: '' });
  const [picked, setPicked] = useState('');  // the radio marked, not yet used; '' follows the number in use
  const reads = useRef(null);
  reads.current ??= createReads();
  const current = numbers?.find(n => n.phone === status?.number)?.id || '';
  const shown = picked || current;

  // every read keeps its answer only while no key or number change has started since (createReads)
  async function loadNumbers(token = reads.current.start()) {
    try {
      const listed = (await telnyx.numbers()).numbers;
      if (reads.current.current(token)) setNumbers(listed);
    } catch (e) {
      if (reads.current.current(token)) { setNumbers([]); setError({ at: 'number', text: errorLine(e) }); }
    }
  }
  async function load() {
    const token = reads.current.start();
    try {
      const st = await telnyx.status();
      if (!reads.current.current(token)) return;
      setStatus(st);
      if (st.key_set) await loadNumbers(token); else setNumbers(null);
    } catch (e) { if (reads.current.current(token)) setError({ at: 'load', text: errorLine(e) }); }
  }
  useEffect(() => { if (online) load(); }, [online]);  // eslint-disable-line react-hooks/exhaustive-deps

  // a key or number change drops every read started before it
  async function run(at, label, call, done) {
    setBusy(label); setError({ at: '', text: '' });
    const token = reads.current.change();
    try {
      const st = await call();
      if (!reads.current.current(token)) return true;
      setStatus(st);
      if (st.key_set) await loadNumbers(token); else setNumbers(null);
      if (done) notify(done);
      return true;
    } catch (e) { setError({ at, text: errorLine(e) }); return false; }
    finally { setBusy(''); }
  }
  async function saveKey(e) {
    e.preventDefault();
    if (await run('key', 'save', () => telnyx.saveKey(key.trim()), 'Telnyx key saved')) setKey('');
  }
  const forget = () => run('key', 'forget', () => telnyx.forgetKey(), 'Telnyx key forgotten');
  async function choose(e) {
    e.preventDefault();
    if (await run('number', 'choose', () => telnyx.chooseNumber(shown), 'Sending number chosen')) setPicked('');
  }

  const here = at => error.at === at && error.text && <p className="form-error" role="alert">{error.text}</p>;
  const off = !online;

  return <section className="resource-row editor-form mail-settings" id={anchorId('apps', 'Telnyx')}>
    <h2>Telnyx</h2>
    <p className="field-hint">{TELNYX_LINE}</p>
    <p className="field-hint">{REGISTER_LINE} <a href={REGISTER_URL} target="_blank" rel="noreferrer">How to register</a></p>
    <p className="field-hint">{TEXTS_POINTER} <button type="button" className="text-link"
      onClick={() => openSettings('notifications', anchorId('notifications', 'Text reminders'))}>Open</button></p>
    {off && <p className="field-hint">Start the harness to connect Telnyx.</p>}
    {online && !status && !error.text && <Spinner size="tiny" label="Loading Telnyx…"/>}
    {here('load')}

    <form className="mail-key" onSubmit={saveKey}>
      <Field label="API key" hint="From the Telnyx portal. Saved in vault-settings, readable by you only. Never sent back to this app.">
        <Input type="password" autoComplete="off" spellCheck={false} value={key} disabled={off || busy === 'save'}
          placeholder={status?.key_set ? 'Saved. Paste a new one to replace it.' : undefined}
          onChange={(e, d) => setKey(d.value)}/>
      </Field>
      <div className="mail-actions">
        <Button appearance="primary" type="submit" disabled={off || busy !== '' || !key.trim()}>{busy === 'save' ? 'Checking…' : 'Save'}</Button>
        {status?.key_set && <>
          <span>Saved key ending …{status.key_hint}</span>
          <Button appearance="outline" disabled={off || busy !== ''} onClick={forget}>{busy === 'forget' ? 'Forgetting…' : 'Forget'}</Button>
        </>}
      </div>
      {here('key')}
    </form>

    {status?.key_set && <div className="mail-inbox">
      <h3 id="telnyx-number-heading">The number ZenBot sends from</h3>
      {numbers === null ? <Spinner size="tiny" label="Reading your numbers…"/>
        : numbers.length === 0 ? <p className="field-hint">This account has no numbers on a messaging profile yet. Add one in the Telnyx portal.</p>
        : <form className="mail-choose" onSubmit={choose}>
            {/* arrow keys only move the mark; the group stays enabled while a change is saved, so focus is kept */}
            <RadioGroup aria-labelledby="telnyx-number-heading" value={shown} disabled={off}
              onChange={(e, d) => setPicked(d.value === current ? '' : d.value)}>
              {numbers.map(n => <Radio key={n.id} value={n.id} label={`${n.phone}${n.id === current ? ' (in use)' : ''}`}/>)}
            </RadioGroup>
            <Button appearance="primary" type="submit" disabled={off || busy !== '' || !shown || shown === current}>
              {busy === 'choose' ? 'Saving…' : 'Use this number'}</Button>
          </form>}
      {here('number')}
    </div>}
  </section>;
}

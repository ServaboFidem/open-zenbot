import React, { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Field, Input, Radio, RadioGroup, Spinner } from '@fluentui/react-components';
import { anchorId } from './settings-index';
import { LEADS, REGISTER_LINE, REGISTER_URL, TELNYX_LINE, canTest, createReads, errorLine, leadLabel, statusLine } from './telnyx.js';
import './mail.css';

// Settings > Connected apps > Telnyx: the API key (saved by the harness, never sent back; only its last four
// characters are shown), the number ZenBot sends from, and, in the desktop app only, where texts go and when. The
// phone number is saved by the main process (window.openzenbot.telnyxTexts), never through the harness, whose API
// has no sign-in: a route that saved it would let a bot send ZenBot's texts anywhere.
// Props: { api, online, notify }
function TextsForm({ bridge, onSaved, notify }) {
  const [form, setForm] = useState(null);
  const [error, setError] = useState(''), [saving, setSaving] = useState(false);
  useEffect(() => {
    let active = true;
    bridge.get().then(value => { if (active) setForm(value); }).catch(e => {
      if (active) setError(e?.message || 'The text settings could not be read.');
    });
    return () => { active = false; };
  }, [bridge]);
  async function save(e) {
    e.preventDefault();
    setSaving(true); setError('');
    try {
      const saved = await bridge.save(form);
      setForm(saved);
      notify(saved.before || saved.rundown ? 'Text settings saved' : 'Texts turned off');
      onSaved();
    } catch (err) { setError(err?.message || 'The text settings could not be saved.'); }
    finally { setSaving(false); }
  }
  const off = !form || saving;
  const set = patch => setForm(f => ({ ...f, ...patch }));
  return <form className="editor-form telnyx-texts" onSubmit={save}>
    <Field label="Your phone number" hint="With its country code, such as +15551234567. Saved on this computer only.">
      <Input type="tel" autoComplete="tel" value={form?.phone || ''} disabled={off} onChange={(_, d) => set({ phone: d.value })}/>
    </Field>
    <div className="mail-actions">
      <Checkbox checked={!!form?.before} disabled={off} label="Text me before each event"
        onChange={(_, d) => set({ before: !!d.checked })}/>
      <Field label="How long before">
        <select aria-label="How long before" value={form?.lead_minutes ?? 30} disabled={off}
          onChange={e => set({ lead_minutes: Number(e.target.value) })}>
          {LEADS.map(m => <option key={m} value={m}>{leadLabel(m)}</option>)}
        </select>
      </Field>
    </div>
    <div className="mail-actions">
      <Checkbox checked={!!form?.rundown} disabled={off} label="Text me tomorrow's events each evening"
        onChange={(_, d) => set({ rundown: !!d.checked })}/>
      <Field label="At">
        <Input type="time" required value={form?.evening || '20:00'} disabled={off} onChange={(_, d) => set({ evening: d.value })}/>
      </Field>
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}
    <Button type="submit" appearance="outline" disabled={off}>{saving ? 'Saving…' : 'Save text settings'}</Button>
  </form>;
}

export function TelnyxSettings({ api, online, notify }) {
  const telnyx = api.telnyx;
  const bridge = window.openzenbot?.telnyxTexts;
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
  async function refresh() {
    const token = reads.current.start();
    try {
      const st = await telnyx.status();
      if (reads.current.current(token)) setStatus(st);
    } catch (e) { if (reads.current.current(token)) setError({ at: 'load', text: errorLine(e) }); }
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

  // changes: a key or number change, which drops every read started before it; a test text changes neither
  async function run(at, label, call, done, changes = true) {
    setBusy(label); setError({ at: '', text: '' });
    const token = changes ? reads.current.change() : reads.current.start();
    try {
      const st = await call();
      if (!reads.current.current(token)) return true;
      setStatus(st);
      if (changes) { if (st.key_set) await loadNumbers(token); else setNumbers(null); }
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
  const sendTest = () => run('test', 'test', () => telnyx.test(), 'Test text sent', false);

  const here = at => error.at === at && error.text && <p className="form-error" role="alert">{error.text}</p>;
  const off = !online;
  const line = statusLine(status);

  return <section className="resource-row editor-form mail-settings" id={anchorId('apps', 'Telnyx')}>
    <h2>Telnyx</h2>
    <p className="field-hint">{TELNYX_LINE}</p>
    <p className="field-hint">{REGISTER_LINE} <a href={REGISTER_URL} target="_blank" rel="noreferrer">How to register</a></p>
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

    <h3>Your texts</h3>
    {bridge ? <TextsForm bridge={bridge} onSaved={refresh} notify={notify}/>
      : <p className="field-hint">Set your phone number in the desktop app.</p>}
    <div className="mail-actions">
      <Button appearance="outline" disabled={off || busy !== '' || !canTest(status)} onClick={sendTest}>
        {busy === 'test' ? 'Sending…' : 'Send a test text'}</Button>
    </div>
    {here('test')}
    {line && <p className="field-hint" role="status">{line}</p>}
  </section>;
}

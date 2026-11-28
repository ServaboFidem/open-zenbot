import React, { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Field, Input } from '@fluentui/react-components';
import { anchorId } from './settings-index';
import { ACCOUNT_POINTER, LEADS, canTest, errorLine, leadLabel, needsAccount, statusLine } from './telnyx.js';
import './mail.css';

// Settings > Notifications > Text reminders: in the desktop app only, where texts go and when, then a test text and how
// sending is going. The Telnyx account (its key and the number ZenBot sends from) is on Connected apps > Telnyx. The
// phone number is saved by the main process (window.openzenbot.telnyxTexts), never through the harness, whose API
// has no sign-in: a route that saved it would let a bot send ZenBot's texts anywhere.
// Props: { api, online, notify, openSettings(panel, anchor) }
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

export function TextReminders({ api, online, notify, openSettings }) {
  const telnyx = api.telnyx;
  const bridge = window.openzenbot?.telnyxTexts;
  const [status, setStatus] = useState(null);
  const [testing, setTesting] = useState(false);
  const [error, setError] = useState('');
  const seq = useRef(0);  // only the newest status read or test answer is kept

  async function refresh() {
    const mine = ++seq.current;
    try {
      const st = await telnyx.status();
      if (mine === seq.current) { setStatus(st); setError(''); }
    } catch (e) { if (mine === seq.current) setError(errorLine(e)); }
  }
  useEffect(() => { if (online) refresh(); }, [online]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function sendTest() {
    const mine = ++seq.current;
    setTesting(true); setError('');
    try {
      const st = await telnyx.test();
      if (mine === seq.current) setStatus(st);
      notify('Test text sent');
    } catch (e) { setError(errorLine(e)); }
    finally { setTesting(false); }
  }

  const line = statusLine(status);
  return <section className="resource-row editor-form mail-settings" id={anchorId('notifications', 'Text reminders')}>
    <h3>Text reminders</h3>
    {!online && <p className="field-hint">Start the harness to see how texts are going.</p>}
    {needsAccount(status) && <p className="field-hint">{ACCOUNT_POINTER} <button type="button" className="text-link"
      onClick={() => openSettings('apps', anchorId('apps', 'Telnyx'))}>Open Connected apps</button></p>}
    {bridge ? <TextsForm bridge={bridge} onSaved={refresh} notify={notify}/>
      : <p className="field-hint">Set your phone number in the desktop app.</p>}
    <div className="mail-actions">
      <Button appearance="outline" disabled={!online || testing || !canTest(status)} onClick={sendTest}>
        {testing ? 'Sending…' : 'Send a test text'}</Button>
    </div>
    {error && <p className="form-error" role="alert">{error}</p>}
    {line && <p className="field-hint" role="status">{line}</p>}
  </section>;
}

import React, { useEffect, useState } from 'react';
import { Button, Field, Input, Radio, RadioGroup, Spinner } from '@fluentui/react-components';
import { anchorId } from './settings-index';
import { MAIL_LINE, errorLine, usernameError } from './mail.js';
import './mail.css';

// Settings > Connected apps > AgentMail: the API key (saved by the harness, never sent back; only its last four
// characters are shown), then ZenBot's inbox: one of the account's inboxes, or a new one made by name.
// Props: { api, online, notify, onChange } — onChange runs after every save, forget, create or choice, so the app
// can read the status again (the AgentMail navigation entry follows it).
export function MailSettings({ api, online, notify, onChange }) {
  const mail = api.mail;
  const [status, setStatus] = useState(null);
  const [inboxes, setInboxes] = useState(null);
  const [key, setKey] = useState('');
  const [name, setName] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState({ at: '', text: '' });

  async function loadInboxes() {
    try { setInboxes((await mail.inboxes()).inboxes); } catch (e) { setInboxes([]); setError({ at: 'inbox', text: errorLine(e) }); }
  }
  async function load() {
    try {
      const st = await mail.status();
      setStatus(st);
      if (st.key_set) await loadInboxes(); else setInboxes(null);
    } catch (e) { setError({ at: 'load', text: errorLine(e) }); }
  }
  useEffect(() => { if (online) load(); }, [online]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function run(at, label, call, done) {
    setBusy(label); setError({ at: '', text: '' });
    try {
      const st = await call();
      setStatus(st);
      if (st.key_set) await loadInboxes(); else setInboxes(null);
      if (done) notify(done);
      onChange?.();
      return true;
    } catch (e) { setError({ at, text: errorLine(e) }); return false; }
    finally { setBusy(''); }
  }
  async function saveKey(e) {
    e.preventDefault();
    if (await run('key', 'save', () => mail.saveKey(key.trim()), 'AgentMail key saved')) setKey('');
  }
  const forget = () => run('key', 'forget', () => mail.forgetKey(), 'AgentMail key forgotten');
  const choose = id => run('inbox', 'choose', () => mail.chooseInbox(id), 'ZenBot\'s inbox chosen');
  async function create(e) {
    e.preventDefault();
    if (await run('create', 'create', () => mail.createInbox(name.trim()), 'Inbox created')) setName('');
  }

  const here = at => error.at === at && error.text && <p className="form-error" role="alert">{error.text}</p>;
  const nameError = name.trim() ? usernameError(name) : '';
  const off = !online;

  return <section className="resource-row editor-form mail-settings" id={anchorId('apps', 'AgentMail')}>
    <h2>AgentMail</h2>
    <p className="field-hint">{MAIL_LINE}</p>
    {off && <p className="field-hint">Start the harness to connect AgentMail.</p>}
    {online && !status && !error.text && <Spinner size="tiny" label="Loading AgentMail…"/>}
    {here('load')}

    <form className="mail-key" onSubmit={saveKey}>
      <Field label="API key" hint="From your AgentMail console. Saved in vault-settings, readable by you only. Never sent back to this app.">
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
      <h3>ZenBot's inbox</h3>
      {inboxes === null ? <Spinner size="tiny" label="Reading your inboxes…"/>
        : inboxes.length === 0 ? <p className="field-hint">This account has no inboxes yet. Create one below.</p>
        : <RadioGroup value={status.inbox?.inbox_id || ''} disabled={off || busy !== ''} onChange={(e, d) => choose(d.value)}>
            {inboxes.map(i => <Radio key={i.inbox_id} value={i.inbox_id} disabled={i.deactivated}
              label={`${i.email}${i.deactivated ? ' (deactivated)' : ''}`}/>)}
          </RadioGroup>}
      {here('inbox')}
      <form className="mail-create" onSubmit={create}>
        <Field label="Create a new inbox:" validationMessage={nameError || undefined}>
          <div className="mail-create-row">
            <Input value={name} disabled={off || busy !== ''} aria-label="New inbox name" placeholder="name"
              onChange={(e, d) => setName(d.value)}/>
            <span>@agentmail.to</span>
            <Button appearance="outline" type="submit" disabled={off || busy !== '' || !name.trim() || !!nameError}>{busy === 'create' ? 'Creating…' : 'Create'}</Button>
          </div>
        </Field>
      </form>
      {here('create')}
    </div>}
  </section>;
}

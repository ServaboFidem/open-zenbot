import React, { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Dropdown, Field, Input, Option, Spinner, Switch } from '@fluentui/react-components';
import { Copy24Regular } from '@fluentui/react-icons';
import { anchorId } from './settings-index';
import { APP_STEPS, POLL_LIMIT_MS, POLL_MS, bringInList, defaultDestination, destinations, pushFailure, pushSentence, watchStatus, shownSections, signInChanged } from './calendar-settings';
import { DiscoverySettings } from './DiscoverySettings';
import './calendar-settings.css';

// Settings > Calendar: the user's own Google app (client ID and secret, saved in the harness, never sent back), the
// Google sign-in, which calendars to bring in, and sending events made here to Google. The sign-in finishes in the
// browser, so after opening Google's address the page asks the harness for its status every 2 seconds.
// Props: { api, online, notify }
const SAVED_HINT = 'Saved. Paste a new one to replace it.';
const message = e => e?.message || 'Something went wrong.';

export function CalendarSettings({ api, online, notify }) {
  const cal = api.calendar;
  const [status, setStatus] = useState(null);
  const [calendars, setCalendars] = useState([]);
  const [listed, setListed] = useState(false);   // the calendar list loaded
  const [failed, setFailed] = useState(false);
  const [form, setForm] = useState({ id: '', secret: '' });
  const [busy, setBusy] = useState('');
  const [error, setError] = useState({ at: '', text: '' });
  const [waiting, setWaiting] = useState(false);
  const [destination, setDestination] = useState('');
  const [offNote, setOffNote] = useState(false);
  const [ask, setAsk] = useState(null);   // a dialog: { title, body, label, run }
  const alive = useRef(true);
  const poll = useRef(null);
  const statusRef = useRef(null);
  statusRef.current = status;

  const stopPolling = () => { if (poll.current) poll.current(); poll.current = null; };
  useEffect(() => { alive.current = true; return () => { alive.current = false; stopPolling(); }; }, []);

  async function take(next) {
    setStatus(next);
    if (next.google.state === 'connected') {
      try { const list = await cal.calendars(); if (alive.current) { setCalendars(list); setListed(true); } } catch (e) { if (alive.current) setError({ at: 'calendars', text: message(e) }); }
    } else { setCalendars([]); setListed(false); }
    return next;
  }
  const load = async () => {
    setFailed(false);
    try { const next = await cal.status(); if (alive.current) await take(next); return next; }
    catch { if (alive.current) setFailed(true); return null; }
  };
  useEffect(() => { if (online) load(); }, [online]);
  useEffect(() => { setDestination(d => destinations(calendars).some(c => c.id === d) ? d : defaultDestination(calendars, status?.push?.destination)); }, [calendars, status?.push?.destination]);

  // Look again every 2 seconds, for at most 10 minutes, until the state or the write permission changes.
  // A new watch, Cancel and unmount all cancel the one before, for good (watchStatus).
  function watch(before, then) {
    stopPolling(); setWaiting(true);
    poll.current = watchStatus({
      fetchStatus: () => cal.status(), changed: next => signInChanged(before, next), now: Date.now,
      setTimer: (fn, ms) => setTimeout(fn, ms), clearTimer: clearTimeout,
      onChange: async next => { if (alive.current) { setWaiting(false); await take(next); } },
      onDone: next => { if (alive.current && then) then(next); },
      onTimeout: () => { if (alive.current) { setWaiting(false); setError({ at: 'google', text: 'The sign-in did not finish. Try again.' }); } },
    });
  }
  async function signIn(write, then) {
    setError({ at: '', text: '' }); setBusy('connect');
    try {
      const { url } = await cal.connect(write);
      window.open(url, '_blank', 'noopener');
      watch(statusRef.current, then);
    } catch (e) { setError({ at: 'google', text: message(e) }); }
    finally { setBusy(''); }
  }

  async function run(which, at, fn, done) {
    setBusy(which); setError({ at: '', text: '' });
    try { const r = await fn(); if (done) notify(done); return r ?? true; }
    catch (e) { setError({ at, text: message(e) }); return null; }
    finally { if (alive.current) setBusy(''); }
  }
  async function saveCredentials(e) {
    e.preventDefault();
    const next = await run('save', 'app', () => cal.saveCredentials(form.id.trim(), form.secret.trim()), 'Google app saved');
    if (next && next !== true) { setForm({ id: '', secret: '' }); await take(next); }
  }
  async function removeCredentials() {
    const next = await run('remove', 'app', () => cal.clearCredentials(), 'Google app removed');
    if (next && next !== true) await take(next);
  }
  async function disconnect() {
    const next = await run('disconnect', 'google', () => cal.disconnect(), 'Disconnected from Google');
    if (next && next !== true) await take(next);
  }
  async function tick(c, selected) {
    const r = await run(`cal:${c.id}`, 'calendars', () => cal.setCalendar(c.id, { selected }));
    if (r && r !== true) setCalendars(list => list.map(x => x.id === r.id ? r : x));
    if (r) load();
  }
  const untick = c => setAsk({
    title: `Stop bringing in ${c.name}?`,
    body: `Its events will be removed from this computer, including changes to them that have not been sent to Google yet. Tick the calendar again to bring them back.`,
    label: 'Stop bringing in', run: () => tick(c, false),
  });

  // Sending events to Google. Without the write permission the sign-in comes first, then the preview, then the confirmation.
  const destName = () => calendars.find(c => c.id === destination)?.name || 'that calendar';
  const askWrite = () => setAsk({
    title: 'Allow sending to Google', body: 'Google will ask to let OpenZenBot add and change events.', label: 'Allow sending to Google',
    run: () => signIn(true, next => { if (next.google.can_write) startPush(next); }),
  });
  async function startPush(fresh) {
    if (!(fresh || statusRef.current)?.google.can_write) return askWrite();
    setError({ at: '', text: '' }); setOffNote(false);
    let counts;
    try { counts = await cal.pushPreview(destination); }
    catch (e) { return setError({ at: 'push', text: message(e) }); }
    setAsk({
      title: `Send events to ${destName()}?`, body: pushSentence(counts, destName()), label: 'Send events to Google',
      run: async () => {
        setBusy('push'); setError({ at: '', text: '' });
        try { await take(await cal.setPush(true, destination)); notify('Sending events to Google'); }
        catch (e) { const f = pushFailure(e); if (f.kind === 'allow') askWrite(); else setError({ at: 'push', text: f.text }); }
        finally { if (alive.current) setBusy(''); }
      },
    });
  }
  async function stopPush() {
    const next = await run('push', 'push', () => cal.setPush(false), 'Stopped sending events to Google');
    if (next && next !== true) { setOffNote(true); await take(next); }
  }

  if (!online) return <div className="resource-row"><p>The harness is offline.</p></div>;
  if (!status && failed) return <div className="resource-row od-row"><p className="od-fill">Could not read the calendar settings.</p><Button appearance="outline" onClick={load}>Retry</Button></div>;
  if (!status) return <div className="resource-row"><Spinner size="tiny" label="Loading calendar settings…"/></div>;

  const g = status.google;
  const show = shownSections(status);
  const linked = g.state === 'connected' || g.state === 'needs_reconnect';
  const here = at => error.at === at && <p className="form-error cal-wrap" role="alert">{error.text}</p>;
  const options = destinations(calendars);
  const pushOn = !!status.push.on;
  const copyUri = async () => { try { await navigator.clipboard.writeText(g.redirect_uri); notify('Copied'); } catch { notify('Could not copy. Select the address and copy it.'); } };

  return <div className="cal-set">
    <form className="resource-row editor-form" onSubmit={saveCredentials}>
      <h3 id={anchorId('calendar', 'Your Google app')}>Your Google app</h3>
      <p className="field-hint">OpenZenBot has no Google app of its own. You make one, so your calendar is shared with no one else.</p>
      <ol className="cal-steps">{APP_STEPS.map(s => <li key={s} className="cal-wrap">{s}</li>)}</ol>
      {g.redirect_uri && <div className="cal-uri"><span>Redirect address, if Google asks for one:</span><code>{g.redirect_uri}</code>
        <Button size="small" appearance="outline" icon={<Copy24Regular/>} onClick={copyUri}>Copy</Button></div>}
      <div className="cal-fields">
        <Field label="Client ID"><Input type="password" autoComplete="off" spellCheck={false} value={form.id} disabled={busy === 'save' || linked}
          placeholder={g.credentials ? SAVED_HINT : undefined} onChange={(e, d) => setForm(f => ({ ...f, id: d.value }))}/></Field>
        <Field label="Client secret"><Input type="password" autoComplete="off" spellCheck={false} value={form.secret} disabled={busy === 'save' || linked}
          placeholder={g.credentials ? SAVED_HINT : undefined} onChange={(e, d) => setForm(f => ({ ...f, secret: d.value }))}/></Field>
      </div>
      <p className="field-hint">Saved in vault-settings, readable by you only. Never sent back to this app.</p>
      {here('app')}
      <div className="cal-actions">
        <Button appearance="primary" type="submit" disabled={busy === 'save' || linked || !form.id.trim() || !form.secret.trim()}>{busy === 'save' ? 'Saving…' : 'Save'}</Button>
        {g.credentials && <>
          <span className="cal-wrap">{g.client_id_hint ? `Saved (ends …${g.client_id_hint})` : 'Saved'}</span>
          <Button appearance="outline" disabled={busy === 'remove' || linked} onClick={removeCredentials}>Remove</Button>
          {linked && <span className="field-hint">Disconnect first.</span>}
        </>}
      </div>
    </form>

    <div className="resource-row editor-form">
      <h3 id={anchorId('calendar', 'Google Calendar')}>Google Calendar</h3>
      {g.state === 'not_set_up' && <p className="field-hint">Save your Google app above first.</p>}
      {g.state === 'ready' && <>
        <p>OpenZenBot asks to see your calendars and their events. It does not ask to change anything yet.</p>
        {g.account && <p className="field-hint cal-wrap" title={g.account}>Last connected as {g.account}. If you connect a different account, events made here are sent to it afresh and the copies in the old account stay there.</p>}
      </>}
      {g.state === 'connected' && <p className="cal-wrap" title={g.account}>Connected{g.account ? ` as ${g.account}` : ''}</p>}
      {g.state === 'needs_reconnect' && <p>Google sign-in has expired.</p>}
      {waiting && <div className="cal-actions" role="status"><Spinner size="tiny" labelPosition="after" label="Finish signing in in your browser."/><Button size="small" appearance="subtle" onClick={() => { stopPolling(); setWaiting(false); }}>Cancel</Button></div>}
      {here('google')}
      <div className="cal-actions">
        {g.state === 'ready' && <Button appearance="primary" disabled={!!busy} onClick={() => signIn(false)}>Connect Google Calendar</Button>}
        {g.state === 'needs_reconnect' && <Button appearance="primary" disabled={!!busy} onClick={() => signIn(g.can_write)}>Reconnect</Button>}
        {linked && <Button appearance="outline" disabled={!!busy} onClick={() => setAsk({
          title: 'Disconnect Google?', body: 'Events from Google will be removed from this computer. Events you made here stay, and so do their copies on Google.', label: 'Disconnect', run: disconnect })}>Disconnect</Button>}
      </div>
    </div>

    {show.calendars && <div className="resource-row editor-form">
      <h3 id={anchorId('calendar', 'Calendars to bring in')}>Calendars to bring in</h3>
      <p className="field-hint">Choose which of your Google calendars show their events here.</p>
      {here('calendars')}
      <div className="cal-list">{bringInList(calendars).map(c => <div className="cal-item" key={c.id}>
        <Checkbox checked={c.selected} disabled={c.locked || busy === `cal:${c.id}`} title={c.name}
          label={<span className="cal-wrap"><span className="cal-swatch" style={{ background: c.color || 'var(--border)' }}/>{c.name}</span>}
          onChange={(e, d) => d.checked ? tick(c, true) : untick(c)}/>
        {!c.writable && <span className="field-hint">view only</span>}
        {c.locked && <span className="field-hint">Holds events sent from here.</span>}
      </div>)}</div>
    </div>}

    {show.push && <div className="resource-row editor-form">
      <h3 id={anchorId('calendar', 'Send events to Google')}>Send events to Google</h3>
      <Switch checked={pushOn} disabled={!!busy || waiting || (!pushOn && !options.length)} label="Send events made here to Google"
        onChange={(e, d) => d.checked ? startPush() : stopPush()}/>
      <Field label="Destination calendar" hint={options.length ? 'New events made here go to this calendar.' : listed ? 'None of your Google calendars lets OpenZenBot add events.' : undefined}>
        <Dropdown aria-label="Destination calendar" disabled={!!busy || waiting || pushOn || !options.length} value={options.find(c => c.id === destination)?.name || ''}
          selectedOptions={destination ? [destination] : []} onOptionSelect={(e, d) => setDestination(d.optionValue)}>
          {options.map(c => <Option key={c.id} value={c.id} text={c.name}>{c.name}</Option>)}
        </Dropdown>
      </Field>
      {!pushOn && offNote && <p className="field-hint">Changes you make here will wait until you turn this back on.</p>}
      {here('push')}
    </div>}

    <DiscoverySettings api={api} online={online} notify={notify}/>

    <Dialog open={!!ask} onOpenChange={(e, d) => { if (!d.open) setAsk(null); }}>
      <DialogSurface className="cal-dialog"><DialogBody>
        <DialogTitle>{ask?.title}</DialogTitle>
        <DialogContent><p className="cal-wrap">{ask?.body}</p></DialogContent>
        <DialogActions>
          <Button appearance="secondary" onClick={() => setAsk(null)}>Cancel</Button>
          <Button appearance="primary" onClick={() => { const a = ask; setAsk(null); a.run(); }}>{ask?.label}</Button>
        </DialogActions>
      </DialogBody></DialogSurface>
    </Dialog>
  </div>;
}

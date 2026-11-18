import React, { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Dropdown, Field, Input, Label, Option, Spinner, Switch, Textarea } from '@fluentui/react-components';
import { anchorId } from './settings-index';
import { KEY_HINT, MAX_INTERESTS, URL_HINT, bodyFrom, canSave, changed, cityName, cityOptions, draftFrom, findLocalLine, keyFieldEnter, nextRunLine, refusalField, toggleCategory, zoneLine } from './discovery-settings';

// Settings > Calendar > Event discovery: where the user lives, their city's Eventbrite page, which kinds of events,
// their interests, and the on/off switch. The harness checks the address and answers 422 with the sentence to show.
// The weekly run's day and time belong to the Routines page; this section only says when the next one is.
// Props: { api, online, notify }
const message = e => e?.message || 'Something went wrong.';

export function DiscoverySettings({ api, online, notify }) {
  const [status, setStatus] = useState(null);
  const [draft, setDraft] = useState(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState({ at: '', text: '' });
  // the key lives here only, is never pre-filled and is cleared after a save
  const [key, setKey] = useState('');
  const [keyBusy, setKeyBusy] = useState(false);
  const [keyError, setKeyError] = useState('');
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const load = async () => {
    setFailed(false);
    try { const next = await api.discover.status(); if (alive.current) { setStatus(next); setDraft(draftFrom(next)); } }
    catch { if (alive.current) setFailed(true); }
  };
  useEffect(() => { if (online) load(); }, [online]);

  const heading = <h3 id={anchorId('calendar', 'Event discovery')}>Event discovery</h3>;
  if (!online) return null;
  if (!status && failed) return <div className="resource-row od-row">{heading}<p className="od-fill">Could not read the event discovery settings.</p><Button appearance="outline" onClick={load}>Retry</Button></div>;
  if (!status || !draft) return <div className="resource-row editor-form">{heading}<Spinner size="tiny" label="Loading event discovery…"/></div>;

  const set = patch => { setDraft(d => ({ ...d, ...patch })); setError({ at: '', text: '' }); };
  const check = canSave(draft);
  const dirty = changed(draft, status);
  const next = nextRunLine(status.routine);

  const fl = status.findlocal || {};
  const cities = cityOptions(status);
  const flLine = findLocalLine(status);
  const zone = zoneLine(status);

  async function keyCall(run, done) {
    setKeyBusy(true); setKeyError('');
    try {
      const fresh = await run();
      if (alive.current) { setStatus(fresh); setKey(''); }
      notify(done);
    } catch (err) { if (alive.current) setKeyError(message(err)); }
    finally { if (alive.current) setKeyBusy(false); }
  }
  const saveKey = () => keyCall(() => api.discover.saveFindLocalKey(key.trim()), 'FindLocal key saved');
  const forgetKey = () => keyCall(() => api.discover.forgetFindLocalKey(), 'FindLocal key removed');

  async function save(e) {
    e.preventDefault();
    if (!check.ok) return;
    setBusy(true); setError({ at: '', text: '' });
    try {
      const fresh = await api.discover.saveSettings(bodyFrom(draft, Intl.DateTimeFormat().resolvedOptions().timeZone));
      if (alive.current) { setStatus(fresh); setDraft(draftFrom(fresh)); }
      notify('Event discovery saved');
    } catch (err) { if (alive.current) setError({ at: refusalField(message(err)), text: message(err) }); }
    finally { if (alive.current) setBusy(false); }
  }

  return <form className="resource-row editor-form" onSubmit={save}>
    {heading}
    <p className="field-hint">Finds events near you on Eventbrite, once a week, and offers the best ones on the Discover tab. Nothing is added to your calendar until you say so.</p>
    <Switch checked={draft.enabled} disabled={busy} label="Look for events near me" onChange={(e, d) => set({ enabled: d.checked })}/>
    {next && <p className="field-hint">{next} Change the day and time on the Routines page.</p>}
    <div className="cal-fields">
      <Field label="City"><Input value={draft.city} maxLength={100} disabled={busy} onChange={(e, d) => set({ city: d.value })}/></Field>
      <Field label="State" hint="United States only for now."><Input value={draft.state} maxLength={100} disabled={busy} onChange={(e, d) => set({ state: d.value })}/></Field>
    </div>
    <Field label="Your city’s Eventbrite page" hint={URL_HINT} validationState={error.at === 'url' ? 'error' : 'none'} validationMessage={error.at === 'url' ? error.text : undefined}>
      <Input value={draft.source_url} spellCheck={false} placeholder="https://www.eventbrite.com/d/oh--columbus/events/" disabled={busy} onChange={(e, d) => set({ source_url: d.value })}/>
    </Field>
    {/* not a Field: a Field gives every checkbox inside it one id, so any label would tick the first box */}
    <div role="group" aria-labelledby="discovery-kinds" className="cal-group">
      <Label id="discovery-kinds">Kinds of events</Label>
      <div className="cal-list">{status.categories.map(c => <div className="cal-item" key={c.tag}>
        <Checkbox label={c.name} checked={draft.categories.includes(c.tag)} disabled={busy}
          onChange={(e, d) => set({ categories: toggleCategory(draft.categories, c.tag, !!d.checked) })}/>
      </div>)}</div>
      <p className="field-hint">Tick none to see them all.</p>
    </div>
    <Field label="Your interests" hint={`In your own words, up to ${MAX_INTERESTS.toLocaleString('en-US')} characters. ${draft.interests.length.toLocaleString('en-US')} used.`}
      validationState={draft.interests.length > MAX_INTERESTS ? 'error' : 'none'}>
      <Textarea resize="vertical" rows={4} value={draft.interests} disabled={busy} onChange={(e, d) => set({ interests: d.value })}/>
    </Field>
    <h4>FindLocal (optional)</h4>
    <p className="field-hint">A second source of events, read once a week. It needs a free key.</p>
    <Field label="FindLocal key" hint={KEY_HINT} validationState={keyError ? 'error' : 'none'} validationMessage={keyError || undefined}>
      <Input type="password" autoComplete="new-password" spellCheck={false} value={key} disabled={keyBusy} onChange={(e, d) => { setKey(d.value); setKeyError(''); }} onKeyDown={e => keyFieldEnter(e, { key, busy: keyBusy }, saveKey)}/>
    </Field>
    <div className="cal-actions">
      <Button appearance="outline" disabled={keyBusy || !key.trim()} onClick={saveKey}>{keyBusy ? 'Saving…' : 'Save key'}</Button>
      {fl.key_set && <>
        <span className="cal-wrap">{fl.key_hint ? `Saved, ending in ${fl.key_hint}` : 'Saved'}</span>
        <Button appearance="outline" disabled={keyBusy} onClick={forgetKey}>Forget</Button>
      </>}
    </div>
    <Field label="FindLocal city" validationState={error.at === 'findlocal_city' ? 'error' : 'none'} validationMessage={error.at === 'findlocal_city' ? error.text : undefined}>
      <Dropdown aria-label="FindLocal city" disabled={busy} value={draft.findlocal_city ? cityName(draft.findlocal_city) : 'None'}
        selectedOptions={[draft.findlocal_city]} onOptionSelect={(e, d) => set({ findlocal_city: d.optionValue })}>
        <Option key="" value="" text="None">None</Option>
        {cities.map(c => <Option key={c.slug} value={c.slug} text={c.name}>{c.name}</Option>)}
      </Dropdown>
    </Field>
    {zone && <p className="field-hint">{zone}</p>}
    {flLine && <p className="field-hint cal-wrap">{flLine}</p>}
    {!check.ok && dirty && <p className="form-error cal-wrap" role="alert">{check.why}</p>}
    <div className="cal-actions">
      <Button appearance="primary" type="submit" disabled={busy || !dirty || !check.ok}>{busy ? 'Saving…' : 'Save'}</Button>
    </div>
  </form>;
}

import React, { useEffect, useRef, useState } from 'react';
import { Button, Checkbox, Field, Input, Spinner, Switch, Textarea } from '@fluentui/react-components';
import { anchorId } from './settings-index';
import { MAX_INTERESTS, URL_HINT, bodyFrom, canSave, changed, draftFrom, nextRunLine, toggleCategory } from './discovery-settings';

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

  async function save(e) {
    e.preventDefault();
    if (!check.ok) return;
    setBusy(true); setError({ at: '', text: '' });
    try {
      const fresh = await api.discover.saveSettings(bodyFrom(draft));
      if (alive.current) { setStatus(fresh); setDraft(draftFrom(fresh)); }
      notify('Event discovery saved');
    } catch (err) { if (alive.current) setError({ at: 'url', text: message(err) }); }
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
    <Field label="Kinds of events" hint="Tick none to see them all.">
      <div className="cal-list">{status.categories.map(c => <div className="cal-item" key={c.tag}>
        <Checkbox label={c.name} checked={draft.categories.includes(c.tag)} disabled={busy}
          onChange={(e, d) => set({ categories: toggleCategory(draft.categories, c.tag, !!d.checked) })}/>
      </div>)}</div>
    </Field>
    <Field label="Your interests" hint={`In your own words, up to ${MAX_INTERESTS.toLocaleString('en-US')} characters. ${draft.interests.length.toLocaleString('en-US')} used.`}
      validationState={draft.interests.length > MAX_INTERESTS ? 'error' : 'none'}>
      <Textarea resize="vertical" rows={4} value={draft.interests} disabled={busy} onChange={(e, d) => set({ interests: d.value })}/>
    </Field>
    {!check.ok && dirty && <p className="form-error cal-wrap" role="alert">{check.why}</p>}
    <div className="cal-actions">
      <Button appearance="primary" type="submit" disabled={busy || !dirty || !check.ok}>{busy ? 'Saving…' : 'Save'}</Button>
    </div>
  </form>;
}

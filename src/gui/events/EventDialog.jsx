import React, { useState } from 'react';
import { Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Field, Input, Switch, Textarea } from '@fluentui/react-components';
import { Dismiss24Regular } from '@fluentui/react-icons';
import { dayTitle } from './CalendarGrid.jsx';
import { addDays, eventDays, instantToParts, timeLabel, wallToInstant } from './dates.js';

const pad = n => String(n).padStart(2, '0');
const longDay = key => dayTitle(key, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });

// When an event happens, in words, in `zone`.
function whenText(event, zone) {
  const days = eventDays(event, zone);
  const first = days[0], last = days[days.length - 1];
  if (event.all_day) return days.length === 1 ? `${longDay(first)}, all day` : `${longDay(first)} to ${longDay(last)}, all day`;
  if (days.length === 1) return `${longDay(first)}, ${timeLabel(event, zone)}`;
  const s = instantToParts(event.start, zone), e = instantToParts(event.end, zone);
  return `${longDay(s.key)} ${s.time} to ${longDay(e.key)} ${e.time}`;
}

// The same times in the event's own zone, when that is not the computer's.
function ownZoneText(event, zone) {
  if (event.all_day || !event.tz || event.tz === zone) return '';
  try { return `In ${event.tz}: ${whenText(event, event.tz)}`; } catch { return `Time zone: ${event.tz}`; }
}

function blankForm(day) {
  const hour = Math.min(new Date().getHours() + 1, 23);   // the next whole hour, one hour long
  return { title: '', all_day: false, startDate: day, startTime: `${pad(hour)}:00`,
    endDate: hour === 23 ? addDays(day, 1) : day, endTime: `${pad((hour + 1) % 24)}:00`, location: '', description: '' };
}

function eventForm(event, zone) {
  const base = { title: event.title, all_day: !!event.all_day, location: event.location || '', description: event.description || '' };
  if (event.all_day) {
    const last = addDays(event.end, -1);
    return { ...base, startDate: event.start, endDate: last < event.start ? event.start : last, startTime: '09:00', endTime: '10:00' };
  }
  const s = instantToParts(event.start, zone), e = instantToParts(event.end, zone);
  return { ...base, startDate: s.key, startTime: s.time, endDate: e.key, endTime: e.time };
}

// One event: its details, or the form that makes or changes it.
// props: cal (api.calendar), zone, event (null for a new one), day (a new event's day), calendarName (the
// event's calendar, plain), calendarLabel (the same with "Google: " where it applies), onClose(),
// onChanged(dayKey | null) after a save or delete
export default function EventDialog({ cal, zone, event, day, calendarName, calendarLabel, onClose, onChanged }) {
  const [mode, setMode] = useState(event ? 'details' : 'form');
  const [form, setForm] = useState(() => (event ? eventForm(event, zone) : blankForm(day)));
  const [error, setError] = useState('');
  const [titleError, setTitleError] = useState('');
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);

  const set = (field, value) => setForm(f => {
    const next = { ...f, [field]: value };
    if (field === 'startDate' && value && next.endDate && next.endDate < value) next.endDate = value;
    return next;
  });
  const close = () => { if (!busy) onClose(); };

  async function save() {
    const title = form.title.trim();
    if (!title) { setTitleError('An event needs a title.'); return; }
    setTitleError('');
    if (!form.startDate || !form.endDate || (!form.all_day && (!form.startTime || !form.endTime))) { setError('Choose when the event starts and ends.'); return; }
    const body = { title, all_day: form.all_day, location: form.location, description: form.description, ...(form.all_day
      ? { start: form.startDate, end: addDays(form.endDate, 1), tz: '' }
      : { start: wallToInstant(form.startDate, form.startTime, zone), end: wallToInstant(form.endDate, form.endTime, zone), tz: zone }) };
    setBusy(true); setError('');
    try {
      if (event) await cal.update(event.id, body); else await cal.create(body);
      setBusy(false);
      onChanged(form.startDate);
    } catch (e) { setBusy(false); setError(e.message || 'The event could not be saved.'); }
  }

  async function remove() {
    setBusy(true); setError('');
    try { await cal.remove(event.id); setBusy(false); onChanged(null); }
    catch (e) { setBusy(false); setConfirming(false); setError(e.message || 'The event could not be deleted.'); }
  }

  const closeButton = <Button appearance="subtle" aria-label="Close" icon={<Dismiss24Regular/>} disabled={busy} onClick={close}/>;
  let title, content, actions;
  if (mode === 'details') {
    const zoneLine = ownZoneText(event, zone);
    const link = event.origin === 'google' && /^https:\/\//i.test(event.link || '') ? event.link : '';
    title = event.title;
    content = <>
      <dl className="events-facts">
        <div><dt>When</dt><dd>{whenText(event, zone)}</dd>{zoneLine && <dd className="events-muted">{zoneLine}</dd>}</div>
        {event.location && <div><dt>Where</dt><dd>{event.location}</dd></div>}
        <div><dt>Calendar</dt><dd>{calendarLabel}</dd></div>
      </dl>
      {event.description && <p className="events-description">{event.description}</p>}
      {event.origin === 'google' && !event.editable && <p className="events-muted">From Google: {calendarName}. Read-only here.</p>}
      {event.origin === 'local' && event.push_error && <p className="events-muted">Not yet on Google.</p>}
      {link && <p><a href={link} target="_blank" rel="noreferrer">Open in Google Calendar</a></p>}
      {error && <p className="events-error" role="alert">{error}</p>}
    </>;
    actions = confirming
      ? <><span className="events-confirm">Delete this event?</span><Button appearance="primary" disabled={busy} onClick={remove}>Delete</Button><Button disabled={busy} onClick={() => setConfirming(false)}>Cancel</Button></>
      : event.editable
        ? <><Button onClick={() => setConfirming(true)}>Delete</Button><Button appearance="primary" onClick={() => { setError(''); setMode('form'); }}>Edit</Button></>
        : <Button onClick={close}>Close</Button>;
  } else {
    title = event ? 'Edit event' : 'New event';
    content = <form className="events-form" id="events-form" noValidate onSubmit={e => { e.preventDefault(); save(); }}>
      <Field label="Title" required validationMessage={titleError || undefined}>
        <Input value={form.title} maxLength={300} onChange={(_, d) => set('title', d.value)}/>
      </Field>
      <Switch label="All day" checked={form.all_day} onChange={(_, d) => set('all_day', d.checked)}/>
      <div className="events-form-when">
        <Field label="Start date"><Input type="date" value={form.startDate} onChange={(_, d) => set('startDate', d.value)}/></Field>
        {!form.all_day && <Field label="Start time"><Input type="time" value={form.startTime} onChange={(_, d) => set('startTime', d.value)}/></Field>}
        <Field label="End date"><Input type="date" value={form.endDate} onChange={(_, d) => set('endDate', d.value)}/></Field>
        {!form.all_day && <Field label="End time"><Input type="time" value={form.endTime} onChange={(_, d) => set('endTime', d.value)}/></Field>}
      </div>
      <Field label="Location"><Input value={form.location} maxLength={500} onChange={(_, d) => set('location', d.value)}/></Field>
      <Field label="Description"><Textarea value={form.description} maxLength={8000} resize="vertical" onChange={(_, d) => set('description', d.value)}/></Field>
      {error && <p className="events-error" role="alert">{error}</p>}
    </form>;
    actions = <>
      <Button disabled={busy} onClick={() => { if (event) { setError(''); setTitleError(''); setForm(eventForm(event, zone)); setMode('details'); } else close(); }}>Cancel</Button>
      <Button appearance="primary" type="submit" form="events-form" disabled={busy}>{busy ? 'Saving…' : 'Save'}</Button>
    </>;
  }

  return <Dialog open onOpenChange={(_, d) => { if (!d.open) close(); }}>
    <DialogSurface className="events-dialog">
      <DialogBody>
        <DialogTitle action={closeButton}>{title}</DialogTitle>
        <DialogContent>{content}</DialogContent>
        <DialogActions>{actions}</DialogActions>
      </DialogBody>
    </DialogSurface>
  </Dialog>;
}

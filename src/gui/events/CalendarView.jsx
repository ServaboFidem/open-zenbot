import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Checkbox, Spinner } from '@fluentui/react-components';
import { Add24Regular, ArrowSync24Regular, ChevronLeft24Regular, ChevronRight24Regular } from '@fluentui/react-icons';
import CalendarGrid from './CalendarGrid.jsx';
import EventDialog from './EventDialog.jsx';
import { dayTitle, eventsByDay, fetchRange, monthGrid, timeLabel, todayKey } from './dates.js';
import { lostEditStep, statusLine } from './page-state.js';

const HEX = /^#[0-9a-f]{6}$/i;
const monthOf = key => { const [year, month] = key.split('-').map(Number); return { year, month }; };
const firstOf = ym => `${String(ym.year).padStart(4, '0')}-${String(ym.month).padStart(2, '0')}-01`;
const sameMonth = (key, ym) => key.startsWith(firstOf(ym).slice(0, 8));

export const calendarLabel = cal => (cal.source === 'google' ? `Google: ${cal.name}` : cal.name);

function StatusLine({ line, openSettings, onRetry }) {
  if (!line.text) return null;
  const act = line.action === 'settings' ? openSettings : onRetry;
  return <div className="events-status" role={line.spinner ? 'status' : undefined}>
    {line.spinner && <Spinner size="tiny"/>}{line.text}
    {line.action && (line.link
      ? <button type="button" className="text-link" onClick={act}>{line.actionLabel}</button>
      : <Button size="small" onClick={act}>{line.actionLabel}</Button>)}
  </div>;
}

// The month calendar, the selected day's agenda and the calendar toggles, against /calendar.
// props: api (createApi), online, notify(text), openSettings()
export default function CalendarView({ api, online, notify, openSettings }) {
  const cal = api.calendar;
  const zone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone, []);
  const [today, setToday] = useState(() => todayKey());
  const [ym, setYm] = useState(() => monthOf(today));
  const [selected, setSelected] = useState(today);
  const [status, setStatus] = useState(null);
  const [calendars, setCalendars] = useState([]);
  const [events, setEvents] = useState([]);
  const [loadError, setLoadError] = useState('');
  const [failures, setFailures] = useState(0);   // failed loads so far: re-arms the look-again below
  const [syncing, setSyncing] = useState(false);
  const [dialog, setDialog] = useState(null);   // { event } or { event: null } for a new one
  const loads = useRef(0);
  const syncedOnMount = useRef(false);
  const lost = useRef({ shown: 0, acking: false, handled: null });   // lost edits: see the effect below

  const grid = useMemo(() => monthGrid(ym.year, ym.month), [ym.year, ym.month]);
  const { start, end } = fetchRange(grid);

  const load = useCallback(async () => {
    const n = ++loads.current;
    try {
      const [s, c, e] = await Promise.all([cal.status(), cal.calendars(), cal.events(start, end)]);
      if (n !== loads.current) return;   // a newer load (another month, a later change) answers instead
      setStatus(s); setCalendars(c); setEvents(e); setLoadError(''); setToday(todayKey());
    } catch (err) {
      if (n === loads.current) { setLoadError(err.message || 'The calendar could not be loaded.'); setFailures(f => f + 1); }
    }
  }, [cal, start, end]);
  const loadRef = useRef(load);
  loadRef.current = load;

  const sync = useCallback(async () => {
    setSyncing(true);
    try {
      const s = await cal.sync();
      setStatus(s);
    } catch (err) { notify(err.message || 'The calendar could not be synced.'); }
    finally { setSyncing(false); }
    await loadRef.current();
  }, [cal, notify]);

  useEffect(() => { if (online) load(); }, [load, online]);
  useEffect(() => {
    if (syncedOnMount.current || status?.google?.state !== 'connected') return;
    syncedOnMount.current = true;
    sync();
  }, [status, sync]);
  // An edit lost in any run, the harness's own included, is in every status until this page has shown it: show it,
  // then take that count off the harness's counter (one counted meanwhile stays for the next status).
  useEffect(() => {
    if (lost.current.handled === status) return;   // this very status again (the effect re-ran for another reason)
    const step = lostEditStep(status, lost.current);
    if (!step) return;
    if (step.notice) notify(step.notice);
    lost.current = { shown: step.ack, acking: true, handled: status };
    cal.ackLostEdits(step.ack).then(
      () => { lost.current = { shown: 0, acking: false, handled: status }; loadRef.current(); },
      () => { lost.current = { shown: step.ack, acking: false, handled: status }; },
    );
  }, [status, cal, notify]);
  // a run the harness started on its own: look again until it ends, a failed look included
  useEffect(() => {
    if (!status?.sync?.running || syncing) return undefined;
    const t = setTimeout(() => loadRef.current(), 2000);
    return () => clearTimeout(t);
  }, [status, syncing, failures]);

  const calById = useMemo(() => new Map(calendars.map(c => [c.id, c])), [calendars]);
  const visible = useMemo(() => events.filter(e => calById.get(e.calendar_id)?.visible !== false), [events, calById]);
  const byDay = useMemo(() => eventsByDay(visible, zone), [visible, zone]);
  const monthEmpty = ![...byDay.keys()].some(k => sameMonth(k, ym));
  const dayEvents = byDay.get(selected) || [];
  const shown = calendars.filter(c => c.source === 'local' || c.selected);

  const colorOf = ev => { const c = calById.get(ev.calendar_id)?.color; return HEX.test(c || '') ? c : 'var(--brand)'; };
  const labelOf = ev => { const c = calById.get(ev.calendar_id); return c ? calendarLabel(c) : ev.calendar_name; };
  const nameOf = ev => calById.get(ev.calendar_id)?.name || ev.calendar_name;

  const select = (key, turnMonth) => { setSelected(key); if (turnMonth) setYm(monthOf(key)); };
  const shiftMonth = delta => {
    const n = ym.year * 12 + (ym.month - 1) + delta;
    const next = { year: Math.floor(n / 12), month: (n % 12) + 1 };
    setYm(next);
    setSelected(sameMonth(today, next) ? today : firstOf(next));
  };
  const goToday = () => { const t = todayKey(); setToday(t); setYm(monthOf(t)); setSelected(t); };

  async function toggle(c, on) {
    setCalendars(cs => cs.map(x => (x.id === c.id ? { ...x, visible: on } : x)));
    try { await cal.setCalendar(c.id, { visible: on }); }
    catch (err) { notify(err.message || 'The calendar could not be changed.'); }
    load();
  }

  function changed(key) {
    setDialog(null);
    if (key) select(key, !sameMonth(key, ym));
    load();
  }

  const line = statusLine(status, { online, failed: !!loadError, syncing, zone });
  return <div className="events-calendar">
    <div className="events-head">
      <h2 className="events-month" aria-live="polite">{dayTitle(firstOf(ym), { month: 'long', year: 'numeric' })}</h2>
      <div className="events-nav">
        <Button appearance="subtle" icon={<ChevronLeft24Regular/>} aria-label="Previous month" onClick={() => shiftMonth(-1)}/>
        <Button onClick={goToday}>Today</Button>
        <Button appearance="subtle" icon={<ChevronRight24Regular/>} aria-label="Next month" onClick={() => shiftMonth(1)}/>
      </div>
      <div className="events-actions">
        {line.refresh && <Button icon={<ArrowSync24Regular/>} disabled={line.refreshDisabled} onClick={sync}>Refresh</Button>}
        <Button appearance="primary" icon={<Add24Regular/>} disabled={!line.ready} onClick={() => setDialog({ event: null })}>New event</Button>
      </div>
    </div>
    {loadError && <p className="events-error" role="alert">{loadError}</p>}
    <div className="events-layout">
      <div className="events-main">
        <CalendarGrid grid={grid} year={ym.year} month={ym.month} selected={selected} today={today} byDay={byDay} colorOf={colorOf} onSelect={select}/>
        <StatusLine line={line} openSettings={openSettings} onRetry={sync}/>
      </div>
      <aside className="events-side" aria-label="Selected day">
        <section className="events-agenda">
          <h3>{dayTitle(selected)}</h3>
          {dayEvents.length
            ? <ul className="events-agenda-list">{dayEvents.map(ev => <li key={ev.id}>
                <button type="button" className="events-agenda-item" onClick={() => setDialog({ event: ev })}>
                  <span className="events-agenda-time">{timeLabel(ev, zone, selected)}</span>
                  <span className="events-agenda-body">
                    <span className="events-agenda-title">{ev.title}</span>
                    {ev.location && <span className="events-agenda-where">{ev.location}</span>}
                    <span className="events-agenda-cal"><span className="events-swatch" style={{ background: colorOf(ev) }}/>{labelOf(ev)}</span>
                  </span>
                </button>
              </li>)}</ul>
            : line.ready && <p className="events-empty">Nothing on this day.{monthEmpty && <><br/>No events this month.</>}</p>}
        </section>
        {shown.length > 0 && <section className="events-calendars">
          <h3>Calendars</h3>
          {shown.map(c => <Checkbox key={c.id} checked={c.visible} label={calendarLabel(c)} onChange={(_, d) => toggle(c, !!d.checked)}/>)}
        </section>}
      </aside>
    </div>
    {dialog && <EventDialog cal={cal} zone={zone} event={dialog.event} day={selected}
      calendarName={dialog.event ? nameOf(dialog.event) : ''} calendarLabel={dialog.event ? labelOf(dialog.event) : ''}
      onClose={() => setDialog(null)} onChanged={changed}/>}
  </div>;
}

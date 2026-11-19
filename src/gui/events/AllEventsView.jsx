import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Field, Input, ProgressBar, Select } from '@fluentui/react-components';
import { CalendarLtr24Regular } from '@fluentui/react-icons';
import EventPanel from './EventPanel.jsx';
import { CREDIT, refreshNotice, viewState } from './discover.js';
import {
  canRefresh, categoryOptions, cells, COLUMNS, countLabel, DEFAULT_SORT, EMPTY_FILTERS, filterEvents, gridCredit,
  gridState, isBusy, keepFilters, MARKS, nearEnd, nextSort, NO_EVENTS, NO_MATCH, PAGE, pollExpired, READING, readLabel,
  rowKeyStep, showSourceFilter, SOURCE_NAMES, sortEvents, visibleCount, withAdded, withState,
} from './all-events.js';

const POLL_MS = 3000;
const POLL_LIMIT_MS = 10 * 60 * 1000;   // a refresh still going after this is left to finish on its own
const STILL_READING = 'The refresh has not finished. Check back later.';

// The filters: text, category, source (only when both sources are present), from and to dates, Clear.
function FilterBar({ filters, setFilters, events }) {
  const set = key => (_, d) => setFilters(f => ({ ...f, [key]: d.value }));
  const blank = Object.keys(EMPTY_FILTERS).every(k => !filters[k]);
  return <div className="all-events-bar" role="search" aria-label="Filter events">
    <Field label="Search" size="small"><Input size="small" value={filters.text} placeholder="Name, venue or city" onChange={set('text')}/></Field>
    <Field label="Category" size="small"><Select size="small" value={filters.category} onChange={set('category')}>
      <option value="">Any</option>
      {categoryOptions(events).map(c => <option key={c} value={c}>{c}</option>)}
    </Select></Field>
    {showSourceFilter(events) && <Field label="Source" size="small"><Select size="small" value={filters.source} onChange={set('source')}>
      <option value="">Any</option>
      {Object.entries(SOURCE_NAMES).map(([v, n]) => <option key={v} value={v}>{n}</option>)}
    </Select></Field>}
    <Field label="From" size="small"><Input size="small" type="date" value={filters.from} onChange={set('from')}/></Field>
    <Field label="To" size="small"><Input size="small" type="date" value={filters.to} onChange={set('to')}/></Field>
    <Button size="small" disabled={blank} onClick={() => setFilters(EMPTY_FILTERS)}>Clear</Button>
  </div>;
}

// The table: a fixed header of sort buttons, rows drawn up to `count`, one row in the Tab order at a time.
function Grid({ rows, count, sort, onSort, focus, setFocus, rowRefs, onOpen, onScroll }) {
  const key = (e, i) => {
    const step = rowKeyStep(e.key, i, rows.length);
    if (!step) return;
    e.preventDefault();
    if (step.open) onOpen(rows[i]);
    else setFocus(step.index, true);
  };
  const ariaSort = k => (sort.key !== k ? 'none' : sort.dir === 'asc' ? 'ascending' : 'descending');
  return <div className="all-events-scroll" role="region" aria-label="All events" onScroll={onScroll}>
    <table className="all-events-table">
      <thead><tr>{COLUMNS.map(c => <th key={c.key} scope="col" aria-sort={ariaSort(c.key)} className={`col-${c.key}`}>
        <button type="button" onClick={() => onSort(c.key)}>{c.label}{sort.key === c.key && <span aria-hidden="true">{sort.dir === 'asc' ? ' ▲' : ' ▼'}</span>}</button>
      </th>)}</tr></thead>
      <tbody>{rows.slice(0, count).map((e, i) => {
        const c = cells(e);
        const mark = Object.hasOwn(MARKS, e.state) ? MARKS[e.state] : null;
        return <tr key={e.id} ref={el => { if (el) rowRefs.current.set(e.id, el); else rowRefs.current.delete(e.id); }}
          tabIndex={i === focus ? 0 : -1} onClick={() => { setFocus(i, false); onOpen(e); }} onKeyDown={ev => key(ev, i)}>
          <td className="col-date">{c.date}</td>
          <td className="col-time">{c.time}</td>
          <td className="col-name">{c.name}{mark && <> <span className="all-events-mark" role="img" aria-label={mark.label} title={mark.label}>{mark.glyph}</span></>}</td>
          <td className="col-venue">{c.venue}</td>
          <td className="col-category">{c.category}</td>
          <td className="col-source">{c.source}</td>
        </tr>;
      })}</tbody>
    </table>
  </div>;
}

// The All Events tab: every event read from the sources, to filter, sort, open and add.
// props: api (createApi), online, notify(text), openSettings()
export default function AllEventsView({ api, online, notify, openSettings }) {
  const discover = api.discover;
  const [status, setStatus] = useState(null);
  const [events, setEvents] = useState([]);
  const [readAt, setReadAt] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState('');
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const [sort, setSort] = useState(DEFAULT_SORT);
  const [count, setCount] = useState(PAGE);
  const [focus, setFocusIndex] = useState(0);
  const [opened, setOpened] = useState(null);   // the slim row the panel is about
  const [reading, setReading] = useState(false);   // a refresh is being watched
  const [timedOut, setTimedOut] = useState(false);   // the last watch ran out of time with the refresh still going
  const [now, setNow] = useState(() => Date.now());
  const rowRefs = useRef(new Map());
  const wantFocus = useRef(false);
  const addLog = useRef([]);   // the ids added here, in order (see load)
  const mounted = useRef(true);
  const notifyRef = useRef(notify);
  notifyRef.current = notify;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 60000); return () => clearInterval(t); }, []);

  // status and the list, together; a refresh in progress (started here or elsewhere) is watched
  const load = useCallback(async () => {
    const from = addLog.current.length;   // adds that finish while this load is out are put back over its answer
    try {
      const [s, list] = await Promise.all([discover.status(), discover.events()]);
      if (!mounted.current) return;
      const rows = withAdded(Array.isArray(list?.events) ? list.events : [], addLog.current.slice(from));
      setStatus(s); setEvents(rows); setReadAt(list?.read_at || ''); setError('');
      setFilters(f => keepFilters(f, rows));   // a category or source no longer listed stops filtering
      if (s?.refreshing) setReading(true);
    } catch (err) {
      if (mounted.current) setError(err?.message || 'Events could not be loaded.');
    } finally { if (mounted.current) { setLoaded(true); setNow(Date.now()); } }
  }, [discover]);
  useEffect(() => { if (online) load(); }, [load, online]);

  // while a refresh goes: look at the status every 3 s until it is done, then load the list again
  useEffect(() => {
    if (!reading) return undefined;
    setTimedOut(false);
    const since = Date.now();
    let cancelled = false, busy = false;
    const t = setInterval(async () => {
      // the limit first, on every tick: a status request that never answers keeps `busy` set, and must not keep
      // the reading line up past the limit. Its answer, if it ever comes, is then ignored (`cancelled`).
      if (pollExpired(since, Date.now(), POLL_LIMIT_MS)) {
        cancelled = true; clearInterval(t); setReading(false); setTimedOut(true); notifyRef.current(STILL_READING);
        return;
      }
      if (busy) return;
      busy = true;
      let s = null;
      try { s = await discover.status(); } catch { s = null; } finally { busy = false; }   // a failed look keeps the list
      if (cancelled) return;
      if (s) setStatus(s);
      if (s && !s.refreshing) { clearInterval(t); setReading(false); load(); }
    }, POLL_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, [reading, discover, load]);

  const refresh = async () => {
    try {
      const s = await discover.refresh();
      if (!mounted.current) return;
      if (s) setStatus(s);
      setReading(true);
    } catch (err) { if (mounted.current) notify(err?.message || 'Something went wrong.'); }
  };

  const shown = useMemo(() => sortEvents(filterEvents(events, filters), sort), [events, filters, sort]);
  useEffect(() => { setCount(PAGE); setFocusIndex(0); }, [filters, sort]);
  const drawn = Math.min(count, shown.length);
  const focusAt = Math.min(focus, Math.max(0, drawn - 1));

  const setFocus = (index, move) => {
    setFocusIndex(index);
    setCount(c => Math.max(c, index + 1));   // End may reach past the rows drawn so far
    wantFocus.current = move;
  };
  useEffect(() => {
    if (!wantFocus.current) return;
    wantFocus.current = false;
    const row = shown[focusAt];
    if (row) rowRefs.current.get(row.id)?.focus();
  });
  const onScroll = e => { if (count < shown.length && nearEnd(e.currentTarget)) setCount(c => visibleCount(c, shown.length)); };
  const close = () => {
    const id = opened?.id;
    setOpened(null);
    setTimeout(() => { if (id) rowRefs.current.get(id)?.focus(); }, 0);   // back to the row the panel came from
  };
  const added = id => { addLog.current.push(id); setEvents(list => withState(list, id, 'added')); };

  const state = gridState({ online, loaded, error: status ? '' : error, status, events, shown });
  const notice = refreshNotice(status);
  const refreshable = canRefresh(status, now, { online }) && !reading;
  const busy = isBusy({ reading, timedOut, status });
  const refreshButton = refreshable && <Button onClick={refresh}>Refresh</Button>;

  let body;
  if (state === 'ready' || state === 'nomatch') {
    body = <>
      <FilterBar filters={filters} setFilters={setFilters} events={events}/>
      <div className="all-events-meta">
        <span role="status">{countLabel(shown.length, events.length)}</span>
        <span>{readLabel(readAt, now)}</span>
        {busy ? <span role="status">{READING}</span> : refreshButton}
      </div>
      {state === 'nomatch'
        ? <div className="discover-state" role="status"><p>{NO_MATCH}</p><Button onClick={() => setFilters(EMPTY_FILTERS)}>Clear</Button></div>
        : <Grid rows={shown} count={count} sort={sort} onSort={k => setSort(s => nextSort(s, k))} focus={focusAt} setFocus={setFocus}
          rowRefs={rowRefs} onOpen={setOpened} onScroll={onScroll}/>}
      {gridCredit(events) && <p className="events-muted"><a href={CREDIT.href} target="_blank" rel="noopener noreferrer">{CREDIT.text}</a></p>}
    </>;
  } else {
    const view = viewState(status, null, { online, loaded: true });   // the Recommendations tab's own sentences
    const text = { offline: view.text, error, loading: 'Loading…', setup: view.text, empty: NO_EVENTS }[state];
    body = <div className="discover-state" role={state === 'error' ? 'alert' : 'status'} aria-busy={state === 'loading' || busy}>
      <CalendarLtr24Regular className="od-icon" aria-hidden="true"/>
      <p>{text}</p>
      {(state === 'offline' || state === 'error') && <Button onClick={load}>Try again</Button>}
      {state === 'setup' && <Button appearance="primary" onClick={openSettings}>Open settings</Button>}
      {state === 'empty' && (busy
        ? <><p className="events-muted">{READING}</p><div className="discover-progress"><ProgressBar aria-label="Reading events"/></div></>
        : refreshable && <Button appearance="primary" onClick={refresh}>Refresh</Button>)}
    </div>;
  }

  return <section className="events-discover all-events">
    <div className="discover-head"><h2>All Events</h2></div>
    {notice && <p className="events-error" role="status">{notice}</p>}
    {/* a list read that failed with the status still on hand: the rows stay, and the read can be tried again here
        (Refresh, which reads the sources, is a different thing and may not be offered for hours) */}
    {error && status && <p className="events-error" role="alert">{error} <Button size="small" onClick={load}>Try again</Button></p>}
    {body}
    {opened && <EventPanel api={api} event={opened} onClose={close} onAdded={added} notify={notify}/>}
  </section>;
}

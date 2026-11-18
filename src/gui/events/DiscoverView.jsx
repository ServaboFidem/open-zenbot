import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button, ProgressBar } from '@fluentui/react-components';
import { ArrowLeft24Regular, ArrowRight24Regular, CalendarLtr24Regular } from '@fluentui/react-icons';
import EventCard from './EventCard.jsx';
import SignupDialog from './SignupDialog.jsx';
import { addTimes, askable, canAdd, canRun, firstLoadDone, lookStep, refreshNotice, shownCards, signupStep, viewState } from './discover.js';

const POLL_MS = 4000;
const PAGE = 20;   // the harness's default page of digests

const dayText = iso => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' }); };
const swap = (list, card) => list.map(d => ({ ...d, cards: d.cards.map(c => (c.id === card.id ? card : c)) }));

// The Discover tab: the newest digest of events, "Find events now", and older digests.
// props: api (createApi), online, notify(text), openSettings()
export default function DiscoverView({ api, online, notify, openSettings }) {
  const discover = api.discover;
  const [status, setStatus] = useState(null);
  const [digests, setDigests] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [looking, setLooking] = useState(null);   // { known: ids of the digests there were, since }
  const [history, setHistory] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [adding, setAdding] = useState(() => new Set());   // ids of cards being added
  const [asking, setAsking] = useState(null);   // the card id the dialog is about
  const [arrived, setArrived] = useState(false);   // a digest came in while a run went
  const runSince = useRef(null);   // when the run being watched began
  const digestsNow = useRef([]);
  const pending = useRef(null);   // the card whose link was followed (signupStep)
  const onScreen = useRef([]);   // the cards drawn now
  const mounted = useRef(true);
  const loading = useRef(false);
  const notifyRef = useRef(notify);
  notifyRef.current = notify;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const load = useCallback(async () => {
    try {
      const [s, list] = await Promise.all([discover.status(), discover.digests()]);
      setStatus(s); setDigests(list); setHasMore(list.length >= PAGE); setLoadError('');
      return { status: s, digests: list };
    } catch (err) {
      setLoadError(err.message || 'Events could not be loaded.');
      return null;
    } finally { setLoaded(true); }
  }, [discover]);

  useEffect(() => { if (online) load(); }, [load, online]);

  // look again while a run goes: the one this page started (until a new digest, the scout stops or time is up),
  // or one that is not its own (the weekly one, or one going when the tab was opened): known is the digests on
  // hand, and it goes on until the run stops or time is up. One time limit per run: runSince holds when it began.
  const running = !!status?.running;
  const watching = !!looking || running;
  useEffect(() => {
    if (!watching) { runSince.current = null; setArrived(false); return undefined; }
    const mine = looking;
    if (!runSince.current) runSince.current = mine ? mine.since : Date.now();
    const since = runSince.current;
    const known = mine ? mine.known : digestsNow.current.map(d => d.id);
    let seenRunning = false;
    let cancelled = false;
    const t = setInterval(async () => {
      if (loading.current) return;   // the last look has not answered
      loading.current = true;
      let got;
      try { got = await load(); } finally { loading.current = false; }
      if (cancelled || !mounted.current) return;
      const step = lookStep({ since, now: Date.now(), known, digests: got ? got.digests : null, running: got ? got.status.running : false, seenRunning, mine: !!mine });
      seenRunning = step.seenRunning;
      if (step.arrived) setArrived(true);
      if (!step.done) return;
      clearInterval(t);
      if (mine) { setLooking(null); notifyRef.current(step.notice); }
    }, POLL_MS);
    return () => { cancelled = true; clearInterval(t); };
  }, [watching, looking, load]);

  digestsNow.current = digests;
  const current = digests[0] || null;

  // "Did you sign up?": the window gets focus again after a link was followed
  useEffect(() => {
    const back = () => {
      const step = signupStep(pending.current, { type: 'focus' });
      pending.current = step.pending;
      const card = askable(step.ask, onScreen.current);
      if (card) setAsking(card.id);
    };
    const visible = () => { if (document.visibilityState === 'visible') back(); };
    window.addEventListener('focus', back);
    document.addEventListener('visibilitychange', visible);
    return () => { window.removeEventListener('focus', back); document.removeEventListener('visibilitychange', visible); };
  }, []);

  const guard = async fn => { try { return await fn(); } catch (err) { if (mounted.current) notify(err.message || 'Something went wrong.'); return undefined; } };
  const answered = () => { pending.current = signupStep(pending.current, { type: 'answered' }).pending; setAsking(null); };

  const run = () => guard(async () => {
    await discover.run();
    runSince.current = Date.now(); setArrived(false);
    setLooking({ known: digests.map(d => d.id), since: runSince.current });
    load();
  });
  const add = card => guard(async () => {
    const times = canAdd(card) ? addTimes(card) : null;
    if (!times) { notify('This listing has no usable date.'); return false; }
    setAdding(a => new Set(a).add(card.id));
    try {
      const r = await discover.add(card.id, times);
      setDigests(ds => swap(ds, r.card));
      notify('Added to your calendar.');
      return true;
    } finally { setAdding(a => { const n = new Set(a); n.delete(card.id); return n; }); }
  });
  const dismiss = card => guard(async () => {
    const next = await discover.dismiss(card.id);
    setDigests(ds => swap(ds, next));
  });
  const opened = card => { pending.current = signupStep(pending.current, { type: 'opened', cardId: card.id }).pending; };
  const yes = async card => { if (await add(card)) answered(); };
  const never = async card => { answered(); await dismiss(card); };
  const showHistory = on => { pending.current = null; setAsking(null); setHistory(on); };
  const more = () => guard(async () => {
    const last = digests[digests.length - 1];
    const list = await discover.digests(last?.created_at);
    setDigests(ds => [...ds, ...list]); setHasMore(list.length >= PAGE);
  });

  const view = viewState(status, current, { online, loaded: firstLoadDone({ loaded, status, loadError }), looking: !!looking, arrived });
  const notice = refreshNotice(status);
  const src = card => discover.imageSrc(api.baseUrl, card);
  const grid = digest => <div className="discover-grid">{shownCards(digest).map(card => <EventCard key={card.id} card={card} src={src(card)} adding={adding.has(card.id)} onOpen={opened} onAdd={add}/>)}</div>;
  onScreen.current = view.kind !== 'digest' || (loadError && !status) ? [] : history ? digests.flatMap(shownCards) : shownCards(current);
  const dialogCard = askable(asking, onScreen.current);

  let body;
  if (loadError && !status) {
    body = <div className="discover-state" role="alert"><p>{loadError}</p><Button onClick={load}>Try again</Button></div>;
  } else if (view.kind === 'offline' || view.kind === 'loading' || view.kind === 'not_set_up' || view.kind === 'empty' || view.kind === 'looking') {
    body = <div className="discover-state" role="status" aria-busy={view.kind === 'looking' || view.kind === 'loading'}>
      <CalendarLtr24Regular className="od-icon" aria-hidden="true"/>
      <p>{view.text}</p>
      {view.kind === 'looking' && <><p className="events-muted">This can take a few minutes. You can leave this page; the events will be here when it finishes.</p><div className="discover-progress"><ProgressBar aria-label="Looking for events"/></div></>}
      {view.action === 'retry' && <Button onClick={load}>Try again</Button>}
      {view.action === 'settings' && <Button appearance="primary" onClick={openSettings}>Open settings</Button>}
      {view.action === 'run' && <Button appearance="primary" onClick={run}>Find events now</Button>}
    </div>;
  } else if (history) {
    body = <>
      <div className="discover-bar"><Button appearance="subtle" icon={<ArrowLeft24Regular className="od-icon"/>} onClick={() => showHistory(false)}>Back to this week</Button></div>
      {digests.map(d => <section key={d.id} className="discover-digest">
        <h3>{dayText(d.created_at) || 'Earlier digest'}</h3>
        {shownCards(d).length ? grid(d) : <p className="events-muted">Nothing left in this digest.</p>}
      </section>)}
      {hasMore && <Button onClick={more}>Show older digests</Button>}
    </>;
  } else {
    body = <>
      {view.text && <p className="events-muted">{view.text}</p>}
      {grid(current)}
      <div className="discover-bar"><Button appearance="subtle" icon={<ArrowRight24Regular className="od-icon"/>} iconPosition="after" onClick={() => showHistory(true)}>Older digests</Button></div>
    </>;
  }

  return <section className="events-discover">
    <div className="discover-head">
      <div><h2>Discover</h2>{current && !history && <p className="events-muted">Picked {dayText(current.created_at)}</p>}</div>
      {canRun(status, { online, looking: !!looking }) && view.kind !== 'empty' && <Button onClick={run}>Find events now</Button>}
    </div>
    {notice && <p className="events-error" role="status">{notice}</p>}
    {body}
    {dialogCard && <SignupDialog card={dialogCard} busy={adding.has(dialogCard.id)} onYes={yes} onNotYet={answered} onNever={never}/>}
  </section>;
}

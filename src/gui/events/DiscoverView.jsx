import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Button, ProgressBar } from '@fluentui/react-components';
import { ArrowLeft24Regular, ArrowRight24Regular, CalendarLtr24Regular } from '@fluentui/react-icons';
import EventCard from './EventCard.jsx';
import SignupDialog from './SignupDialog.jsx';
import { LOOK_LIMIT_MS, addTimes, canAdd, refreshNotice, shownCards, signupStep, viewState } from './discover.js';

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
  const [adding, setAdding] = useState('');   // the card being added
  const [asking, setAsking] = useState(null);   // the card id the dialog is about
  const pending = useRef(null);   // the card whose link was followed (signupStep)
  const cardsRef = useRef([]);

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

  // waiting for a run this page started: until a new digest appears, the scout stops, or time is up
  useEffect(() => {
    if (!looking) return undefined;
    const t = setInterval(async () => {
      const got = await load();
      if (!got) return;
      if (got.digests.some(d => !looking.known.includes(d.id))) { setLooking(null); notify('New events are in.'); }
      else if (!got.status.running) { setLooking(null); notify('The scout finished without new events.'); }
      else if (Date.now() - looking.since > LOOK_LIMIT_MS) { setLooking(null); notify('The scout has not finished. Check back later.'); }
    }, POLL_MS);
    return () => clearInterval(t);
  }, [looking, load, notify]);
  // a run the harness started on its own (the weekly one): look again until it ends
  const running = !!status?.running && !looking;
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [running, load]);

  const current = digests[0] || null;
  cardsRef.current = digests.flatMap(d => d.cards);

  // "Did you sign up?": the window gets focus again after a link was followed
  useEffect(() => {
    const back = () => {
      const step = signupStep(pending.current, { type: 'focus' });
      pending.current = step.pending;
      const card = step.ask && cardsRef.current.find(c => c.id === step.ask);
      if (card && card.state === 'new') setAsking(card.id);
    };
    const visible = () => { if (document.visibilityState === 'visible') back(); };
    window.addEventListener('focus', back);
    document.addEventListener('visibilitychange', visible);
    return () => { window.removeEventListener('focus', back); document.removeEventListener('visibilitychange', visible); };
  }, []);

  const guard = async fn => { try { return await fn(); } catch (err) { notify(err.message || 'Something went wrong.'); return undefined; } };
  const answered = () => { pending.current = signupStep(pending.current, { type: 'answered' }).pending; setAsking(null); };

  const run = () => guard(async () => {
    await discover.run();
    setLooking({ known: digests.map(d => d.id), since: Date.now() });
    load();
  });
  const add = card => guard(async () => {
    const times = canAdd(card) ? addTimes(card) : null;
    if (!times) { notify('This listing has no usable date.'); return false; }
    setAdding(card.id);
    try {
      const r = await discover.add(card.id, times);
      setDigests(ds => swap(ds, r.card));
      notify('Added to your calendar.');
      return true;
    } finally { setAdding(''); }
  });
  const dismiss = card => guard(async () => {
    const next = await discover.dismiss(card.id);
    setDigests(ds => swap(ds, next));
  });
  const opened = card => { pending.current = signupStep(pending.current, { type: 'opened', cardId: card.id }).pending; };
  const yes = async card => { await add(card); answered(); };
  const never = async card => { answered(); await dismiss(card); };
  const more = () => guard(async () => {
    const last = digests[digests.length - 1];
    const list = await discover.digests(last?.created_at);
    setDigests(ds => [...ds, ...list]); setHasMore(list.length >= PAGE);
  });

  const view = viewState(status, current, { online, loaded: loaded && !(!status && loadError), looking: !!looking });
  const notice = refreshNotice(status);
  const src = card => discover.imageSrc(api.baseUrl, card);
  const grid = digest => <div className="discover-grid">{shownCards(digest).map(card => <EventCard key={card.id} card={card} src={src(card)} adding={adding === card.id} onOpen={opened} onAdd={add}/>)}</div>;
  const dialogCard = asking && cardsRef.current.find(c => c.id === asking && c.state === 'new');

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
      <div className="discover-bar"><Button appearance="subtle" icon={<ArrowLeft24Regular className="od-icon"/>} onClick={() => setHistory(false)}>Back to this week</Button></div>
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
      <div className="discover-bar"><Button appearance="subtle" icon={<ArrowRight24Regular className="od-icon"/>} iconPosition="after" onClick={() => setHistory(true)}>Older digests</Button></div>
    </>;
  }

  const canRun = online && view.kind !== 'not_set_up' && view.kind !== 'looking' && view.kind !== 'loading' && view.kind !== 'offline' && !(loadError && !status);
  return <section className="events-discover">
    <div className="discover-head">
      <div><h2>Discover</h2>{current && !history && <p className="events-muted">Picked {dayText(current.created_at)}</p>}</div>
      {canRun && view.kind !== 'empty' && <Button onClick={run}>Find events now</Button>}
    </div>
    {notice && <p className="events-error" role="status">{notice}</p>}
    {body}
    {dialogCard && <SignupDialog card={dialogCard} busy={adding === dialogCard.id} onYes={yes} onNotYet={answered} onNever={never}/>}
  </section>;
}

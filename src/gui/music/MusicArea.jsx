import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Tab, TabList } from '@fluentui/react-components';
import { Clock24Regular, Settings24Regular } from '@fluentui/react-icons';
import DigestView from './DigestView.jsx';
import GenrePanel from './GenrePanel.jsx';
import MusicHistory from './MusicHistory.jsx';
import MusicSettings from './MusicSettings.jsx';
import MusicSetup from './MusicSetup.jsx';
import TasteMap from './TasteMap.jsx';
import { dayLabel, withCard } from './helpers.js';
import './music.css';

const PAGE = 20;
const DIG_LIMIT_MS = 15 * 60 * 1000;
const HEADINGS = {
  digest: ['A few overlooked albums. A good reason to put a record on.'],
  map: ['Your taste, mapped.', 'What you love, and what might be waiting nearby.'],
  history: ['The records along the way.', 'Every pick and every reaction, remembered.'],
  settings: ['Music preferences', 'A little direction makes for better discoveries.'],
};

// The Music area: This week, Taste map, History, Settings, and first-run setup, all against /music.
// props: api (createApi), status (GET /music/status, polled by the app), refreshStatus() -> status, notify(text), initialTab
export default function MusicArea({ api, status, refreshStatus, notify, initialTab = 'digest' }) {
  const music = api.music;
  const [tab, setTab] = useState(initialTab);
  const [setup, setSetup] = useState(false);
  const [selected, setSelected] = useState(null);
  const [digests, setDigests] = useState([]);
  const [loaded, setLoaded] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [map, setMap] = useState(null);
  const [routine, setRoutine] = useState(null);
  const [wanting, setWanting] = useState('');
  const [wantErr, setWantErr] = useState({});
  const [digging, setDigging] = useState(null);  // { known: ids of the digests there were, since }
  const [discovering, setDiscovering] = useState('');
  const marked = useRef(new Set());

  const unread = status?.unread;
  const scanState = status?.scan?.state;
  const scanDone = status?.scan?.finished_at;
  const atlasState = status?.atlas?.state;
  const focusKey = (status?.settings?.focus || []).join('|');
  const routineId = status?.routine_id;

  const loadDigests = useCallback(async () => {
    try { const list = await music.digests({ limit: PAGE }); setDigests(list); setHasMore(list.length === PAGE); return list; }
    catch { return null; } finally { setLoaded(true); }
  }, [music]);
  const loadMap = useCallback(async () => { try { setMap(await music.map()); } catch { /* 503 until the atlas is ready: retried when its state changes */ } }, [music]);
  const loadRoutine = useCallback(async () => {
    if (!routineId) { setRoutine(null); return; }
    try { setRoutine((await api.routines()).find(r => r.id === routineId) || null); } catch { /* keep what was shown */ }
  }, [api, routineId]);

  useEffect(() => { loadDigests(); }, [unread]);
  useEffect(() => { loadMap(); }, [atlasState, scanDone, focusKey]);
  useEffect(() => { loadRoutine(); }, [routineId]);
  // a running scan moves faster than the app's 5 s poll
  useEffect(() => {
    if (scanState !== 'running') return undefined;
    const t = setInterval(() => { refreshStatus(); }, 1500);
    return () => clearInterval(t);
  }, [scanState]);
  // waiting for a digest the librarian is making (first digest, or an on-request pick)
  useEffect(() => {
    if (!digging) return undefined;
    const t = setInterval(async () => {
      const list = await loadDigests();
      if (list?.some(d => !digging.known.includes(d.id))) { setDigging(null); setDiscovering(''); notify('New picks are in.'); refreshStatus(); }
      else if (Date.now() - digging.since > DIG_LIMIT_MS) { setDigging(null); setDiscovering(''); notify('The librarian has not finished. Check History later.'); }
    }, 4000);
    return () => clearInterval(t);
  }, [digging]);

  const current = digests.find(d => d.kind === 'weekly') || null;
  // opening a digest marks it read
  useEffect(() => {
    if (tab !== 'digest' || !current || current.read_at || marked.current.has(current.id)) return;
    marked.current.add(current.id);
    music.markRead(current.id).then(() => { setDigests(ds => ds.map(d => (d.id === current.id ? { ...d, read_at: new Date().toISOString() } : d))); refreshStatus(); }).catch(() => marked.current.delete(current.id));
  }, [tab, current?.id, current?.read_at]);

  const names = useMemo(() => (map ? map.points.map(p => p[0]) : []), [map]);
  const guard = async fn => { try { return await fn(); } catch (e) { notify(e.message || 'Something went wrong.'); return undefined; } };

  const react = (card, reaction) => guard(async () => {
    const next = await music.react(card.id, reaction);
    setDigests(ds => withCard(ds, next));
    if (reaction !== 'listened') notify(reaction === 'owned' ? 'Marked as owned. It will not be suggested again.' : reaction === 'passed' ? 'Passed. Your next picks can learn from this.' : 'Reaction cleared.');
  });
  const want = card => guard(async () => {
    setWanting(card.id);
    try {
      const r = await music.want(card.id);
      if (r.ok) { setDigests(ds => withCard(ds, r.card)); setWantErr(e => ({ ...e, [card.id]: undefined })); notify('Added to your Discogs wantlist.'); }
      else { setWantErr(e => ({ ...e, [card.id]: r.error || 'unknown' })); if (r.error === 'token_invalid') refreshStatus(); }
    } finally { setWanting(''); }
  });
  const unwant = card => guard(async () => {
    const r = await music.unwant(card.id);
    if (r.ok) { setDigests(ds => withCard(ds, r.card)); notify('Removed from your Discogs wantlist.'); }
    else notify('Could not remove it from your Discogs wantlist. Try again in a moment.');
  });
  const listen = card => { if (!card.reaction) react(card, 'listened'); };
  const cardProps = card => ({ base: api.baseUrl, wanting: wanting === card.id, wantError: wantErr[card.id], onReact: react, onWant: want, onUnwant: unwant, onListen: listen });

  const discover = name => guard(async () => {
    setDiscovering(name);
    try { await music.discover(name); } catch (e) { setDiscovering(''); throw e; }
    setDigging({ known: digests.map(d => d.id), since: Date.now() });
    notify(`The librarian is digging in ${name}. The picks will land in History.`);
  });
  const firstDigest = () => guard(async () => {
    await api.runRoutine(routineId);
    setDigging({ known: digests.map(d => d.id), since: Date.now() });
  });
  const more = () => guard(async () => {
    const last = digests[digests.length - 1];
    const list = await music.digests({ before: last?.created_at, limit: PAGE });
    setDigests(ds => [...ds, ...list]); setHasMore(list.length === PAGE);
  });
  const scan = () => guard(async () => { await music.scan(); await refreshStatus(); });
  const toTab = t => { setTab(t); setSelected(null); };

  if (!status) return <div className="music-area"><p className="muted">Loading Music…</p></div>;
  const firstRun = !status.settings.enabled && !status.connection.username;
  if (setup || firstRun) return <div className="music-area"><MusicSetup status={status} music={music} api={api} names={names} refreshStatus={refreshStatus} onCancel={firstRun ? undefined : () => setSetup(false)} onFinish={() => { setSetup(false); toTab('map'); loadMap(); loadRoutine(); loadDigests(); }}/></div>;

  const [blurb, blurb2] = HEADINGS[tab];
  const heading = tab === 'digest' ? (current ? `Week of ${dayLabel(current.week_of)}` : 'Your weekly digest') : blurb;
  const description = tab === 'digest' ? blurb : blurb2;
  const panel = selected && <GenrePanel name={selected} music={music} suggestion={map?.related?.find(g => g.genre === selected)} onClose={() => setSelected(null)} onSelect={setSelected} onDiscover={discover} discovering={!!discovering} canDiscover={status.enabled && status.connection.token}/>;
  return <div className="music-area">
    <div className="page-heading"><div><div className="eyebrow">Your music librarian</div><h1>{heading}</h1><p className="description">{description}</p></div><div className="actions">{tab === 'digest' ? <Button icon={<Clock24Regular className="od-icon"/>} onClick={() => toTab('history')}>Past digests</Button> : tab === 'map' ? <Button icon={<Settings24Regular className="od-icon"/>} onClick={() => toTab('settings')}>Edit focus</Button> : null}</div></div>
    <TabList selectedValue={tab} className="music-tabs" onTabSelect={(_, d) => toTab(d.value)} aria-label="Music sections"><Tab value="digest">This week{unread > 0 ? ` (${unread})` : ''}</Tab><Tab value="map">Taste map</Tab><Tab value="history">History</Tab><Tab value="settings">Settings</Tab></TabList>
    {tab === 'digest' && <DigestView digest={current} status={status} routine={routine} cardProps={cardProps} digging={digging} loaded={loaded} onFirstDigest={firstDigest} onSettings={() => toTab('settings')} onHistory={() => toTab('history')}/>}
    {tab === 'map' && (map ? <TasteMap map={map} status={status} selected={selected} setSelected={setSelected} onScan={scan} needsToken={status.digest === 'needs_token'} onToken={() => toTab('settings')} panel={panel}/> : <div className="empty-state" role="status"><p>{atlasState === 'failed' ? 'The genre atlas could not be downloaded. Check your connection; it retries when Music is switched on.' : 'The genre atlas is getting ready. The map appears here once it has downloaded.'}</p></div>)}
    {tab === 'history' && <MusicHistory digests={digests} cardProps={cardProps} hasMore={hasMore} onMore={more} loaded={loaded}/>}
    {tab === 'settings' && <MusicSettings status={status} music={music} api={api} routine={routine} names={names} refreshStatus={refreshStatus} reloadRoutine={loadRoutine} notify={notify} startSetup={() => setSetup(true)}/>}
    <footer className="music-footer"><span>Discogs · Soundlore · MusicBrainz · YouTube Music</span></footer>
  </div>;
}

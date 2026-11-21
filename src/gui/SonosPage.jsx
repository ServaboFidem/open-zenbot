import React, { useEffect, useRef, useState } from 'react';
import { Badge, Button, Checkbox, Input, Slider, Spinner } from '@fluentui/react-components';
import { ArrowSync24Regular, Next24Regular, Pause24Filled, Play24Filled, Previous24Regular, Search24Regular, WeatherSunny24Regular } from '@fluentui/react-icons';
import { errorLine } from './mail.js';
import { MORNING_LINE, MORNING_NO_SKILL, MORNING_TITLE, NO_FAVORITES, NO_SPEAKERS, POLL_MS, VOLUME_WAIT_MS, canMakeRoutine, canPlay,
  favKey, groupsOf, initialPick, matchFavorites, orderPicked, routineDraft, toggleAction, togglePick, volumeMax, zenbotHasSkill } from './sonos-panel.js';
import './sonos.css';

const STATE = { playing: ['Playing', 'success'], paused: ['Paused', 'warning'], stopped: ['Stopped', 'subtle'] };

// Music > Sonos: play a Sonos Favorite or playlist on the speakers picked, and pause, skip and set each speaker's
// volume. Every command goes through the harness (/sonos), which keeps the highest volume ZenBot may set. Room names,
// titles and artists come from the speakers and music services and are shown as plain text. What plays is read again
// every few seconds while the page is open and visible.
// Props: { api, online, bots, notify, onMorningRoutine(draft), openSonosSettings() }
export default function SonosPage({ api, online, bots, notify, onMorningRoutine, openSonosSettings }) {
  const sonos = api.sonos;
  const [rows, setRows] = useState(null);         // GET /sonos/rooms
  const [ceiling, setCeiling] = useState(60);
  const [favs, setFavs] = useState(null);         // GET /sonos/favorites
  const [query, setQuery] = useState('');
  const [chosen, setChosen] = useState('');       // favKey of the picked Favorite
  const [picked, setPicked] = useState(null);     // speaker names; null until the first read picks some
  const [busy, setBusy] = useState('');           // 'play', or 'control:<group lead>'
  const [error, setError] = useState('');
  const [levels, setLevels] = useState({});       // speaker -> volume being set, shown over what was read
  const timers = useRef({});
  const mounted = useRef(true);

  async function readRooms() {
    try {
      const got = await sonos.rooms();
      if (!mounted.current) return;
      setRows(got.rooms); setCeiling(got.max_volume);
      setPicked(p => p ?? initialPick(got.rooms));
      setError('');
    } catch (e) { if (mounted.current) setError(errorLine(e)); }
  }
  async function readFavorites() {
    try {
      const got = await sonos.favorites();
      if (mounted.current) setFavs(got.favorites);
    } catch (e) { if (mounted.current) setError(errorLine(e)); }
  }
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; Object.values(timers.current).forEach(clearTimeout); };
  }, []);
  useEffect(() => { if (online) { readRooms(); readFavorites(); } }, [online]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!online) return undefined;
    const id = setInterval(() => { if (document.visibilityState === 'visible') readRooms(); }, POLL_MS);
    return () => clearInterval(id);
  }, [online]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function run(label, call) {
    setBusy(label);
    try { notify((await call()).message); } catch (e) { notify(errorLine(e)); }
    finally { if (mounted.current) { setBusy(''); readRooms(); } }
  }
  // a slider move is sent once it rests; until the harness answers, the page shows the level being set
  function slide(name, level) {
    setLevels(l => ({ ...l, [name]: level }));
    clearTimeout(timers.current[name]);
    timers.current[name] = setTimeout(async () => {
      try { await sonos.volume(name, level); } catch (e) { notify(errorLine(e)); }
      if (!mounted.current) return;
      setLevels(l => { if (l[name] !== level) return l; const next = { ...l }; delete next[name]; return next; });
      readRooms();
    }, VOLUME_WAIT_MS);
  }

  const pick = picked || [];
  const favorite = (favs || []).find(f => favKey(f) === chosen) || null;
  const shown = matchFavorites(favs, query);
  const groups = groupsOf(rows);
  const play = () => run('play', () => sonos.play(orderPicked(pick, rows), favorite.title));
  const refresh = () => { readRooms(); readFavorites(); };

  if (!online) return <p className="field-hint">Start the harness to control your Sonos speakers.</p>;
  if (rows === null && !error) return <Spinner size="small" label="Finding your speakers…"/>;

  return <>
    <section className="sonos-morning od-row" aria-labelledby="sonos-morning-title">
      <span className="sonos-morning-icon"><WeatherSunny24Regular/></span>
      <div className="od-field od-fill">
        <strong id="sonos-morning-title">{MORNING_TITLE}</strong>
        <span>{zenbotHasSkill(bots) ? MORNING_LINE : MORNING_NO_SKILL}</span>
      </div>
      <Button appearance="outline" disabled={!canMakeRoutine(bots, favorite, pick)}
        onClick={() => onMorningRoutine(routineDraft(favorite, pick, rows))}>Make this my morning routine</Button>
    </section>

    {error && <p className="form-error" role="alert">{error}</p>}
    {rows && !rows.length ? <div className="sonos-card"><p>{NO_SPEAKERS}</p>
      <div className="od-row"><Button appearance="primary" onClick={openSonosSettings}>Sonos settings</Button>
        <Button appearance="outline" icon={<ArrowSync24Regular/>} onClick={refresh}>Refresh</Button></div></div>
    : rows && <div className="sonos-layout">
      <section className="sonos-card" aria-labelledby="sonos-play-title">
        <div className="od-row"><h2 id="sonos-play-title" className="od-fill">Play something</h2>
          <Button appearance="subtle" icon={<ArrowSync24Regular/>} aria-label="Refresh favorites and speakers" onClick={refresh}/></div>
        {favs === null ? <Spinner size="tiny" label="Loading favorites…"/> : !favs.length ? <p className="field-hint">{NO_FAVORITES}</p> : <>
          <Input type="search" aria-label="Search favorites" placeholder="Search favorites" contentBefore={<Search24Regular/>}
            value={query} onChange={(e, d) => setQuery(d.value)}/>
          <div className="sonos-favs" role="radiogroup" aria-label="Favorites">
            {shown.map(f => { const key = favKey(f); return <label key={key} className={`sonos-fav ${key === chosen ? 'selected' : ''}`}>
              <input type="radio" name="sonos-favorite" checked={key === chosen} onChange={() => setChosen(key)}/>
              <span className="sonos-fav-title">{f.title}</span>
              <span className="sonos-muted">{f.kind === 'playlist' ? 'Playlist' : 'Favorite'}</span>
            </label>; })}
            {!shown.length && <p className="field-hint sonos-none">No favorites match “{query.trim()}”.</p>}
          </div>
        </>}
        <fieldset className="sonos-speakers">
          <legend>Speakers</legend>
          {rows.map(r => <Checkbox key={r.name} label={r.name} checked={pick.includes(r.name)}
            onChange={() => setPicked(p => togglePick(p || [], r.name))}/>)}
        </fieldset>
        <div className="od-row">
          <Button appearance="primary" icon={<Play24Filled/>} disabled={!canPlay({ online, favorite, picked: pick, busy })} onClick={play}>
            {busy === 'play' ? 'Starting…' : 'Play'}</Button>
          <span className="field-hint">{pick.length > 1 ? 'The speakers you pick play together.' : ''}</span>
        </div>
      </section>

      <section className="sonos-card" aria-labelledby="sonos-now-title">
        <h2 id="sonos-now-title">Now playing</h2>
        {groups.map(g => {
          const [badge, color] = STATE[g.state] || ['Not answering', 'subtle'];
          const label = `control:${g.lead}`;
          return <article className="sonos-group" key={g.lead} aria-label={g.rooms.map(r => r.name).join(' and ')}>
            <div className="od-row"><strong className="od-fill">{g.rooms.map(r => r.name).join(' + ')}</strong>
              <Badge appearance="tint" color={color}>{badge}</Badge></div>
            <p className="sonos-track">{g.title ? <>{g.title}{g.artist ? <span className="sonos-muted"> · {g.artist}</span> : null}</> : <span className="sonos-muted">Nothing playing</span>}</p>
            <div className="sonos-transport">
              <Button appearance="subtle" icon={<Previous24Regular/>} aria-label={`Previous track in ${g.lead}`} disabled={!!busy}
                onClick={() => run(label, () => sonos.control(g.lead, 'previous'))}/>
              <Button appearance="subtle" icon={g.state === 'playing' ? <Pause24Filled/> : <Play24Filled/>} disabled={!!busy}
                aria-label={`${g.state === 'playing' ? 'Pause' : 'Play'} in ${g.lead}`}
                onClick={() => run(label, () => sonos.control(g.lead, toggleAction(g.state)))}/>
              <Button appearance="subtle" icon={<Next24Regular/>} aria-label={`Next track in ${g.lead}`} disabled={!!busy}
                onClick={() => run(label, () => sonos.control(g.lead, 'next'))}/>
            </div>
            {g.rooms.map(r => { const level = levels[r.name] ?? r.volume; return <div className="sonos-volume" key={r.name}>
              <span>{r.name}</span>
              <Slider min={0} max={volumeMax(ceiling, r.volume)} value={level ?? 0} disabled={r.volume === null}
                aria-label={`${r.name} volume`} onChange={(e, d) => slide(r.name, d.value)}/>
              <span className="sonos-level">{level ?? '–'}</span>
            </div>; })}
          </article>;
        })}
        <p className="field-hint">Volume stops at {ceiling}, the highest ZenBot may set. <button type="button" className="text-link" onClick={openSonosSettings}>Change it in Settings</button>.</p>
      </section>
    </div>}
  </>;
}

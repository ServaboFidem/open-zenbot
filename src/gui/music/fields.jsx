import React, { useMemo, useState } from 'react';
import { Input } from '@fluentui/react-components';
import { Dismiss24Regular, Search24Regular } from '@fluentui/react-icons';
import { DAYS } from './helpers.js';

// Focus genres: chips for the chosen ones and a search over the atlas's genre names.
// props: focus, setFocus, names (every atlas genre; empty while the atlas is not loaded)
export function GenrePicker({ focus, setFocus, names }) {
  const [query, setQuery] = useState('');
  const matches = useMemo(() => {
    const q = query.trim().toLocaleLowerCase();
    return q ? names.filter(n => n.toLocaleLowerCase().includes(q) && !focus.includes(n)).slice(0, 12) : [];
  }, [query, focus, names]);
  const add = name => { setFocus([...focus, name]); setQuery(''); };
  return <div className="od-stack" style={{ '--od-gap': 'var(--s4)' }}>
    <div className="selected-genres">{focus.map(g => <span className="genre-chip" key={g}><span>{g}</span><button aria-label={`Remove ${g}`} onClick={() => setFocus(focus.filter(x => x !== g))}><Dismiss24Regular className="od-icon" aria-hidden="true"/></button></span>)}</div>
    <div className="field"><label htmlFor="genre-search">Add a focus genre</label><Input id="genre-search" value={query} onChange={(_, d) => setQuery(d.value)} contentBefore={<Search24Regular className="od-icon" aria-hidden="true"/>} placeholder={names.length ? `Search ${names.length.toLocaleString()} genres` : 'The genre atlas is not loaded yet'} disabled={!names.length} onKeyDown={e => { if (e.key === 'Enter' && matches[0]) { e.preventDefault(); add(matches[0]); } }}/><span className="hint">Your focus can be narrower than your collection. That&rsquo;s normal.</span></div>
    {query && <div className="genre-options" aria-live="polite">{matches.length ? matches.map(n => <button key={n} onClick={() => add(n)}>{n}</button>) : <p className="small muted">No new matches. Try a shorter genre name.</p>}</div>}
  </div>;
}

// The digest form. value: {day, time, count, era, obscurity (percent)}, schedule: the managed routine's schedule or null
// (no routine yet: day and time are hidden).
export function DigestFields({ value, onChange, schedule }) {
  const update = (key, val) => onChange({ ...value, [key]: val });
  const kind = schedule?.kind;
  return <>
    <div className="form-grid">
      {kind === 'weekly' && <div className="field"><label htmlFor="digest-day">Day</label><select id="digest-day" value={value.day} onChange={e => update('day', Number(e.target.value))}>{DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}</select></div>}
      {(kind === 'weekly' || kind === 'daily') && <div className="field"><label htmlFor="digest-time">Time</label><input id="digest-time" type="time" value={value.time} onChange={e => update('time', e.target.value)}/><span className="hint">Local time on this computer</span></div>}
      {kind === 'interval' && <p className="small muted">This digest runs every {schedule.hours} hours. Change that on the Routines page.</p>}
      <div className="field"><label htmlFor="digest-count">Albums per digest</label><select id="digest-count" value={value.count} onChange={e => update('count', Number(e.target.value))}>{Array.from({ length: 10 }, (_, i) => i + 1).map(n => <option key={n} value={n}>{n} {n === 1 ? 'album' : 'albums'}</option>)}</select></div>
    </div>
    <div className="field"><label htmlFor="era-mix">Era mix</label><input type="range" id="era-mix" min="0" max="100" step="5" value={value.era} onChange={e => update('era', Number(e.target.value))} aria-valuetext={`${100 - value.era}% classics, ${value.era}% newer discoveries`}/><div className="range-labels"><span>More classics</span><strong>{100 - value.era}% / {value.era}%</strong><span>More new</span></div></div>
    <div className="field"><label htmlFor="obscurity">How far off the beaten path?</label><input type="range" id="obscurity" min="0" max="100" step="5" value={value.obscurity} onChange={e => update('obscurity', Number(e.target.value))} aria-valuetext={`${value.obscurity} out of 100 toward deep cuts`}/><div className="range-labels"><span>Well-known</span><strong>{value.obscurity}/100</strong><span>Deep cuts</span></div></div>
  </>;
}

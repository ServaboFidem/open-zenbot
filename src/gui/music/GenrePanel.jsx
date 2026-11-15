import React, { useEffect, useState } from 'react';
import { Button, Spinner } from '@fluentui/react-components';
import { Dismiss24Regular } from '@fluentui/react-icons';

const GROUPS = [['characteristic', 'Characteristic'], ['frequent', 'Often'], ['occasional', 'Sometimes']];

// The genre explorer: Soundlore's detail for one genre plus what the collection holds in it.
// props: name, music (api), suggestion (a map.related row or undefined), onClose, onSelect, onDiscover, discovering, canDiscover
export default function GenrePanel({ name, music, suggestion, onClose, onSelect, onDiscover, discovering, canDiscover }) {
  const [detail, setDetail] = useState(null);
  const [error, setError] = useState('');
  const [trait, setTrait] = useState(null);
  useEffect(() => {
    let alive = true;
    setDetail(null); setError(''); setTrait(null);
    music.genre(name).then(d => { if (alive) setDetail(d); }).catch(e => { if (alive) setError(e.message || 'Could not load this genre.'); });
    return () => { alive = false; };
  }, [name]);
  const made = detail?.made_of || {};
  const traitById = new Map(Object.values(made).flat().map(t => [t.id, t]));
  const share = detail?.holdings?.share || 0;
  return <aside className="genre-panel" aria-labelledby="genre-panel-title">
    <div className="genre-panel-header"><div><div className="eyebrow">Genre explorer</div><h2 id="genre-panel-title">{name}</h2><p className="muted small">{detail?.family}</p></div><Button className="close" appearance="subtle" icon={<Dismiss24Regular className="od-icon"/>} aria-label="Close genre panel" onClick={onClose}/></div>
    <div className="genre-panel-body">
      {error && <p className="field-error" role="alert">{error}</p>}
      {!detail && !error && <Spinner size="small" label="Loading genre"/>}
      {detail && <>
        <div className="ownership">{share > 0 ? <><strong>{(share * 100).toFixed(2)}%</strong> of your mapped taste profile{detail.holdings.owned ? ` · ${detail.holdings.owned} ${detail.holdings.owned === 1 ? 'record' : 'records'}` : ''}</> : <>A new corner of your collection.<br/><strong>No mapped holdings</strong> in this genre.</>}{suggestion && <p className="tiny" style={{ marginTop: 'var(--s2)' }}>Because you have {suggestion.because} · {suggestion.temperature}/100 closeness</p>}</div>
        <dl className="genre-facts">
          {detail.area?.length > 0 && <div><dt>Area</dt><dd>{detail.area.join(', ')}</dd></div>}
          {detail.broader?.length > 0 && <div><dt>Broader genre</dt><dd>{detail.broader.join(', ')}</dd></div>}
          {detail.influenced_by?.length > 0 && <div><dt>Influenced by</dt><dd>{detail.influenced_by.join(' · ')}</dd></div>}
        </dl>
        {traitById.size > 0 && <section><h3>What it&rsquo;s made of</h3><p className="tiny muted">Select a trait to see its meaning.</p>{GROUPS.filter(([k]) => made[k]?.length).map(([k, label]) => <div className="trait-group" key={k}><strong>{label}</strong><div className="trait-tags">{made[k].map(t => <button className="trait-button" key={t.id} title={`${t.facet}: ${t.definition}`} aria-pressed={trait?.id === t.id} onClick={() => setTrait(trait?.id === t.id ? null : t)}>{t.label}</button>)}</div></div>)}{trait && <div className="trait-definition" role="status"><strong>{trait.label}</strong><p className="tiny muted">{trait.facet}</p><p>{trait.definition}</p></div>}</section>}
        {detail.closest_genres?.length > 0 && <section><h3>Closest genres</h3><p className="tiny muted">Closeness describes shared musical traits.</p><div className="closest-list">{detail.closest_genres.map(g => <div className="closest-item" key={g.genre}><div className="od-row"><button className="text-button" onClick={() => onSelect(g.genre)}>{g.genre}</button><strong className="od-nowrap">{g.temperature}/100</strong></div>{g.shared?.length > 0 && <p>Shared: {g.shared.map(id => traitById.get(id)?.label || id.replaceAll('_', ' ')).join(', ')}</p>}</div>)}</div></section>}
        {detail.artists?.length > 0 && <section><h3>Artists in this genre</h3><div className="artist-list">{detail.artists.map(a => <span key={a.mbid}>{a.name}</span>)}</div></section>}
      </>}
      <Button appearance="primary" disabled={discovering || !canDiscover} onClick={() => onDiscover(name)}>{discovering ? 'The librarian is digging…' : 'Find me albums in this genre'}</Button>
      <p className="tiny muted">{canDiscover ? 'A one-off request. It will not change your weekly focus; results land in History.' : 'Needs Music turned on and a Discogs token (Settings).'}</p>
    </div>
  </aside>;
}

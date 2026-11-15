import React, { useState } from 'react';
import { Button } from '@fluentui/react-components';
import { Add24Regular, Checkmark24Regular, Play24Regular, Record24Regular, ShieldCheckmark24Regular } from '@fluentui/react-icons';
import { coverSrc, wantNote } from '../music-api.js';
import { listenUrl, REACTION_LABELS } from './helpers.js';

const Icon = ({ as: Component, ...rest }) => <Component className="od-icon" aria-hidden="true" {...rest}/>;
const count = n => (typeof n === 'number' ? n.toLocaleString() : 'an unknown number of');

// The record sleeve when a card has no cover (or the file would not load).
function Cover({ card, base }) {
  const [failed, setFailed] = useState(false);
  const src = coverSrc(base, card.cover_url);
  if (src && !failed) return <img className="cover" src={src} alt={`Cover of ${card.album} by ${card.artist}`} loading="lazy" onError={() => setFailed(true)}/>;
  return <div className="cover-fallback" role="img" aria-label={`Cover unavailable for ${card.album} by ${card.artist}`}><Icon as={Record24Regular}/><span>{card.cover_url ? 'Artwork unavailable' : 'No cover available'}</span></div>;
}

// props: card, base (harness URL), wanting (an add is in flight), wantError (kind from the last failed add),
// onReact(card, reaction|null), onWant(card), onUnwant(card), onListen(card)
export default function AlbumCard({ card, base, wanting, wantError, onReact, onWant, onUnwant, onListen }) {
  const reaction = card.reaction ?? null;
  const wanted = reaction === 'wanted';
  const ob = card.obscurity || {};
  return <article className="album-card" aria-labelledby={`album-${card.id}`}>
    <div className="album-header"><Cover card={card} base={base}/><div><p className="album-artist">{card.artist}</p><h2 className="album-title" id={`album-${card.id}`}>{card.album}</h2><p className="album-meta">{[card.year, card.style].filter(Boolean).join(' · ')}</p><div className="album-tags">{card.era && <span className={`era-tag ${card.era === 'newer discovery' ? 'new' : ''}`}>{card.era}</span>}{ob.label && <span className="obscurity">{ob.label}</span>}</div></div></div>
    <p className="recommendation">{card.why}</p>
    {card.standout_tracks?.length > 0 && <div className="track-section"><span className="section-label">Start with these</span><ol className="track-list">{card.standout_tracks.map((track, i) => <li key={track.title}><span className="track-number">0{i + 1}</span><span>{track.title}</span></li>)}</ol></div>}
    {card.similar_to_owned?.length > 0 && <p className="similar">On your shelf: <strong>{card.similar_to_owned.join(' · ')}</strong></p>}
    <div className="card-bottom">
      <div className="card-actions">
        <Button as="a" appearance="primary" icon={<Icon as={Play24Regular}/>} href={listenUrl(card)} target="_blank" rel="noopener noreferrer" onClick={() => onListen(card)}>Listen</Button>
        <Button icon={<Icon as={wanted ? Checkmark24Regular : Add24Regular}/>} disabled={wanting || wanted} onClick={() => onWant(card)}>{wanting ? 'Adding…' : wanted ? 'Wanted' : wantError ? 'Retry' : 'Want it'}</Button>
        <Button appearance="subtle" aria-pressed={reaction === 'passed'} onClick={() => onReact(card, reaction === 'passed' ? null : 'passed')}>Pass</Button>
        <Button className="minor-action" appearance="subtle" aria-pressed={reaction === 'owned'} onClick={() => onReact(card, reaction === 'owned' ? null : 'owned')}>I own this</Button>
      </div>
      <div className="card-status">{wantError
        ? <span className="field-error" role="alert">{wantNote(wantError)}</span>
        : reaction
          ? <><Icon as={Checkmark24Regular}/><span className={`reaction-text ${reaction}`}>{REACTION_LABELS[reaction] || reaction}</span><button className="text-button" onClick={() => (wanted ? onUnwant(card) : onReact(card, null))}>Undo</button></>
          : <><Icon as={ShieldCheckmark24Regular}/><span>{card.on_wantlist ? 'On your Discogs wantlist' : 'Checked against your shelf and past picks'}</span></>}</div>
      {(ob.discogs_have != null || ob.discogs_want != null) && <details className="obscurity"><summary>Discogs details</summary><p>{count(ob.discogs_have)} collectors own it · {count(ob.discogs_want)} want it.</p></details>}
    </div>
  </article>;
}

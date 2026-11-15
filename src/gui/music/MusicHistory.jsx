import React, { useState } from 'react';
import { Button } from '@fluentui/react-components';
import { Clock24Regular } from '@fluentui/react-icons';
import AlbumCard from './AlbumCard.jsx';
import { digestTitle } from './helpers.js';

const FILTERS = [['all', 'All reactions'], ['wanted', 'Wanted'], ['passed', 'Passed'], ['owned', 'Already owned'], ['listened', 'Listened'], ['none', 'No reaction']];

// Every digest, newest first (weekly and on-request), each card with its reaction.
export default function MusicHistory({ digests, cardProps, hasMore, onMore, loaded }) {
  const [filter, setFilter] = useState('all');
  const keep = c => filter === 'all' || (filter === 'none' ? !c.reaction : c.reaction === filter);
  const groups = digests.map(d => ({ d, cards: d.cards.filter(keep) })).filter(g => g.cards.length);
  const total = groups.reduce((n, g) => n + g.cards.length, 0);
  if (loaded && !digests.length) return <div className="empty-state"><Icon/><h2>Your story starts here.</h2><p>Past digests will appear here as the librarian builds a history.</p></div>;
  return <>
    <div className="history-tools"><div className="field"><label htmlFor="history-reaction">Reaction</label><select id="history-reaction" value={filter} onChange={e => setFilter(e.target.value)}>{FILTERS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div><span className="history-summary">{total} {total === 1 ? 'album' : 'albums'} · reactions can be changed anytime</span></div>
    {groups.length ? groups.map(({ d, cards }) => <section key={d.id}><div className="history-week"><h2>{digestTitle(d)}</h2><p className="small muted">{d.note || `${d.cards.length} ${d.cards.length === 1 ? 'pick' : 'picks'}`}</p></div><div className="digest-grid">{cards.map(card => <AlbumCard key={card.id} card={card} {...cardProps(card)}/>)}</div></section>)
      : <div className="empty-state"><Icon/><h2>No albums with that reaction.</h2><p>Try another filter, or return to this week&rsquo;s digest to leave a reaction.</p><Button onClick={() => setFilter('all')}>Show everything</Button></div>}
    {hasMore && <div className="save-row"><Button onClick={onMore}>Load earlier digests</Button></div>}
  </>;
}
const Icon = () => <Clock24Regular className="od-icon" aria-hidden="true"/>;

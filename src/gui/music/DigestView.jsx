import React from 'react';
import { Button, ProgressBar } from '@fluentui/react-components';
import { ArrowRight24Regular, Bot24Regular, Record24Regular } from '@fluentui/react-icons';
import AlbumCard from './AlbumCard.jsx';
import { digestTitle, scheduleLabel, whenLabel } from './helpers.js';

const Icon = ({ as: Component, ...rest }) => <Component className="od-icon" aria-hidden="true" {...rest}/>;

// This week: the newest weekly digest, or the reason there is none yet.
// props: digest, status, routine, cardProps (passed to each AlbumCard), digging, onFirstDigest, onSettings, onHistory
export default function DigestView({ digest, status, routine, cardProps, digging, onFirstDigest, onSettings, onHistory, loaded }) {
  const focus = status?.settings?.focus || [];
  const next = routine?.enabled ? whenLabel(routine.next_run) : '';
  const needsToken = status?.digest === 'needs_token';
  const context = <div className="digest-context"><div><span className="section-label">Your focus</span><div className="focus-list">{focus.length ? focus.map(g => <span key={g} className="focus-chip">{g}</span>) : <span className="muted small">No focus genres yet</span>}</div></div><div className="next-digest">Next digest<strong>{next || (routine ? 'Schedule paused' : '—')}</strong></div></div>;

  if (needsToken && !digest) return <>{context}<div className="empty-state"><Icon as={Record24Regular}/><h2>Paste your Discogs token to get weekly picks</h2><p>Your taste map works without one. Weekly picks check every album against your collection through Discogs, which needs a personal access token.</p><Button appearance="primary" onClick={onSettings}>Add a token</Button></div></>;
  if (!loaded) return <>{context}<div className="empty-state" aria-busy="true" role="status"><Icon as={Bot24Regular}/><p>Loading your digests…</p></div></>;
  if (digging && !digest) return <>{context}<div className="empty-state" aria-busy="true" role="status"><Icon as={Bot24Regular}/><h2>The librarian is digging.</h2><p>Checking your shelf, previous picks, and track lists. A digest usually takes a minute or two. You can leave this page; it will show up here.</p><div className="progress"><ProgressBar aria-label="Making your digest"/></div></div></>;
  if (!digest) return <>{context}<div className="empty-state"><Icon as={Record24Regular}/><h2>A few good records are worth the wait.</h2><p>{routine ? `Your first digest arrives ${scheduleLabel(routine)}.` : 'Turn the weekly digest on in Settings to get one every week.'}{status?.digest === 'on' ? ' Want one now?' : ''}</p>{status?.digest === 'on' && <Button appearance="primary" disabled={!status.routine_id} onClick={onFirstDigest}>Make my first digest</Button>}{status?.digest !== 'on' && <Button appearance="primary" onClick={onSettings}>Open settings</Button>}</div></>;

  return <>
    {context}
    <div className="digest-intro"><h2>{digest.cards.length === 1 ? 'One record' : `${digest.cards.length} records`} for your next listening session.</h2><p className="muted small">{digest.note || `${digestTitle(digest)}. Picked with your collection in mind. Take your time with them.`}</p></div>
    <div className="digest-grid">{digest.cards.map(card => <AlbumCard key={card.id} card={card} {...cardProps(card)}/>)}</div>
    <p className="data-note">Listening opens the album on YouTube Music in your browser. Reactions teach next week&rsquo;s picks.</p>
    <div className="save-row" style={{ marginTop: 'var(--s5)' }}><Button appearance="subtle" onClick={onHistory}>View digest history <Icon as={ArrowRight24Regular}/></Button></div>
  </>;
}

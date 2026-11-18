import React, { useState } from 'react';
import { Button } from '@fluentui/react-components';
import { CalendarLtr24Regular, Checkmark24Regular, Open24Regular } from '@fluentui/react-icons';
import { canAdd, whenLabel, whereLabel } from './discover.js';

export const NO_DATE = 'This listing has no usable date.';
const EVENTBRITE = 'https://www.eventbrite.com/';

// The listing's link only when it is an Eventbrite page; anything else gets no link.
export const eventLink = card => (typeof card.url === 'string' && card.url.startsWith(EVENTBRITE) ? card.url : '');

// The picture, or a plain tile when the card has none or the file would not load.
function Picture({ card, src }) {
  const [failed, setFailed] = useState(false);
  if (src && !failed) return <img className="discover-picture" src={src} alt="" loading="lazy" onError={() => setFailed(true)}/>;
  return <div className="discover-picture-fallback" role="img" aria-label="No picture for this event"><CalendarLtr24Regular className="od-icon" aria-hidden="true"/></div>;
}

// props: card, src (the picture's address, '' for none), adding (an add is in flight), onOpen(card), onAdd(card)
// Every string of the card is drawn as text.
export default function EventCard({ card, src, adding, onOpen, onAdd }) {
  const added = card.state === 'added';
  const link = eventLink(card);
  const addable = canAdd(card);
  const where = whereLabel(card);
  return <article className="discover-card" aria-labelledby={`event-${card.id}`}>
    <Picture card={card} src={src}/>
    <h3 className="discover-name" id={`event-${card.id}`}>{card.name}</h3>
    <p className="discover-when">{whenLabel(card)}</p>
    {where && <p className="discover-where">{where}</p>}
    {card.why && <p className="discover-why"><strong>Why:</strong> {card.why}</p>}
    <div className="discover-actions">
      {link && <Button as="a" appearance="primary" icon={<Open24Regular className="od-icon"/>} href={link} target="_blank" rel="noopener noreferrer" onClick={() => onOpen(card)}>View on Eventbrite</Button>}
      {added
        ? <Button disabled icon={<Checkmark24Regular className="od-icon"/>}>On your calendar</Button>
        : <Button disabled={adding || !addable} onClick={() => onAdd(card)}>{adding ? 'Adding…' : 'Add'}</Button>}
    </div>
    {!added && !addable && <p className="events-muted">{NO_DATE}</p>}
  </article>;
}

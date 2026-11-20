import React, { useState } from 'react';
import { Button } from '@fluentui/react-components';
import { CalendarLtr24Regular, Checkmark24Regular } from '@fluentui/react-icons';
import { addTimes, canAdd, whenLabel, whereLabel } from './discover.js';
import { readLabel } from './all-events.js';
import { NO_DATE } from './EventCard.jsx';

export const BIRTHDAY_NUDGE = 'Nudge them in both chat and feed a week before their birthday.';
export const OFFLINE_TEXT = 'The harness is offline.';
export const NOT_READY_TEXT = 'The Facebook helper is not ready yet.';

// What the helper status line says from props only: last read, error, and ready.
// status: { lastRead (UTC instant or ''), error ('' when clean), ready, refreshing }.
export function helperStatusText(status, { online = true, now = Date.now() } = {}) {
  if (!online) return OFFLINE_TEXT;
  const s = status || {};
  if (s.error) {
    const fresh = s.lastRead ? ` Showing what was read (${readLabel(s.lastRead, now)}).` : '';
    return `${s.error}${fresh}`;
  }
  if (!s.ready) return NOT_READY_TEXT;
  if (s.refreshing) return 'Reading Facebook events…';
  return readLabel(s.lastRead, now);
}

// The freshness + error line for the helper. Props only: status, online, now.
export function HelperStatus({ status, online = true, now = Date.now() }) {
  const text = helperStatusText(status, { online, now });
  const isError = !!(status && status.error) || !online;
  return <p className="events-muted" role={isError ? 'alert' : 'status'}>{text}</p>;
}

// One Facebook invite row: the listing's own time and place as text, Add through
// the existing pool add route (discover.addEvent), never a new path.
// props: invite, adding (an add is in flight), onAdd(invite)
export function InviteRow({ invite, adding, onAdd }) {
  const added = invite.state === 'added';
  const addable = canAdd(invite);
  const where = whereLabel(invite);
  return <article className="discover-card" aria-labelledby={`fb-invite-${invite.id}`}>
    <h3 className="discover-name" id={`fb-invite-${invite.id}`}>{invite.name}</h3>
    <p className="discover-when">{whenLabel(invite)}</p>
    {where && <p className="discover-where">{where}</p>}
    <div className="discover-actions">
      {added
        ? <Button disabled icon={<Checkmark24Regular className="od-icon" />}>On your calendar</Button>
        : <Button disabled={adding || !addable} onClick={() => onAdd(invite)}>{adding ? 'Adding…' : 'Add'}</Button>}
    </div>
    {!added && !addable && <p className="events-muted">{NO_DATE}</p>}
  </article>;
}

// One approval card: an RSVP or personal event held for approval. Approve adds
// through the existing card add route (discover.add); dismiss uses the existing
// card dismiss route (discover.dismiss). Every string is drawn as text.
// props: card, adding, dismissing, onApprove(card), onDismiss(card)
export function ApprovalRow({ card, adding, dismissing, onApprove, onDismiss }) {
  const added = card.state === 'added';
  const addable = canAdd(card);
  const where = whereLabel(card);
  return <article className="discover-card" aria-labelledby={`fb-approval-${card.id}`}>
    <h3 className="discover-name" id={`fb-approval-${card.id}`}>{card.name}</h3>
    <p className="discover-when">{whenLabel(card)}</p>
    {where && <p className="discover-where">{where}</p>}
    {card.why && <p className="discover-why"><strong>Why:</strong> {card.why}</p>}
    <div className="discover-actions">
      {added
        ? <Button disabled icon={<Checkmark24Regular className="od-icon" />}>On your calendar</Button>
        : <Button appearance="primary" disabled={adding || !addable} onClick={() => onApprove(card)}>{adding ? 'Adding…' : 'Approve'}</Button>}
      {!added && <Button disabled={dismissing} onClick={() => onDismiss(card)}>Dismiss</Button>}
    </div>
    {!added && !addable && <p className="events-muted">{NO_DATE}</p>}
  </article>;
}

// One birthday row: a nudge, never an add. The nudge names both chat and feed.
// props: person { id, name, date, daysUntil }
export function BirthdayRow({ person }) {
  const when = Number.isInteger(person.daysUntil)
    ? (person.daysUntil <= 0 ? 'Today' : `In ${person.daysUntil} day${person.daysUntil === 1 ? '' : 's'}`)
    : '';
  return <article className="discover-card" aria-labelledby={`fb-birthday-${person.id}`}>
    <h3 className="discover-name" id={`fb-birthday-${person.id}`}>{person.name}</h3>
    {person.date && <p className="discover-when">{person.date}{when ? ` — ${when}` : ''}</p>}
    <p className="events-muted">{BIRTHDAY_NUDGE}</p>
  </article>;
}

// Facebook invites: every event the helper saw, to add through the pool route.
// props: invites, api (createApi), notify(text), onChanged(invite)
export function InvitesPanel({ invites, api, notify, onChanged }) {
  const discover = api?.discover;
  const [adding, setAdding] = useState(() => new Set());
  const list = Array.isArray(invites) ? invites : [];
  const add = async invite => {
    const times = addTimes(invite);
    if (!times) { notify(NO_DATE); return; }
    setAdding(s => new Set(s).add(invite.id));
    try {
      await discover.addEvent(invite.id, times);
      notify('Added to your calendar.');
      if (onChanged) onChanged({ ...invite, state: 'added' });
    } catch (err) {
      notify(err?.message || 'Something went wrong.');
    } finally {
      setAdding(s => { const n = new Set(s); n.delete(invite.id); return n; });
    }
  };
  if (!list.length) return <p className="events-muted">No Facebook invites.</p>;
  return <div className="discover-grid">{list.map(invite =>
    <InviteRow key={invite.id} invite={invite} adding={adding.has(invite.id)} onAdd={add} />)}</div>;
}

// Approval cards: RSVP and personal events held for approval.
// props: approvals, api (createApi), notify(text), onChanged(card)
export function ApprovalsPanel({ approvals, api, notify, onChanged }) {
  const discover = api?.discover;
  const [adding, setAdding] = useState(() => new Set());
  const [dismissing, setDismissing] = useState(() => new Set());
  const list = Array.isArray(approvals) ? approvals : [];
  const approve = async card => {
    const times = addTimes(card);
    if (!times) { notify(NO_DATE); return; }
    setAdding(s => new Set(s).add(card.id));
    try {
      await discover.add(card.id, times);
      notify('Added to your calendar.');
      if (onChanged) onChanged({ ...card, state: 'added' });
    } catch (err) {
      notify(err?.message || 'Something went wrong.');
    } finally {
      setAdding(s => { const n = new Set(s); n.delete(card.id); return n; });
    }
  };
  const dismiss = async card => {
    setDismissing(s => new Set(s).add(card.id));
    try {
      await discover.dismiss(card.id);
      if (onChanged) onChanged({ ...card, state: 'dismissed' });
    } catch (err) {
      notify(err?.message || 'Something went wrong.');
    } finally {
      setDismissing(s => { const n = new Set(s); n.delete(card.id); return n; });
    }
  };
  if (!list.length) return <p className="events-muted">Nothing waiting for approval.</p>;
  return <div className="discover-grid">{list.map(card =>
    <ApprovalRow key={card.id} card={card} adding={adding.has(card.id)} dismissing={dismissing.has(card.id)} onApprove={approve} onDismiss={dismiss} />)}</div>;
}

// Birthdays: the week's nudges, each naming both chat and feed.
// props: birthdays
export function BirthdaysPanel({ birthdays }) {
  const list = Array.isArray(birthdays) ? birthdays : [];
  if (!list.length) return <p className="events-muted">No birthdays this week.</p>;
  return <div className="discover-grid">{list.map(person =>
    <BirthdayRow key={person.id} person={person} />)}</div>;
}

// The Facebook dashboard: invites, approval cards, birthdays, and helper status.
// All reads come through props (invites, approvals, birthdays, status); all writes
// go through the existing card and pool add routes (discover.add, discover.addEvent)
// and the existing card dismiss route (discover.dismiss). Nothing here shows a disk
// path or a key.
// props: invites, approvals, birthdays, status { lastRead, error, ready, refreshing },
// online, api (createApi), notify(text), now (ms), onChanged(item)
export default function FacebookPanels({ invites, approvals, birthdays, status, online = true, api, notify = () => {}, now = Date.now(), onChanged }) {
  return <section className="events-discover" aria-label="Facebook events">
    <div className="discover-head">
      <div><h2>Facebook</h2></div>
    </div>
    <HelperStatus status={status} online={online} now={now} />
    <h3>Invites</h3>
    {api ? <InvitesPanel invites={invites} api={api} notify={notify} onChanged={onChanged} />
      : <p className="events-muted">No Facebook invites.</p>}
    <h3>Waiting for approval</h3>
    {api ? <ApprovalsPanel approvals={approvals} api={api} notify={notify} onChanged={onChanged} />
      : <p className="events-muted">Nothing waiting for approval.</p>}
    <h3>Birthdays</h3>
    <BirthdaysPanel birthdays={birthdays} />
    {!online && <p className="events-muted"><CalendarLtr24Regular className="od-icon" aria-hidden="true" />Connect to see Facebook events.</p>}
  </section>;
}

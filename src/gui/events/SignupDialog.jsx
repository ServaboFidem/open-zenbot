import React from 'react';
import { canAdd } from './discover.js';
import { NO_DATE } from './EventCard.jsx';
import { Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle } from '@fluentui/react-components';

// "Did you sign up?" after the user followed a card's link (or a pool event's, from the All Events panel) and
// came back. props: card (a card or a pool event), busy, onYes(card), onNotYet(card), onNever(card): "Not for
// me" is offered only with onNever (a card can be dismissed; a pool event cannot).
export default function SignupDialog({ card, busy, onYes, onNotYet, onNever }) {
  return <Dialog open onOpenChange={(_, d) => { if (!d.open && !busy) onNotYet(card); }}>
    <DialogSurface className="events-dialog">
      <DialogBody>
        <DialogTitle>Did you sign up for {card.name}?</DialogTitle>
        <DialogContent><p className="events-muted">If you did, it can go on My Calendar.</p>{!canAdd(card) && <p className="events-muted">{NO_DATE}</p>}</DialogContent>
        <DialogActions>
          {onNever && <Button disabled={busy} onClick={() => onNever(card)}>Not for me</Button>}
          <Button disabled={busy} onClick={() => onNotYet(card)}>Not yet</Button>
          <Button appearance="primary" disabled={busy || !canAdd(card)} onClick={() => onYes(card)}>{busy ? 'Adding…' : 'Yes, add it'}</Button>
        </DialogActions>
      </DialogBody>
    </DialogSurface>
  </Dialog>;
}

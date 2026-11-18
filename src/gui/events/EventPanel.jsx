import React, { useEffect, useRef, useState } from 'react';
import { Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Spinner } from '@fluentui/react-components';
import { Checkmark24Regular, Open24Regular } from '@fluentui/react-icons';
import { Picture, NO_DATE } from './EventCard.jsx';
import SignupDialog from './SignupDialog.jsx';
import { addTimes, cardLink, CREDIT, signupStep, whenLabel } from './discover.js';
import { panelCredit, SOURCE_NAMES } from './all-events.js';

export const GONE = 'This event is no longer listed.';

const names = e => (Array.isArray(e?.category_names) ? e.category_names.filter(n => typeof n === 'string' && n) : []);
const text = v => (typeof v === 'string' ? v : '');

// One event of the pool, opened from the All Events grid: asks the harness for the whole row, shows its picture
// and details, its link (cardLink's rule), and Add to calendar. Following the link and coming back asks "Did you
// sign up?" as a card does. Every string of the listing is drawn as text.
// props: api (createApi), event (the grid's slim row), onClose(), onAdded(id), notify(text)
export default function EventPanel({ api, event, onClose, onAdded, notify }) {
  const discover = api.discover;
  const [full, setFull] = useState(null);
  const [phase, setPhase] = useState('loading');   // loading | ready | gone | error
  const [error, setError] = useState('');
  const [adding, setAdding] = useState(false);
  const [asking, setAsking] = useState(false);
  const pending = useRef(null);   // the event whose link was followed (signupStep)
  const fullRef = useRef(null);
  const mounted = useRef(true);
  fullRef.current = full;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const load = async () => {
    setPhase('loading');
    try {
      const row = await discover.event(event.id);
      if (!mounted.current) return;
      setFull(row); setPhase('ready');
    } catch (err) {
      if (!mounted.current) return;
      if (err?.status === 404) { setPhase('gone'); return; }
      setError(err?.message || 'This event could not be loaded.'); setPhase('error');
    }
  };
  useEffect(() => { load(); }, [event.id]);   // once per opened event

  // "Did you sign up?": the window gets focus again after the link was followed
  useEffect(() => {
    const back = () => {
      const step = signupStep(pending.current, { type: 'focus' });
      pending.current = step.pending;
      const now = fullRef.current;
      if (step.ask && now && step.ask === now.id && now.state !== 'added') setAsking(true);
    };
    const visible = () => { if (document.visibilityState === 'visible') back(); };
    window.addEventListener('focus', back);
    document.addEventListener('visibilitychange', visible);
    return () => { window.removeEventListener('focus', back); document.removeEventListener('visibilitychange', visible); };
  }, []);

  const answered = () => { pending.current = signupStep(pending.current, { type: 'answered' }).pending; setAsking(false); };
  const opened = () => { pending.current = signupStep(pending.current, { type: 'opened', cardId: full.id }).pending; };
  const add = async () => {
    const times = addTimes(full);
    if (!times) { notify(NO_DATE); return false; }
    setAdding(true);
    try {
      const r = await discover.addEvent(full.id, times);
      if (mounted.current) setFull(r.event);
      onAdded(full.id);
      notify('Added to your calendar.');
      return true;
    } catch (err) {
      notify(err?.message || 'Something went wrong.');
      return false;
    } finally { if (mounted.current) setAdding(false); }
  };
  const yes = async () => { if (await add()) answered(); };

  let title = text(event.name) || 'Event';
  let content;
  let actions = <Button onClick={onClose}>Close</Button>;
  if (phase === 'loading') {
    content = <Spinner size="small" label="Loading…"/>;
  } else if (phase === 'gone') {
    content = <p>{GONE}</p>;
    actions = <Button appearance="primary" onClick={onClose}>OK</Button>;
  } else if (phase === 'error') {
    content = <p className="events-error" role="alert">{error}</p>;
    actions = <><Button onClick={load}>Try again</Button><Button onClick={onClose}>Close</Button></>;
  } else {
    title = text(full.name) || title;
    const link = cardLink(full);
    const added = full.state === 'added';
    const addable = addTimes(full) !== null;
    const where = [text(full.venue), text(full.address)].filter(Boolean).join(', ') || text(full.city);
    const cats = names(full);
    const source = Object.hasOwn(SOURCE_NAMES, full.source) ? SOURCE_NAMES[full.source] : text(full.source);
    content = <>
      <Picture src={discover.eventImageSrc(api.baseUrl, full)}/>
      <dl className="events-facts">
        <div><dt>When</dt><dd>{whenLabel(full)}</dd></div>
        {where && <div><dt>Where</dt><dd>{where}</dd></div>}
        {cats.length > 0 && <div><dt>{cats.length === 1 ? 'Category' : 'Categories'}</dt><dd>{cats.join(', ')}</dd></div>}
        {source && <div><dt>Source</dt><dd>{source}</dd></div>}
      </dl>
      {text(full.summary) && <p className="events-description">{full.summary}</p>}
      {!added && !addable && <p className="events-muted">{NO_DATE}</p>}
      {panelCredit(full) && <p className="events-muted"><a href={CREDIT.href} target="_blank" rel="noopener noreferrer">{CREDIT.text}</a></p>}
    </>;
    actions = <>
      {link.href && <Button as="a" icon={<Open24Regular className="od-icon"/>} href={link.href} target="_blank" rel="noopener noreferrer" onClick={opened}>{link.label}</Button>}
      {added
        ? <Button disabled icon={<Checkmark24Regular className="od-icon"/>}>On your calendar</Button>
        : addable && <Button appearance="primary" disabled={adding} onClick={add}>{adding ? 'Adding…' : 'Add to calendar'}</Button>}
      <Button onClick={onClose}>Close</Button>
    </>;
  }

  // the sign-up question is a dialog nested in the panel's surface, so it stacks over the panel and Escape
  // closes it before the panel
  return <Dialog open onOpenChange={(_, d) => { if (!d.open && !adding && !asking) onClose(); }}>
    <DialogSurface className="events-dialog" aria-busy={phase === 'loading'}>
      <DialogBody>
        <DialogTitle>{title}</DialogTitle>
        <DialogContent>{content}</DialogContent>
        <DialogActions>{actions}</DialogActions>
      </DialogBody>
      {asking && full && <SignupDialog card={full} busy={adding} onYes={yes} onNotYet={answered}/>}
    </DialogSurface>
  </Dialog>;
}

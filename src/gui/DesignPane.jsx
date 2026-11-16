import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Button, Dropdown, Input, Option } from '@fluentui/react-components';
import { Comment20Regular, Dismiss16Regular, PanelRightContract20Regular, PanelRightExpand20Regular } from '@fluentui/react-icons';
import { acceptPick, acceptReady, addPick, MAX_PICKS, studioFrameUrl } from './design.js';

// The docked design pane in a studio bot's conversation: frames a design revision (the newest unless
// the menu picks another) with the harness's bridge. In Comment mode a click in the frame comes back as
// a pick; the pane appends it to `picks` and sends the numbered pins back. The framed page is made by a
// bot: every message goes through acceptReady/acceptPick, and a pick only ever becomes a chip.
const post = (frame, msg) => { try { frame.current?.contentWindow?.postMessage(msg, '*'); } catch { /* frame gone */ } };  // opaque origin: '*' is the only target
const versionLabel = (r, many) => many ? `${r.title} · Version ${r.version}` : `Version ${r.version}`;

export default function DesignPane({ revisions, urlFor, picks, onPicks, disabled }) {
  const newest = revisions.at(-1);
  const [shownId, setShownId] = useState(null);  // null follows the newest
  const [open, setOpen] = useState(true);
  const [loads, setLoads] = useState(0);         // ready signals from the current frame; 0 until its script runs
  const [loaded, setLoaded] = useState(false);   // the frame's load event; loaded with no ready means no bridge (over 5 MB)
  const [pick, setPick] = useState(false);
  const frame = useRef(null);
  const shown = revisions.find(r => r.attId === shownId) || newest;
  const onNewest = shown.attId === newest.attId;
  const src = studioFrameUrl(urlFor(shown.attId));
  const ready = loads > 0;
  const canPick = onNewest && ready && !disabled;
  const full = picks.length >= MAX_PICKS;
  const active = pick && canPick;
  const latest = useRef({}); latest.current = { active, picks, onPicks };
  const why = disabled ? 'Wait for the task to finish before commenting.'
    : !onNewest ? `Comments go on the newest version. Switch to ${versionLabel(newest, false)} to comment.`
    : !ready ? (loaded ? 'Picking is off for this file: it is too large to comment on.' : 'The design is still loading.')
    : full ? `${MAX_PICKS} comments is the most one message can carry. Send these first.`
    : 'Click elements in the design to comment on them';
  const many = new Set(revisions.map(r => r.design)).size > 1;
  const pins = onNewest ? picks.map((p, i) => ({ n: i + 1, elementId: p.elementId, selector: p.selector })) : [];
  const pinsKey = JSON.stringify(pins);

  // A layout effect runs before the frame can post anything, so no message from it is missed.
  useLayoutEffect(() => {
    const onMessage = e => {
      const win = frame.current?.contentWindow;
      if (acceptReady(e, win)) { setLoads(n => n + 1); return; }
      const p = acceptPick(e, win), l = latest.current;
      if (!p || !l.active) return;
      const next = addPick(l.picks, p);
      if (next !== l.picks) { l.picks = next; l.onPicks(next); }  // a second pick before the next render builds on the first
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);
  // A new frame (another revision, a new conversation, the pane shown again) starts unready, not picking.
  useLayoutEffect(() => { setLoads(0); setLoaded(false); setPick(false); }, [src, open]);
  useEffect(() => { setShownId(null); }, [newest.attId]);  // a new revision brings the pane to it
  useEffect(() => { if (!canPick) setPick(false); }, [canPick]);
  useEffect(() => { if (ready) post(frame, { zen: 'mode', pick: active }); }, [active, loads]);
  useEffect(() => { if (ready) post(frame, { zen: 'pins', pins }); }, [pinsKey, loads]);

  if (!open) return <section className="design-pane" aria-label="Design"><Button appearance="subtle" icon={<PanelRightExpand20Regular/>} aria-label="Show the design" title="Show the design" onClick={() => setOpen(true)}/></section>;
  return <section className="design-pane open" aria-label="Design">
    <header className="design-pane-head od-row"><strong className="od-fill">{shown.title}</strong><Dropdown className="design-version" aria-label="Version" value={versionLabel(shown, many)} selectedOptions={[shown.attId]} onOptionSelect={(e, d) => d.optionValue && setShownId(d.optionValue === newest.attId ? null : d.optionValue)}>{[...revisions].reverse().map(r => <Option key={r.attId} value={r.attId} text={versionLabel(r, many)}>{versionLabel(r, many)}</Option>)}</Dropdown><Button appearance={active ? 'primary' : 'outline'} icon={<Comment20Regular/>} aria-pressed={active} disabledFocusable={!canPick} title={why} onClick={() => setPick(v => !v)}>Comment</Button><Button appearance="subtle" icon={<PanelRightContract20Regular/>} aria-label="Hide the design" title="Hide the design" onClick={() => setOpen(false)}/></header>
    <iframe key={src} ref={frame} className="design-frame" sandbox="allow-scripts" src={src} onLoad={() => setLoaded(true)} title={`${shown.title}, version ${shown.version}`}/>
  </section>;
}

// Above the composer: one row per pick, its pin number, what was picked and the note to send with it.
export function PickChips({ picks, onPicks }) {
  if (!picks.length) return null;
  const setNote = (i, note) => onPicks(picks.map((p, j) => j === i ? { ...p, note } : p));
  return <ol className="pick-chips" aria-label="Comments on the design">{picks.map((p, i) => <li className="pick-chip od-row" key={i}><span className="pick-n">{i + 1}</span><span className="pick-target" title={p.text || p.selector}>[{p.elementId || p.tag}]</span><Input className="od-fill" size="small" value={p.note} maxLength={1000} placeholder="What should change here?" aria-label={`Note for comment ${i + 1}`} onChange={(e, d) => setNote(i, d.value)}/><Button appearance="subtle" size="small" icon={<Dismiss16Regular/>} aria-label={`Remove comment ${i + 1}`} title="Remove" onClick={() => onPicks(picks.filter((_, j) => j !== i))}/></li>)}</ol>;
}

// Under a sent message: the comments it carried, numbered as they were pinned.
export function SentComments({ comments }) {
  return <ol className="pick-chips sent">{comments.map((c, i) => <li className="pick-chip od-row" key={i}><span className="pick-n">{i + 1}</span><span className="pick-target">[{String(c.element_id || c.tag || '')}]</span><span className="od-fill">{String(c.note || '')}</span></li>)}</ol>;
}

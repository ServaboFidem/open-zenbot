import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Button, Dropdown, Input, Option } from '@fluentui/react-components';
import { Comment20Regular, Dismiss16Regular, Edit20Regular, History20Regular, Options20Regular, PanelRightContract20Regular, PanelRightExpand20Regular, SplitVertical20Regular } from '@fluentui/react-icons';
import { acceptPick, acceptReady, addPick, MAX_PICKS, studioFrameUrl } from './design.js';
import { body, canChange, count, EMPTY, frameMessages, HOLD, saveLabel, setImage, setText, setTweak, slotFor, whyNot } from './design-edits.js';
import DesignCompare from './DesignCompare';
import DesignEditor from './DesignEditor';
import DesignPendingBar from './DesignPendingBar';
import TweaksPanel from './TweaksPanel';

// The docked design pane in a studio bot's conversation: frames a design revision (the newest unless the menu picks
// another) with the harness's bridge. Three exclusive modes: Comment (a click in the frame comes back as a pick, which
// becomes a chip), Edit (a pick on a slot opens the editor) and Tweaks (the knobs). Edits and tweaks collect as
// `pending` (the app holds it) and the frame shows them; one save makes a new version. The framed page is made by a
// bot: every message from it goes through acceptReady/acceptPick, and a pick only ever becomes a chip or opens the
// editor on text the harness gave (the slots answer).
const post = (frame, msg) => { try { frame.current?.contentWindow?.postMessage(msg, '*'); } catch { /* frame gone */ } };  // opaque origin: '*' is the only target
const versionLabel = (r, many) => many ? `${r.title} · Version ${r.version}` : `Version ${r.version}`;
const NOT_A_SLOT = "This element can't be edited directly. Comment on it instead.";

export default function DesignPane({ api, convId, revisions, urlFor, picks, onPicks, pending = EMPTY, onPending, onSaved, onStale, disabled }) {
  const newest = revisions.at(-1);
  const [shownId, setShownId] = useState(null);  // null follows the newest
  const [open, setOpen] = useState(true);
  const [loads, setLoads] = useState(0);         // ready signals from the current frame; 0 until its script runs
  const [loaded, setLoaded] = useState(false);   // the frame's load event; loaded with no ready means no bridge (over 5 MB)
  const [mode, setMode] = useState(null);        // null | 'comment' | 'edit' | 'tweaks'
  const [comparing, setComparing] = useState(false);
  const [slots, setSlots] = useState(null);      // GET .../design/slots for the newest version; null until read
  const [slotsWhy, setSlotsWhy] = useState('');  // the harness's sentence when the slots could not be read
  const [editing, setEditing] = useState(null);  // { slot, rect, before }: the editor open on one slot
  const [note, setNote] = useState('');          // a sentence under the frame: a save's refusal, "Nothing changed.", not a slot
  const [saving, setSaving] = useState(false);
  const frame = useRef(null), stage = useRef(null);
  const shown = revisions.find(r => r.attId === shownId) || newest;
  const onNewest = shown.attId === newest.attId;
  const src = studioFrameUrl(urlFor(shown.attId));
  const ready = loads > 0;
  const many = new Set(revisions.map(r => r.design)).size > 1;
  const n = count(pending);
  const canPick = onNewest && ready && !disabled && !comparing;
  const full = picks.length >= MAX_PICKS;
  const commenting = mode === 'comment' && canPick;
  const why = disabled ? 'Wait for the task to finish before commenting.'
    : comparing ? 'Close Compare to comment.'
    : !onNewest ? `Comments go on the newest version. Switch to ${versionLabel(newest, false)} to comment.`
    : !ready ? (loaded ? 'Picking is off for this file: it is too large to comment on.' : 'The design is still loading.')
    : full ? `${MAX_PICKS} comments is the most one message can carry. Send these first.`
    : 'Click elements in the design to comment on them';
  const changeable = canChange({ onNewest, ready, disabled, comparing }) && !!slots;
  const whyChange = whyNot({ onNewest, ready, disabled, comparing, newestLabel: versionLabel(newest, false) }) || (slots ? '' : slotsWhy || 'The design is still loading.');
  const editingOn = mode === 'edit' && changeable, tweaking = mode === 'tweaks' && changeable;
  const restorable = !onNewest && !comparing && shown.design === newest.design;
  const pins = onNewest ? picks.map((p, i) => ({ n: i + 1, elementId: p.elementId, selector: p.selector })) : [];
  const pinsKey = JSON.stringify(pins);
  const latest = useRef({});
  latest.current = { commenting, editingOn, picks, onPicks, slots, pending };

  // A layout effect runs before the frame can post anything, so no message from it is missed.
  useLayoutEffect(() => {
    const onMessage = e => {
      const win = frame.current?.contentWindow;
      if (acceptReady(e, win)) { setLoads(k => k + 1); return; }
      const p = acceptPick(e, win), l = latest.current;
      if (!p) return;
      if (l.commenting) {
        const next = addPick(l.picks, p);
        if (next !== l.picks) { l.picks = next; l.onPicks(next); }  // a second pick before the next render builds on the first
      } else if (l.editingOn) {
        const slot = slotFor(l.slots, p);  // the text shown comes from the slots answer, never from the pick
        setNote(slot ? '' : NOT_A_SLOT);
        setEditing(slot ? { slot, rect: p.rect, before: l.pending } : null);
      }
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, []);
  // A new frame (another revision, a new conversation, the pane shown again, Compare closed) starts unready, in no mode.
  useLayoutEffect(() => { setLoads(0); setLoaded(false); setMode(null); }, [src, open, comparing]);
  useEffect(() => { setShownId(null); }, [newest.attId]);  // a new revision brings the pane to it
  useEffect(() => { if ((mode === 'comment' && !canPick) || ((mode === 'edit' || mode === 'tweaks') && !changeable)) setMode(null); }, [mode, canPick, changeable]);
  useEffect(() => { if (!editingOn) setEditing(null); }, [editingOn]);
  useEffect(() => { setNote(''); }, [mode]);
  useEffect(() => { if (ready) post(frame, { zen: 'mode', pick: commenting || editingOn }); }, [commenting, editingOn, loads]);
  useEffect(() => { if (ready) post(frame, { zen: 'pins', pins }); }, [pinsKey, loads]);
  // Every change to pending, and every new frame, shows the whole pending state again from the saved design.
  useEffect(() => { if (ready && onNewest) for (const m of frameMessages(pending, slots)) post(frame, m); }, [pending, slots, loads, onNewest]);
  // The slots are read for each newest version, and again when a task ends (it may have changed the file).
  useEffect(() => {
    if (disabled) return undefined;
    let current = true;
    setSlots(null); setSlotsWhy('');
    api.designSlots(convId).then(s => {
      if (!current) return;
      setSlots(s);
      if (s?.version !== newest.version) onStale?.();  // the harness made a version the pane has not seen yet
    }).catch(e => { if (current) setSlotsWhy(e?.message || ''); });
    return () => { current = false; };
  }, [convId, newest.attId, disabled]);

  async function save() {
    setSaving(true); setNote('');
    try {
      const r = await api.saveDesignChanges(convId, body(pending, slots.version));
      onPending(EMPTY); setEditing(null);
      if (r?.version == null) setNote('Nothing changed.');
      if (r?.conversation) onSaved(r.conversation);
    } catch (e) { setNote(e?.message || 'The changes could not be saved.'); }  // the changes stay
    finally { setSaving(false); }
  }
  async function restore() {
    if (!window.confirm(`Make Version ${shown.version} the newest version?`)) return;
    setSaving(true); setNote('');
    try {
      const r = await api.restoreDesign(convId, shown.version, newest.version);
      onPending(EMPTY);
      if (r?.version == null) setNote('Nothing changed.');
      if (r?.conversation) onSaved(r.conversation);
    } catch (e) { setNote(e?.message || 'This version could not be restored.'); }
    finally { setSaving(false); }
  }
  const restoreWhy = disabled ? 'Wait for the task to finish before changing the design.' : n ? HOLD : '';
  const modeButton = (key, label, icon, can, title) => <Button appearance={mode === key && can ? 'primary' : 'outline'} icon={icon} aria-pressed={mode === key && can} disabledFocusable={!can} title={title} onClick={() => setMode(m => m === key ? null : key)}>{label}</Button>;

  if (!open) return <section className="design-pane" aria-label="Design"><Button appearance="subtle" icon={<PanelRightExpand20Regular/>} aria-label="Show the design" title="Show the design" onClick={() => setOpen(true)}/></section>;
  return <section className="design-pane open" aria-label="Design">
    <header className="design-pane-head">
      <div className="od-row design-pane-top">
        <strong className="od-fill">{shown.title}</strong>
        {!comparing && <Dropdown className="design-version" aria-label="Version" value={versionLabel(shown, many)} selectedOptions={[shown.attId]} onOptionSelect={(e, d) => d.optionValue && setShownId(d.optionValue === newest.attId ? null : d.optionValue)}>{[...revisions].reverse().map(r => <Option key={r.attId} value={r.attId} text={versionLabel(r, many)}>{versionLabel(r, many)}</Option>)}</Dropdown>}
        {revisions.length > 1 && <Button appearance={comparing ? 'primary' : 'subtle'} icon={<SplitVertical20Regular/>} aria-pressed={comparing} title="Show two versions side by side" onClick={() => { setComparing(v => !v); setEditing(null); }}>Compare</Button>}
        {restorable && <Button appearance="outline" icon={<History20Regular/>} disabledFocusable={!!restoreWhy || saving} title={restoreWhy || `Make Version ${shown.version} the newest version`} onClick={restore}>Restore</Button>}
        {/* Export (Task 9): the Export menu on the version shown goes here. */}
        <Button appearance="subtle" icon={<PanelRightContract20Regular/>} aria-label="Hide the design" title="Hide the design" onClick={() => setOpen(false)}/>
      </div>
      <div className="od-row design-modes" role="toolbar" aria-label="Design tools">
        {modeButton('comment', 'Comment', <Comment20Regular/>, canPick, why)}
        {modeButton('edit', 'Edit', <Edit20Regular/>, changeable, whyChange || 'Click a text or a picture in the design to change it')}
        {modeButton('tweaks', 'Tweaks', <Options20Regular/>, changeable, whyChange || 'Adjust the colours and sizes the Designer made adjustable')}
      </div>
    </header>
    {tweaking && <TweaksPanel knobs={slots.tweaks} values={pending.tweaks} onChange={(name, value) => onPending(setTweak(pending, slots, name, value))} onReset={() => onPending({ ...pending, tweaks: {} })}/>}
    {comparing
      ? <DesignCompare revisions={revisions} urlFor={urlFor} label={r => versionLabel(r, many)}/>
      : <div className="design-stage" ref={stage}>
        <iframe key={src} ref={frame} className="design-frame" sandbox="allow-scripts" src={src} onLoad={() => setLoaded(true)} title={`${shown.title}, version ${shown.version}`}/>
        {editing && <DesignEditor key={editing.slot.id} slot={editing.slot} rect={editing.rect} stage={stage.current}
          text={pending.texts[editing.slot.id] ?? editing.slot.text} picture={pending.images[editing.slot.id]}
          onText={t => onPending(setText(pending, slots, editing.slot.id, t))} onPicture={p => onPending(setImage(pending, editing.slot.id, p))}
          onDone={() => setEditing(null)} onCancel={() => { onPending(editing.before); setEditing(null); }}/>}
      </div>}
    {note && <p className="design-note" role="status">{note}</p>}
    {n > 0 && <DesignPendingBar count={n} label={saveLabel(pending, slots?.version ?? newest.version)} saving={saving} canSave={!!slots && !disabled} why={disabled ? 'Wait for the task to finish before changing the design.' : ''} onSave={save} onDiscard={() => { onPending(EMPTY); setEditing(null); }}/>}
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

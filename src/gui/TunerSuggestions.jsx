import React, { useEffect, useRef, useState } from 'react';
import { Button } from '@fluentui/react-components';
import { anchorId } from './settings-index';
import { LEFT_OUT, attachChosen, cardSource, createLatest, createQueue, doneLine, initialPicks, offeredBots, sameSlugs } from './skilltuner.js';

const POLL_MS = 15000;

// The cards at the top of Settings > Skills. Everything a suggestion carries is untrusted and shown as plain text
// children, never as HTML or Markdown. ctx: { api, online, notify }; onChanged() reads bots, skills and fits again; onDecided() runs after each Approve or Reject (the app reads the waiting count again);
// onDiscovered(slugs) tells the list which skills Remove may act on.
export function TunerSuggestions({ ctx, bots, onChanged, onDiscovered, onDecided }) {
  const { api, online, notify } = ctx;
  const [list, setList] = useState([]);
  const [open, setOpen] = useState({});      // id -> full suggestion (See what changed)
  const [errors, setErrors] = useState({});  // id or skill ref -> sentence
  const [busy, setBusy] = useState(() => new Set());  // ids and skill refs with an action queued or running
  const [chosen, setChosen] = useState([]);  // approved, bots not chosen yet: { skill, fit, picks, dialog }
  const botsRef = useRef(bots); botsRef.current = bots;

  const latest = useRef(createLatest()).current;  // a list read in flight during Approve or Reject is dropped
  const queue = useRef(createQueue()).current;    // Approve, Reject and Done never overlap: Done's skill lists stay current
  const mark = (id, on) => setBusy(b => { const n = new Set(b); if (on) n.add(id); else n.delete(id); return n; });
  // Marked busy at the click, so the button is off while the action waits its turn; cleared only by its own end.
  const queued = (id, fn) => { mark(id, true); return queue.run(fn).finally(() => mark(id, false)); };
  const slugs = useRef([]);
  const load = () => {
    const t = latest.start();
    api.tuner.suggestions().then(l => { if (Array.isArray(l) && latest.fresh(t)) setList(l); }).catch(() => {});
    api.tuner.discovered().then(d => { const n = d?.slugs || []; if (latest.fresh(t) && !sameSlugs(slugs.current, n)) { slugs.current = n; onDiscovered?.(n); } }).catch(() => {});
  };
  useEffect(() => {
    if (!online) return undefined;
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [online]);

  const fail = (id, e) => setErrors(x => ({ ...x, [id]: e?.message || 'Something went wrong.' }));
  const clear = id => setErrors(x => ({ ...x, [id]: '' }));
  async function toggleChanges(s) {
    if (open[s.id]) { setOpen(o => ({ ...o, [s.id]: null })); return; }
    clear(s.id);
    try { const full = await api.tuner.suggestion(s.id); setOpen(o => ({ ...o, [s.id]: full })); } catch (e) { fail(s.id, e); }
  }
  function approve(s) {
    clear(s.id);
    return queued(s.id, async () => {
      latest.bump();
      try {
        const r = await api.tuner.approve(s.id);
        latest.bump();
        setList(l => l.filter(x => x.id !== s.id));
        setChosen(c => [...c, { skill: r.skill, fit: r.fit || null, picks: initialPicks(botsRef.current, r.fit), dialog: true, done: { confirmed: [], updated: [] } }]);
        notify(`${s.name} was added to the skills`);
        onChanged?.(); onDecided?.(); load();
      } catch (e) { fail(s.id, e); }
    });
  }
  function reject(s) {
    clear(s.id);
    return queued(s.id, async () => {
      latest.bump();
      try { await api.tuner.reject(s.id); latest.bump(); setList(l => l.filter(x => x.id !== s.id)); onDecided?.(); load(); } catch (e) { fail(s.id, e); }
    });
  }
  const patch = (ref, fn) => setChosen(c => c.map(x => (x.skill.ref === ref ? fn(x) : x)));
  function done(item) {
    const ref = item.skill.ref;
    const progress = { confirmed: [...item.done.confirmed], updated: [...item.done.updated] };  // kept, so a retry skips these
    clear(ref);
    return queued(ref, async () => {
      try {
        const names = await attachChosen(api, item, progress);  // reads the bots' stored skills itself
        notify(doneLine(names));
        // no bot ticked: the skill is still on no bot, so the small card stays
        setChosen(c => (names.length ? c.filter(x => x.skill.ref !== ref) : c.map(x => (x.skill.ref === ref ? { ...x, dialog: false, done: progress } : x))));
        onChanged?.();
      } catch (e) {
        patch(ref, it => ({ ...it, done: progress, partial: true }));  // stays until Done works, even once a bot has it
        fail(ref, e); onChanged?.();
      }
    });
  }

  const offered = offeredBots(bots);
  const placed = ref => (bots || []).some(b => (b.skills || []).includes(ref));
  const waiting = chosen.filter(x => x.partial || !placed(x.skill.ref));
  if (!list.length && !waiting.length) return null;
  return <>
    <div className="section-heading"><h2 id={anchorId('skills', 'Suggested by SkillTuner')}>Suggested by SkillTuner</h2></div>
    <div className="list-panel">
      {list.map(s => <article className="resource-row od-stack" key={s.id}>
        <div className="od-field"><h3>{s.name}</h3><p>{s.use}</p>
          <p className="quiet-label">Found for: {s.topic}</p>
          <p className="quiet-label">{cardSource(s)}</p>
          {s.left_out && <p className="quiet-label">{LEFT_OUT}</p>}</div>
        {open[s.id] && <div className="od-row-top">
          <div className="od-field od-fill"><span className="quiet-label">Original</span><pre className="full-text">{open[s.id].original}</pre></div>
          <div className="od-field od-fill"><span className="quiet-label">Converted</span><pre className="full-text">{open[s.id].converted}</pre></div>
        </div>}
        {errors[s.id] && <p className="form-error" role="alert">{errors[s.id]}</p>}
        <div className="approval-actions">
          <Button appearance="outline" disabled={!online} onClick={() => toggleChanges(s)}>See what changed</Button>
          <Button appearance="primary" disabled={!online || busy.has(s.id)} onClick={() => approve(s)}>Approve</Button>
          <Button appearance="outline" disabled={!online || busy.has(s.id)} onClick={() => reject(s)}>Reject</Button>
        </div>
      </article>)}
      {waiting.map(x => x.dialog
        ? <article className="resource-row od-stack" key={x.skill.ref}>
          <div className="od-field"><h3>{x.skill.name}</h3><p className="quiet-label">Which bots should use it?</p></div>
          {offered.length ? <ul className="model-list" aria-label="Which bots should use it?">{offered.map(b => <li key={b.id}><label className="od-row">
            <input type="checkbox" checked={x.picks.has(b.id)} onChange={e => patch(x.skill.ref, it => { const p = new Set(it.picks); if (e.target.checked) p.add(b.id); else p.delete(b.id); return { ...it, picks: p }; })}/>
            {b.name}{x.fit?.bot_ids?.includes(b.id) && <span className="quiet-label"> ({Number(x.fit.role_scores?.[b.id] ?? 0).toFixed(2)})</span>}</label></li>)}</ul>
            : <p className="quiet-line">No bot can use a skill yet.</p>}
          {errors[x.skill.ref] && <p className="form-error" role="alert">{errors[x.skill.ref]}</p>}
          <div className="approval-actions">
            <Button appearance="subtle" onClick={() => patch(x.skill.ref, it => ({ ...it, dialog: false }))}>Close</Button>
            <Button appearance="primary" disabled={busy.has(x.skill.ref)} onClick={() => done(x)}>Done</Button>
          </div>
        </article>
        : <article className="resource-row od-row" key={x.skill.ref}>
          <div className="od-field od-fill"><span className="model-name">{x.skill.name}</span><span className="quiet-label">{doneLine([])}</span></div>
          <Button appearance="outline" onClick={() => patch(x.skill.ref, it => ({ ...it, dialog: true }))}>Choose bots</Button>
        </article>)}
    </div>
  </>;
}

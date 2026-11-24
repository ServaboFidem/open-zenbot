import React, { useEffect, useState } from 'react';
import { Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Field } from '@fluentui/react-components';
import { DAY_NAMES, LOGIN_LABEL, MAX_SLOTS, SAYS_LINE, WEATHER_SET, WEATHER_UNSET,
  defaultForm, hostedNote, keeperSentence, offersSignIn, readinessFix, saveBlocked, ceilingLow, speakerChoices, signInStart, validate, formBlocker } from './briefings.js';
import { toggleDay } from './routine-schedule.js';

// Create or edit the morning briefing. `briefing` is the one to edit or null; `status` is GET /briefings (ceiling,
// rooms, hosted); `ready`/`reason` say what is missing (Sonos off, no speaker, no voice) with a link to fix it;
// `weatherSet` says whether a weather city is set (null while unknown); `keeper` is what the desktop app's
// briefingKeeper.get() answered ({ managed, platform, login, wake_error }) or null (a plain browser: no sign-in box,
// no keeper sentence). The sign-in box shows only when `managed`.
// Props: { briefing, status, ready, reason, weatherSet, keeper, onSave(body, login), onClose, openSettings(panel, title) }
export default function BriefingDialog({ briefing, status, ready, reason, weatherSet, keeper, onSave, onClose, openSettings }) {
  const ceiling = status?.ceiling ?? 0;
  const rooms = status?.rooms || [];
  const [form, setForm] = useState(() => (briefing
    ? { enabled: briefing.enabled, slots: briefing.slots.map(s => ({ days: [...s.days], time: s.time })), speakers: briefing.speakers ? [...briefing.speakers] : null, volume: briefing.volume }
    : defaultForm(ceiling)));
  const [login, setLogin] = useState(() => signInStart(keeper, !!briefing));  // ticked by default when the first briefing is added
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { setLogin(signInStart(keeper, !!briefing)); }, [keeper, briefing]);
  const set = patch => setForm(f => ({ ...f, ...patch }));
  const setSlot = (i, patch) => set({ slots: form.slots.map((s, j) => (j === i ? { ...s, ...patch } : s)) });
  const hosted = hostedNote(status?.hosted);
  const blocker = formBlocker(status);
  const noVolume = ceilingLow(status);  // no volume picker; Save is still on for a switched-off briefing
  const blocked = saveBlocked(status, form);
  const fix = readinessFix(reason || (noVolume ? 'ceiling_zero' : null));
  const signIn = offersSignIn(keeper);
  const sentence = keeperSentence(keeper);
  async function submit(e) {
    e.preventDefault();
    const problem = validate(form, ceiling);
    if (problem) { setError(problem); return; }
    setBusy(true); setError('');
    try { await onSave(form, signIn ? login : null); } catch (err) { setError(err?.message || 'The briefing could not be saved.'); } finally { setBusy(false); }
  }
  return <Dialog open onOpenChange={(e, v) => { if (!v.open) onClose(); }}><DialogSurface className="edit-dialog"><DialogBody>
    <DialogTitle>Morning briefing</DialogTitle>
    <DialogContent><form id="briefing-form" className="editor-form briefing-form" onSubmit={submit}>
      {blocker && <div role="alert" className="form-error">{blocker}{fix && <> <button type="button" className="text-link" onClick={() => openSettings(fix.panel, fix.title)}>Open settings</button></>}</div>}
      {error && <div role="alert" className="form-error">{error}</div>}
      <Field label="When">
        <div className="briefing-slots">
          {form.slots.map((s, i) => <div className="od-row briefing-slot" key={i} role="group" aria-label={`Line ${i + 1}`}>
            {DAY_NAMES.map((name, d) => <label key={name} className="od-row"><input type="checkbox" checked={s.days.includes(d)} onChange={() => setSlot(i, { days: toggleDay(s.days, d) })}/> {name}</label>)}
            <span>at</span>
            <input type="time" aria-label={`Time for line ${i + 1}`} value={s.time} onChange={e => setSlot(i, { time: e.target.value })}/>
            {form.slots.length > 1 && <button type="button" className="text-link" onClick={() => set({ slots: form.slots.filter((_, j) => j !== i) })}>remove</button>}
          </div>)}
          {form.slots.length < MAX_SLOTS && <button type="button" className="text-link" onClick={() => set({ slots: [...form.slots, { days: [], time: '08:00' }] })}>+ Add another time</button>}
        </div>
      </Field>
      <Field label="Speakers">
        <div className="od-stack" role="radiogroup" aria-label="Speakers">
          <label className="od-row"><input type="radio" name="briefing-speakers" checked={form.speakers === null} onChange={() => set({ speakers: null })}/> Every speaker</label>
          <label className="od-row"><input type="radio" name="briefing-speakers" checked={form.speakers !== null} onChange={() => set({ speakers: form.speakers || [] })}/> Choose:</label>
          {form.speakers !== null && <div className="od-row briefing-rooms">{rooms.length === 0 && <span>No Sonos speakers answer right now.</span>}
            {speakerChoices(rooms, form.speakers).map(({ name, missing }) => <label key={name} className="od-row"><input type="checkbox" checked={form.speakers.includes(name)}
              onChange={() => set({ speakers: form.speakers.includes(name) ? form.speakers.filter(x => x !== name) : [...form.speakers, name] })}/> {name}{missing && ' (not answering)'}</label>)}</div>}
        </div>
      </Field>
      {!noVolume && <Field label="Volume" hint={`Never above your Sonos limit of ${ceiling}.`}>
        <div className="od-row"><input type="range" aria-label="Volume" min="1" max={ceiling} value={form.volume}
          onChange={e => set({ volume: Number(e.target.value) })}/> <span>{form.volume}</span></div>
      </Field>}
      <p className="field-hint">{SAYS_LINE}</p>
      {weatherSet !== null && <p className="field-hint">{weatherSet ? WEATHER_SET : WEATHER_UNSET}</p>}
      {hosted && <p className="field-hint">{hosted}</p>}
      {signIn && <label className="od-row"><input type="checkbox" checked={login} onChange={e => setLogin(e.target.checked)}/> {LOGIN_LABEL}</label>}
      {sentence && <p className="field-hint">{sentence}</p>}
    </form></DialogContent>
    <DialogActions><Button appearance="secondary" onClick={onClose}>Cancel</Button><Button appearance="primary" type="submit" form="briefing-form" disabled={busy || blocked}>Save</Button></DialogActions>
  </DialogBody></DialogSurface></Dialog>;
}

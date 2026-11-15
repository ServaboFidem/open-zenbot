import React, { useState } from 'react';
import { Button, Field, Input, Switch, Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions } from '@fluentui/react-components';
import { DAYS } from './music/helpers.js';
import { botSkills, defaultSchedule, toggleDay, validateSchedule } from './routine-schedule.js';

// Repeat weekly (chosen days), daily, or every N hours. value is a harness schedule { kind, days, time, hours }.
export function ScheduleEditor({ value, onChange }) {
  const set = patch => onChange({ ...value, ...patch });
  return <div className="od-stack">
    <Field label="Repeat">
      <select aria-label="Repeat" value={value.kind} onChange={e => set({ kind: e.target.value })}>
        <option value="weekly">Weekly</option><option value="daily">Daily</option><option value="interval">Every N hours</option>
      </select>
    </Field>
    {value.kind === 'weekly' && <Field label="Days">
      <div className="od-row" role="group" aria-label="Days">{DAYS.map((name, i) => <label key={name} className="od-row"><input type="checkbox" checked={(value.days || []).includes(i)} onChange={() => set({ days: toggleDay(value.days || [], i) })}/> {name.slice(0, 3)}</label>)}</div>
    </Field>}
    {value.kind === 'interval'
      ? <Field label="Hours between runs"><input type="number" aria-label="Hours between runs" min="1" max="168" value={value.hours} onChange={e => set({ hours: e.target.value === '' ? '' : Number(e.target.value) })}/></Field>
      : <Field label="Time"><input type="time" aria-label="Time" value={value.time} onChange={e => set({ time: e.target.value })}/></Field>}
  </div>;
}

// Create or edit a routine. routine: the stored one to edit, or null for a new one.
// A managed routine (the Music digest) keeps its bot and skill; only its name, schedule and switch change.
export default function RoutineDialog({ routine, bots, skills, onSave, onClose }) {
  const managed = !!routine?.managed_by;
  const [name, setName] = useState(routine?.name || '');
  const [bot, setBot] = useState(routine?.bot || bots[0]?.id || '');
  const [skill, setSkill] = useState(routine?.skill || botSkills(routine?.bot || bots[0]?.id, bots, skills)[0]?.ref || '');
  const offered = botSkills(bot, bots, skills);
  function chooseBot(id) {
    setBot(id);
    const next = botSkills(id, bots, skills);
    if (!next.some(s => s.ref === skill)) setSkill(next[0]?.ref || '');
  }
  const [enabled, setEnabled] = useState(routine?.enabled ?? true);
  const [schedule, setSchedule] = useState(routine?.schedule ? { ...defaultSchedule(), ...routine.schedule } : defaultSchedule());
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function submit(e) {
    e.preventDefault();
    const problem = !name.trim() ? 'Give this routine a name.' : !bot || !skill ? 'Choose a bot and a skill.'
      : !managed && !offered.some(s => s.ref === skill) ? 'Choose one of this bot\'s skills.' : validateSchedule(schedule);
    if (problem) { setError(problem); return; }
    setBusy(true);
    try { await onSave({ id: routine?.id, name: name.trim(), bot, skill, enabled, managed_by: routine?.managed_by || '', schedule: { ...schedule, hours: Number(schedule.hours) || 24 } }); }
    finally { setBusy(false); }
  }
  return <Dialog open onOpenChange={(e, v) => { if (!v.open) onClose(); }}><DialogSurface className="edit-dialog"><DialogBody>
    <DialogTitle>{routine ? 'Edit routine' : 'Create a routine'}</DialogTitle>
    <DialogContent><form id="routine-form" className="editor-form" onSubmit={submit}>
      {error && <div role="alert" className="form-error">{error}</div>}
      <Field label="Name" required><Input autoFocus maxLength={64} value={name} onChange={(e, v) => setName(v.value)}/></Field>
      {managed
        ? <p className="field-hint">Managed by Music: its bot and skill are fixed. Delete it to turn the weekly digest off.</p>
        : <><Field label="Bot" required><select aria-label="Bot" value={bot} onChange={e => chooseBot(e.target.value)}>{bots.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}</select></Field>
          <Field label="Skill" required hint={offered.length ? 'Only skills attached to this bot can run on a schedule.' : 'This bot has no skills attached. Attach one on the Bots page first.'}><select aria-label="Skill" value={offered.some(s => s.ref === skill) ? skill : ''} onChange={e => setSkill(e.target.value)}>{!offered.some(s => s.ref === skill) && <option value="" disabled>Choose a skill</option>}{offered.map(s => <option key={s.ref} value={s.ref}>{s.name}</option>)}</select></Field></>}
      <ScheduleEditor value={schedule} onChange={setSchedule}/>
      <Switch checked={enabled} label={enabled ? 'Enabled' : 'Paused'} onChange={(e, v) => setEnabled(v.checked)}/>
    </form></DialogContent>
    <DialogActions><Button appearance="secondary" onClick={onClose}>Cancel</Button><Button appearance="primary" type="submit" form="routine-form" disabled={busy}>Save routine</Button></DialogActions>
  </DialogBody></DialogSurface></Dialog>;
}

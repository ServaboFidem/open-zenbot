// A bot's question form: labelled controls and one "Send answers" button. Everything shown is plain
// React text; the parser (question-form.js) has already dropped unknown keys. Read-only when not live
// (an older exchange, a room, no connection): the answers stay visible but cannot be changed or sent.
import React, { useId, useState } from 'react';
import { Button, Checkbox, Field, Input, Radio, RadioGroup, Slider, Textarea } from '@fluentui/react-components';
import { answersMessage, initialValues, NO_COLOR } from './question-form.js';

function Control({ q, value, onChange, disabled, id }) {
  if (q.type === 'radio') return <RadioGroup value={value} disabled={disabled} onChange={(e, d) => onChange(d.value)}>{q.options.map(o => <Radio key={o} value={o} label={o}/>)}</RadioGroup>;
  if (q.type === 'checkbox') return <div className="od-stack">{q.options.map(o => <Checkbox key={o} label={o} disabled={disabled} checked={value.includes(o)} onChange={(e, d) => onChange(d.checked ? [...value, o] : value.filter(x => x !== o))}/>)}</div>;
  if (q.type === 'select') return <select id={id} value={value} disabled={disabled} onChange={e => onChange(e.target.value)}><option value="">Choose one</option>{q.options.map(o => <option key={o} value={o}>{o}</option>)}</select>;
  if (q.type === 'textarea') return <Textarea id={id} rows={3} resize="vertical" value={value} disabled={disabled} onChange={(e, d) => onChange(d.value)}/>;
  if (q.type === 'range') return <div className="od-row"><Slider id={id} min={q.min} max={q.max} step={q.step || 1} value={value} disabled={disabled} onChange={(e, d) => onChange(d.value)}/><span>{value}</span></div>;
  if (q.type === 'color') return <input id={id} type="color" value={/^#[0-9a-fA-F]{6}$/.test(value) ? value : NO_COLOR} disabled={disabled} onChange={e => onChange(e.target.value)}/>;
  return <Input id={id} value={value} disabled={disabled} onChange={(e, d) => onChange(d.value)}/>;
}

export default function QuestionForm({ form, live = false, onAnswer }) {
  const uid = useId();
  const [values, setValues] = useState(() => initialValues(form));
  const [others, setOthers] = useState({});
  const set = (id, v) => setValues(s => ({ ...s, [id]: v }));
  return <form className={live ? 'question-form' : 'question-form readonly'} aria-label={form.title || 'Questions'}
    onSubmit={e => { e.preventDefault(); if (live && onAnswer) onAnswer(answersMessage(form, values, others)); }}>
    {form.title && <h4 className="question-form-title">{form.title}</h4>}
    {form.questions.map(q => {
      const id = `${uid}-${q.id}`;
      // the label names the control by id: the native select and colour input take nothing from Field's context.
      // A radio or checkbox group has no single control to point at.
      const label = q.type === 'radio' || q.type === 'checkbox' ? q.label : { children: q.label, htmlFor: id };
      return <Field key={q.id} label={label} className="question-field">
        <Control q={q} id={id} value={values[q.id]} onChange={v => set(q.id, v)} disabled={!live}/>
        {q.allow_custom && <Input aria-label={`Other: ${q.label}`} placeholder="Other" value={others[q.id] || ''} disabled={!live} onChange={(e, d) => setOthers(s => ({ ...s, [q.id]: d.value }))}/>}
      </Field>;
    })}
    {live ? <div className="question-form-actions"><Button appearance="primary" type="submit">Send answers</Button></div> : <p className="field-hint">These questions are closed.</p>}
  </form>;
}

import React, { useId } from 'react';
import { Button, Slider } from '@fluentui/react-components';

// The design's tweaks (the knobs the Designer declared, as the harness's slots answer lists them): a colour input or
// a slider per knob, its label shown as plain text. A change goes to the pending changes, which the frame shows;
// Reset drops every pending tweak. The pane builds the value the frame gets from the knob's type (cssValue).

export const NO_TWEAKS = 'This design has no tweaks. Ask the Designer to add some.';
const HEX = /^#[0-9a-fA-F]{6}$/;

function Knob({ knob, value, disabled, onChange }) {
  const id = useId();
  const unit = knob.unit || '';
  return <div className="tweak od-row">
    <label className="tweak-label" htmlFor={id}>{knob.label}</label>
    {knob.type === 'color'
      ? <input id={id} type="color" value={HEX.test(value) ? value.toLowerCase() : '#000000'} disabled={disabled} onChange={e => onChange(e.target.value)}/>
      : <><Slider id={id} className="od-fill" min={knob.min} max={knob.max} step={knob.step || 1} value={value} disabled={disabled} onChange={(e, d) => onChange(d.value)}/><span className="tweak-value">{value}{unit}</span></>}
  </div>;
}

// `disabled` while a save is on its way, so a change then is not wiped when the save clears the pending changes.
export default function TweaksPanel({ knobs, values, disabled = false, onChange, onReset }) {
  if (!knobs?.length) return <div className="tweaks-panel"><p className="field-hint">{NO_TWEAKS}</p></div>;
  return <div className="tweaks-panel od-stack" role="group" aria-label="Tweaks">
    {knobs.map(k => <Knob key={k.var} knob={k} value={k.var in values ? values[k.var] : k.value} disabled={disabled} onChange={v => onChange(k.var, v)}/>)}
    <div className="od-row"><Button appearance="subtle" size="small" disabled={disabled || !Object.keys(values).length} onClick={onReset}>Reset</Button></div>
  </div>;
}

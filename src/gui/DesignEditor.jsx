import React, { useEffect, useRef, useState } from 'react';
import { Button, Input, Textarea } from '@fluentui/react-components';
import { pictureProblem } from './design-edits.js';

// The small editor the pane opens over the frame for one slot the user picked in Edit mode. A text slot gets a
// textarea holding the harness's own text for it (the slots answer, never the frame's); a picture slot gets
// Choose picture and an alt text. Every keystroke or picture goes straight to the pending changes, which the
// frame shows; Cancel puts the pending changes back as they were when the editor opened.

const TEXT_MAX = 2000;  // a slot's text cap, the worksheet's and the bridge's

// Where the editor sits inside the stage: under the picked element, or above it when there is no room below,
// kept inside the stage. `rect` is in the frame's viewport, which is the stage's own box.
function place(rect, stage) {
  const W = stage?.clientWidth || 400, H = stage?.clientHeight || 400;
  const width = Math.min(Math.max(rect.w, 280), W - 16);
  const left = Math.min(Math.max(8, rect.x), Math.max(8, W - width - 8));
  const below = rect.y + rect.h + 6;
  const top = below + 200 <= H ? below : Math.max(8, rect.y - 206);
  return { left, top: Math.min(top, Math.max(8, H - 200)), width };
}

// `disabled` while a save is on its way: what is typed then would be wiped when the save clears the pending changes.
export default function DesignEditor({ slot, rect, stage, text, picture, disabled = false, onText, onPicture, onDone, onCancel }) {
  const [where] = useState(() => place(rect, stage));
  const first = useRef(null);
  const fileInput = useRef(null);
  const [alt, setAlt] = useState(picture?.alt ?? slot.alt ?? '');
  const [problem, setProblem] = useState('');
  useEffect(() => {  // focus the textarea (caret after the text) or Choose picture
    const el = first.current;
    el?.focus();
    if (typeof el?.setSelectionRange === 'function') el.setSelectionRange(el.value.length, el.value.length);
  }, []);
  const onKeyDown = e => { if (e.key === 'Escape' && !disabled) { e.preventDefault(); e.stopPropagation(); onCancel(); } };

  function choose(e) {
    const file = e.target.files?.[0];
    e.target.value = '';  // choosing the same file again still fires
    if (!file) return;
    const why = pictureProblem(file);
    setProblem(why);
    if (why) return;
    const reader = new FileReader();
    reader.onload = () => { if (typeof reader.result === 'string') onPicture({ name: file.name, dataUrl: reader.result, alt }); };
    reader.onerror = () => setProblem('That picture could not be read.');
    reader.readAsDataURL(file);
  }
  function changeAlt(value) {
    setAlt(value);
    if (picture) onPicture({ ...picture, alt: value });
  }

  const label = slot.kind === 'text' ? `Edit the text of ${slot.id}` : `Change the picture ${slot.id}`;
  return <div className="design-editor od-stack" role="dialog" aria-label={label} style={where} onKeyDown={onKeyDown}>
    {slot.kind === 'text'
      ? <Textarea ref={first} className="design-editor-text" aria-label={label} value={text} maxLength={TEXT_MAX} resize="vertical" rows={3} disabled={disabled} onChange={(e, d) => onText(d.value)}/>
      : <>
        <div className="od-row design-editor-pick">
          <Button ref={first} appearance="outline" disabled={disabled} onClick={() => fileInput.current?.click()}>Choose picture</Button>
          <span className="design-editor-name">{picture ? picture.name : slot.name}</span>
          <input ref={fileInput} type="file" hidden accept="image/png,image/jpeg,image/webp,image/gif" onChange={choose}/>
        </div>
        <label className="design-editor-alt od-stack">Alt text<Input value={alt} maxLength={TEXT_MAX} disabled={disabled || !picture} onChange={(e, d) => changeAlt(d.value)}/></label>
        {!picture && <span className="field-hint">The alt text is saved with a new picture.</span>}
        {problem && <span className="field-hint" role="alert">{problem}</span>}
      </>}
    <div className="od-row design-editor-actions"><Button appearance="primary" size="small" disabled={disabled} onClick={onDone}>Done</Button><Button appearance="subtle" size="small" disabled={disabled} onClick={onCancel}>Cancel</Button></div>
  </div>;
}

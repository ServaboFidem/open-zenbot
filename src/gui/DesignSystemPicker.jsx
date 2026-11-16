import React from 'react';
import { Dropdown, Option } from '@fluentui/react-components';

// "Design system" dropdown in a studio bot's chat heading: None, then the user's own systems, then the
// shipped ones. Names and summaries come from files a bot may have written, so they render as plain text.
// A saved slug that no longer lists shows as unavailable; the user can still pick another or None.
const NONE = '__none__';
const cut = (v, n = 140) => { const s = String(v || '').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };

export default function DesignSystemPicker({ systems, current, onPick, disabled }) {
  const list = Array.isArray(systems) ? systems : [];
  const ordered = [...list.filter(s => s.source === 'yours'), ...list.filter(s => s.source !== 'yours')];
  const shown = ordered.find(s => s.slug === current);
  const label = !current ? 'None' : shown ? (shown.name || shown.slug) : `${current} (unavailable)`;
  return <Dropdown className="design-system-picker" aria-label="Design system" disabled={disabled}
    value={`Design system: ${label}`} selectedOptions={[current || NONE]}
    onOptionSelect={(e, d) => {
      const next = d.optionValue === NONE ? '' : d.optionValue;
      if (next !== (current || '')) onPick(next || null);
    }}>
    <Option value={NONE} text="None">None</Option>
    {ordered.map(s => <Option key={s.slug} value={s.slug} text={s.name || s.slug} title={cut(s.summary)}>{`${s.name || s.slug}${s.source === 'yours' ? ' (yours)' : ''}`}</Option>)}
  </Dropdown>;
}

import React from 'react';
import { Dropdown, Option } from '@fluentui/react-components';
import { conversationLabel } from './actions';

// The conversation dropdown at the top of a bot chat or a room: the current conversation's title, the
// others newest first with their date, and "+ New conversation" at the top. Picking one calls onSelect
// with its id; the new entry calls onNew. The parent reloads the chat or room afterwards.
const NEW = '__new__';

export default function ConversationPicker({ current, conversations, onNew, onSelect, disabled }) {
  const list = conversations || [];
  const shown = list.find(c => c.id === current);
  const title = String(shown?.title || '').trim() || 'New conversation';
  return <Dropdown className="conversation-picker" aria-label="Conversation" disabled={disabled}
    value={title} selectedOptions={current ? [current] : []}
    onOptionSelect={(e, d) => {
      if (d.optionValue === NEW) onNew();
      else if (d.optionValue && d.optionValue !== current) onSelect(d.optionValue);
    }}>
    <Option value={NEW} text="+ New conversation">+ New conversation</Option>
    {list.map(c => <Option key={c.id} value={c.id} text={conversationLabel(c)}>{conversationLabel(c)}</Option>)}
  </Dropdown>;
}

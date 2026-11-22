import React from 'react';
import { Button } from '@fluentui/react-components';

// Under the design pane's frame while there are unsaved changes: how many, Discard, and "Save as Version N".
// Both buttons wait while a save is on its way.
export default function DesignPendingBar({ count, label, saving, canSave, why, onSave, onDiscard }) {
  return <div className="design-pending od-row" role="group" aria-label="Unsaved changes">
    <span className="od-fill">{count === 1 ? '1 change not saved' : `${count} changes not saved`}</span>
    <Button appearance="subtle" size="small" disabled={saving} onClick={onDiscard}>Discard</Button>
    <Button appearance="primary" size="small" disabledFocusable={saving || !canSave} title={why || undefined} onClick={onSave}>{saving ? 'Saving…' : label}</Button>
  </div>;
}

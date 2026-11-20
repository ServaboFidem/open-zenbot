import React, { useEffect, useState } from 'react';
import { Button, Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions } from '@fluentui/react-components';

const SETTLE_MS = 400;

// One pending approval, shown over whatever page is open. Closing it (Escape, a click outside, Decide
// later) decides nothing: the request stays pending in the Approvals tab. Approve never has first focus.
// The app gives each approval its own key, so the next one in line mounts afresh: focus goes back to
// Decide later, and Approve and Reject wait a moment, so a double click meant for the last approval
// cannot decide this one.
export default function ApprovalDialog({ approval, bot, count, busy, onDecide, onLater }) {
  const [settled, setSettled] = useState(false);
  useEffect(() => { const t = setTimeout(() => setSettled(true), SETTLE_MS); return () => clearTimeout(t); }, []);
  if (!approval) return null;
  const held = busy || !settled;
  return <Dialog open modalType="modal" onOpenChange={(e, v) => { if (!v.open && !busy) onLater(approval); }}><DialogSurface className="edit-dialog approval-dialog"><DialogBody>
    <DialogTitle>{bot} needs your approval</DialogTitle>
    <DialogContent>
      {count && <p className="quiet-label">{count}</p>}
      <p><strong>{approval.title}</strong></p>
      {approval.detail && <pre className="approval-detail">{approval.detail}</pre>}
      {approval.rule && <p className="quiet-label">rule: {approval.rule}</p>}
    </DialogContent>
    <DialogActions>
      <Button appearance="secondary" autoFocus disabled={busy} onClick={() => onLater(approval)}>Decide later</Button>
      <Button appearance="outline" disabled={held} onClick={() => onDecide(approval, false)}>Reject</Button>
      <Button appearance="primary" disabled={held} onClick={() => onDecide(approval, true)}>Approve</Button>
    </DialogActions>
  </DialogBody></DialogSurface></Dialog>;
}

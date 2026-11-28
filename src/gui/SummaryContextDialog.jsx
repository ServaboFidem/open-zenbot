import React, { useEffect, useState } from 'react';
import { Button, Dialog, DialogSurface, DialogBody, DialogTitle, DialogContent, DialogActions, Spinner } from '@fluentui/react-components';
import { advice } from './summary-context';

// The summary model's context: LM Studio's numbers and what to change. Reads the state fresh on open.
// Everything from the harness is plain text.
export default function SummaryContextDialog({ api, onClose }) {
  const [status, setStatus] = useState(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    api.summaryContext(true).then(s => { if (live) setStatus(s); }).catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, []);
  const a = status ? advice(status) : null;
  return <Dialog open modalType="modal" onOpenChange={(e, v) => { if (!v.open) onClose(); }}><DialogSurface className="edit-dialog"><DialogBody>
    <DialogTitle>Summary model context</DialogTitle>
    <DialogContent className="od-stack">
      {!status && !failed && <Spinner size="tiny" label="Reading…"/>}
      {failed && <p className="form-error" role="alert">The summary model's context could not be read.</p>}
      {a && <>
        {status.provider && status.server !== 'hosted' && <p>Summary model: <strong>{status.model}</strong> ({status.provider.name})</p>}
        {a.facts.length > 0 && <dl>{a.facts.map(([k, v]) => <div key={k} className="od-row"><dt className="quiet-label">{k}</dt><dd>{v}</dd></div>)}</dl>}
        {a.lines.map(l => <p key={l}>{l}</p>)}
        {a.alternatives.length > 0 && <ul aria-label="Models that could replace it">{a.alternatives.map(m => <li key={m.id}>{m.id}: {m.max} ({m.note})</li>)}</ul>}
        {a.alternatives.length > 0 && <p className="quiet-line">Choose one under Summary model on this page.</p>}
      </>}
    </DialogContent>
    <DialogActions><Button appearance="primary" autoFocus onClick={onClose}>Close</Button></DialogActions>
  </DialogBody></DialogSurface></Dialog>;
}

import React, { useEffect, useState } from 'react';
import { Button, Switch } from '@fluentui/react-components';
import { anchorId } from './settings-index';
import { noticeOf, statusLine, appVersion, updatesApi } from './updates';

// Read the update State once on mount; `refresh` reads it again after the user acts. The main process does the daily check.
function useUpdateState() {
  const api = updatesApi(typeof window === 'undefined' ? null : window);
  const [state, setState] = useState(null);
  const apply = p => Promise.resolve(p).then(s => { setState(s || null); return s; }).catch(() => {});
  useEffect(() => { if (api) apply(api.get()); }, []);
  return { api, state, apply };
}

// A dismissible bar above the page when a newer release exists. Release notes are untrusted text: shown as plain text only.
export function UpdateNotice() {
  const { api, state, apply } = useUpdateState();
  const [dismissed, setDismissed] = useState(false);
  const n = noticeOf(state);
  if (!api || !n || dismissed) return null;
  return <div className="storage-warning update-notice" role="status">
    <div className="od-row"><span className="od-fill">OpenZenBot {n.version} is available.</span>
      <Button appearance="primary" onClick={() => apply(api.open())}>Update</Button>
      <Button appearance="outline" onClick={() => { setDismissed(true); apply(api.skip(n.version)); }}>Skip this version</Button>
      <Button appearance="subtle" aria-label="Dismiss" onClick={() => setDismissed(true)}>Dismiss</Button></div>
    {n.notes && <details><summary>What's new</summary><pre className="update-notes">{n.notes}</pre></details>}
  </div>;
}

// Settings > About > Version and updates. In a browser it shows only the version.
export function VersionSection() {
  const { api, state, apply } = useUpdateState();
  const [busy, setBusy] = useState(false);
  const version = appVersion(typeof window === 'undefined' ? null : window);
  return <div className="resource-row editor-form">
    <h3 id={anchorId('about', 'Version and updates')}>Version and updates</h3>
    <p>{version ? `OpenZenBot ${version}` : 'OpenZenBot'}</p>
    {api && <>
      <p>{statusLine(state)}</p>
      <div className="od-row"><Button appearance="outline" disabled={busy} onClick={() => { setBusy(true); apply(api.check()).finally(() => setBusy(false)); }}>Check for updates</Button>
        {state?.newer && <Button appearance="primary" onClick={() => apply(api.open())}>Update</Button>}</div>
      <Switch checked={!!state?.auto} label="Check for updates automatically" onChange={(e, d) => apply(api.setAuto(d.checked))}/>
    </>}
  </div>;
}

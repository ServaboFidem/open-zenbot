import React, { useState } from 'react';
import { Button } from '@fluentui/react-components';
import { copyText } from './copy.js';
import { depProblem, manualLink, recheckNotice, unmetDeps } from './mcp-deps.js';

// The requirements on an MCP server's card: programs it needs on this machine that are missing or too old.
// Shows the fix command to copy; it never runs one. Recheck re-probes and reconnects the server.
// ctx: { api, online, notify }; onSaved gets the new /mcps listing.
export default function McpDeps({ server, ctx, onSaved }) {
  const { api, online, notify } = ctx;
  const unmet = unmetDeps(server.deps);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  if (!server.deps?.length || (!unmet.length && server.state !== 'needs_install')) return null;

  async function recheck() {
    setBusy(true); setError('');
    try {
      const r = await api.recheckMcp(server.name);
      onSaved(r);
      notify(recheckNotice(server.name, r[server.name]));
    } catch (e) { setError(e?.message || 'Something went wrong.'); }
    finally { setBusy(false); }
  }

  return <div className="mcp-deps od-stack" aria-label={`${server.name} requirements`}>
    {unmet.map(d => {
      const link = manualLink(d);
      return <div key={d.bin} className="od-stack">
        <strong>{d.bin} — {depProblem(d)}</strong>
        {d.fix && <div className="od-row"><code className="od-fill">{d.fix}</code>
          <Button appearance="subtle" size="small" onClick={async () => { const ok = await copyText(d.fix); notify(ok ? 'Copied' : "Couldn't copy"); }}>Copy</Button></div>}
        {d.fix && d.privileged && <span className="field-hint">Needs admin rights: run it in your own terminal.</span>}
        {link && <a className="field-hint" href={link.href} target="_blank" rel="noreferrer">Or: {link.text} ↗</a>}
      </div>;
    })}
    {error && <p className="field-hint">{error}</p>}
    <div className="od-row"><div className="od-fill"/>
      <Button appearance="primary" disabled={!online || busy} onClick={recheck}>{busy ? 'Rechecking…' : 'Recheck'}</Button></div>
  </div>;
}

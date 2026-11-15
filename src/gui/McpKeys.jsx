import React, { useEffect, useState } from 'react';
import { Button, Field, Input } from '@fluentui/react-components';
import { keyHint, keysBody, keysDirty, keysForm } from './mcp-keys.js';

// The key fields on an MCP server's card: one per ${VAR} its mcp.json entry names in env, headers or url.
// Saved in vault-settings/mcp_keys.json beside the vault (owner-only, out of the bots' reach), over .env; saving reconnects that server at once.
// The page never holds a key: fields start empty, and a blank one keeps the key in force.
// ctx: { api, online, notify }; onSaved gets the new /mcps listing.
export default function McpKeys({ server, ctx, onSaved }) {
  const { api, online, notify } = ctx;
  const keys = server.keys || [];
  const [form, setForm] = useState(keysForm(keys));
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const names = keys.map(k => k.name).join(',');
  useEffect(() => setForm(keysForm(keys)), [server.name, names]);
  if (!keys.length) return null;

  async function run(which, fn, done) {
    setBusy(which); setError('');
    try {
      const r = await fn();
      onSaved(r); setForm(keysForm(r[server.name]?.keys || keys));
      const st = r[server.name]?.state;
      notify(st === 'connected' ? `${done}; ${server.name} is connected` : `${done}; ${server.name} did not connect`);
    } catch (e) { setError(e?.message || 'Something went wrong.'); }
    finally { setBusy(''); }
  }

  const save = e => { e.preventDefault(); run('save', () => api.setMcpKeys(server.name, keysBody(form)), keys.length === 1 ? 'Key saved' : 'Keys saved'); };
  const label = k => keys.length === 1 ? 'API key' : k.name;

  return <form className="mcp-keys" onSubmit={save} aria-label={`${server.name} API keys`}>
    {keys.map((k, i) => <Field key={k.name} label={<>{label(k)}{keys.length === 1 && <span className="quiet-label"> · {k.name}</span>}</>}
      hint={keyHint(k)} validationMessage={i === keys.length - 1 && error ? error : undefined}>
      <Input type="password" value={form[k.name] || ''} placeholder={k.source ? '••••••••' : 'Paste the key'} disabled={!online || !!busy}
        onChange={(e, d) => setForm(f => ({ ...f, [k.name]: d.value }))} autoComplete="new-password" spellCheck={false}/>
    </Field>)}
    <div className="od-row"><div className="od-fill"/>
      {keys.filter(k => k.source === 'saved').map(k => <Button key={k.name} appearance="subtle" disabled={!online || !!busy}
        onClick={() => run(`clear:${k.name}`, () => api.clearMcpKey(server.name, k.name), 'Saved key cleared')}>
        {busy === `clear:${k.name}` ? 'Clearing…' : keys.length === 1 ? 'Clear saved key' : `Clear ${k.name}`}</Button>)}
      {keysDirty(form) && <Button appearance="subtle" disabled={!!busy} onClick={() => setForm(keysForm(keys))}>Discard</Button>}
      <Button appearance="primary" type="submit" disabled={!online || !!busy || !keysDirty(form)}>{busy === 'save' ? 'Connecting…' : 'Save'}</Button>
    </div>
  </form>;
}

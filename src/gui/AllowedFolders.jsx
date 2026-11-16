import React, { useEffect, useState } from 'react';
import { anchorId } from './settings-index';
import { Badge, Button, Field, Input, Spinner, ToggleButton } from '@fluentui/react-components';

// Settings > Allowed Folders: the folders the files MCP server may reach, each Full Access (read and write) or
// Read-Only (the server refuses every write inside it). The vault (and ZEN_FS_ROOT from .env) are always there
// with Full Access; the rest are added here, kept in vault-settings/allowed_folders.json beside the vault (where a
// bot cannot rewrite it), and the server reconnects with them at once. The harness refuses a drive root, the home
// folder or anything above it, system folders, its own settings and ledger folders, and nestings that would not
// mean what they say (AllowedFolders.place). ctx: { api, online, guard, notify }
const ACCESS = [['full', 'Full Access'], ['read_only', 'Read-Only']];
const accessLabel = v => ACCESS.find(([k]) => k === v)?.[1] || v;

function AccessToggle({ value, onChange, disabled, label }) {
  return <div className="od-row" role="radiogroup" aria-label={label}>
    {ACCESS.map(([v, text]) => <ToggleButton key={v} size="small" role="radio" aria-checked={value === v} checked={value === v}
      appearance={value === v ? 'primary' : 'outline'} disabled={disabled} onClick={() => value !== v && onChange(v)}>{text}</ToggleButton>)}
  </div>;
}

export function AllowedFoldersTab({ ctx }) {
  const { api, online, guard, notify } = ctx;
  const canPick = typeof window !== 'undefined' && typeof window.openzenbot?.pickDirectory === 'function';
  const [s, setS] = useState(null);
  const [path, setPath] = useState('');
  const [access, setAccess] = useState('full');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState({ at: '', text: '' });  // at: 'add', or the folder whose row failed

  useEffect(() => { if (online) guard(() => api.allowedFolders(), { quiet: true }).then(r => r && setS(r)); }, [online]);

  async function run(which, at, fn, done) {
    setBusy(which); setError({ at: '', text: '' });
    try { setS(await fn()); notify(done); return true; }
    catch (e) { setError({ at, text: e?.message || 'Something went wrong.' }); return false; }
    finally { setBusy(''); }
  }
  const add = async p => {
    const dir = p.trim();
    if (dir && await run('add', 'add', () => api.addAllowedFolder(dir, access), `Allowed ${dir} · ${accessLabel(access)}`)) setPath('');
  };
  async function pick() {
    const dir = await window.openzenbot.pickDirectory();
    if (dir) await add(dir);
  }

  if (!online) return <div className="resource-row"><p>The harness is offline.</p></div>;
  if (!s) return <div className="resource-row"><Spinner size="tiny" label="Loading allowed folders…"/></div>;

  const fixedLabel = { vault: 'Vault · always allowed', '.env': 'ZEN_FS_ROOT · from .env' };
  return <>
    <div className="resource-row intro-row" id={anchorId('folders', 'Allowed Folders')}><p>Folders your bots can reach through the files tool, besides the vault. With Full Access a bot can read, change, or delete anything inside the folder; with Read-Only it can only read, list, and search. Add the folders you want it working in, not whole drives. A Read-Only folder can sit inside a Full Access one to protect that corner of it. Changes reconnect the files tool right away.</p></div>
    <div className="list-panel">
      {s.fixed.map(f => <div className="resource-row od-row" key={f.path}><div className="od-field od-fill"><strong>{f.path}</strong><span className="quiet-label">{fixedLabel[f.source] || f.source}</span></div><Badge appearance="outline">Full Access</Badge></div>)}
      {s.folders.map(f => <div className="resource-row od-row-top" key={f.path}>
        <div className="od-field od-fill"><strong>{f.path}</strong>
          {!f.exists && <span className="field-hint">This folder is gone, so the files tool skips it.</span>}
          {error.at === f.path && <span className="form-error" role="alert">{error.text}</span>}
        </div>
        {!f.exists && <Badge appearance="tint" color="warning">Missing</Badge>}
        <AccessToggle value={f.access} label={`Access to ${f.path}`} disabled={!!busy}
          onChange={v => run(`acc:${f.path}`, f.path, () => api.setFolderAccess(f.path, v), `${f.path} · ${accessLabel(v)}`)}/>
        <Button appearance="subtle" disabled={!!busy} onClick={() => run(`rm:${f.path}`, f.path, () => api.removeAllowedFolder(f.path), `Removed ${f.path}`)}>{busy === `rm:${f.path}` ? 'Removing…' : 'Remove'}</Button>
      </div>)}
    </div>
    <form className="resource-row editor-form" onSubmit={e => { e.preventDefault(); add(path); }}>
      <h3 id={anchorId('folders', 'Add a folder')}>Add a folder</h3>
      <Field label="Access"><AccessToggle value={access} onChange={setAccess} disabled={!!busy} label="Access for the new folder"/></Field>
      {canPick && <div className="od-row"><Button appearance="primary" disabled={!!busy} onClick={pick}>{busy === 'add' ? 'Adding…' : 'Choose folder…'}</Button><span className="field-hint">or type its path below</span></div>}
      <Field label="Folder path" hint="The full path, like D:\Projects\site or /home/you/notes." validationMessage={error.at === 'add' ? error.text : undefined}>
        <Input value={path} placeholder="Full path to a folder" onChange={(e, d) => setPath(d.value)} autoComplete="off" spellCheck={false}/>
      </Field>
      <div className="od-row"><div className="od-fill"/><Button appearance={canPick ? 'outline' : 'primary'} type="submit" disabled={!!busy || !path.trim()}>{busy === 'add' && !canPick ? 'Adding…' : 'Add folder'}</Button></div>
    </form>
  </>;
}

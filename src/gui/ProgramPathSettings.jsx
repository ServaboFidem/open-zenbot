import React, { useEffect, useState } from 'react';
import { Badge, Button, Field, Input, Spinner } from '@fluentui/react-components';

// Settings > Harness Tools > ImageMagick (and any other host program the harness runs by full path): the path
// is kept in vault-settings/tools.json, out of the bots' reach, and the harness runs that file rather than
// whatever PATH holds. scripts/install-deps fills it in; saving here checks the file is the right program and
// new enough, and reconnects the MCP servers that need it. ctx: { api, online, guard, notify }
const SUMMARY = {
  imagemagick: 'Runs the image edits behind the magick tool: resizing, format changes, crops, contact sheets. The harness runs ImageMagick from this path, under its own security policy, not from PATH.',
};
const EXAMPLE = {
  imagemagick: 'Like C:\\Program Files\\ImageMagick-7.1.2-Q16-HDRI\\magick.exe or /opt/homebrew/bin/magick.',
};

export function ProgramPathSection({ ctx, programKey }) {
  const { api, online, guard, notify } = ctx;
  const canPick = typeof window !== 'undefined' && typeof window.openzenbot?.pickFile === 'function';
  const [s, setS] = useState(null);
  const [path, setPath] = useState('');
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');

  const take = r => { setS(r); setPath(r.saved || ''); };
  useEffect(() => { if (online) guard(() => api.programPath(programKey), { quiet: true }).then(r => r && take(r)); }, [online, programKey]);

  async function run(which, fn, done) {
    setBusy(which); setError('');
    try { const r = await fn(); take(r); notify(done(r)); }
    catch (e) { setError(e?.message || 'Something went wrong.'); }
    finally { setBusy(''); }
  }
  const save = p => run('save', () => api.setProgramPath(programKey, p.trim()), r => `${r.name}: ${r.path}`);
  async function pick() {
    const f = await window.openzenbot.pickFile();
    if (f) { setPath(f); await save(f); }
  }

  if (!online) return null;
  if (!s) return <div className="resource-row"><Spinner size="tiny" label="Checking ImageMagick…"/></div>;

  const dirty = path.trim() !== (s.saved || '');
  const badge = s.ok
    ? <Badge appearance="tint" color="success">{s.version} · {s.source === 'saved' ? 'saved path' : 'from PATH'}</Badge>
    : s.path ? <Badge appearance="tint" color="danger">Not working</Badge>
    : <Badge appearance="tint" color="warning">Not set</Badge>;
  return <form className="resource-row editor-form" onSubmit={e => { e.preventDefault(); save(path); }}>
    <div className="od-row-top tool-heading"><div className="od-field od-fill"><h3>{s.name}</h3><p>{SUMMARY[programKey] || ''}</p></div>{badge}</div>
    {s.source === 'PATH' && s.ok && <p className="field-hint">No path is saved, so the harness uses the {s.bin} it finds on PATH: {s.path}. Save a path to keep using this copy whatever PATH says.</p>}
    {!s.path && <p className="field-hint">The magick tool cannot run until a path is set. Run scripts/install-deps (install-deps.ps1 on Windows) to install {s.name} {s.min_version}+ and fill this in, or enter the path below.</p>}
    {s.problem && <p className="form-error" role="alert">{s.source === 'saved' ? 'The saved path does not work: ' : ''}{s.problem}</p>}
    {s.found && <div className="od-row"><span className="field-hint od-fill">Found {s.name} at {s.found}</span><Button appearance="outline" disabled={!!busy} onClick={() => save(s.found)}>Use this</Button></div>}
    <Field label={`Path to ${s.bin}`} hint={`${EXAMPLE[programKey] || 'The full path to the program.'} Saving runs it once to check it is ${s.name} ${s.min_version} or newer.`} validationMessage={error || undefined}>
      <Input value={path} placeholder={`Full path to ${s.bin}`} onChange={(e, d) => setPath(d.value)} autoComplete="off" spellCheck={false}/>
    </Field>
    <div className="od-row">
      {canPick && <Button appearance="outline" disabled={!!busy} onClick={pick}>Choose file…</Button>}
      <div className="od-fill"/>
      {s.saved && <Button appearance="subtle" disabled={!!busy} onClick={() => run('clear', () => api.clearProgramPath(programKey), r => `${r.name}: path cleared`)}>Clear</Button>}
      {dirty && <Button appearance="subtle" disabled={!!busy} onClick={() => { setPath(s.saved || ''); setError(''); }}>Discard</Button>}
      <Button appearance="primary" type="submit" disabled={!!busy || !dirty || !path.trim()}>{busy === 'save' ? 'Checking…' : 'Save'}</Button>
    </div>
  </form>;
}

// The MCP servers page's requirements: host programs a server needs (node, uv, ...) that are missing or too old.
// GET /mcps gives each server's deps with a problem code, a fix command and a manual download note.

export const unmetDeps = deps => (deps || []).filter(d => !d.ok);

/** The problem in plain words, for "<bin> — <problem>". */
export function depProblem(d) {
  switch (d.problem) {
    case 'missing': return 'not installed';
    case 'too_old': return d.version ? `found ${d.version}, needs ${d.min_version}`
      : `an older version is installed (${String(d.found_path || '').split(/[\\/]/).pop()})`;
    case 'not_on_path': return 'installed, but not on PATH';
    case 'probe_failed': return 'found, but its version check failed';
    default: return 'not available';
  }
}

/** The manual note's first URL as a link, with the rest of the note as its text. */
export function manualLink(d) {
  const m = /https?:\/\/\S+/.exec(d?.manual || '');
  if (!m) return null;
  const text = d.manual.replace(m[0], '').trim();
  return { href: m[0], text: text || 'Download page' };
}

/** The toast after Recheck: what state the server ended in. */
export function recheckNotice(name, st) {
  if (st?.state === 'connected') return `${name} is connected`;
  if (st?.state === 'needs_install') return `${name} still needs install`;
  return `${name} failed to start` + (st?.error ? `: ${st.error}` : '');
}

// The MCP servers page's key fields. A server's keys are the ${VAR}s its mcp.json entry names; GET /mcps
// gives each one's source (saved here, .env, or none) and last four characters, never the key itself.

/** The hint under a key's field: where it comes from and how it ends, or that it is missing. */
export function keyHint(k) {
  if (!k?.source) return 'Not set. Paste the key and save; the server reconnects with it.';
  const ends = k.hint && k.hint !== 'set' ? ` · ends in …${k.hint}` : '';
  return k.source === 'saved' ? `Saved here${ends}. Paste a new one to replace it.` : `From .env${ends}. A key saved here wins over it.`;
}

export const keysForm = keys => Object.fromEntries((keys || []).map(k => [k.name, '']));

/** What a save sends: the trimmed keys that were typed; a blank one keeps what is in force. */
export function keysBody(form) {
  return { keys: Object.fromEntries(Object.entries(form || {}).map(([k, v]) => [k, (v || '').trim()]).filter(([, v]) => v)) };
}

export const keysDirty = form => Object.values(form || {}).some(v => (v || '').trim());

/** A server whose key is missing gets a nudge in its heading. */
export const missingKeys = keys => (keys || []).filter(k => !k.source).map(k => k.name);

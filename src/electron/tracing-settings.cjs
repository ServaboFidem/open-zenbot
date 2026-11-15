// Settings > Tracing, saved in vault-settings beside the vault: the Sentry DSN wins over .env. Read once at
// launch (the harness applies a change at once; this app on its next start). The vault is found like the harness
// finds it (src/vault_settings.py owns the mapping); an older <vault>/settings/ the harness has not moved yet still reads.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

function vaultRoot(env = process.env) {
  const v = (env.ZEN_VAULT || '').trim() || '~/.zen-agents/vault';
  return v.startsWith('~') ? path.join(os.homedir(), v.slice(1)) : path.resolve(v);
}

function settingsDir(env = process.env) {
  const v = vaultRoot(env);
  return path.join(path.dirname(v), `${path.basename(v)}-settings`);
}

/** The saved DSN ('' saved means off), or null when nothing is saved, the file is missing or unreadable. */
function savedDsn(env = process.env) {
  for (const file of [path.join(settingsDir(env), 'tracing.json'), path.join(vaultRoot(env), 'settings', 'tracing.json')]) {
    let raw;
    try { raw = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { continue; }
    const s = raw && typeof raw === 'object' ? raw.sentry : null;
    return s && typeof s === 'object' ? String(s.dsn ?? '').trim() : null;
  }
  return null;
}

/** Put the saved DSN in `env` (over .env) so the main process and the preload both see it. Returns its source. */
function applySavedDsn(env = process.env) {
  const dsn = savedDsn(env);
  if (dsn === null) return (env.ZEN_GLITCHTIP_DSN || '').trim() ? '.env' : 'off';
  env.ZEN_GLITCHTIP_DSN = dsn;
  return 'saved';
}

module.exports = { vaultRoot, settingsDir, savedDsn, applySavedDsn };

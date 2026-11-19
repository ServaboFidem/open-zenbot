// Browse your vault: open the vault's root folder in the OS file manager. It takes no path from the page: the
// folder is the one the harness uses (ZEN_VAULT, else ~/.zen-agents/vault; tracing-settings.cjs vaultRoot), so the
// page cannot make the shell open anything else.
const fs = require('node:fs');
const { vaultRoot } = require('./tracing-settings.cjs');

/** Opens the vault's root. Answers '' when it opened, else a sentence to show (never a path). */
async function openVault({ env = process.env, shell } = {}) {
  const dir = vaultRoot(env);
  let found = false;
  try { found = fs.statSync(dir).isDirectory(); } catch { /* missing or unreadable */ }
  if (!found) return 'Your vault folder was not found.';
  return (await shell.openPath(dir)) ? 'Your vault could not be opened.' : '';
}

module.exports = { openVault };

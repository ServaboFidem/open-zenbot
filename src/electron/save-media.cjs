// Save… for a preview card. The renderer passes a harness URL and a suggested name, never a path to
// write: the OS save dialog picks the path, the main process fetches the bytes from the harness and
// writes them there. The dialog opens in the folder of the last save (kept in userData).
const path = require('node:path');
const { Readable } = require('node:stream');
const { pipeline } = require('node:stream/promises');

const MAX_NAME = 120;
const PICTURES = /\.(jpe?g|png|gif|webp|avif|svg|mp4|webm|mov)$/i;
const ROUTES = [/^\/media$/, /^\/rooms\/[^/]+\/attachments\/[^/]+$/, /^\/conversations\/[^/]+\/attachments\/[^/]+$/,
  /^\/conversations\/[^/]+\/design\/export\.zip$/, /^\/uploads\/[^/]+$/];

/** The name's last segment, without characters Windows refuses, trimmed, at most 120 characters with its extension kept. */
function safeName(name) {
  let base = String(name || '').split(/[\\/]/).pop();
  base = base.replace(/[<>:"/\\|?*\x00-\x1f]/g, '').replace(/^[\s.]+|[\s.]+$/g, '');
  if (!base) return 'download';
  if (base.length <= MAX_NAME) return base;
  const ext = path.extname(base).slice(0, 16);
  return base.slice(0, MAX_NAME - ext.length) + ext;
}

/** Only the harness's media route, attachment routes, a design's zip and a chat message's files, on the harness's own origin. */
function allowedUrl(url, harnessBase) {
  let u, base;
  try { u = new URL(url); base = new URL(harnessBase); } catch { return false; }
  return u.origin === base.origin && ROUTES.some(r => r.test(u.pathname));
}

const defaultFolder = (name, paths) => (PICTURES.test(name) ? paths.pictures : paths.downloads);

/** The folder of the last save, when it is remembered and still there; else null. */
function lastDir(fs, storePath) {
  try {
    const dir = JSON.parse(fs.readFileSync(storePath, 'utf8')).lastDir;
    return typeof dir === 'string' && dir && fs.existsSync(dir) ? dir : null;
  } catch { return null; }
}

/** Remember the folder of `saved` for the next dialog; a failure only loses the memory. */
function rememberDir(fs, storePath, saved) {
  try {
    fs.mkdirSync(path.dirname(storePath), { recursive: true });
    fs.writeFileSync(storePath, JSON.stringify({ lastDir: path.dirname(saved) }));
  } catch { /* the save worked; only the folder memory did not */ }
}

/** Show the save dialog, fetch `url` from the harness, write it where the user chose. */
async function saveMedia({ url, name } = {}, deps) {
  const { harnessBase, dialog, fetch, storePath, paths, fs } = deps;
  if (!allowedUrl(url, harnessBase)) return { error: 'That item cannot be saved.' };
  const file = safeName(name);
  const dir = lastDir(fs, storePath) || defaultFolder(file, paths);
  const choice = await dialog.showSaveDialog({ defaultPath: path.join(dir, file) });
  if (choice.canceled || !choice.filePath) return { canceled: true };
  const chosen = choice.filePath;
  let res;
  try { res = await fetch(url); } catch { return { error: 'The file could not be fetched (the harness did not answer).' }; }
  if (!res.ok || !res.body) return { error: `The file could not be fetched (${res.status}).` };
  try {
    await pipeline(Readable.fromWeb(res.body), fs.createWriteStream(chosen));
  } catch (e) {
    try { fs.rmSync(chosen, { force: true }); } catch { /* nothing more to clean */ }
    return { error: `The file could not be saved (${e.code || e.message}).` };
  }
  rememberDir(fs, storePath, chosen);
  return { saved: chosen };
}

module.exports = { safeName, allowedUrl, defaultFolder, lastDir, rememberDir, saveMedia };

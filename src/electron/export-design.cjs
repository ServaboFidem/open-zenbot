// Export a design as PDF or PNG. The renderer sends two ids, a kind and a title, never a URL or a path: this module
// builds the closed attachment URL on the harness's own origin (served under the preview policy, so the page loads
// nothing and has no pick bridge), the OS save dialog picks the path, and a window the main process makes
// (deps.makeWindow) lays the page out at WIDTH x VIEW_HEIGHT, as in a normal window, and renders it whole: 1440 px wide
// at its own height, capped at 16,000 px. The PDF is one page of that size. The page's own answer for its height is
// untrusted: only a number or a numeric string counts.
const path = require('node:path');
const { safeName, defaultFolder, lastDir, rememberDir } = require('./save-media.cjs');

const WIDTH = 1440;
const VIEW_HEIGHT = 900;  // the window height the page is laid out at; vh resolves against it
const MAX_HEIGHT = 16000;
const TIMEOUT_MS = 20_000;  // making the window, loading, measuring and rendering, all together
const ID = /^[A-Za-z0-9_-]{1,80}$/;
const KINDS = new Set(['pdf', 'png']);
const PX_PER_INCH = 96;  // CSS pixels; printToPDF's pageSize is in inches (Electron 45 PrintToPDFOptions)

class TimedOut extends Error {}

/** The numbered copy, served closed and without the bridge: `<base>/conversations/<id>/attachments/<id>`, no query. */
function pageUrl(harnessBase, convId, attId) {
  for (const id of [convId, attId]) if (typeof id !== 'string' || !ID.test(id)) throw new Error('not an id');
  const base = String(harnessBase).replace(/\/+$/, '');
  return `${base}/conversations/${encodeURIComponent(convId)}/attachments/${encodeURIComponent(attId)}`;
}

/** What the page said its height is, as a whole number from 1 to MAX_HEIGHT; anything else is 1. */
function pageHeight(said) {
  const n = typeof said === 'number' ? said : typeof said === 'string' && /^\s*\d+(\.\d+)?\s*$/.test(said) ? Number(said) : NaN;
  if (!Number.isFinite(n)) return 1;
  return Math.min(MAX_HEIGHT, Math.max(1, Math.floor(n)));
}

/** printToPDF's options for one page of `width` x `height` CSS pixels: no margins, backgrounds on. */
const pdfOptions = ({ width, height }) => ({
  printBackground: true,
  pageSize: { width: width / PX_PER_INCH, height: height / PX_PER_INCH },
  margins: { top: 0, bottom: 0, left: 0, right: 0 },
  preferCSSPageSize: false,
  pageRanges: '1',
});

/**
 * Run in the page (as FREEZE_UNITS, in an isolated world) before printing: every vh, vb, vmin and vmax (and their
 * s, l and d forms) in the style sheets and style attributes becomes the pixels it is in the window now. Chromium's
 * print layout resolves those units against the page box, which is the whole page here, so a 60vh hero would
 * otherwise grow with the page and push the end of the design off the single page. Uses only the page's globals.
 */
function freezeViewportUnits() {
  const h = innerHeight / 100, w = innerWidth / 100;
  const size = { vh: h, vb: h, vmin: Math.min(h, w), vmax: Math.max(h, w) };
  const unit = /(?<![\w#.])(-?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)[sld]?(vh|vb|vmin|vmax)\b/gi;
  let fixed = 0;
  const fix = style => {
    const was = style.cssText, now = was.replace(unit, (_, n, u) => `${+(Number(n) * size[u.toLowerCase()]).toFixed(3)}px`);
    if (now !== was) { style.cssText = now; fixed += 1; }
  };
  const walk = rules => { for (const r of rules) { if (r.style) fix(r.style); if (r.cssRules) walk(r.cssRules); } };
  for (const sheet of document.styleSheets) { try { walk(sheet.cssRules); } catch { /* a sheet it may not read */ } }
  for (const el of document.querySelectorAll('[style]')) fix(el.style);
  return fixed;
}
const FREEZE_UNITS = `(${freezeViewportUnits.toString()})()`;

function suggestedName(name, kind) {
  const file = safeName(`${String(name ?? '').trim()}.${kind}`);
  return file.toLowerCase().endsWith(`.${kind}`) && file.length > kind.length + 1 ? file : `design.${kind}`;
}

/** Make the window, measure the page and render it, within `ms`; the window is always closed, a late one at once. */
async function render(url, kind, makeWindow, ms) {
  let win = null, over = false, timer;
  const work = (async () => {
    const w = await makeWindow(url, { width: WIDTH, height: VIEW_HEIGHT });
    if (over) { w.close(); throw new TimedOut(); }
    win = w;
    const size = { width: WIDTH, height: pageHeight(await w.height()) };
    return kind === 'pdf' ? w.pdf(size) : w.png(size);
  })();
  work.catch(() => {});  // after a time-out nobody awaits it
  const limit = new Promise((_, reject) => { timer = setTimeout(() => reject(new TimedOut()), ms); });
  try {
    return await Promise.race([work, limit]);
  } finally {
    over = true;
    clearTimeout(timer);
    if (win) win.close();
  }
}

/** Ask where to save, render the design, write it there. Answers {saved}, {canceled: true} or {error}. */
async function exportDesign({ convId, attId, kind, name } = {}, deps) {
  const { harnessBase, dialog, makeWindow, fs, storePath, paths, timeoutMs = TIMEOUT_MS, warn = console.warn } = deps;
  let url;
  try { url = pageUrl(harnessBase, convId, attId); } catch { return { error: 'That design cannot be exported.' }; }
  if (!KINDS.has(kind)) return { error: 'That design cannot be exported.' };
  const file = suggestedName(name, kind);
  const dir = lastDir(fs, storePath) || defaultFolder(file, paths);
  const choice = await dialog.showSaveDialog({ defaultPath: path.join(dir, file) });
  if (choice.canceled || !choice.filePath) return { canceled: true };
  const chosen = choice.filePath;
  let bytes;
  try {
    bytes = await render(url, kind, makeWindow, timeoutMs);
  } catch (e) {
    // makeWindow's own load limit names its error TimeoutError
    const late = e instanceof TimedOut || e?.name === 'TimeoutError';
    // for whoever reads the main process's log: the cause (the URL in it holds only the two ids)
    warn(`export-design: ${kind} failed: ${late ? 'TimeoutError: the time limit passed' : `${e?.name || 'Error'}: ${e?.message || e}`}`);
    return { error: late ? 'The design took too long to export.' : 'The design could not be exported.' };
  }
  try {
    fs.writeFileSync(chosen, bytes);
  } catch (e) {
    try { fs.rmSync(chosen, { force: true }); } catch { /* nothing more to clean */ }
    return { error: `The file could not be saved (${e.code || e.message}).` };
  }
  rememberDir(fs, storePath, chosen);
  return { saved: chosen };
}

module.exports = { WIDTH, VIEW_HEIGHT, MAX_HEIGHT, TIMEOUT_MS, FREEZE_UNITS, pageUrl, pageHeight, pdfOptions, freezeViewportUnits, exportDesign };

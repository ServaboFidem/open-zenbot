const { app, BrowserWindow, protocol, net, ipcMain, dialog, shell, Notification, powerMonitor, session } = require('electron');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { saveMedia } = require('./save-media.cjs');
const { exportDesign, pdfOptions, FREEZE_UNITS, TIMEOUT_MS: EXPORT_TIMEOUT_MS } = require('./export-design.cjs');
const { loadEnvFile } = require('./env-file.cjs');
const { createArrival } = require('./arrival.cjs');
const { createIdleReport } = require('./idle-report.cjs');
const { applySavedDsn, settingsDir } = require('./tracing-settings.cjs');
const { registerMorningEmail } = require('./morning-email.cjs');
const { registerTelnyxTexts } = require('./telnyx-texts.cjs');
const { allowFrameNavigation } = require('./frame-nav.cjs');
const { createUpdateChecker } = require('./update-check.cjs');
const { runtimePaths } = require('./runtime-paths.cjs');

// The installed app (or ZEN_MANAGED=1 from the repo) sets itself up, runs its own harness and checks for updates
// (managed-app.cjs); a developer run does none of that. Its bundled uv and harness are in <resources>: from the repo
// only ZEN_RESOURCES points at them, else a folder that does not exist, so setup fails at its first uv step.
const managed = app.isPackaged || process.env.ZEN_MANAGED === '1';
// one window: a second launch hands over to the first ('second-instance' below) and quits
if (managed && !app.requestSingleInstanceLock()) { app.quit(); return; }
const resources = app.isPackaged ? process.resourcesPath : (process.env.ZEN_RESOURCES || path.join(__dirname, 'no-bundled-resources'));
const paths = managed ? runtimePaths({ userData: app.getPath('userData'), resources, platform: process.platform }) : null;

// first: every setting below reads the environment; the .env (the repo's, or <userData>/.env for the installed
// app) fills what the shell left unset
const envLoaded = loadEnvFile(managed ? paths.envFile : path.join(__dirname, '..', '..', '.env'));
if (envLoaded.length) console.log(`.env: ${envLoaded.length} ${envLoaded.length === 1 ? 'setting' : 'settings'} (${envLoaded.sort().join(', ')})`);

// Settings > Tracing's saved DSN wins over .env (and '' saved turns it off); the preload reads the same env
// a relative ZEN_VAULT counts from the folder the harness runs in: the installed app's own harness runs in paths.harness
const vaultBase = managed ? paths.harness : process.cwd();
const dsnSource = applySavedDsn(process.env, vaultBase);
if (dsnSource === 'saved') console.log('sentry: DSN from vault-settings/tracing.json');

// The installed app runs its own harness, always on 127.0.0.1: ZEN_HARNESS_URL can only move its port. The URL is
// written back, so the page (the preload reads the same env), the harness it starts and everything below name one.
const managedPort = managed ? (require('./harness-process.cjs').ownPort(process.env.ZEN_HARNESS_URL) || 8765) : null;
if (managed) process.env.ZEN_HARNESS_URL = `http://127.0.0.1:${managedPort}`;

// The harness this app talks to: frame navigation, arrival polling and Save… all name the same base.
const harnessBase = () => (process.env.ZEN_HARNESS_URL || 'http://127.0.0.1:8765').replace(/\/+$/, '');

// ZEN_GLITCHTIP_DSN set: errors and crashes from the shell, errors and traces from the renderer, go to GlitchTip.
// Started before the app is ready. The renderer reaches it over IPC only (the bundled preload), never the network.
const glitchtipDsn = (process.env.ZEN_GLITCHTIP_DSN || '').trim();
if (glitchtipDsn) {
  const Sentry = require('@sentry/electron/main');
  // errors and crashes only: the main process's own requests are the app's files; the renderer traces
  Sentry.init({ dsn: glitchtipDsn, sendDefaultPii: false, dataCollection: { userInfo: false }, ipcMode: Sentry.IPCMode.Classic });
}

// The renderer is served from its own origin, app://openzenbot, so the harness
// can admit exactly that origin in CORS (ZEN_GUI_ORIGINS) and the page's
// CSP can name the harness loopback origin as its only connect target.
protocol.registerSchemesAsPrivileged([{ scheme: 'app', privileges: { standard: true, secure: true, supportFetchAPI: true } }]);
const ROOT = path.join(__dirname, '..', '..', 'dist');  // the built app: `npm run build`

function serveApp(request) {
  const { pathname } = new URL(request.url);
  const rel = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '');
  const file = path.normalize(path.join(ROOT, rel));
  if (!file.startsWith(ROOT + path.sep) || rel.includes('..')) return new Response('not found', { status: 404 });
  return net.fetch(pathToFileURL(file).toString());
}

const appWindows = new WeakSet();
// page: index.html (the workspace) or setup.html (the installed app's setup and start screen)
function createWindow(page = 'index.html') {
  const win = new BrowserWindow({
    width: 1440, height: 960, minWidth: 375, minHeight: 640,
    title: 'OpenZenBot', icon: path.join(__dirname, '..', 'assets', 'zenbot-enso-icon.png'), backgroundColor: '#f5f5f5', autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(ROOT, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true,
      // the sandboxed preload cannot read package.json: it reads these from process.argv
      additionalArguments: [`--zen-version=${app.getVersion()}`, `--zen-managed=${managed ? '1' : '0'}`],
    }
  });
  appWindows.add(win.webContents);
  // links in a bot's reply (target=_blank) open in the system browser; only http(s), never a new app window
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (event, url) => {
    if (url !== win.webContents.getURL()) event.preventDefault();
  });
  // a framed page (a bot's design) can navigate its own frame, which will-navigate never sees: keep every
  // subframe on the harness's own origin or this machine
  win.webContents.on('will-frame-navigate', (event) => {
    if (!event.isMainFrame && !allowFrameNavigation(event.url, harnessBase())) event.preventDefault();
  });
  win.loadURL(`app://openzenbot/${page}`);
  return win;
}
// Export as PDF or PNG: an offscreen window for one bot-made page (export-design.cjs builds its URL, never the renderer).
// Its scripts run as in the preview, under the harness's closed policy; the window has no preload, its own in-memory
// session (no permission, no download), goes nowhere else and opens nothing. A load that fails, answers other than
// 200 or takes longer than the limit closes it. Once loaded, the DevTools protocol pins the viewport at WIDTH x
// VIEW_HEIGHT and scale 1, as in a normal window on any display, and it stays so: the PNG is captured beyond that
// viewport (so vh is what it is on screen, and a 16,000 px page is not doubled on a display scaled above 1), and the
// PDF, whose print layout would resolve vh against the whole page, is printed after vh-style units are fixed to their
// on-screen pixels (FREEZE_UNITS). A hidden, non-offscreen window was tried first: a protocol screenshot never answered
// there, and capturePage after a resize sometimes failed (UnknownVizError).
const MEASURE = '(() => { const d = document.documentElement, b = document.body; return Math.max(d.scrollHeight, b ? b.scrollHeight : 0); })()';
let exportSessionReady = false;
function exportSession() {
  const ses = session.fromPartition('export-design');  // no persist: prefix, so in memory only
  if (!exportSessionReady) {
    ses.setPermissionRequestHandler((_wc, _permission, answer) => answer(false));
    ses.setPermissionCheckHandler(() => false);
    ses.setDevicePermissionHandler(() => false);
    ses.on('will-download', event => event.preventDefault());
    exportSessionReady = true;
  }
  return ses;
}
async function makeExportWindow(url, { width, height }) {
  const win = new BrowserWindow({
    show: false, width, height, useContentSize: true,
    webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, session: exportSession(), offscreen: true, backgroundThrottling: false },
  });
  const wc = win.webContents;
  // One limit for everything this window does, from its making: when it passes the window is destroyed, whatever the
  // page is doing (a page that blocks its main thread after load would otherwise hold any call that needs it), and
  // every wait below races it (bounded), so none can outlive the window.
  let timer;
  const close = () => { clearTimeout(timer); if (!win.isDestroyed()) win.destroy(); };
  const limit = new Promise((_, reject) => {
    timer = setTimeout(() => { close(); reject(Object.assign(new Error('the export did not finish in time'), { name: 'TimeoutError' })); }, EXPORT_TIMEOUT_MS);
  });
  limit.catch(() => {});  // nobody may be waiting when it fires
  const bounded = work => Promise.race([work, limit]);
  const refuse = event => event.preventDefault();
  wc.on('will-navigate', refuse);
  wc.on('will-redirect', refuse);
  wc.on('will-frame-navigate', refuse);
  wc.setWindowOpenHandler(() => ({ action: 'deny' }));
  const cdp = wc.debugger;
  let status = 0;
  wc.once('did-navigate', (_event, _url, code) => { status = code; });
  try {
    await bounded(wc.loadURL(url));
    if (status !== 200) throw new Error(`the page answered ${status}`);
    // the viewport the page is measured, captured and frozen at, whatever size and scale the display gives the window
    // (with the display at scale 2 the window alone came out about 700 px tall)
    cdp.attach('1.3');
    await bounded(cdp.sendCommand('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false }));
  } catch (e) { close(); throw e; }
  return {
    // measured in a world of its own, so the page's scripts cannot replace what measures it; still only a number
    height: () => bounded(wc.executeJavaScriptInIsolatedWorld(1001, [{ code: MEASURE }])),
    pdf: async size => {
      await bounded(wc.executeJavaScriptInIsolatedWorld(1001, [{ code: FREEZE_UNITS }]));
      return bounded(wc.printToPDF(pdfOptions(size)));
    },
    png: async ({ width: w, height: h }) => {
      const shot = await bounded(cdp.sendCommand('Page.captureScreenshot',
        { format: 'png', clip: { x: 0, y: 0, width: w, height: h, scale: 1 }, captureBeyondViewport: true }));
      const png = Buffer.from(String(shot?.data || ''), 'base64');
      if (!png.length) throw new Error('the capture came back empty');
      return png;
    },
    close,
  };
}

// IPC from this app's own pages only (never a framed page or another origin)
const fromApp = (event, page = '') => String(event.senderFrame?.url || '').startsWith(`app://openzenbot/${page}`);
// The update check, on GitHub Releases: Settings checks by hand in every run; the installed app also checks at launch
// and daily (update-check.cjs). open() opens only this repo's release pages.
const updateStore = () => path.join(app.getPath('userData'), 'update-check.json');
const checker = createUpdateChecker({
  version: app.getVersion(),
  fetch: (url, opts) => net.fetch(url, opts),
  readStore: () => JSON.parse(require('node:fs').readFileSync(updateStore(), 'utf8')),
  writeStore: s => require('node:fs').writeFileSync(updateStore(), JSON.stringify(s)),
  openExternal: url => shell.openExternal(url),
});
function handleUpdates() {
  const handle = (channel, fn) => ipcMain.handle(channel, (event, arg) => {
    if (!fromApp(event)) throw new Error('not allowed');
    return fn(arg);
  });
  handle('updates:get', () => checker.get());
  handle('updates:check', () => checker.check());
  handle('updates:skip', v => {
    if (typeof v !== 'string' || v.length > 32) throw new Error('a version is a short string');
    return checker.skip(v);
  });
  handle('updates:set-auto', on => {
    if (typeof on !== 'boolean') throw new Error('on is true or false');
    return checker.setAuto(on);
  });
  handle('updates:open', () => checker.open());
}
let arrivalStarted = false;
// A new weekly digest: poll the harness every 60 s; the notice's click brings the window forward and opens Music > This week.
function startArrivalPolling() {
  if (arrivalStarted) return;
  arrivalStarted = true;
  const base = harnessBase();
  const store = path.join(app.getPath('userData'), 'arrival-notified.json');
  const fs = require('node:fs');
  const arrival = createArrival({
    load: () => { try { const v = JSON.parse(fs.readFileSync(store, 'utf8')); return Array.isArray(v) ? v : null; } catch { return null; } },
    save: ids => fs.writeFileSync(store, JSON.stringify(ids)),
    get: async p => { const r = await net.fetch(base + p); if (!r.ok) throw new Error(String(r.status)); return r.json(); },
    show: ({ title, body }) => {
      if (!Notification.isSupported()) return;
      const n = new Notification({ title, body });
      n.on('click', () => {
        let win = BrowserWindow.getAllWindows()[0];
        if (!win) { createWindow(); win = BrowserWindow.getAllWindows()[0]; }
        if (win.isMinimized()) win.restore();
        win.show(); win.focus();
        const open = () => win.webContents.send('open-music');
        if (win.webContents.isLoading()) win.webContents.once('did-finish-load', () => setTimeout(open, 500)); else open();
      });
      n.show();
    },
  });
  arrival.poll();
  setInterval(() => arrival.poll(), 60_000);
  startIdleReport();
}
// The computer's idle time goes to the harness every 60 s, and at once when the user comes back (checked every 5 s),
// so it knows when it may dream (POST /dream/idle).
let idleReport = null;
function startIdleReport() {
  if (idleReport) return;
  const base = harnessBase();
  idleReport = createIdleReport({
    idleSeconds: () => powerMonitor.getSystemIdleTime(),
    post: async (p, body) => {
      await net.fetch(base + p, { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) });
    },
  });
  idleReport.start();
}
app.whenReady().then(() => {
  protocol.handle('app', serveApp);
  // The app's IPC channels (Sentry adds its own when the DSN is set): the renderer asks for a directory or a file, the user
  // picks one in the OS dialog; or it asks to save a preview, and the user picks where in the OS save dialog. Then the
  // update check's updates:* (handleUpdates) and, in the installed app, the setup page's setup:* (managed-app.cjs).
  ipcMain.handle('pick-directory', async () => {
    const r = await dialog.showOpenDialog({ properties: ['openDirectory'] });
    return r.canceled ? null : r.filePaths[0];
  });
  ipcMain.handle('pick-file', async () => {
    const r = await dialog.showOpenDialog({ properties: ['openFile'] });
    return r.canceled ? null : r.filePaths[0];
  });
  // Browse your vault: the vault's root in the OS file manager. The page sends no path (open-vault.cjs)
  ipcMain.handle('open-vault', event => {
    if (!fromApp(event)) throw new Error('not allowed');
    return require('./open-vault.cjs').openVault({ shell, base: vaultBase });
  });
  // Save…: the page names a harness URL and a file name; the dialog names the path; nothing else is written
  ipcMain.handle('save-media', (event, args) => saveMedia(args, {
    harnessBase: harnessBase(),
    dialog: { showSaveDialog: opts => dialog.showSaveDialog(BrowserWindow.fromWebContents(event.sender), opts) },
    fetch: url => net.fetch(url),
    storePath: path.join(app.getPath('userData'), 'media-save.json'),
    paths: { pictures: app.getPath('pictures'), downloads: app.getPath('downloads') },
    fs: require('node:fs'),
  }));
  // Export as PDF or PNG: the page sends two ids, a kind and a title; the URL is built here and the dialog names the path
  ipcMain.handle('export-design', (event, args) => {
    if (!fromApp(event)) throw new Error('not allowed');
    const { convId, attId, kind, name } = args || {};
    return exportDesign({ convId, attId, kind, name }, {
      harnessBase: harnessBase(),
      dialog: { showSaveDialog: opts => dialog.showSaveDialog(BrowserWindow.fromWebContents(event.sender), opts) },
      makeWindow: makeExportWindow,
      storePath: path.join(app.getPath('userData'), 'media-save.json'),
      paths: { pictures: app.getPath('pictures'), downloads: app.getPath('downloads') },
      fs: require('node:fs'),
    });
  });
  registerMorningEmail(ipcMain, appWindows, settingsDir(process.env, vaultBase));
  registerTelnyxTexts(ipcMain, appWindows, settingsDir(process.env, vaultBase));
  handleUpdates();
  if (managed) {
    require('./managed-app.cjs').startManaged({ app, ipcMain, shell, net, createWindow, checker, paths, port: managedPort, fromApp, startArrivalPolling });
    return;
  }
  createWindow();
  startArrivalPolling();
  app.on('activate', () => { if (!BrowserWindow.getAllWindows().length) createWindow(); });
});
app.on('before-quit', () => { if (idleReport) idleReport.stop(); });
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });

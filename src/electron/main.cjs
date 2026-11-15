const { app, BrowserWindow, protocol, net, ipcMain, dialog, shell, Notification } = require('electron');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const { saveMedia } = require('./save-media.cjs');
const { loadEnvFile } = require('./env-file.cjs');
const { createArrival } = require('./arrival.cjs');
const { applySavedDsn } = require('./tracing-settings.cjs');

// first: every setting below reads the environment; the repo's .env fills what the shell left unset
const envLoaded = loadEnvFile(path.join(__dirname, '..', '..', '.env'));
if (envLoaded.length) console.log(`.env: ${envLoaded.length} ${envLoaded.length === 1 ? 'setting' : 'settings'} (${envLoaded.sort().join(', ')})`);

// Settings > Tracing's saved DSN wins over .env (and '' saved turns it off); the preload reads the same env
const dsnSource = applySavedDsn();
if (dsnSource === 'saved') console.log('sentry: DSN from vault-settings/tracing.json');

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

function createWindow() {
  const win = new BrowserWindow({
    width: 1440, height: 960, minWidth: 375, minHeight: 640,
    title: 'OpenZenBot', icon: path.join(__dirname, '..', 'assets', 'zenbot-logo.png'), backgroundColor: '#f5f5f5', autoHideMenuBar: true,
    webPreferences: { preload: path.join(ROOT, 'preload.cjs'), nodeIntegration: false, contextIsolation: true, sandbox: true }
  });
  // links in a bot's reply (target=_blank) open in the system browser; only http(s), never a new app window
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (event, url) => {
    if (url !== win.webContents.getURL()) event.preventDefault();
  });
  win.loadURL('app://openzenbot/index.html');
}
// A new weekly digest: poll the harness every 60 s; the notice's click brings the window forward and opens Music > This week.
function startArrivalPolling() {
  const base = (process.env.ZEN_HARNESS_URL || 'http://127.0.0.1:8765').replace(/\/+$/, '');
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
}
app.whenReady().then(() => {
  protocol.handle('app', serveApp);
  // The app's only IPC channels (Sentry adds its own when the DSN is set): the renderer asks for a directory or a file, the user
  // picks one in the OS dialog; or it asks to save a preview, and the user picks where in the OS save dialog.
  ipcMain.handle('pick-directory', async () => {
    const r = await dialog.showOpenDialog({ properties: ['openDirectory'] });
    return r.canceled ? null : r.filePaths[0];
  });
  ipcMain.handle('pick-file', async () => {
    const r = await dialog.showOpenDialog({ properties: ['openFile'] });
    return r.canceled ? null : r.filePaths[0];
  });
  // Save…: the page names a harness URL and a file name; the dialog names the path; nothing else is written
  ipcMain.handle('save-media', (event, args) => saveMedia(args, {
    harnessBase: (process.env.ZEN_HARNESS_URL || 'http://127.0.0.1:8765').replace(/\/+$/, ''),
    dialog: { showSaveDialog: opts => dialog.showSaveDialog(BrowserWindow.fromWebContents(event.sender), opts) },
    fetch: url => net.fetch(url),
    storePath: path.join(app.getPath('userData'), 'media-save.json'),
    paths: { pictures: app.getPath('pictures'), downloads: app.getPath('downloads') },
    fs: require('node:fs'),
  }));
  createWindow();
  startArrivalPolling();
  app.on('activate', () => { if (!BrowserWindow.getAllWindows().length) createWindow(); });
});
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });

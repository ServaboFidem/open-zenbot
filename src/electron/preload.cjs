// The bridge between the renderer and the shell: where the harness listens,
// two calls that open the OS directory or file picker, one that opens the vault's root in the file manager, Save… for a
// preview and Export for a design as PDF or PNG (the OS save dialog picks the path), and one subscription for the
// new-digest notice click. No key, no filesystem beyond
// those dialogs. The renderer talks to the harness itself over fetch
// and EventSource, under the page's CSP. Bundled into dist/ by build-gui, so the
// sandboxed preload can carry Sentry's IPC bridge.
// Also the app's version, whether it is the installed (managed) app, the update check (updates.*) and the setup
// page's few calls (setup.*; the main process answers them from setup.html only). The version and the managed
// switch come from the main process as --zen-* arguments (webPreferences.additionalArguments), since a sandboxed
// preload cannot read package.json.
const { contextBridge, ipcRenderer } = require('electron');
const harnessUrl = (process.env.ZEN_HARNESS_URL || 'http://127.0.0.1:8765').replace(/\/+$/, '');
const zenArg = name => {
  const prefix = `--zen-${name}=`;
  const found = (process.argv || []).find(a => a.startsWith(prefix));
  return found ? found.slice(prefix.length) : '';
};
const subscribe = (channel, cb) => {
  const handler = (_event, value) => cb(value);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
};
contextBridge.exposeInMainWorld('openzenbot', Object.freeze({
  harnessUrl,
  origin: 'app://openzenbot',
  version: zenArg('version'),
  managed: zenArg('managed') === '1',
  pickDirectory: () => ipcRenderer.invoke('pick-directory'),
  pickFile: () => ipcRenderer.invoke('pick-file'),
  // Browse your vault: no path is sent; answers '' or a sentence to show
  openVault: () => ipcRenderer.invoke('open-vault'),
  saveMedia: (url, name) => ipcRenderer.invoke('save-media', { url, name }),
  // a design as PDF or PNG: two ids, never a URL; the main process builds the URL and the dialog picks the path
  exportDesign: (convId, attId, kind, name) => ipcRenderer.invoke('export-design', { convId, attId, kind, name }),
  // the main process tells the page to open Music > This week (the user clicked the new-digest notice); returns an unsubscribe
  onOpenMusic: cb => {
    const handler = () => cb();
    ipcRenderer.on('open-music', handler);
    return () => ipcRenderer.removeListener('open-music', handler);
  },
  updates: Object.freeze({
    get: () => ipcRenderer.invoke('updates:get'),
    check: () => ipcRenderer.invoke('updates:check'),
    skip: version => ipcRenderer.invoke('updates:skip', version),
    setAuto: on => ipcRenderer.invoke('updates:set-auto', on),
    open: () => ipcRenderer.invoke('updates:open'),
  }),
  setup: Object.freeze({
    state: () => ipcRenderer.invoke('setup:state'),
    onState: cb => subscribe('setup:state', cb),
    retry: () => ipcRenderer.invoke('setup:retry'),
    proceed: () => ipcRenderer.invoke('setup:proceed'),
    openLogs: () => ipcRenderer.invoke('setup:open-logs'),
    restartHarness: () => ipcRenderer.invoke('setup:restart-harness'),
  }),
}));
// ZEN_GLITCHTIP_DSN set: the bridge Sentry's renderer SDK sends its events over, IPC to the main process
if ((process.env.ZEN_GLITCHTIP_DSN || '').trim()) require('@sentry/electron/preload');

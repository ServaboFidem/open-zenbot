// The bridge between the renderer and the shell: where the harness listens,
// two calls that open the OS directory or file picker, Save… for a
// preview (the OS save dialog picks the path), and one subscription for the
// new-digest notice click. No key, no filesystem beyond
// those dialogs. The renderer talks to the harness itself over fetch
// and EventSource, under the page's CSP. Bundled into dist/ by build-gui, so the
// sandboxed preload can carry Sentry's IPC bridge.
const { contextBridge, ipcRenderer } = require('electron');
const harnessUrl = (process.env.ZEN_HARNESS_URL || 'http://127.0.0.1:8765').replace(/\/+$/, '');
contextBridge.exposeInMainWorld('openzenbot', Object.freeze({
  harnessUrl,
  origin: 'app://openzenbot',
  pickDirectory: () => ipcRenderer.invoke('pick-directory'),
  pickFile: () => ipcRenderer.invoke('pick-file'),
  saveMedia: (url, name) => ipcRenderer.invoke('save-media', { url, name }),
  // the main process tells the page to open Music > This week (the user clicked the new-digest notice); returns an unsubscribe
  onOpenMusic: cb => {
    const handler = () => cb();
    ipcRenderer.on('open-music', handler);
    return () => ipcRenderer.removeListener('open-music', handler);
  },
}));
// ZEN_GLITCHTIP_DSN set: the bridge Sentry's renderer SDK sends its events over, IPC to the main process
if ((process.env.ZEN_GLITCHTIP_DSN || '').trim()) require('@sentry/electron/preload');

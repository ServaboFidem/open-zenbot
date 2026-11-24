// The installed app's launch, put together with the real parts: the window on setup.html, the update checker,
// setup (setup.cjs) with the real runner, tar, verified downloads and disk check (managed-host.cjs), the harness
// (harness-process.cjs) with its rotated log, and the setup page's IPC. The order of things is managed.cjs's.
// Called from main.cjs once the app is ready, only when the app is managed.
const fs = require('node:fs');
const nodeNet = require('node:net');
const path = require('node:path');
const { spawn, execFile } = require('node:child_process');
const { createManaged } = require('./managed.cjs');
const { needsSetup, createSetup } = require('./setup.cjs');
const { createHarness, rotateLog } = require('./harness-process.cjs');
const { downloadVerified } = require('./download.cjs');
const pins = require('./pins.json');
const host = require('./managed-host.cjs');

const SETUP_PAGE = 'app://openzenbot/setup.html';
const WORKSPACE_PAGE = 'app://openzenbot/index.html';
const HEALTH_TIMEOUT_MS = 3000;
const sleep = ms => new Promise(r => setTimeout(r, ms));

// ZEN_SETUP_LOG=1 (testing by hand): each state goes to stdout as one line, and the page is saved as
// <logs>/setup-page.png shortly after, since nothing else shows what the window shows.
function trace(paths, winOf) {
  if (process.env.ZEN_SETUP_LOG !== '1') return () => {};
  let timer = null;
  return state => {
    const s = state.setup;
    const steps = s ? s.steps.map(x => `${x.id}:${x.status}`).join(',') : '';
    console.log(`setup-log: phase=${state.phase} steps=${steps} error=${JSON.stringify(s && s.error)} harness=${JSON.stringify(state.harness)} update=${JSON.stringify(state.update && { latest: state.update.latest, notify: state.update.notify, error: state.update.error })}`);
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const win = winOf();
      if (!win || win.isDestroyed()) return;
      try {
        const image = await win.webContents.capturePage();
        fs.mkdirSync(paths.logs, { recursive: true });
        fs.writeFileSync(path.join(paths.logs, 'setup-page.png'), image.toPNG());
        console.log(`setup-log: page saved (${win.webContents.getURL()})`);
      } catch (e) { console.log(`setup-log: capture failed: ${e.message}`); }
    }, 1500);
  };
}

// port: the harness's port, the one in ZEN_HARNESS_URL (main.cjs), so the page and the harness it starts agree.
function startManaged({ app, ipcMain, shell, net, createWindow, checker, paths, port = 8765, fromApp, startArrivalPolling, hidden = false }) {
  const platform = process.platform;
  const appVersion = app.getVersion();
  const lockHash = host.fileSha256(fs, path.join(paths.harness, 'uv.lock'));
  const killTree = host.killTreeWith({ execFile });
  let win = createWindow('setup.html');
  let current = SETUP_PAGE;
  const winOf = () => (win && !win.isDestroyed() ? win : null);
  const log = trace(paths, winOf);
  let workspace = false;
  let harnessLog = null;

  function show(page) {
    const w = winOf();
    workspace = page === 'workspace';
    const url = workspace ? WORKSPACE_PAGE : SETUP_PAGE;
    if (w && current !== url) { current = url; w.loadURL(url); }  // from here: will-navigate refuses the page's own moves
    if (workspace) startArrivalPolling();
  }

  function makeSetup(emit) {
    fs.mkdirSync(paths.logs, { recursive: true });
    const setupLog = fs.createWriteStream(path.join(paths.logs, 'setup.log'), { flags: 'w' }).on('error', () => {});
    const run = host.createRun({ spawn, platform, killTree, log: l => setupLog.write(`${l}\n`) });
    return createSetup({
      paths, platform, arch: process.arch, appVersion, lockHash, pins, run, fs, emit,
      download: opts => downloadVerified({ ...opts, fetch: (url, init) => net.fetch(url, init), fs }),
      extract: host.createExtract({ run, platform }),
      freeBytes: host.freeBytesWith(fs),
      findNode: host.findNodeWith({ platform, fs, execFile, managedDir: paths.nodeBin }),
      runtimeExists: async () => host.readStamp(fs, paths.stamp) !== null || host.isFile(fs, paths.python),
    });
  }

  async function makeHarness() {
    if (harnessLog) { const old = harnessLog; await new Promise(r => old.end(r)); }
    rotateLog({ dir: paths.logs, fs, platform });
    harnessLog = fs.createWriteStream(path.join(paths.logs, 'harness.log'), { flags: 'a' }).on('error', () => {});
    return createHarness({
      paths, platform, port, spawn, killTree, logStream: harnessLog, sleep,
      fetch: url => fetch(url, { signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS) }),
      portInUse: host.portInUseWith(nodeNet),
    });
  }

  const ctl = createManaged({
    checker, makeSetup, makeHarness, show, hidden,
    reveal: () => { const w = winOf(); if (w) { if (w.isMinimized()) w.restore(); w.show(); } },
    needsSetup: async () => needsSetup({
      stamp: host.readStamp(fs, paths.stamp), appVersion, lockHash, pythonPresent: host.isFile(fs, paths.python),
    }),
    send: state => {
      log(state);
      const w = winOf();
      if (w) w.webContents.send('setup:state', state);
    },
    startChecker: () => checker.start(),
    logError: e => {
      console.error(`managed launch: ${(e && e.stack) || e}`);
      fs.mkdirSync(paths.logs, { recursive: true });
      fs.appendFileSync(path.join(paths.logs, 'setup.log'), `OpenZenBot could not start: ${(e && e.stack) || e}\n`);
    },
  });

  // the setup page's IPC, from setup.html only; none takes an argument
  const handle = (channel, fn) => ipcMain.handle(channel, event => {
    if (!fromApp(event, 'setup.html')) throw new Error('not allowed');
    return fn();
  });
  handle('setup:state', () => ctl.state());
  handle('setup:proceed', () => { ctl.proceed(); });
  handle('setup:retry', () => { ctl.retry(); });
  handle('setup:restart-harness', () => { ctl.restartHarness(); });
  handle('setup:open-logs', async () => {
    fs.mkdirSync(paths.logs, { recursive: true });
    return shell.openPath(paths.logs);
  });

  const bringBack = () => {
    const w = winOf();
    if (!w) return false;
    if (w.isMinimized()) w.restore();
    w.show(); w.focus();
    return true;
  };
  app.on('second-instance', bringBack);
  // macOS Dock: a window hidden to the tray is shown; with none, one is made
  app.on('activate', () => { if (!bringBack()) win = createWindow(workspace ? 'index.html' : 'setup.html'); });

  // quit: cancel setup (not waited for: a Node download ends on its own), stop the harness once, then quit
  let quitReady = false;
  app.on('before-quit', event => {
    if (quitReady) return;
    event.preventDefault();
    ctl.shutdown().catch(() => {}).finally(() => { quitReady = true; app.quit(); });
  });

  ctl.run();  // never rejects: an error lands on the failed phase
}

module.exports = { startManaged };

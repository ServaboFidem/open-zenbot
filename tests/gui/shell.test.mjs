// The Electron shell: the preload exposes only the harness URL, the two OS pickers, Save…, Export, the version, the
// update check and the setup page's few calls; the CSP
// lets the renderer connect to the harness loopback origin and nothing else;
// the main process serves the app from its own origin so CORS can name it.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import Module from 'node:module';
import { readFileSync } from 'node:fs';

const require = createRequire(import.meta.url);

function loadPreload(env, argv = []) {
  const exposed = {};
  const invoked = [];
  const listeners = [];
  const stub = {
    contextBridge: { exposeInMainWorld: (name, value) => { exposed[name] = value; } },
    ipcRenderer: { on: (ch, fn) => { listeners.push([ch, fn]); }, removeListener: () => {}, invoke: (ch, ...a) => { invoked.push([ch, ...a]); return Promise.resolve('F:/picked'); }, send: () => {} },
  };
  const original = Module._load;
  Module._load = function (request, ...rest) { return request === 'electron' ? stub : original.call(this, request, ...rest); };
  const saved = { ...process.env };
  const savedArgv = process.argv;
  process.argv = [...savedArgv, ...argv];
  Object.assign(process.env, { ZEN_GLITCHTIP_DSN: '' }, env);
  const hadWindow = 'window' in globalThis;
  if (!hadWindow) globalThis.window = {};  // the sentry bridge writes window.__SENTRY_IPC__
  try {
    delete require.cache[require.resolve('../../src/electron/preload.cjs')];
    require('../../src/electron/preload.cjs');
    delete require.cache[require.resolve('@sentry/electron/preload')];
  } finally { Module._load = original; process.env = saved; process.argv = savedArgv; if (!hadWindow) delete globalThis.window; }
  return { exposed, invoked, listeners };
}

const KEYS = ['briefingKeeper', 'exportDesign', 'harnessUrl', 'managed', 'morningEmail', 'onOpenMusic', 'openVault', 'origin', 'pickDirectory', 'pickFile', 'saveMedia', 'setup', 'telnyxTexts', 'updates', 'version'];

test('preload exposes only the workspace controls and named settings groups', async () => {
  const { exposed, invoked } = loadPreload({ ZEN_HARNESS_URL: '' });
  assert.deepEqual(Object.keys(exposed), ['openzenbot']);
  assert.deepEqual(Object.keys(exposed.openzenbot).sort(), KEYS);
  assert.equal(exposed.openzenbot.harnessUrl, 'http://127.0.0.1:8765');
  assert.equal(exposed.openzenbot.origin, 'app://openzenbot');
  assert.equal(await exposed.openzenbot.pickDirectory(), 'F:/picked');
  assert.equal(await exposed.openzenbot.pickFile(), 'F:/picked');
  assert.equal(await exposed.openzenbot.saveMedia('http://127.0.0.1:8765/media?url=x&ok=1', 'a.png'), 'F:/picked');
  assert.equal(await exposed.openzenbot.exportDesign('c1', 'a1', 'pdf', 'Page'), 'F:/picked');
  assert.deepEqual(invoked, [['pick-directory'], ['pick-file'], ['save-media', { url: 'http://127.0.0.1:8765/media?url=x&ok=1', name: 'a.png' }],
    ['export-design', { convId: 'c1', attId: 'a1', kind: 'pdf', name: 'Page' }]]);
  assert.equal(loadPreload({ ZEN_HARNESS_URL: 'http://127.0.0.1:9000' }).exposed.openzenbot.harnessUrl, 'http://127.0.0.1:9000');
});

test('morning email bridge passes just the recipient and time', async () => {
  const { exposed, invoked } = loadPreload({});
  const bridge = exposed.openzenbot.morningEmail;
  assert.deepEqual(Object.keys(bridge).sort(), ['get', 'save']);
  await bridge.get();
  await bridge.save({ recipient: 'owner@example.com', time: '07:00', path: 'other', send: true });
  assert.deepEqual(invoked, [['morning-email:get'], ['morning-email:save', { recipient: 'owner@example.com', time: '07:00' }]]);
});

test('telnyx texts bridge passes just the five settings', async () => {
  const { exposed, invoked } = loadPreload({});
  const bridge = exposed.openzenbot.telnyxTexts;
  assert.deepEqual(Object.keys(bridge).sort(), ['get', 'save']);
  await bridge.get();
  await bridge.save({ phone: '+15551234567', lead_minutes: 30, evening: '20:00', before: true, rundown: false, to: 'other' });
  assert.deepEqual(invoked, [['telnyx-texts:get'],
    ['telnyx-texts:save', { phone: '+15551234567', lead_minutes: 30, evening: '20:00', before: true, rundown: false }]]);
});

test('preload reads the version and the managed switch from the main process\'s --zen-* arguments', () => {
  const dev = loadPreload({}).exposed.openzenbot;
  assert.equal(dev.version, '');
  assert.equal(dev.managed, false);
  const app = loadPreload({}, ['--zen-version=0.1.0', '--zen-managed=1']).exposed.openzenbot;
  assert.equal(app.version, '0.1.0');
  assert.equal(app.managed, true);
  assert.equal(loadPreload({ ZEN_MANAGED: '1' }, ['--zen-managed=0']).exposed.openzenbot.managed, false);
});

test('preload updates and setup are one invoke each, and onState returns an unsubscribe', async () => {
  const { exposed, invoked, listeners } = loadPreload({});
  const { updates, setup } = exposed.openzenbot;
  assert.deepEqual(Object.keys(updates).sort(), ['check', 'get', 'open', 'setAuto', 'skip']);
  assert.deepEqual(Object.keys(setup).sort(), ['onState', 'openLogs', 'proceed', 'restartHarness', 'retry', 'state']);
  await updates.get(); await updates.check(); await updates.skip('0.2.0'); await updates.setAuto(false); await updates.open();
  await setup.state(); await setup.retry(); await setup.proceed(); await setup.openLogs(); await setup.restartHarness();
  assert.deepEqual(invoked, [['updates:get'], ['updates:check'], ['updates:skip', '0.2.0'], ['updates:set-auto', false], ['updates:open'],
    ['setup:state'], ['setup:retry'], ['setup:proceed'], ['setup:open-logs'], ['setup:restart-harness']]);
  const got = [];
  const off = setup.onState(s => got.push(s));
  const [channel, handler] = listeners.at(-1);
  assert.equal(channel, 'setup:state');
  handler({ sender: 'not passed on' }, { phase: 'setup' });
  assert.deepEqual(got, [{ phase: 'setup' }]);
  assert.equal(typeof off, 'function');
});

test('preload adds the sentry ipc bridge only when ZEN_GLITCHTIP_DSN is set', () => {
  const { exposed } = loadPreload({ ZEN_GLITCHTIP_DSN: 'https://key@glitchtip.example/1' });
  assert.deepEqual(Object.keys(exposed).sort(), ['__SENTRY_IPC__', 'openzenbot']);
  assert.deepEqual(Object.keys(exposed.openzenbot).sort(), KEYS);
  assert.ok(!JSON.stringify(Object.keys(exposed.__SENTRY_IPC__)).includes('glitchtip'));  // the page never sees the DSN
});

test('main starts sentry only from ZEN_GLITCHTIP_DSN, over classic ipc', () => {
  const src = readFileSync(new URL('../../src/electron/main.cjs', import.meta.url), 'utf8').replace(/\r\n/g, '\n');  // checked out with CRLF on Windows
  assert.match(src, /process\.env\.ZEN_GLITCHTIP_DSN/);
  assert.match(src, /if \(glitchtipDsn\) \{\n  const Sentry = require\('@sentry\/electron\/main'\)/);
  assert.match(src, /ipcMode: Sentry\.IPCMode\.Classic/);
  assert.match(src, /sendDefaultPii: false/);
});

test('main registers the directory and file pickers, save, export, and the update check\'s five channels, from the app\'s own pages', () => {
  const src = readFileSync(new URL('../../src/electron/main.cjs', import.meta.url), 'utf8').replace(/\r\n/g, '\n');  // checked out with CRLF on Windows
  assert.equal((src.match(/ipcMain\.handle\(/g) || []).length, 7);  // five, the updates:* helper and the keeper's
  assert.match(src, /ipcMain\.handle\('export-design', \(event, args\) => \{\n    if \(!fromApp\(event\)\) throw/);
  assert.match(src, /require\('\.\/export-design\.cjs'\)/);
  assert.match(src, /ipcMain\.handle\('open-vault', event => \{\n    if \(!fromApp\(event\)\) throw/);
  assert.match(src, /openVault\(\{ shell, base: vaultBase \}\)/);  // no argument from the page reaches it
  assert.deepEqual(src.match(/handle\('updates:[a-z-]+'/g), ["handle('updates:get'", "handle('updates:check'", "handle('updates:skip'", "handle('updates:set-auto'", "handle('updates:open'"]);
  assert.match(src, /if \(!fromApp\(event\)\) throw/);
  assert.match(src, /ipcMain\.handle\('save-media'/);
  assert.match(src, /require\('\.\/save-media\.cjs'\)/);
  assert.match(src, /ipcMain\.handle\('pick-directory'/);
  assert.match(src, /properties: \['openDirectory'\]/);
  assert.match(src, /ipcMain\.handle\('pick-file'/);
  assert.match(src, /properties: \['openFile'\]/);
});

test('csp connect-src names the harness loopback origin only', () => {
  const html = readFileSync(new URL('../../src/gui/index.html', import.meta.url), 'utf8');
  const csp = html.match(/http-equiv="Content-Security-Policy" content="([^"]+)"/)[1];
  const connect = csp.split(';').map(s => s.trim()).find(s => s.startsWith('connect-src'));
  assert.equal(connect, "connect-src 'self' http://127.0.0.1:8765 http://localhost:8765");
  assert.match(csp, /default-src 'self'/);
});

test('main registers the app scheme, loads the preload, and keeps the sandbox', () => {
  const src = readFileSync(new URL('../../src/electron/main.cjs', import.meta.url), 'utf8');
  assert.match(src, /registerSchemesAsPrivileged\(\[\{ scheme: 'app'/);
  assert.match(src, /protocol\.handle\('app'/);
  assert.match(src, /function createWindow\(page = 'index\.html'\)/);
  assert.match(src, /loadURL\(`app:\/\/openzenbot\/\$\{page\}`\)/);
  assert.match(src, /preload: path\.join\(ROOT, 'preload\.cjs'\)/);  // bundled into dist/ by build-gui
  assert.match(src, /contextIsolation: true, sandbox: true/);
});

test('the export window is offscreen, sandboxed, without a preload, in its own in-memory session, and goes nowhere', () => {
  const src = readFileSync(new URL('../../src/electron/main.cjs', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
  const body = name => { const start = src.indexOf(name); assert.ok(start > 0, name); return src.slice(start, src.indexOf('\n}\n', start)); };
  const fn = body('async function makeExportWindow');
  assert.match(fn, /show: false/);
  assert.match(fn, /webPreferences: \{ sandbox: true, contextIsolation: true, nodeIntegration: false, session: exportSession\(\), offscreen: true/);
  assert.doesNotMatch(fn, /preload/);
  for (const ev of ['will-navigate', 'will-redirect', 'will-frame-navigate']) assert.ok(fn.includes(`wc.on('${ev}', refuse)`), ev);
  assert.ok(fn.includes("setWindowOpenHandler(() => ({ action: 'deny' }))"));
  assert.ok(fn.includes('catch (e) { close(); throw e; }'));  // a failed, refused or slow load closes the window
  assert.ok(fn.includes('deviceScaleFactor: 1'));  // the PNG is 1440 px wide whatever the display's scale
  assert.ok(fn.includes('FREEZE_UNITS'));  // the PDF keeps vh as on screen
  // one limit from the window's making destroys it, and every wait races that limit, so a page that blocks its main
  // thread after load (which holds any call into it) cannot leave the window open
  assert.match(fn, /setTimeout\(\(\) => \{ close\(\); reject\(/);
  assert.ok(fn.includes('const bounded = work => Promise.race([work, limit]);'));
  const waits = fn.match(/\bawait\b[^;]*/g);
  assert.ok(waits.length >= 4);
  for (const w of waits) assert.match(w, /^await bounded\(/, w);
  for (const call of ['wc.loadURL(', "cdp.sendCommand('Emulation.setDeviceMetricsOverride'", 'wc.executeJavaScriptInIsolatedWorld(1001, [{ code: MEASURE }])', 'wc.printToPDF(', "cdp.sendCommand('Page.captureScreenshot'"]) {
    assert.ok(fn.includes(`bounded(${call}`), call);
  }
  const ses = body('function exportSession');
  assert.ok(ses.includes("session.fromPartition('export-design')"));  // no persist: prefix: in memory only
  assert.ok(ses.includes('setPermissionRequestHandler((_wc, _permission, answer) => answer(false))'));
  assert.ok(ses.includes('setPermissionCheckHandler(() => false)'));
  assert.ok(ses.includes('setDevicePermissionHandler(() => false)'));
  assert.ok(ses.includes("ses.on('will-download', event => event.preventDefault())"));
});

test('csp lets media and frames come from the harness only, with no wildcard', () => {
  const html = readFileSync(new URL('../../src/gui/index.html', import.meta.url), 'utf8');
  const csp = html.match(/http-equiv="Content-Security-Policy" content="([^"]+)"/)[1];
  const directive = name => csp.split(';').map(s => s.trim()).find(s => s.startsWith(name + ' '));
  const harness = 'http://127.0.0.1:8765 http://localhost:8765';
  assert.equal(directive('img-src'), `img-src 'self' data: ${harness}`);
  assert.equal(directive('media-src'), `media-src blob: ${harness}`);  // blob: plays a reply read aloud (speech.js)
  assert.equal(directive('frame-src'), `frame-src http://127.0.0.1:9999 http://localhost:9999 ${harness}`);
  assert.equal(directive('worker-src'), "worker-src 'self'");
  assert.ok(!csp.includes('*'));
  assert.doesNotMatch(readFileSync(new URL('../../src/electron/main.cjs', import.meta.url), 'utf8'), /plugins/);
});

test('the build ships the pdf.js worker beside the app', () => {
  const src = readFileSync(new URL('../../scripts/build-gui.mjs', import.meta.url), 'utf8');
  assert.match(src, /pdfjs-dist\/build\/pdf\.worker\.min\.mjs/);
  assert.match(src, /dist\/pdf\.worker\.mjs/);
});

test('pdf.js never evaluates code from a PDF', () => {
  const src = readFileSync(new URL('../../src/gui/pdf.js', import.meta.url), 'utf8');
  assert.match(src, /isEvalSupported: false/);
  assert.match(src, /workerSrc = '\.\/pdf\.worker\.mjs'/);
});

test('onOpenMusic subscribes to the one open-music channel and hands the page no event', () => {
  const { exposed, listeners } = loadPreload({});
  let calls = 0; let arg;
  exposed.openzenbot.onOpenMusic((...a) => { calls += 1; arg = a; });
  assert.deepEqual(listeners.map(l => l[0]), ['open-music']);
  listeners[0][1]({ sender: 'ipc' }, 'x');
  assert.equal(calls, 1);
  assert.deepEqual(arg, []);
});

test('the setup page\'s channels answer setup.html only and take no argument', () => {
  const src = readFileSync(new URL('../../src/electron/managed-app.cjs', import.meta.url), 'utf8');
  assert.equal((src.match(/ipcMain\.handle\(/g) || []).length, 1);
  assert.match(src, /ipcMain\.handle\(channel, event => \{\n    if \(!fromApp\(event, 'setup\.html'\)\) throw/);
  assert.deepEqual(src.match(/handle\('setup:[a-z-]+'/g), ["handle('setup:state'", "handle('setup:proceed'", "handle('setup:retry'", "handle('setup:restart-harness'", "handle('setup:open-logs'"]);
  assert.match(src, /shell\.openPath\(paths\.logs\)/);
});

test('the setup page has its own closed CSP and sets text, never markup', () => {
  const html = readFileSync(new URL('../../src/gui/setup.html', import.meta.url), 'utf8');
  const csp = html.match(/http-equiv="Content-Security-Policy" content="([^"]+)"/)[1];
  assert.equal(csp, "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'");
  const js = readFileSync(new URL('../../src/gui/setup.js', import.meta.url), 'utf8');
  assert.doesNotMatch(js, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  const build = readFileSync(new URL('../../scripts/build-gui.mjs', import.meta.url), 'utf8');
  assert.match(build, /entryPoints: \['src\/gui\/setup\.js'\]/);
  assert.match(build, /copyFileSync\('src\/gui\/setup\.html', 'dist\/setup\.html'\)/);
});

test('main runs schtasks by its full path and guards the tray', () => {
  const src = readFileSync(new URL('../../src/electron/main.cjs', import.meta.url), 'utf8').replace(/\r\n/g, '\n');  // checked out with CRLF on Windows
  assert.match(src, /path\.join\(process\.env\.SystemRoot \|\| 'C:\\\\Windows', 'System32', 'schtasks\.exe'\)/);
  assert.doesNotMatch(src, /execFile\('schtasks\.exe'/);
  assert.match(src, /trayParts = openTray\(/);  // a tray that cannot be made never fails the start
  assert.doesNotMatch(src, /createTray\(/);
  assert.match(src, /app\.on\('window-all-closed', \(\) => allClosed\(\{ hasTray, quit: \(\) => app\.quit\(\) \}\)\);/);
  assert.match(src, /closeToTray\(win, \{ isQuitting: \(\) => quitting, hasTray,/);
});

test('a Dock click on macOS shows a window hidden to the tray, in both modes', () => {
  const main = readFileSync(new URL('../../src/electron/main.cjs', import.meta.url), 'utf8');
  assert.match(main, /app\.on\('activate', showWindow\)/);  // showWindow shows a hidden one, makes one when none
  const managed = readFileSync(new URL('../../src/electron/managed-app.cjs', import.meta.url), 'utf8');
  assert.match(managed, /app\.on\('activate', \(\) => \{ if \(!bringBack\(\)\) win = createWindow\(/);
  assert.match(managed, /const bringBack = \(\) => \{[^}]*w\.show\(\)/s);
});

// The Electron shell: the preload exposes only the harness URL, the two OS pickers and Save…; the CSP
// lets the renderer connect to the harness loopback origin and nothing else;
// the main process serves the app from its own origin so CORS can name it.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import Module from 'node:module';
import { readFileSync } from 'node:fs';

const require = createRequire(import.meta.url);

function loadPreload(env) {
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
  Object.assign(process.env, { ZEN_GLITCHTIP_DSN: '' }, env);
  const hadWindow = 'window' in globalThis;
  if (!hadWindow) globalThis.window = {};  // the sentry bridge writes window.__SENTRY_IPC__
  try {
    delete require.cache[require.resolve('../../src/electron/preload.cjs')];
    require('../../src/electron/preload.cjs');
    delete require.cache[require.resolve('@sentry/electron/preload')];
  } finally { Module._load = original; process.env = saved; if (!hadWindow) delete globalThis.window; }
  return { exposed, invoked, listeners };
}

test('preload exposes the harness url, the directory and file pickers, and save, nothing else', async () => {
  const { exposed, invoked } = loadPreload({ ZEN_HARNESS_URL: '' });
  assert.deepEqual(Object.keys(exposed), ['openzenbot']);
  assert.deepEqual(Object.keys(exposed.openzenbot).sort(), ['harnessUrl', 'onOpenMusic', 'origin', 'pickDirectory', 'pickFile', 'saveMedia']);
  assert.equal(exposed.openzenbot.harnessUrl, 'http://127.0.0.1:8765');
  assert.equal(exposed.openzenbot.origin, 'app://openzenbot');
  assert.equal(await exposed.openzenbot.pickDirectory(), 'F:/picked');
  assert.equal(await exposed.openzenbot.pickFile(), 'F:/picked');
  assert.equal(await exposed.openzenbot.saveMedia('http://127.0.0.1:8765/media?url=x&ok=1', 'a.png'), 'F:/picked');
  assert.deepEqual(invoked, [['pick-directory'], ['pick-file'], ['save-media', { url: 'http://127.0.0.1:8765/media?url=x&ok=1', name: 'a.png' }]]);
  assert.equal(loadPreload({ ZEN_HARNESS_URL: 'http://127.0.0.1:9000' }).exposed.openzenbot.harnessUrl, 'http://127.0.0.1:9000');
});

test('preload adds the sentry ipc bridge only when ZEN_GLITCHTIP_DSN is set', () => {
  const { exposed } = loadPreload({ ZEN_GLITCHTIP_DSN: 'https://key@glitchtip.example/1' });
  assert.deepEqual(Object.keys(exposed).sort(), ['__SENTRY_IPC__', 'openzenbot']);
  assert.deepEqual(Object.keys(exposed.openzenbot).sort(), ['harnessUrl', 'onOpenMusic', 'origin', 'pickDirectory', 'pickFile', 'saveMedia']);
  assert.ok(!JSON.stringify(Object.keys(exposed.__SENTRY_IPC__)).includes('glitchtip'));  // the page never sees the DSN
});

test('main starts sentry only from ZEN_GLITCHTIP_DSN, over classic ipc', () => {
  const src = readFileSync(new URL('../../src/electron/main.cjs', import.meta.url), 'utf8').replace(/\r\n/g, '\n');  // checked out with CRLF on Windows
  assert.match(src, /process\.env\.ZEN_GLITCHTIP_DSN/);
  assert.match(src, /if \(glitchtipDsn\) \{\n  const Sentry = require\('@sentry\/electron\/main'\)/);
  assert.match(src, /ipcMode: Sentry\.IPCMode\.Classic/);
  assert.match(src, /sendDefaultPii: false/);
});

test('main registers exactly three ipc handlers, the directory and file pickers and save', () => {
  const src = readFileSync(new URL('../../src/electron/main.cjs', import.meta.url), 'utf8');
  assert.equal((src.match(/ipcMain\.handle\(/g) || []).length, 3);
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
  assert.match(src, /loadURL\('app:\/\/openzenbot\/index\.html'\)/);
  assert.match(src, /preload: path\.join\(ROOT, 'preload\.cjs'\)/);  // bundled into dist/ by build-gui
  assert.match(src, /contextIsolation: true, sandbox: true/);
});

test('csp lets media and frames come from the harness only, with no wildcard', () => {
  const html = readFileSync(new URL('../../src/gui/index.html', import.meta.url), 'utf8');
  const csp = html.match(/http-equiv="Content-Security-Policy" content="([^"]+)"/)[1];
  const directive = name => csp.split(';').map(s => s.trim()).find(s => s.startsWith(name + ' '));
  const harness = 'http://127.0.0.1:8765 http://localhost:8765';
  assert.equal(directive('img-src'), `img-src 'self' data: ${harness}`);
  assert.equal(directive('media-src'), `media-src ${harness}`);
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

// Save…: the OS save dialog opens in the last folder used, and the shell writes only what it fetched
// from the harness, to the path the dialog returned.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { safeName, allowedUrl, defaultFolder, saveMedia } = require('../../src/electron/save-media.cjs');
const BASE = 'http://127.0.0.1:8765';

function setup({ status = 200, body = 'abc', choose } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'save-media-'));
  const paths = { pictures: path.join(root, 'Pictures'), downloads: path.join(root, 'Downloads') };
  fs.mkdirSync(paths.pictures); fs.mkdirSync(paths.downloads);
  const calls = { dialog: [], fetch: [] };
  const deps = {
    harnessBase: BASE, paths, fs, storePath: path.join(root, 'media-save.json'),
    dialog: { showSaveDialog: async opts => { calls.dialog.push(opts); return choose ? choose(opts) : { canceled: false, filePath: opts.defaultPath }; } },
    fetch: async url => { calls.fetch.push(url); return new Response(status === 200 ? body : 'nope', { status }); },
  };
  return { root, paths, deps, calls };
}

test('names are made safe', () => {
  assert.equal(safeName('..\\..\\evil.exe'), 'evil.exe'); assert.equal(safeName('a:b?.png'), 'ab.png');
  assert.equal(safeName(''), 'download'); assert.equal(safeName(' .. '), 'download');
  assert.equal(safeName('x'.repeat(300) + '.pdf').length, 120);
  assert.ok(safeName('x'.repeat(300) + '.pdf').endsWith('.pdf'));
});

test('only harness media and attachment urls are fetched', () => {
  assert.ok(allowedUrl(BASE + '/media?url=https%3A%2F%2Fa%2Fb.png&ok=1', BASE));
  assert.ok(allowedUrl(BASE + '/rooms/r1/attachments/a1', BASE)); assert.ok(allowedUrl(BASE + '/conversations/c1/attachments/a1', BASE));
  assert.ok(!allowedUrl('https://images.pexels.com/a.png', BASE)); assert.ok(!allowedUrl(BASE + '/settings', BASE));
  assert.ok(!allowedUrl('http://127.0.0.1:9999/media?url=x', BASE));
  assert.ok(!allowedUrl(BASE + '/rooms/r1/attachments/a1/../../settings', BASE));
  assert.ok(!allowedUrl('not a url', BASE));
});

test('a design\'s zip is fetched, and nothing else under its design route', () => {
  assert.ok(allowedUrl(BASE + '/conversations/c1/design/export.zip', BASE));
  assert.ok(!allowedUrl(BASE + '/conversations/c1/design/slots', BASE));
  assert.ok(!allowedUrl(BASE + '/conversations/c1/design/export.zip/x', BASE));
  assert.ok(!allowedUrl(BASE + '/conversations/c1/x/design/export.zip', BASE));
  assert.ok(!allowedUrl('http://127.0.0.1:9999/conversations/c1/design/export.zip', BASE));
});

test('pictures for images and video, downloads for the rest', () => {
  const p = { pictures: 'P', downloads: 'D' };
  assert.equal(defaultFolder('a.JPEG', p), 'P'); assert.equal(defaultFolder('a.mp4', p), 'P');
  assert.equal(defaultFolder('a.pdf', p), 'D'); assert.equal(defaultFolder('flow.html', p), 'D');
});

test('first save starts in pictures, the next in the last folder, and the folder is remembered', async () => {
  const { root, paths, deps, calls } = setup();
  const elsewhere = path.join(root, 'Keep'); fs.mkdirSync(elsewhere);
  deps.dialog.showSaveDialog = async opts => { calls.dialog.push(opts); return { canceled: false, filePath: path.join(elsewhere, 'lake.jpeg') }; };
  const first = await saveMedia({ url: BASE + '/media?url=x&ok=1', name: 'lake.jpeg' }, deps);
  assert.deepEqual(first, { saved: path.join(elsewhere, 'lake.jpeg') });
  assert.equal(calls.dialog[0].defaultPath, path.join(paths.pictures, 'lake.jpeg'));
  assert.equal(fs.readFileSync(path.join(elsewhere, 'lake.jpeg'), 'utf8'), 'abc');
  assert.deepEqual(JSON.parse(fs.readFileSync(deps.storePath, 'utf8')), { lastDir: elsewhere });
  await saveMedia({ url: BASE + '/rooms/r/attachments/a', name: 'r.pdf' }, deps);
  assert.equal(calls.dialog[1].defaultPath, path.join(elsewhere, 'r.pdf'));
});

test('a cancelled dialog writes nothing and fetches nothing', async () => {
  const { deps, calls } = setup({ choose: () => ({ canceled: true }) });
  assert.deepEqual(await saveMedia({ url: BASE + '/media?url=x&ok=1', name: 'a.png' }, deps), { canceled: true });
  assert.deepEqual(calls.fetch, []); assert.ok(!fs.existsSync(deps.storePath));
});

test('a 404 reports the status and leaves no file', async () => {
  const { paths, deps } = setup({ status: 404 });
  const out = await saveMedia({ url: BASE + '/conversations/c/attachments/a', name: 'gone.pdf' }, deps);
  assert.deepEqual(out, { error: 'The file could not be fetched (404).' });
  assert.ok(!fs.existsSync(path.join(paths.downloads, 'gone.pdf')));
});

test('a refused url never opens the dialog', async () => {
  const { deps, calls } = setup();
  assert.deepEqual(await saveMedia({ url: 'https://evil.example/a.png', name: 'a.png' }, deps), { error: 'That item cannot be saved.' });
  assert.deepEqual(calls.dialog, []); assert.deepEqual(calls.fetch, []);
});

test('a broken folder memory falls back to the default folder', async () => {
  const { paths, deps, calls } = setup();
  fs.writeFileSync(deps.storePath, '{not json');
  await saveMedia({ url: BASE + '/media?url=x&ok=1', name: 'a.png' }, deps);
  assert.equal(calls.dialog[0].defaultPath, path.join(paths.pictures, 'a.png'));
});

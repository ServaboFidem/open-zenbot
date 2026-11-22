// Export a design as PDF or PNG: the main process builds the closed attachment URL from two ids, renders it in a
// window it makes (a fake here: no test opens a real window), and writes the bytes where the Save dialog chose.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { pageUrl, exportDesign, pdfOptions, WIDTH, MAX_HEIGHT } = require('../../src/electron/export-design.cjs');
const { safeName } = require('../../src/electron/save-media.cjs');
const BASE = 'http://127.0.0.1:8765';

function fakeWindow({ height = 900, fail = null, hang = false } = {}) {
  const w = {
    asked: [], closed: 0,
    height: async () => { if (fail === 'height') throw new Error('boom'); return hang ? new Promise(() => {}) : height; },
    pdf: async size => { w.asked.push(['pdf', size]); if (fail === 'render') throw new Error('boom'); return Buffer.from('%PDF-fake'); },
    png: async size => { w.asked.push(['png', size]); if (fail === 'render') throw new Error('boom'); return Buffer.from('PNGfake'); },
    close: () => { w.closed += 1; },
  };
  return w;
}

function setup({ win = fakeWindow(), choose, timeoutMs } = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'export-design-'));
  const paths = { pictures: path.join(root, 'Pictures'), downloads: path.join(root, 'Downloads') };
  fs.mkdirSync(paths.pictures); fs.mkdirSync(paths.downloads);
  const calls = { dialog: [], windows: [] };
  const deps = {
    harnessBase: BASE, paths, fs, storePath: path.join(root, 'media-save.json'), timeoutMs,
    dialog: { showSaveDialog: async opts => { calls.dialog.push(opts); return choose ? choose(opts) : { canceled: false, filePath: opts.defaultPath }; } },
    makeWindow: async (url, opts) => { calls.windows.push({ url, opts }); return win; },
  };
  return { root, paths, deps, calls, win };
}

test('the page url is the closed attachment route on the harness origin, built from checked ids', () => {
  assert.equal(pageUrl(BASE, 'c1', 'a_B-9'), `${BASE}/conversations/c1/attachments/a_B-9`);
  assert.equal(pageUrl(BASE + '/', 'c1', 'a1'), `${BASE}/conversations/c1/attachments/a1`);
  for (const bad of ['a/b', 'a?b', '..', '', 'a b', 'a%2F', 'x'.repeat(81), null, undefined, 7, {}]) {
    assert.throws(() => pageUrl(BASE, bad, 'a1'));
    assert.throws(() => pageUrl(BASE, 'c1', bad));
  }
  assert.equal(WIDTH, 1440); assert.equal(MAX_HEIGHT, 16000);
});

test('an id holding a slash, a query or dots is an error and makes no window and no dialog', async () => {
  for (const [convId, attId] of [['c/1', 'a1'], ['c1', 'a?x=1'], ['..', 'a1'], ['c1', '../../settings']]) {
    const { deps, calls } = setup();
    const out = await exportDesign({ convId, attId, kind: 'pdf', name: 'Page' }, deps);
    assert.ok(out.error, JSON.stringify(out));
    assert.deepEqual(calls.windows, []); assert.deepEqual(calls.dialog, []);
  }
});

test('only pdf and png are made', async () => {
  const { deps, calls } = setup();
  const out = await exportDesign({ convId: 'c1', attId: 'a1', kind: 'html', name: 'Page' }, deps);
  assert.ok(out.error); assert.deepEqual(calls.windows, []); assert.deepEqual(calls.dialog, []);
});

test('a pdf is the page at 1440 px wide and its own height, written where the dialog chose', async () => {
  const { deps, calls, win, paths } = setup();
  const out = await exportDesign({ convId: 'c1', attId: 'a1', kind: 'pdf', name: 'Bakery landing' }, deps);
  const file = path.join(paths.downloads, 'Bakery landing.pdf');
  assert.deepEqual(out, { saved: file });
  assert.deepEqual(calls.windows, [{ url: `${BASE}/conversations/c1/attachments/a1`, opts: { width: WIDTH } }]);
  assert.deepEqual(win.asked, [['pdf', { width: 1440, height: 900 }]]);
  assert.equal(fs.readFileSync(file, 'utf8'), '%PDF-fake');
  assert.equal(win.closed, 1);
  assert.deepEqual(JSON.parse(fs.readFileSync(deps.storePath, 'utf8')), { lastDir: paths.downloads });
});

test('a png starts in pictures, the next export in the last folder', async () => {
  const { deps, calls, win, paths, root } = setup();
  const out = await exportDesign({ convId: 'c1', attId: 'a1', kind: 'png', name: 'Bakery' }, deps);
  assert.deepEqual(out, { saved: path.join(paths.pictures, 'Bakery.png') });
  assert.deepEqual(win.asked, [['png', { width: 1440, height: 900 }]]);
  assert.equal(fs.readFileSync(path.join(paths.pictures, 'Bakery.png'), 'utf8'), 'PNGfake');
  const elsewhere = path.join(root, 'Keep'); fs.mkdirSync(elsewhere);
  fs.writeFileSync(deps.storePath, JSON.stringify({ lastDir: elsewhere }));
  await exportDesign({ convId: 'c1', attId: 'a1', kind: 'pdf', name: 'Bakery' }, deps);
  assert.equal(calls.dialog[1].defaultPath, path.join(elsewhere, 'Bakery.pdf'));
});

test('the height asked of the window is the page\'s, a whole number from 1 to 16,000', async () => {
  for (const [said, used] of [[99999, MAX_HEIGHT], [16000, 16000], [12.7, 12], ['640', 640], [-5, 1], [0, 1],
    [NaN, 1], [Infinity, 1], ['tall', 1], [null, 1], [{ valueOf: () => 50 }, 1], [[300], 1]]) {
    const win = fakeWindow({ height: said });
    const { deps } = setup({ win });
    await exportDesign({ convId: 'c1', attId: 'a1', kind: 'pdf', name: 'P' }, deps);
    assert.deepEqual(win.asked, [['pdf', { width: WIDTH, height: used }]], `page said ${String(said)}`);
  }
});

test('a cancelled dialog makes no window and writes nothing', async () => {
  const { deps, calls } = setup({ choose: () => ({ canceled: true }) });
  assert.deepEqual(await exportDesign({ convId: 'c1', attId: 'a1', kind: 'png', name: 'P' }, deps), { canceled: true });
  assert.deepEqual(calls.windows, []); assert.ok(!fs.existsSync(deps.storePath));
});

test('the window is closed after a thrown error, and the error is a sentence', async () => {
  for (const fail of ['height', 'render']) {
    const win = fakeWindow({ fail });
    const { deps, paths } = setup({ win });
    const out = await exportDesign({ convId: 'c1', attId: 'a1', kind: 'pdf', name: 'P' }, deps);
    assert.deepEqual(out, { error: 'The design could not be exported.' });
    assert.equal(win.closed, 1);
    assert.deepEqual(fs.readdirSync(paths.downloads), []);
  }
});

test('a window that cannot be made is an error', async () => {
  const { deps } = setup();
  deps.makeWindow = async () => { throw new Error('load failed'); };
  assert.deepEqual(await exportDesign({ convId: 'c1', attId: 'a1', kind: 'png', name: 'P' }, deps), { error: 'The design could not be exported.' });
});

test('a page that never answers is stopped after the time limit and its window closed', async () => {
  const win = fakeWindow({ hang: true });
  const { deps } = setup({ win, timeoutMs: 30 });
  const out = await exportDesign({ convId: 'c1', attId: 'a1', kind: 'pdf', name: 'P' }, deps);
  assert.deepEqual(out, { error: 'The design took too long to export.' });
  assert.equal(win.closed, 1);
});

test('a page that does not load within the window\'s own limit is too slow, not broken', async () => {
  const { deps } = setup();
  deps.makeWindow = async () => { throw Object.assign(new Error('the page did not load in time'), { name: 'TimeoutError' }); };
  assert.deepEqual(await exportDesign({ convId: 'c1', attId: 'a1', kind: 'pdf', name: 'P' }, deps), { error: 'The design took too long to export.' });
});

test('a window made after the time limit is closed at once', async () => {
  const win = fakeWindow();
  const { deps } = setup({ win, timeoutMs: 20 });
  deps.makeWindow = () => new Promise(resolve => setTimeout(() => resolve(win), 60));
  const out = await exportDesign({ convId: 'c1', attId: 'a1', kind: 'pdf', name: 'P' }, deps);
  assert.deepEqual(out, { error: 'The design took too long to export.' });
  await new Promise(r => setTimeout(r, 80));
  assert.equal(win.closed, 1); assert.deepEqual(win.asked, []);
});

test('a write failure answers an error, leaves no file and closes the window', async () => {
  const { deps, win, root } = setup({ choose: () => ({ canceled: false, filePath: path.join(root, 'missing', 'P.pdf') }) });
  const out = await exportDesign({ convId: 'c1', attId: 'a1', kind: 'pdf', name: 'P' }, deps);
  assert.match(out.error, /^The file could not be saved \(/);
  assert.ok(!fs.existsSync(path.join(root, 'missing')));
  assert.equal(win.closed, 1); assert.ok(!fs.existsSync(deps.storePath));
});

test('the suggested name is the title made safe, ending .pdf or .png', async () => {
  for (const [name, kind, want] of [['..\\..\\evil', 'pdf', 'evil.pdf'], ['a:b?', 'png', 'ab.png'], ['???', 'pdf', 'design.pdf'],
    ['', 'png', 'design.png'], [undefined, 'pdf', 'design.pdf'], ['x'.repeat(300), 'pdf', null]]) {
    const { deps, calls } = setup({ choose: () => ({ canceled: true }) });
    await exportDesign({ convId: 'c1', attId: 'a1', kind, name }, deps);
    const base = path.basename(calls.dialog[0].defaultPath);
    if (want) assert.equal(base, want);
    assert.ok(base.endsWith(`.${kind}`), base);
    assert.equal(safeName(base), base);
  }
});

test('the pdf is one page of the given size in inches, with no margins and the backgrounds on', () => {
  assert.deepEqual(pdfOptions({ width: 1440, height: 960 }), {
    printBackground: true, pageSize: { width: 15, height: 10 }, margins: { top: 0, bottom: 0, left: 0, right: 0 },
    preferCSSPageSize: false, pageRanges: '1',
  });
});

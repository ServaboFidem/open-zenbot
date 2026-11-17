// Pinned downloads: a file reaches its destination only when its SHA-256 matches. fetch is a fake giving a
// stream of known bytes; the folder is a real temporary one; nothing touches the network.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs, { mkdtempSync, readdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const { downloadVerified, pinFor, ChecksumError, OfflineError } = require('../../src/electron/download.cjs');
const pins = require('../../src/electron/pins.json');

const BYTES = Buffer.from('some archive bytes, long enough to split into chunks'.repeat(10));
const HASH = createHash('sha256').update(BYTES).digest('hex');
const chunked = async function* (buf, size = 100) { for (let i = 0; i < buf.length; i += size) yield buf.subarray(i, i + size); };
const answer = (body, extra = {}) => ({ ok: true, status: 200, headers: { get: n => (n.toLowerCase() === 'content-length' ? String(BYTES.length) : null) }, body, ...extra });

function setup(t) {
  const dir = mkdtempSync(join(tmpdir(), 'zen-download-'));
  t.after(() => rmSync(dir, { recursive: true, force: true }));
  const calls = [];
  const state = { next: () => answer(chunked(BYTES)) };
  const args = {
    url: 'https://example.test/archive.tar.gz', dest: join(dir, 'archive.tar.gz'), sha256: HASH, fs,
    fetch: async url => { calls.push(url); return state.next(); },
  };
  return { dir, args, calls, state };
}

test('a download with the right checksum lands at dest', async t => {
  const { dir, args } = setup(t);
  const seen = [];
  const out = await downloadVerified({ ...args, onProgress: p => seen.push(p) });
  assert.equal(out, args.dest);
  assert.deepEqual(readFileSync(args.dest), BYTES);
  assert.deepEqual(readdirSync(dir), ['archive.tar.gz']);
  assert.equal(seen.at(-1).received, BYTES.length);
  assert.equal(seen.at(-1).total, BYTES.length);
});

test('a wrong checksum leaves nothing behind and rejects with ChecksumError', async t => {
  const { dir, args } = setup(t);
  await assert.rejects(downloadVerified({ ...args, sha256: 'f'.repeat(64) }), ChecksumError);
  assert.deepEqual(readdirSync(dir), []);
});

test('a stream cut off midway leaves no dest, and the next call starts clean', async t => {
  const { dir, args, state } = setup(t);
  state.next = () => answer((async function* () { yield BYTES.subarray(0, 150); throw new Error('socket hang up'); })());
  await assert.rejects(downloadVerified(args), OfflineError);
  assert.deepEqual(readdirSync(dir), []);
  state.next = () => answer(chunked(BYTES));
  assert.equal(await downloadVerified(args), args.dest);
  assert.deepEqual(readFileSync(args.dest), BYTES);
  assert.deepEqual(readdirSync(dir), ['archive.tar.gz']);
});

test('a stale .part from an earlier run is not mixed into the new download', async t => {
  const { dir, args } = setup(t);
  writeFileSync(args.dest + '.part', 'junk from a crash');
  await downloadVerified(args);
  assert.deepEqual(readFileSync(args.dest), BYTES);
  assert.deepEqual(readdirSync(dir), ['archive.tar.gz']);
});

test('an existing dest with the right hash is not fetched again', async t => {
  const { args, calls } = setup(t);
  writeFileSync(args.dest, BYTES);
  assert.equal(await downloadVerified(args), args.dest);
  assert.equal(calls.length, 0);
});

test('an existing dest with the wrong hash is replaced', async t => {
  const { args, calls } = setup(t);
  writeFileSync(args.dest, 'old and wrong');
  await downloadVerified(args);
  assert.equal(calls.length, 1);
  assert.deepEqual(readFileSync(args.dest), BYTES);
});

test('a refused or failed fetch is an OfflineError', async t => {
  const { dir, args, state } = setup(t);
  state.next = () => answer(null, { ok: false, status: 404 });
  await assert.rejects(downloadVerified(args), e => e instanceof OfflineError && e.message === 'No internet connection, or the download server did not answer.');
  state.next = () => { throw new TypeError('fetch failed'); };
  await assert.rejects(downloadVerified(args), OfflineError);
  assert.deepEqual(readdirSync(dir), []);
});

test('pinFor builds the key from platform and arch and refuses others', () => {
  assert.deepEqual(pinFor(pins, 'uv', 'linux', 'x64'), pins.uv['linux-x64']);
  assert.deepEqual(pinFor(pins, 'node', 'win32', 'x64'), pins.node['win32-x64']);
  assert.throws(() => pinFor(pins, 'node', 'darwin', 'arm64'), /darwin-arm64/);
  assert.throws(() => pinFor(pins, 'nope', 'linux', 'x64'), /nope/);
});

test('pins.json names an https url and a 64-hex sha256 for every platform', () => {
  for (const tool of ['uv', 'node']) for (const plat of ['linux-x64', 'win32-x64']) {
    assert.match(pins[tool][plat].url, /^https:\/\//);
    assert.match(pins[tool][plat].sha256, /^[0-9a-f]{64}$/);
  }
  assert.match(pins.node.version, /^24\./);
  // the system tar unpacks .tar.gz with gzip, which every Linux has; .tar.xz needs xz, which a minimal install may lack
  assert.equal(pins.node['linux-x64'].url, `https://nodejs.org/dist/v${pins.node.version}/node-v${pins.node.version}-linux-x64.tar.gz`);
});

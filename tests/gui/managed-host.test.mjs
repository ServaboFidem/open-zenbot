// The installed app's real side effects (src/electron/managed-host.cjs), with fake children, a fake file system
// and a fake execFile: nothing is downloaded, unpacked or run.
import test from 'node:test';
import assert from 'node:assert/strict';
import { EventEmitter } from 'node:events';
import { PassThrough } from 'node:stream';
import { createRequire } from 'node:module';

const { createRun, createExtract, killTreeWith, findNodeWith, nodeMajor, fileSha256, readStamp } =
  createRequire(import.meta.url)('../../src/electron/managed-host.cjs');

function fakeChild(pid = 4242) {
  const c = new EventEmitter();
  c.pid = pid;
  c.stdout = new PassThrough();
  c.stderr = new PassThrough();
  return c;
}

test('run passes an argument array with no shell, reports each line and the exit code', async () => {
  const child = fakeChild();
  const seen = [];
  const run = createRun({ platform: 'linux', spawn: (cmd, args, opts) => { seen.push({ cmd, args, opts }); return child; } });
  const lines = [];
  const p = run('/opt/app/bin/uv', ['python', 'install', '3.12'], { env: { A: '1' }, cwd: '/tmp/x y', onLine: l => lines.push(l) });
  child.stdout.write('Downloading cpython\nInstalled');
  child.stderr.write('warn\r\n');
  child.stdout.end(); child.stderr.end();
  await new Promise(r => setImmediate(r));
  child.emit('close', 0);
  assert.deepEqual(await p, { code: 0 });
  assert.deepEqual(lines.sort(), ['Downloading cpython', 'Installed', 'warn']);
  assert.equal(seen[0].cmd, '/opt/app/bin/uv');
  assert.deepEqual(seen[0].args, ['python', 'install', '3.12']);
  assert.equal(seen[0].opts.shell, undefined);
  assert.equal(seen[0].opts.detached, true);
  assert.equal(seen[0].opts.cwd, '/tmp/x y');
  assert.deepEqual(seen[0].opts.env, { A: '1' });
});

test('a program that is not there resolves with a readable line, not a throw', async () => {
  const child = fakeChild(undefined);
  const run = createRun({ platform: 'linux', spawn: () => child });
  const lines = [];
  const p = run('/nowhere/bin/uv', [], { onLine: l => lines.push(l) });
  child.emit('error', Object.assign(new Error('spawn /nowhere/bin/uv ENOENT'), { code: 'ENOENT' }));
  child.emit('close', -2);
  assert.deepEqual(await p, { code: -1 });
  assert.deepEqual(lines, ['Could not start /nowhere/bin/uv: the program is not there']);
});

test('abort on Linux sends TERM to the process group, and the run resolves when the child closes', async () => {
  const child = fakeChild(777);
  const kills = [];
  const run = createRun({ platform: 'linux', spawn: () => child, kill: (pid, sig) => kills.push([pid, sig]) });
  const ctl = new AbortController();
  const p = run('uv', [], { signal: ctl.signal });
  ctl.abort();
  assert.deepEqual(kills, [[-777, 'SIGTERM']]);
  child.emit('close', null);
  assert.deepEqual(await p, { code: -1 });
});

test('abort on Windows kills the whole tree with taskkill', async () => {
  const child = fakeChild(31);
  const trees = [];
  const run = createRun({ platform: 'win32', spawn: () => child, killTree: pid => { trees.push(pid); return Promise.resolve(); } });
  const ctl = new AbortController();
  const p = run('uv.exe', [], { signal: ctl.signal });
  ctl.abort();
  child.emit('close', 1);
  await p;
  assert.deepEqual(trees, [31]);
  const calls = [];
  await killTreeWith({ env: { SystemRoot: 'C:\\Windows' }, execFile: (f, a, o, cb) => { calls.push([f, a]); cb(null); } })(31);
  assert.deepEqual(calls, [['C:\\Windows\\System32\\taskkill.exe', ['/PID', '31', '/T', '/F']]]);
});

test('extract runs the system tar on the archive as it is, without stripping its top folder', async () => {
  const calls = [];
  const extract = createExtract({ platform: 'win32', env: { SystemRoot: 'C:\\Windows' }, run: async (cmd, args) => { calls.push([cmd, args]); return { code: 0 }; } });
  await extract('C:\\Users\\Zoë\\runtime\\downloads\\node.zip', 'C:\\Users\\Zoë\\runtime\\node.part');
  assert.deepEqual(calls, [['C:\\Windows\\System32\\tar.exe', ['-xf', 'C:\\Users\\Zoë\\runtime\\downloads\\node.zip', '-C', 'C:\\Users\\Zoë\\runtime\\node.part']]]);
  const failing = createExtract({ platform: 'linux', run: async (cmd, args, { onLine }) => { onLine('xz: not found'); return { code: 2 }; } });
  await assert.rejects(failing('/r/node.tar.xz', '/r/node.part'), /Could not unpack node\.tar\.xz: xz: not found/);
});

test('findNode takes the first node on PATH, and only Node 24 or newer', async () => {
  const fs = { statSync: f => { if (['/a/node', '/b/node'].includes(f)) return { isFile: () => true }; throw new Error('ENOENT'); } };
  const versions = { '/a/node': 'v20.11.0\n', '/b/node': 'v24.1.0\n' };
  const execFile = (f, a, o, cb) => cb(null, versions[f]);
  assert.equal(await findNodeWith({ platform: 'linux', fs, execFile, env: { PATH: 'relative:/none:/b' } })(), '/b/node');
  assert.equal(await findNodeWith({ platform: 'linux', fs, execFile, env: { PATH: '/a:/b' } })(), null);
  assert.equal(await findNodeWith({ platform: 'linux', fs, execFile, env: { PATH: '.:node' } })(), null);
  const winFs = { statSync: f => { if (f === 'C:\\Node\\node.exe') return { isFile: () => true }; throw new Error('ENOENT'); } };
  assert.equal(await findNodeWith({ platform: 'win32', fs: winFs, execFile: (f, a, o, cb) => cb(null, 'v24.0.0'), env: { Path: 'C:\\Node' } })(), 'C:\\Node\\node.exe');
  assert.equal(nodeMajor('v24.22.3'), 24);
  assert.equal(nodeMajor('nonsense'), null);
});

test('the lock hash and the stamp read safely', () => {
  const fs = { readFileSync: (f, enc) => { if (f === 'lock') return Buffer.from('abc'); if (f === 'stamp') return '{"appVersion":"0.1.0"}'; throw new Error('ENOENT'); } };
  assert.equal(fileSha256(fs, 'lock'), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
  assert.equal(fileSha256(fs, 'missing'), '');
  assert.deepEqual(readStamp(fs, 'stamp'), { appVersion: '0.1.0' });
  assert.equal(readStamp(fs, 'missing'), null);
});

// Download the pinned uv for one target and put uv and uvx where electron-builder takes them from:
// build/bin/<os>-<arch>/, with <os> spelled the way electron-builder's ${os} macro spells it (linux, win).
//
//   node scripts/fetch-uv.mjs linux-x64
//   node scripts/fetch-uv.mjs win32-x64
//
// The archive is verified against src/electron/pins.json before it is unpacked (downloadVerified), and kept in
// build/bin/downloads/ so a second run does not download it again. Unpacking runs the system tar (a .zip on a
// host other than Windows, whose tar is bsdtar, goes through unzip), with an argument array and no shell.
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const { pinFor, downloadVerified } = require('../src/electron/download.cjs');
const pins = require('../src/electron/pins.json');

const TARGETS = {
  'linux-x64': { folder: 'linux-x64', bins: ['uv', 'uvx'] },
  'win32-x64': { folder: 'win-x64', bins: ['uv.exe', 'uvx.exe'] },
};

const target = process.argv[2] || `${process.platform}-${process.arch}`;  // no argument: this machine (npm run pack)
if (!TARGETS[target]) {
  console.error(`usage: node scripts/fetch-uv.mjs <${Object.keys(TARGETS).join('|')}>`);
  process.exit(2);
}
const [platform, arch] = target.split('-');
const { folder, bins } = TARGETS[target];

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const downloads = path.join(repo, 'build', 'bin', 'downloads');
const out = path.join(repo, 'build', 'bin', folder);
const { url, sha256 } = pinFor(pins, 'uv', platform, arch);
const archive = path.join(downloads, path.basename(new URL(url).pathname));

fs.mkdirSync(downloads, { recursive: true });
console.log(`uv ${pins.uv.version} for ${target}: ${url}`);
await downloadVerified({ url, dest: archive, sha256, fetch, fs });

// unpack into a fresh folder, then copy just the two binaries out, whatever top folder the archive has
const unpacked = fs.mkdtempSync(path.join(downloads, 'unpack-'));
try {
  if (archive.endsWith('.zip') && process.platform !== 'win32') execFileSync('unzip', ['-q', archive, '-d', unpacked]);
  else execFileSync('tar', ['-xf', archive, '-C', unpacked]);
  const found = new Map();
  const walk = dir => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (e.isFile() && bins.includes(e.name)) {
        if (found.has(e.name)) throw new Error(`The uv archive holds more than one ${e.name}.`);
        found.set(e.name, p);
      }
    }
  };
  walk(unpacked);
  fs.rmSync(out, { recursive: true, force: true });
  fs.mkdirSync(out, { recursive: true });
  for (const b of bins) {
    if (!found.has(b)) throw new Error(`The uv archive has no ${b}.`);
    const dest = path.join(out, b);
    fs.copyFileSync(found.get(b), dest);
    fs.chmodSync(dest, 0o755);
  }
  console.log(`uv and uvx in ${path.relative(repo, out)}`);
} finally {
  fs.rmSync(unpacked, { recursive: true, force: true });
}

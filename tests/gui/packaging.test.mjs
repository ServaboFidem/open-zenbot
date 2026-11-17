// The electron-builder config and its build resources: a .deb and a per-user NSIS installer, the AppArmor rule for
// the installed binary, an uninstaller that removes only the downloaded runtime, and never --no-sandbox.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

const root = new URL('../../', import.meta.url).pathname;
const read = rel => readFileSync(join(root, rel), 'utf8');
// a tiny line-based read: "key: value" pairs, comments and quotes dropped (no YAML dependency)
const lines = () => read('electron-builder.yml').split('\n').map(l => l.replace(/\s+#.*$/, '').trim()).filter(Boolean);
const value = key => {
  const line = lines().find(l => l.startsWith(`${key}:`));
  return line === undefined ? undefined : line.slice(key.length + 1).trim().replace(/^["']|["']$/g, '');
};

test('the config builds a .deb with an AppArmor profile and a per-user one-click NSIS installer', () => {
  const text = lines().join('\n');
  assert.match(text, /\bdeb\b/);
  assert.match(text, /\bnsis\b/);
  assert.equal(value('appArmorProfile'), 'build/apparmor-profile');
  assert.equal(value('createDesktopShortcut'), 'always');
  assert.equal(value('oneClick'), 'true');
  assert.equal(value('perMachine'), 'false');
  assert.equal(value('include'), 'build/installer.nsh');
  assert.equal(value('productName'), 'OpenZenBot');
  assert.equal(value('appId'), 'com.zenandtheartofcode.openzenbot');
});

test('the AppArmor profile only lets the installed binary use user namespaces', () => {
  const profile = read('build/apparmor-profile');
  assert.match(profile, /^\s*userns,\s*$/m);
  assert.match(profile, /flags=\(unconfined\)/);
  assert.match(profile, /\/opt\/OpenZenBot\//);
});

test('the uninstaller removes the downloaded runtime and never the vault', () => {
  const nsh = read('build/installer.nsh');
  assert.match(nsh, /!macro customUnInstall/);
  assert.match(nsh, /\$APPDATA\\OpenZenBot\\runtime/);
  assert.doesNotMatch(nsh, /vault|zen-agents/i);
});

test('package.json has the packaging scripts and pins electron-builder exactly', () => {
  const pkg = JSON.parse(read('package.json'));
  for (const s of ['fetch-uv', 'pack', 'dist:linux', 'dist:win']) assert.ok(pkg.scripts[s], s);
  assert.match(pkg.scripts['dist:linux'], /fetch-uv(\.mjs)?( --)? linux-x64/);
  assert.match(pkg.scripts['dist:win'], /fetch-uv(\.mjs)?( --)? win32-x64/);
  assert.match(pkg.devDependencies['electron-builder'] || '', /^\d+\.\d+\.\d+$/);
});

test('nothing in the packaging turns the sandbox off', () => {
  const walk = dir => readdirSync(dir).flatMap(n => {
    const p = join(dir, n);
    if (n === 'bin') return [];  // downloaded binaries, git-ignored
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
  const files = [...walk(join(root, 'build')), join(root, 'electron-builder.yml'), join(root, 'package.json')];
  for (const f of files) assert.doesNotMatch(readFileSync(f, 'latin1'), /no-sandbox|executableArgs/, f);
});

test('every Python package under src/ ships with the harness, and the GUI and shell do not', () => {
  const from = new Set(lines().filter(l => l.startsWith('- from: src/')).map(l => l.slice('- from: src/'.length)));
  const shipped = readdirSync(join(root, 'src')).filter(n => !['gui', 'electron', 'assets', '__pycache__'].includes(n));
  for (const n of shipped) assert.ok(from.has(n), `src/${n} is not in extraResources`);
  for (const n of ['gui', 'electron', 'assets']) assert.ok(![...from].some(f => f === n || f.startsWith(`${n}/`)), n);
});

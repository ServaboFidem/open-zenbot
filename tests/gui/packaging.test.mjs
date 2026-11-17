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

// "!node_modules/<package glob>/**" lines in files: the renderer libraries already bundled into dist/
const excludes = () => lines().filter(l => /^- ["']?!node_modules\//.test(l))
  .map(l => l.replace(/^- ["']?!node_modules\//, '').replace(/\/\*\*["']?$/, ''))
  .map(g => new RegExp('^' + g.replace(/[.+?^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '[^/]*') + '$'));
const excluded = name => excludes().some(re => re.test(name));
const packageOf = spec => spec.startsWith('@') ? spec.split('/').slice(0, 2).join('/') : spec.split('/')[0];

test('the renderer-only libraries bundled into dist/ are left out of the app', () => {
  for (const name of ['react', 'react-dom', '@fluentui/react-components', '@fluentui/react-icons', 'pdfjs-dist', 'docx',
                      'pptxgenjs', 'react-markdown', 'remark-gfm', 'remark-parse', 'unified', '@napi-rs/canvas',
                      '@napi-rs/canvas-linux-x64-gnu'])
    assert.ok(excluded(name), name);
});

test('every module the main process and preload require is a dependency the app keeps, with its own dependencies', () => {
  const pkg = JSON.parse(read('package.json'));
  const lock = JSON.parse(read('package-lock.json')).packages;
  const dir = join(root, 'src', 'electron');
  const required = new Set();
  for (const f of readdirSync(dir).filter(n => n.endsWith('.cjs')))
    for (const m of readFileSync(join(dir, f), 'utf8').matchAll(/require\(\s*['"]([^'"]+)['"]\s*\)/g))
      if (!m[1].startsWith('.') && !m[1].startsWith('node:') && m[1] !== 'electron') required.add(packageOf(m[1]));
  assert.ok(required.has('@sentry/electron'));
  // everything these reach through package-lock.json, none of it excluded
  const seen = new Set();
  const visit = name => {
    if (seen.has(name)) return;
    seen.add(name);
    assert.ok(!excluded(name), `${name} is needed at runtime but excluded`);
    const entry = lock[`node_modules/${name}`];
    for (const d of Object.keys({ ...(entry?.dependencies || {}), ...(entry?.optionalDependencies || {}) })) visit(d);
  };
  for (const name of required) {
    assert.ok(pkg.dependencies[name], `${name} is not in dependencies`);
    visit(name);
  }
});

test('a user sees OpenZenBot, never the npm name', () => {
  assert.equal(value('executableName'), 'openzenbot');
  assert.equal(value('packageName'), 'openzenbot');
  assert.equal(value('desktopName'), 'openzenbot.desktop');
  assert.equal(value('syncDesktopName'), 'true');
  const names = lines().filter(l => l.startsWith('artifactName:')).map(l => l.slice('artifactName:'.length).trim().replace(/^["']|["']$/g, ''));
  assert.deepEqual(names.sort(), ['OpenZenBot-Setup-${version}.${ext}', 'OpenZenBot_${version}_${arch}.${ext}']);
  assert.match(read('build/apparmor-profile'), /^profile openzenbot \/opt\/OpenZenBot\/openzenbot flags=\(unconfined\) \{$/m);
  // the launcher's comment and the package description come from here, not package.json's developer description
  assert.match(value('description') || '', /^OpenZenBot/);
  assert.doesNotMatch(read('electron-builder.yml') + read('build/apparmor-profile'), /zen-agents/);
});

// The release workflow: a draft release from a v* tag, built only after both test suites pass.
const workflow = () => read('.github/workflows/release.yml');
const jobBlock = name => {
  const m = new RegExp(`^  ${name}:\\n([\\s\\S]*?)(?=^  [a-z-]+:\\n|(?![\\s\\S]))`, 'm').exec(workflow());
  assert.ok(m, `job ${name}`);
  return m[1];
};

test('the release workflow runs only on v* tags', () => {
  const on = /^on:\n([\s\S]*?)^\S/m.exec(workflow() + '\nx')[1];
  assert.match(on, /^  push:\n    tags: \['v\*'\]\s*$/m);
  assert.doesNotMatch(on, /branches|pull_request|workflow_dispatch|schedule/);
});

test('the release workflow tests before it builds, and creates only a draft release', () => {
  const check = jobBlock('check');
  assert.match(check, /uv run pytest/);
  assert.match(check, /npm test/);
  assert.match(check, /uv lock --check --project packaging\/harness/);
  assert.match(check, /package\.json/);  // the tag must equal v + the package version
  assert.match(jobBlock('build'), /^    needs: check$/m);
  assert.match(jobBlock('build'), /os: ubuntu-22\.04\n\s+script: dist:linux/);
  assert.match(jobBlock('build'), /os: windows-latest\n\s+script: dist:win/);
  assert.match(jobBlock('build'), /run: npm run \$\{\{ matrix\.script \}\}/);
  assert.match(jobBlock('release'), /^    needs: build$/m);
  assert.match(jobBlock('release'), /gh release create .*--draft/);
  assert.doesNotMatch(workflow(), /--latest|gh release edit|--draft=false/);
});

test('the release workflow uploads the files electron-builder names, with least privilege', () => {
  const text = workflow();
  assert.match(text, /release\/OpenZenBot_\*_amd64\.deb/);
  assert.match(text, /release\/OpenZenBot-Setup-\*\.exe/);
  assert.match(text, /^permissions:\n  contents: read$/m);
  assert.match(jobBlock('release'), /^    permissions:\n      contents: write$/m);
  assert.doesNotMatch(jobBlock('check') + jobBlock('build'), /contents: write/);
});

test('every action is pinned to a 40-character commit SHA and no script interpolates the tag name', () => {
  const uses = workflow().split('\n').filter(l => /\buses:/.test(l));
  assert.ok(uses.length >= 5);
  for (const l of uses) assert.match(l, /uses: [\w.-]+\/[\w.-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$/, l);
  for (const l of workflow().split('\n').filter(l => !/^\s*(TAG|REF_NAME):/.test(l)))
    assert.doesNotMatch(l, /\$\{\{\s*github\.(ref_name|ref)\s*\}\}/, l);
});

test('the README and AGENTS.md state the free space setup needs, as setup.cjs has it', async () => {
  const { createRequire } = await import('node:module');
  const { NEEDED_BYTES } = createRequire(import.meta.url)('../../src/electron/setup.cjs');
  const gb = String(Number((NEEDED_BYTES / 1024 ** 3).toFixed(1)));
  assert.match(read('README.md'), new RegExp(`about ${gb} GB (of )?free`));
  assert.match(read('AGENTS.md'), new RegExp(`less than about ${gb} GB free`));
});

// Start at sign-in on Linux: an XDG autostart entry (src/electron/autostart.cjs), with a fake file system.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const { createAutostart, execLine } = createRequire(import.meta.url)('../../src/electron/autostart.cjs');

function rig({ env = {}, execPath = '/opt/OpenZenBot/openzenbot' } = {}) {
  const files = new Map(), log = [];
  const fs = {
    mkdir: d => log.push(['mkdir', d]),
    write: (f, t) => { files.set(f, t); log.push(['write', f]); },
    rename: (a, b) => { files.set(b, files.get(a)); files.delete(a); log.push(['rename', a, b]); },
    remove: f => { files.delete(f); log.push(['remove', f]); },
    exists: f => files.has(f),
  };
  return { a: createAutostart({ fs, env, home: '/home/zen', execPath }), files, log };
}

test('enable writes the entry tmp-then-rename under ~/.config/autostart', () => {
  const { a, files, log } = rig();
  assert.equal(a.enabled(), false);
  a.enable();
  const f = '/home/zen/.config/autostart/openzenbot.desktop';
  assert.equal(a.enabled(), true);
  assert.deepEqual(log.map(l => l[0]), ['mkdir', 'write', 'rename']);
  assert.equal(log[1][1], `${f}.tmp`);
  assert.equal(files.get(f), '[Desktop Entry]\nType=Application\nName=OpenZenBot\nExec="/opt/OpenZenBot/openzenbot" --hidden\nX-GNOME-Autostart-enabled=true\n');
  assert.ok(![...files.keys()].some(k => k.endsWith('.tmp')));
});

test('disable removes it and enabled reads the file', () => {
  const { a } = rig();
  a.enable(); a.disable();
  assert.equal(a.enabled(), false);
});

test('XDG_CONFIG_HOME moves the folder', () => {
  const { a } = rig({ env: { XDG_CONFIG_HOME: '/cfg' } });
  a.enable();
  assert.equal(a.file, '/cfg/autostart/openzenbot.desktop');
  assert.equal(a.enabled(), true);
});

test('a path with a space is quoted', () => {
  assert.equal(execLine('/home/a b/OpenZenBot/openzenbot'), '"/home/a b/OpenZenBot/openzenbot" --hidden');
  assert.equal(execLine('/x/"y"'), '"/x/\\\\"y\\\\"" --hidden');
  assert.equal(execLine("/x/$y"), String.raw`"/x/\\$y" --hidden`);
});

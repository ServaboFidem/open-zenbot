// The system tray: closing the window hides it while ZenBot keeps running; the tray's menu opens or quits.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const { createTray, closeToTray, NOTE } = createRequire(import.meta.url)('../../src/electron/tray.cjs');

class FakeTray {
  constructor(icon) { this.icon = icon; this.handlers = {}; this.menu = null; this.tip = null; }
  setToolTip(t) { this.tip = t; }
  setContextMenu(m) { this.menu = m; }
  on(name, fn) { this.handlers[name] = fn; }
}
const FakeMenu = { buildFromTemplate: template => ({ template }) };
function make(over = {}) {
  const calls = { show: 0, quit: 0, notify: [], write: 0 };
  const seen = { value: false };
  const parts = createTray({
    Tray: FakeTray, Menu: FakeMenu, icon: 'icon.png',
    showWindow: () => { calls.show++; }, quit: () => { calls.quit++; },
    notify: s => calls.notify.push(s),
    seenStore: { read: () => seen.value, write: () => { calls.write++; seen.value = true; } },
    ...over,
  });
  return { ...parts, calls };
}

test('the menu offers Open ZenBot and Quit ZenBot', () => {
  const { tray, calls } = make();
  const items = tray.menu.template;
  assert.deepEqual(items.map(i => i.label), ['Open ZenBot', 'Quit ZenBot']);
  items[0].click(); items[1].click();
  assert.equal(calls.show, 1);
  assert.equal(calls.quit, 1);
});

test('a left click opens the window', () => {
  const { tray, calls } = make();
  tray.handlers.click();
  assert.equal(calls.show, 1);
});

function fakeWin() {
  const w = { hidden: 0, handlers: {} };
  w.on = (n, fn) => { w.handlers[n] = fn; };
  w.hide = () => { w.hidden++; };
  return w;
}

test('closing hides the window while not quitting', () => {
  const win = fakeWin(); let hid = 0, prevented = 0;
  closeToTray(win, { isQuitting: () => false, onHidden: () => { hid++; } });
  win.handlers.close({ preventDefault: () => { prevented++; } });
  assert.equal(prevented, 1);
  assert.equal(win.hidden, 1);
  assert.equal(hid, 1);
});

test('closing during quit closes for real', () => {
  const win = fakeWin(); let prevented = 0, hid = 0;
  closeToTray(win, { isQuitting: () => true, onHidden: () => { hid++; } });
  win.handlers.close({ preventDefault: () => { prevented++; } });
  assert.equal(prevented, 0);
  assert.equal(win.hidden, 0);
  assert.equal(hid, 0);
});

test('the tray note is shown once ever', () => {
  const { onHidden, calls } = make();
  onHidden(); onHidden();
  assert.deepEqual(calls.notify, [NOTE]);
  assert.equal(calls.write, 1);
  const again = make({ seenStore: { read: () => true, write: () => {} } });
  again.onHidden();
  assert.deepEqual(again.calls.notify, []);
});

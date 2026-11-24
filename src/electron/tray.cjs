// The system tray: closing the window hides it and ZenBot keeps running (the harness, reminders and the morning
// briefing need it up). The tray's menu opens the window or quits. Electron's Tray, Menu and Notification arrive as
// arguments, so tests stand them in.
const NOTE = 'ZenBot is still running in the tray. Right-click its icon to open it or quit.';

// seenStore: { read(): boolean, write(): void }. onHidden() tells the user once, ever, where the window went.
function createTray({ Tray, Menu, icon, showWindow, quit, notify, seenStore }) {
  const tray = new Tray(icon);
  tray.setToolTip('ZenBot');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Open ZenBot', click: () => showWindow() },
    { label: 'Quit ZenBot', click: () => quit() },
  ]));
  tray.on('click', () => showWindow());
  let told = null;
  function onHidden() {
    if (told === null) { try { told = !!seenStore.read(); } catch { told = false; } }
    if (told) return;
    told = true;
    notify(NOTE);
    try { seenStore.write(); } catch { /* the note may show again next time */ }
  }
  return { tray, onHidden };
}

// createTray, or null when no tray can be made (a Linux desktop without one): the app goes on without it, and only
// the error's message is logged.
function openTray(options, { log = console.error } = {}) {
  try {
    return createTray(options);
  } catch (e) {
    log(`tray could not be made: ${e && e.message ? e.message : 'unknown error'}`);
    return null;
  }
}

// A closed window is hidden, not destroyed, unless the app is quitting or there is no tray to bring it back from.
function closeToTray(win, { isQuitting, onHidden, hasTray = () => true }) {
  win.on('close', event => {
    if (isQuitting() || !hasTray()) return;
    event.preventDefault();
    win.hide();
    if (onHidden) onHidden();
  });
}

// The last window closed: with a tray ZenBot keeps running there; without one it would run invisibly, so it quits.
function allClosed({ hasTray, quit }) {
  if (!hasTray()) quit();
}

// A --hidden start (at sign-in) stays out of sight only when there is a tray to open the window from.
const startsHidden = (argv, hasTray) => (argv || []).includes('--hidden') && !!hasTray;

module.exports = { createTray, openTray, closeToTray, allClosed, startsHidden, NOTE };

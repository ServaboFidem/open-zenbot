// The update notice and Settings > About: what the main process's update State (src/electron/update-check.cjs) means
// for the page. Pure, so it is tested without the page (tests/gui/updates.test.mjs).

// The version baked in by scripts/build-gui.mjs, for a browser with no window.openzenbot. The node tests import this
// module without the build, where the global is undefined.
const BUILT_VERSION = typeof __APP_VERSION__ === 'string' ? __APP_VERSION__ : '';

export function noticeOf(state) {
  if (!state || !state.notify) return null;
  return { version: state.latest, notes: typeof state.notes === 'string' ? state.notes : '' };
}

export function statusLine(state) {
  if (!state || !state.checkedAt) return 'Not checked yet.';
  if (state.error) return 'Could not check for updates.';
  if (state.newer) return `Version ${state.latest} is available.`;
  return 'You have the latest version.';
}

export function appVersion(win) {
  return win?.openzenbot?.version || BUILT_VERSION;
}

// The update calls, or null in a browser (no window.openzenbot, or an older shell without updates).
export function updatesApi(win) {
  const u = win?.openzenbot?.updates;
  return u && typeof u.get === 'function' ? u : null;
}

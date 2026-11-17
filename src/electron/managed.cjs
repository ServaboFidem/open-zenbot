// The installed app's launch, in order: one update check (when automatic checks are on) and, when a newer release
// is out, a wait for "get the new version" or "continue"; first-launch setup when needsSetup says so, waiting for
// Retry after a failed step; then the harness; the workspace once it answers. A harness that stops later puts the
// setup page back in the failed phase with Restart, which makes a new harness (after the old one has stopped)
// rather than starting the old one again. shutdown() cancels setup without waiting for it, stops the harness
// once, and from then on every late result is ignored. Anything that throws along the way (the update check, setup,
// the log rotation in makeHarness, the harness start) is logged with logError, whatever it started is cancelled or
// stopped, and the page shows the failed phase with reason 'error': "OpenZenBot could not start." with the log folder
// only. No Retry or Restart there, since the step that threw may have left its part half done. The page's state is { phase, update, setup, harness }
// (src/gui/setup-view.js reads it).
//
// Everything with a side effect is passed in, so this file needs no Electron:
//   checker: createUpdateChecker (update-check.cjs); needsSetup() -> Promise<boolean>
//   makeSetup(emit) -> createSetup(...) (setup.cjs) reporting its state to emit
//   makeHarness() -> Promise<createHarness(...)> (harness-process.cjs), log rotated and opened
//   show('setup' | 'workspace'); send(state) to the page; startChecker() -> stop (the periodic update check)
//   logError(error) writes an unexpected error to the setup log

function createManaged({ checker, needsSetup, makeSetup, makeHarness, show, send, startChecker, logError = () => {} }) {
  let state = { phase: 'update', update: null, setup: null, harness: null };
  let quitting = false;
  let shutdownPromise = null;
  let setup = null;
  let harness = null;
  let restarting = false;
  let stopChecker = null;
  let waiting = null;  // { phase, resolve } while a button is awaited

  const snapshot = () => ({ ...state });
  function set(patch) {
    state = { ...state, ...patch };
    send(snapshot());
  }
  const waitFor = phase => new Promise(resolve => { waiting = { phase, resolve }; });
  function release(phase) {
    if (!waiting || waiting.phase !== phase || state.phase !== phase) return;
    const { resolve } = waiting;
    waiting = null;
    resolve();
  }

  async function startHarness(firstStart) {
    const h = await makeHarness();
    if (quitting) return;
    harness = h;
    h.onExit(() => {
      if (quitting || harness !== h) return;
      set({ phase: 'failed', harness: { reason: 'exited', detail: '' } });
      show('setup');
    });
    set({ phase: 'starting', harness: { firstStart } });
    const r = await h.start();
    if (quitting || harness !== h) return;  // a stop() during start answers 'exited': not a failure after quit
    if (r && r.ok) {
      show('workspace');
      if (!stopChecker) stopChecker = startChecker();
      return;
    }
    set({ phase: 'failed', harness: { reason: (r && r.reason) || 'exited', detail: (r && r.detail) || '' } });
  }

  // an unexpected error: log it, leave nothing running, and say so on the setup page
  async function fail(error) {
    try { logError(error); } catch { /* the page still says it */ }
    if (quitting) return;
    if (setup) setup.cancel();
    const h = harness;
    harness = null;  // its late exit is not "the harness stopped"
    set({ phase: 'failed', harness: { reason: 'error', detail: String((error && error.message) || error) } });
    show('setup');
    if (h) { try { await h.stop(); } catch { /* already gone */ } }
  }

  async function run() {
    try {
      await sequence();
    } catch (e) {
      await fail(e);
    }
  }

  async function sequence() {
    show('setup');
    if (checker.get().auto) {
      set({ phase: 'update', update: null });
      const update = await checker.check();
      if (quitting) return;
      if (update && update.notify) {
        set({ update });
        await waitFor('update');
        if (quitting) return;
      }
    }
    let firstStart = false;
    if (await needsSetup()) {
      if (quitting) return;
      firstStart = true;
      setup = makeSetup(s => { if (!quitting) set({ setup: s }); });
      set({ phase: 'setup', setup: setup.state() });
      for (;;) {
        const r = await setup.start();
        if (quitting || (r && r.cancelled)) return;
        if (r && r.ok) break;
        await waitFor('setup');
        if (quitting) return;
      }
    }
    if (quitting) return;
    await startHarness(firstStart);
  }

  async function restartHarness() {
    if (quitting || restarting || state.phase !== 'failed' || !state.harness || state.harness.reason !== 'exited') return;
    restarting = true;
    try {
      const old = harness;
      if (old) await old.stop();
      if (!quitting) await startHarness(false);
    } catch (e) {
      await fail(e);
    } finally {
      restarting = false;
    }
  }

  function shutdown() {
    if (!shutdownPromise) {
      quitting = true;
      if (setup) setup.cancel();
      if (waiting) { const { resolve } = waiting; waiting = null; resolve(); }
      if (stopChecker) stopChecker();
      shutdownPromise = (async () => { if (harness) await harness.stop(); })();
    }
    return shutdownPromise;
  }

  return {
    run,
    state: snapshot,
    proceed: () => release('update'),
    retry: () => release('setup'),
    openUpdate: () => checker.open(),
    restartHarness,
    shutdown,
  };
}

module.exports = { createManaged };

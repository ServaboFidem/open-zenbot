// Tells the harness how long the computer has been idle, so it knows when it may dream. The main process does it
// (no window needed); every failure is swallowed, since the harness may not be up yet.
function createIdleReport({ idleSeconds, post, every = 60_000, setInterval: setTimer = setInterval, clearInterval: clearTimer = clearInterval }) {
  let timer = null;
  async function tick() {
    try { await post('/dream/idle', { idle_s: idleSeconds() }); } catch { /* the harness may not be up yet */ }
  }
  function start() {
    if (timer !== null) return;
    timer = setTimer(() => { tick(); }, every);
    tick();
  }
  function stop() {
    if (timer === null) return;
    clearTimer(timer);
    timer = null;
  }
  return { start, stop, tick };
}

module.exports = { createIdleReport };

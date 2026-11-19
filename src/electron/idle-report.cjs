// Tells the harness how long the computer has been idle, so it knows when it may dream. The main process does it
// (no window needed); every failure is swallowed, since the harness may not be up yet. It posts every `every` ms, and
// also reads the idle time every `watchEvery` ms and posts at once when it dropped below the previous reading: the
// user touched the computer, and a dream under way should stop now, not up to a minute later.
function createIdleReport({ idleSeconds, post, every = 60_000, watchEvery = 5_000,
  setInterval: setTimer = setInterval, clearInterval: clearTimer = clearInterval }) {
  let timer = null, watcher = null, last = null;
  function read() {
    try { return idleSeconds(); } catch { return null; }  // no monitor, say
  }
  async function send(idle) {
    try { await post('/dream/idle', { idle_s: idle }); } catch { /* the harness may not be up yet */ }
  }
  async function tick() {  // post now
    const idle = read();
    if (idle === null) return;
    last = idle;
    await send(idle);
  }
  function watch() {
    const idle = read();
    if (idle === null) return;
    const dropped = last !== null && idle < last;
    last = idle;
    if (dropped) send(idle);
  }
  function start() {
    if (timer !== null) return;
    timer = setTimer(() => { tick(); }, every);
    watcher = setTimer(watch, watchEvery);
    tick();
  }
  function stop() {
    if (timer === null) return;
    clearTimer(timer);
    clearTimer(watcher);
    timer = watcher = null;
  }
  return { start, stop, tick };
}

module.exports = { createIdleReport };

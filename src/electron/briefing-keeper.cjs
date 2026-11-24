// Keeps ZenBot ready at a briefing's time: a Windows wake task per enabled slot line (8 minutes early), the
// power-save blocker held around the slot, and start at sign-in (installed app only). The harness says when
// briefings are due (GET /briefings); nothing here schedules from its own copy. Every side effect arrives as an
// argument, so tests stand them in. schtasks runs from an argument array (never a shell).
const PREFIX = 'OpenZenBot briefing wake';
const WAKE_LEAD_MIN = 8;
const WAKE_LEAD_MS = WAKE_LEAD_MIN * 60_000;
const LATE_LIMIT_MS = 15 * 60_000;
const MAX_LINES = 7;
const TICK_MS = 60_000;
const WAKE_ERROR = 'Windows would not set a wake-up time for the briefing.';
const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];

// A window opens unless the app was started hidden (at sign-in): then only the tray shows.
const shouldShowWindow = argv => !(argv || []).includes('--hidden');

const taskName = n => `${PREFIX} ${n}`;
const two = n => String(n).padStart(2, '0');

// "06:45" and days [0..6] (0 = Monday) -> the wake time 8 minutes before, rolled back a day over midnight
function wakeAt(line) {
  const m = /^(\d{2}):(\d{2})$/.exec(line.time || '');
  if (!m) return null;
  let minutes = Number(m[1]) * 60 + Number(m[2]) - WAKE_LEAD_MIN;
  let back = 0;
  if (minutes < 0) { minutes += 1440; back = 1; }
  const days = [...new Set((line.days || []).filter(d => Number.isInteger(d) && d >= 0 && d <= 6))]
    .map(d => (d + 7 - back) % 7).sort((a, b) => a - b);
  if (!days.length) return null;
  return { hh: two(Math.floor(minutes / 60)), mm: two(minutes % 60), days };
}

function taskXml({ hh, mm, days }) {
  const names = days.map(d => `<${DAYS[d]}/>`).join('');
  return `<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-01-01T${hh}:${mm}:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByWeek>
        <DaysOfWeek>${names}</DaysOfWeek>
        <WeeksInterval>1</WeeksInterval>
      </ScheduleByWeek>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>false</StartWhenAvailable>
    <ExecutionTimeLimit>PT1M</ExecutionTimeLimit>
    <WakeToRun>true</WakeToRun>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>/c exit 0</Arguments>
    </Exec>
  </Actions>
</Task>
`;
}

// The briefings of a status, switched on or off; a status that is not an object, or entries that are not, count as none
function briefingsOf(status) {
  const list = status && typeof status === 'object' && Array.isArray(status.briefings) ? status.briefings : [];
  return list.filter(b => b && typeof b === 'object');
}

// The enabled briefings of a status
const enabledOf = status => briefingsOf(status).filter(b => b.enabled === true);

// The enabled slot lines of every briefing, in order
function lines(status) {
  const out = [];
  for (const b of enabledOf(status)) {
    for (const s of Array.isArray(b.slots) ? b.slots : []) if (s && typeof s === 'object') out.push({ days: s.days, time: s.time });
  }
  return out.slice(0, MAX_LINES);
}

// fetchStatus(): GET /briefings. runSchtasks(args: string[]). writeTaskXml(xml) -> { path, remove() }.
function createBriefingKeeper({ platform, fetchStatus, runSchtasks, writeTaskXml, powerSaveBlocker, setLoginItem, getLoginItem,
  managed, now = Date.now, setTimer = setInterval, clearTimer = clearInterval }) {
  let blockerId = null, wakeError = null, synced = null, timer = null, chain = Promise.resolve();
  let deadline = null, ticking = false;  // deadline: from the last good status, what justified the hold + LATE_LIMIT

  async function applyTasks(wanted) {
    let failed = false;
    for (let i = 0; i < wanted.length; i++) {
      const at = wakeAt(wanted[i]);
      if (!at) { try { await runSchtasks(['/Delete', '/TN', taskName(i + 1), '/F']); } catch { /* none there */ } continue; }
      let file = null;
      try {
        file = await writeTaskXml(taskXml(at));
        await runSchtasks(['/Create', '/TN', taskName(i + 1), '/XML', file.path, '/F']);
      } catch { failed = true; } finally {
        try { if (file) await file.remove(); } catch { /* a temp file */ }
      }
    }
    for (let n = wanted.length + 1; n <= MAX_LINES; n++) {
      try { await runSchtasks(['/Delete', '/TN', taskName(n), '/F']); } catch { /* it was not there */ }
    }
    wakeError = failed ? WAKE_ERROR : null;
    return !failed;
  }

  function sync() {
    chain = chain.then(async () => {
      if (platform !== 'win32') return;
      let status;
      try { status = await fetchStatus(); } catch { return; }  // the harness may not be up yet
      if (!status || typeof status !== 'object') return;
      const wanted = lines(status);
      // only lines that were all made count as synced: a failed /Create is tried again on the next tick
      synced = (await applyTasks(wanted)) ? JSON.stringify(wanted) : null;
    }).catch(() => { synced = null; wakeError = WAKE_ERROR; });
    return chain;
  }

  function hold(on) {
    if (on && blockerId === null) blockerId = powerSaveBlocker.start('prevent-app-suspension');
    else if (!on && blockerId !== null) { powerSaveBlocker.stop(blockerId); blockerId = null; }
  }

  async function tick() {
    if (ticking) return;  // a read that hangs must not pile up ticks
    ticking = true;
    try { await tickOnce(); } finally { ticking = false; }
  }

  async function tickOnce() {
    let status = null;
    try { status = await fetchStatus(); } catch { status = null; }
    if (!status || typeof status !== 'object') {
      // the harness did not answer: keep what was held only until the last good status's deadline
      if (blockerId !== null && (deadline === null || now() > deadline)) hold(false);
      return;
    }
    const on = enabledOf(status);
    // Play now runs a switched-off briefing too: any briefing under way holds the computer awake
    const busy = briefingsOf(status).some(b => b.state === 'building' || b.state === 'playing');
    const t = now();
    const near = on.some(b => {
      const next = Date.parse(b.next_run || '');
      return Number.isFinite(next) && next - t <= WAKE_LEAD_MS && t <= next + LATE_LIMIT_MS;
    });
    // the deadline comes only from what justifies the hold: a slot inside its window, or a briefing under way (whose
    // next_run has already moved on to a later slot)
    const reasons = on.map(b => Date.parse(b.next_run || '')).filter(n => Number.isFinite(n) && n - t <= WAKE_LEAD_MS && t <= n + LATE_LIMIT_MS)
      .map(n => n + LATE_LIMIT_MS);
    if (busy) reasons.push(t + LATE_LIMIT_MS);
    deadline = reasons.length ? Math.min(Math.max(...reasons), t + WAKE_LEAD_MS + LATE_LIMIT_MS) : null;
    hold(busy || near);
    if (JSON.stringify(lines(status)) !== synced) await sync();
  }

  function loginState() {
    let login = false;
    if (managed) { try { login = !!getLoginItem(); } catch { login = false; } }
    return { managed: !!managed, platform, login, wake_error: wakeError };
  }

  function setLogin(on) {
    if (!managed || typeof on !== 'boolean') return loginState();
    setLoginItem({ openAtLogin: on, args: ['--hidden'] });
    return loginState();
  }

  function start() {
    if (timer !== null) return;
    const run = () => { tick().catch(() => {}); };
    timer = setTimer(run, TICK_MS);
    run();
  }
  function stop() {
    if (timer !== null) { clearTimer(timer); timer = null; }
    hold(false);
  }

  return { sync, tick, loginState, setLogin, start, stop };
}

module.exports = { createBriefingKeeper, shouldShowWindow, taskXml, wakeAt, PREFIX, LATE_LIMIT_MS, WAKE_ERROR };

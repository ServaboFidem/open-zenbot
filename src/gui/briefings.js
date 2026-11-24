// The Briefings section's decisions (BriefingsSection.jsx, BriefingDialog.jsx). Pure, so they test without a DOM
// (tests/gui/briefings.test.mjs). Speaker names come from Sonos: shown as plain text, never as markup.
// The sentences in `validate` and `READINESS` are the harness's own (src/briefing/settings.py `check`,
// src/briefing/routes.py `NOT_READY`): keep them equal. `validate` mirrors only the sentences this form can trigger
// (not "Only the morning briefing exists." or "Enabled must be on or off.").

export const POLL_MS = 15000;  // the status is read again this often while the Routines page is open
export const MAX_SLOTS = 7;
export const MAX_SPEAKERS = 32;
export const VOLUME_MAX = 100;
export const DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];  // 0 = Monday, as routines

export const SECTION_LINE = 'ZenBot speaks to you on your Sonos speakers.';
export const SAYS_LINE = "What it says: a good-morning greeting, a new joke, the weather, today's birthdays and today's calendar.";
export const WEATHER_SET = 'Uses the city from Morning digest settings';
export const WEATHER_UNSET = 'Set a weather city in Settings > Calendar > Morning digest to hear the forecast';
export const KEEPER_LINUX = 'Keep this computer from sleeping overnight, or the briefing will be skipped.';
export const KEEPER_WAKE = 'Windows wakes the computer for a briefing only when "Allow wake timers" is on in the power plan. Some laptops ignore wake timers.';
export const LOGIN_LABEL = 'Start ZenBot when I sign in to this computer';
export const LOGIN_FAILED = 'The briefing is saved, but starting ZenBot when you sign in could not be changed. Try again.';

/** Whether a sign-in change took: the keeper's answer says the login state, which must be the one asked for. An
 * answer without one (an older desktop app) is taken as done. */
export const loginTook = (asked, state) => !state || typeof state !== 'object' || typeof state.login !== 'boolean' || state.login === asked;

const READINESS = {
  sonos_off: 'Sonos is off. Switch it on in Settings > Connected apps.',
  no_speakers: 'No Sonos speaker was found.',
  no_voice: 'Set up a voice in Settings > Harness Tools > Read aloud first.',
  ceiling_zero: 'Your Sonos volume limit is 0.',
};

/** The sentence for a readiness code from GET /briefings; '' when ready. */
export const readinessLine = reason => (!reason ? '' : READINESS[reason] || 'The briefing is not ready.');

/** Where a missing piece is fixed: the Settings panel and, where there is one, the section's anchor title. */
export function readinessFix(reason) {
  if (reason === 'no_voice') return { panel: 'tools', title: '' };
  if (reason === 'sonos_off' || reason === 'no_speakers' || reason === 'ceiling_zero') return { panel: 'apps', title: 'Sonos' };
  return null;
}

/** The note about a hosted voice, or null (Kokoro runs on this computer; no provider means nothing is sent). */
export const hostedNote = provider => (!provider || provider === 'kokoro' ? null
  : `Your briefing, including calendar titles and birthday names, is sent to ${provider} to be spoken. Kokoro keeps it on this computer.`);

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

/** "06:45" as "6:45 AM". */
export function clock12(time) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(time || ''));
  if (!m) return String(time || '');
  const h = Number(m[1]);
  return `${h % 12 === 0 ? 12 : h % 12}:${m[2]} ${h < 12 ? 'AM' : 'PM'}`;
}

/** Days as "Mon–Fri" for a run of three or more in a row, else a comma list ("Sat, Sun"). */
function daysLabel(days) {
  const d = [...new Set(days)].sort((a, b) => a - b);
  const runs = [];
  for (const x of d) {
    const last = runs.at(-1);
    if (last && last[1] === x - 1) last[1] = x; else runs.push([x, x]);
  }
  return runs.flatMap(([a, b]) => (b - a >= 2 ? [`${DAY_NAMES[a]}–${DAY_NAMES[b]}`] : Array.from({ length: b - a + 1 }, (_, i) => DAY_NAMES[a + i]))).join(', ');
}

/** "Mon–Fri at 6:45 AM · Sat, Sun at 8:30 AM". */
export const scheduleLabel = slots => (Array.isArray(slots) ? slots : [])
  .map(s => `${daysLabel(s.days || [])} at ${clock12(s.time)}`).join(' · ');

/** True when a day is in two lines (or twice in one). */
export function overlap(slots) {
  const seen = new Set();
  for (const s of Array.isArray(slots) ? slots : []) {
    for (const d of s.days || []) {
      if (seen.has(d)) return true;
      seen.add(d);
    }
  }
  return false;
}

/** The harness's sentence for a form it would refuse, else null. `form`: { slots, speakers (null = every), volume }. */
export function validate(form, ceiling) {
  const slots = form.slots;
  if (!Array.isArray(slots) || !slots.length) return 'Add at least one day and time.';
  if (slots.length > MAX_SLOTS) return `Use at most ${MAX_SLOTS} lines.`;
  const seen = new Set();
  for (const s of slots) {
    if (!Array.isArray(s.days) || !s.days.length) return 'Pick at least one day for each line.';
    if (typeof s.time !== 'string' || !TIME_RE.test(s.time)) return 'Enter each time as HH:MM, from 00:00 to 23:59.';
    if (new Set(s.days).size !== s.days.length || s.days.some(d => seen.has(d))) return 'Each day can have only one time.';
    s.days.forEach(d => seen.add(d));
  }
  // an enabled briefing is held to the Sonos limit, a switched-off one to 1..100 only (so it can always be switched off)
  const on = form.enabled !== false;
  if (on && !(ceiling >= 1)) return READINESS.ceiling_zero;
  const top = on ? Math.min(ceiling, VOLUME_MAX) : VOLUME_MAX;
  if (!Number.isInteger(form.volume) || form.volume < 1 || form.volume > top) return `Pick a volume from 1 to ${top}.`;
  if (form.speakers !== null && form.speakers !== undefined) {
    const sp = form.speakers;
    if (!Array.isArray(sp) || sp.length < 1 || sp.length > MAX_SPEAKERS || sp.some(x => typeof x !== 'string' || !x.trim())) {
      return `Pick from 1 to ${MAX_SPEAKERS} speakers, or use every speaker.`;
    }
    if (new Set(sp.map(x => x.toLowerCase())).size !== sp.length) return 'Each speaker can be listed only once.';
  }
  return null;
}

/** The body the row's switch saves: the saved volume to switch off, min(volume, limit) to switch on (it plays at that
 * anyway). Under a zero limit the harness then refuses with 'Your Sonos volume limit is 0.'. */
export const toggleBody = (b, enabled, ceiling) => ({
  enabled, slots: b.slots, speakers: b.speakers, volume: enabled ? Math.min(b.volume, Math.max(0, ceiling ?? 0)) : b.volume,
});

/** Weekdays at 06:45 on every speaker, at volume min(25, ceiling). */
export const defaultForm = ceiling => ({ enabled: true, slots: [{ days: [0, 1, 2, 3, 4], time: '06:45' }], speakers: null, volume: Math.min(25, ceiling) });

const SKIP_WHY = {
  sonos_off: 'Sonos is off',
  no_speakers: 'no Sonos speaker was found',
  no_voice: 'no voice is set up',
  ceiling_zero: 'your Sonos volume limit is 0',
};

const list = names => (names.length < 3 ? names.join(' and ') : `${names.slice(0, -1).join(', ')} and ${names.at(-1)}`);

// the slot is the harness's local time with no zone: read the hour and minute as written
const slotClock = slot => {
  const m = /T(\d{2}:\d{2})/.exec(String(slot || ''));
  return m ? `${Number(m[1].slice(0, 2))}:${m[1].slice(3)}` : '';
};

/** The last run in words. `outcome` decides; `reason` is a code for a skip and a sentence for a failure. */
export function lastRunLine(lastRun, lastSkip) {
  const r = lastRun || lastSkip;
  if (!r) return '';
  if (r.outcome === 'played') {
    const n = r.played || 0;
    let line = `played on ${n} ${n === 1 ? 'speaker' : 'speakers'}`;
    const failed = Object.keys(r.failed || {});
    if (failed.length) line += `; ${list(failed)} did not answer`;
    const phone = r.phone_paused || [];
    if (phone.length) line += `; ${phone.map(n2 => `${n2} was playing from a phone: press play there to resume.`).join(' ')}`;
    return line;
  }
  if (r.outcome === 'skipped_late') {
    const at = slotClock(r.slot);
    return at ? `skipped: ZenBot was not running at ${at}` : 'skipped: ZenBot was not running';
  }
  if (r.outcome === 'skipped') return SKIP_WHY[r.reason] ? `skipped: ${SKIP_WHY[r.reason]}` : 'skipped';
  if (r.outcome === 'failed') return r.reason ? `failed: ${r.reason}` : 'failed';
  return '';
}

/** The last run was skipped because ZenBot was not running or the computer was asleep. */
export const skippedLate = lastRun => lastRun?.outcome === 'skipped_late';

/** Play now is offered while nothing is under way and the briefing could play. */
export const canPlay = b => b?.state === 'idle' && b?.ready === true;

/** The desktop app's keeper, window.openzenbot.briefingKeeper: { get(), setLogin(on), refresh() }; null in a plain browser. */
export const keeperOf = win => win?.openzenbot?.briefingKeeper || null;

/** What keeper.get() answers is { managed, platform, login, wake_error }; each decision below takes it (or null). */
/** The sign-in box shows in the installed app only. */
export const offersSignIn = state => state?.managed === true;

/** The box starts as the setting is, and ticked when the first briefing is added. */
export const signInStart = (state, hasBriefing) => (hasBriefing ? state?.login === true : true);

/** The sentence about waking or staying awake: Linux cannot wake the computer; Windows needs wake timers on. */
export const keeperSentence = state => (state?.platform === 'linux' ? KEEPER_LINUX : state?.platform === 'win32' ? KEEPER_WAKE : '');

/** The warning on the briefing row when no wake task could be made. */
export const wakeWarning = state => (typeof state?.wake_error === 'string' ? state.wake_error : '');

/** What is missing, from the top-level status (it holds even before a briefing exists); a ceiling below 1 counts. */
export function formBlocker(status) {
  if (!status) return null;
  const reason = status.reason || (status.ceiling < 1 ? 'ceiling_zero' : null);
  return reason ? readinessLine(reason) : null;
}

/** No volume can be picked: the Sonos limit is below 1. */
export const ceilingLow = status => !!status && (status.reason === 'ceiling_zero' || status.ceiling < 1);

/** Save is off when the form would play and no volume can be picked. A switched-off briefing keeps its saved volume
 * (held to 1..100 only, as `validate` and the harness's `check` do), so its days, times and speakers can still be
 * changed under a limit of 0. */
export const saveBlocked = (status, form) => ceilingLow(status) && form?.enabled !== false;

/** The speakers the form offers: the ones that answer now, then any saved one that does not (marked missing), so a
 * saved choice can always be seen and unticked. */
export function speakerChoices(rooms, chosen) {
  const found = Array.isArray(rooms) ? rooms.filter(n => typeof n === 'string') : [];
  const seen = new Set(found);
  const missing = (Array.isArray(chosen) ? chosen : []).filter(n => typeof n === 'string' && !seen.has(n) && seen.add(n));
  return [...found.map(name => ({ name, missing: false })), ...missing.map(name => ({ name, missing: true }))];
}

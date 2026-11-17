// The update check: asks GitHub Releases whether a newer release exists. It only reads one public page,
// sends a User-Agent with the app version and nothing else about the machine or the user, and never
// installs anything. fetch(url, {headers, signal}), readStore() -> the saved object or null,
// writeStore(obj), now() -> ms and openExternal(url) are passed in, so this file needs no Electron.
// open() takes no argument and opens only a stored link under RELEASE_PAGES (safeUrl): a release's html_url that
// points anywhere else is dropped when it is read from GitHub or from the store, and checked again by open().
const RELEASES_API = 'https://api.github.com/repos/ServaboFidem/open-zenbot/releases/latest';
const RELEASE_PAGES = 'https://github.com/ServaboFidem/open-zenbot/releases/';
const NOTES_MAX = 2000;
const TIMEOUT_MS = 10_000;
const DAY_MS = 86_400_000;

const SEMVER = /^v?(\d+)\.(\d+)\.(\d+)$/;

// -1, 0 or 1; null when either side is not MAJOR.MINOR.PATCH (a prerelease suffix, "nightly", "1.2").
function compareVersions(a, b) {
  const x = typeof a === 'string' ? SEMVER.exec(a) : null;
  const y = typeof b === 'string' ? SEMVER.exec(b) : null;
  if (!x || !y) return null;
  for (let i = 1; i <= 3; i++) {
    const d = Number(x[i]) - Number(y[i]);
    if (d) return d < 0 ? -1 : 1;
  }
  return 0;
}

const bare = v => (typeof v === 'string' && SEMVER.test(v) ? v.replace(/^v/, '') : null);
const RELEASE_PATH = new URL(RELEASE_PAGES).pathname;

// The parsed, normalised link when it is a page under this repo's releases on https://github.com, else null. A
// prefix test on the raw string is not enough: a browser resolves `..`, `%2e%2e` and backslashes into another
// repository. So the string must hold no backslash and no encoded dot, slash or backslash, the parsed origin must be
// exactly https://github.com with no user or password, and the parser's normalised path must still be under the
// releases. The caller opens the parsed href, never the raw string. Used on GitHub's answer and on the stored one.
function safeUrl(u) {
  if (typeof u !== 'string' || u.includes('\\') || /%(2e|2f|5c)/i.test(u)) return null;
  let url;
  try { url = new URL(u); } catch { return null; }
  if (url.origin !== 'https://github.com' || url.username || url.password) return null;
  if (!url.pathname.startsWith(RELEASE_PATH)) return null;
  return url.href;
}

function createUpdateChecker({ version, fetch, readStore = () => null, writeStore = () => {}, now = Date.now, openExternal = () => {} }) {
  let saved;       // undefined until read
  let error = null;

  function load() {
    if (saved === undefined) {
      let s = null;
      try { s = readStore(); } catch { /* an unreadable store is an empty one */ }
      s = s && typeof s === 'object' ? s : {};
      saved = {
        latest: bare(s.latest),
        notes: typeof s.notes === 'string' ? s.notes.slice(0, NOTES_MAX) : '',
        url: safeUrl(s.url),
        checkedAt: typeof s.checkedAt === 'string' ? s.checkedAt : null,
        skipped: bare(s.skipped),
        auto: typeof s.auto === 'boolean' ? s.auto : true,
      };
    }
    return saved;
  }
  function persist() {
    try { writeStore({ ...saved }); } catch { /* the answer still holds for this run */ }
  }

  function get() {
    const s = load();
    const newer = s.latest !== null && compareVersions(s.latest, version) === 1;
    return {
      current: version, latest: s.latest, newer, notify: newer && s.latest !== s.skipped,
      notes: s.notes, url: s.url, checkedAt: s.checkedAt, skipped: s.skipped, auto: s.auto, error,
    };
  }

  async function check() {
    const s = load();
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
    try {
      const res = await fetch(RELEASES_API, {
        headers: { 'User-Agent': `OpenZenBot/${version}`, Accept: 'application/vnd.github+json' },
        signal: ctl.signal,
      });
      if (!res || !res.ok) throw new Error(`GitHub answered ${res && res.status}`);
      const body = await res.json();
      if (!body || typeof body !== 'object') throw new Error('unexpected answer');
      if (body.draft || body.prerelease) throw new Error('the latest release is not a final one');
      const latest = bare(body.tag_name);
      if (!latest) throw new Error('the latest release has no version number');
      s.latest = latest;
      s.notes = typeof body.body === 'string' ? body.body.slice(0, NOTES_MAX) : '';
      s.url = safeUrl(body.html_url);
      s.checkedAt = new Date(now()).toISOString();
      error = null;
      persist();
    } catch (e) {
      error = String((e && e.message) || e) || 'check failed';
    } finally {
      clearTimeout(timer);
    }
    return get();
  }

  function skip(v) {
    const b = bare(v);
    if (b) { load().skipped = b; persist(); }
    return get();
  }

  function setAuto(on) {
    load().auto = Boolean(on);
    persist();
    return get();
  }

  function open() {
    const url = safeUrl(load().url);
    if (!url) return false;
    openExternal(url);
    return true;
  }

  // Checks now and every everyMs while auto is on; the returned function stops it.
  function start(everyMs = DAY_MS, { setInterval: si = setInterval, clearInterval: ci = clearInterval } = {}) {
    const run = () => { if (get().auto) check().catch(() => {}); };
    run();
    const id = si(run, everyMs);
    return () => ci(id);
  }

  return { get, check, skip, setAuto, open, start };
}

module.exports = { RELEASES_API, RELEASE_PAGES, compareVersions, createUpdateChecker };

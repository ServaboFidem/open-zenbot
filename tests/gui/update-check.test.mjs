// The update check: asks GitHub Releases for the latest release and says whether it is newer. Everything
// outside the module (fetch, the store, the clock, the browser) is passed in.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const { compareVersions, createUpdateChecker, RELEASES_API, RELEASE_PAGES } =
  createRequire(import.meta.url)('../../src/electron/update-check.cjs');

const NOW = Date.parse('2026-11-16T10:00:00Z');
const release = (tag, extra = {}) => ({ ok: true, status: 200, json: async () => ({ tag_name: tag, body: 'notes', html_url: `${RELEASE_PAGES}tag/${tag}`, ...extra }) });
function setup({ version = '0.1.0', answer = release('v0.2.0'), stored = null } = {}) {
  const t = { answer, saved: stored, calls: [], opened: [] };
  const checker = createUpdateChecker({
    version,
    fetch: async (url, opts) => { t.calls.push({ url, opts }); if (t.answer instanceof Error) throw t.answer; return t.answer; },
    readStore: () => t.saved,
    writeStore: o => { t.saved = o; },
    now: () => NOW,
    openExternal: u => t.opened.push(u),
  });
  return { t, checker };
}

test('compareVersions', () => {
  assert.equal(compareVersions('0.2.0', 'v0.10.0'), -1);
  assert.equal(compareVersions('v1.0.0', '1.0.0'), 0);
  assert.equal(compareVersions('1.0.1', '1.0.0'), 1);
  for (const bad of ['nightly', '1.2', '1.0.0-beta.1', '', null, undefined, 5]) assert.equal(compareVersions(bad, '1.0.0'), null);
  assert.equal(compareVersions('1.0.0', 'nightly'), null);
});

test('a newer release is found, kept, and offered', async () => {
  const { t, checker } = setup();
  const s = await checker.check();
  assert.deepEqual([s.latest, s.newer, s.notify, s.notes, s.error], ['0.2.0', true, true, 'notes', null]);
  assert.equal(t.saved.latest, '0.2.0');
  assert.equal(s.url, `${RELEASE_PAGES}tag/v0.2.0`);
  assert.equal(s.checkedAt, new Date(NOW).toISOString());
  assert.equal(t.calls[0].url, RELEASES_API);
});

test('the same or an older release is not newer', async () => {
  for (const tag of ['v0.1.0', 'v0.0.9']) {
    const { checker } = setup({ answer: release(tag) });
    const s = await checker.check();
    assert.deepEqual([s.newer, s.notify, s.error], [false, false, null]);
  }
});

test('skipping a version silences it until a later one', async () => {
  const { t, checker } = setup();
  await checker.check();
  const s = checker.skip('0.2.0');
  assert.deepEqual([s.skipped, s.newer, s.notify], ['0.2.0', true, false]);
  assert.equal(t.saved.skipped, '0.2.0');
  t.answer = release('v0.3.0');
  const later = await checker.check();
  assert.deepEqual([later.latest, later.notify], ['0.3.0', true]);
});

test('notes are cut to 2000 characters', async () => {
  const { checker } = setup({ answer: release('v0.2.0', { body: 'x'.repeat(5000) }) });
  assert.equal((await checker.check()).notes.length, 2000);
});

test('notes are plain text, never markup the GUI could run', async () => {
  const { checker } = setup({ answer: release('v0.2.0', { body: { evil: 1 } }) });
  assert.equal((await checker.check()).notes, '');
});

test('unexpected answers never throw and never notify', async () => {
  for (const answer of [{ ok: false, status: 403 }, { ok: true, json: async () => ({ tag_name: 'nightly' }) },
                        { ok: true, json: async () => { throw new SyntaxError('<html>'); } }, new Error('offline'),
                        { ok: true, json: async () => ({ tag_name: 'v9.0.0', prerelease: true }) },
                        { ok: true, json: async () => ({ tag_name: 'v9.0.0', draft: true }) }]) {
    const { checker } = setup({ answer });
    const s = await checker.check();
    assert.equal(s.notify, false);
    assert.equal(typeof s.error, 'string');
  }
});

test('a failed check keeps the last good answer and sets error', async () => {
  const { t, checker } = setup();
  await checker.check();
  t.answer = new Error('offline');
  const s = await checker.check();
  assert.deepEqual([s.latest, s.newer, s.notify], ['0.2.0', true, true]);
  assert.equal(typeof s.error, 'string');
});

test('get() reads the store, and a broken store is ignored', () => {
  const { checker } = setup({ stored: { latest: '0.5.0', notes: 'n', url: `${RELEASE_PAGES}tag/v0.5.0`, checkedAt: 'x', skipped: null, auto: false } });
  const s = checker.get();
  assert.deepEqual([s.current, s.latest, s.newer, s.auto], ['0.1.0', '0.5.0', true, false]);
  const bad = setup({ stored: 'junk' }).checker.get();
  assert.deepEqual([bad.latest, bad.newer, bad.auto], [null, false, true]);
});

test("open() goes only to this repo's release pages", async () => {
  const { t, checker } = setup();
  assert.equal(checker.open(), false);  // nothing stored yet
  await checker.check();
  assert.equal(checker.open(), true);
  assert.deepEqual(t.opened, [`${RELEASE_PAGES}tag/v0.2.0`]);

  for (const url of ['https://evil.example/x', 'https://github.com/ServaboFidem/open-zenbot/releases', 'javascript:alert(1)']) {
    const e = setup({ answer: release('v0.2.0', { html_url: url }) });
    const s = await e.checker.check();
    assert.equal(s.url, null);
    assert.equal(e.checker.open(), false);
    assert.deepEqual(e.t.opened, []);
  }
  // a stored url from somewhere else is never opened either
  const f = setup({ stored: { latest: '0.5.0', url: 'https://evil.example/x' } });
  assert.equal(f.checker.open(), false);
  assert.deepEqual(f.t.opened, []);
});

test('setAuto saves the switch', () => {
  const { t, checker } = setup();
  assert.equal(checker.setAuto(false).auto, false);
  assert.equal(t.saved.auto, false);
});

function timers() {
  const m = { set: [], cleared: [] };
  m.setInterval = (fn, ms) => { m.set.push({ fn, ms }); return m.set.length; };
  m.clearInterval = id => m.cleared.push(id);
  return m;
}
const tick = () => new Promise(r => setImmediate(r));

test('start() checks now and on the interval, only while auto is on, and stops', async () => {
  const m = timers();
  const { t, checker } = setup();
  const stop = checker.start(1000, m);
  await tick();
  assert.equal(t.calls.length, 1);
  assert.equal(m.set[0].ms, 1000);
  m.set[0].fn(); await tick();
  assert.equal(t.calls.length, 2);
  checker.setAuto(false);
  m.set[0].fn(); await tick();
  assert.equal(t.calls.length, 2);
  stop();
  assert.deepEqual(m.cleared, [1]);
});

test('start() does not check while auto is off', async () => {
  const m = timers();
  const { t, checker } = setup({ stored: { auto: false } });
  checker.start(1000, m);
  await tick();
  assert.equal(t.calls.length, 0);
});

test('the check sends a version User-Agent and nothing else about the machine', async () => {
  const { t, checker } = setup({ version: '0.1.0' });
  await checker.check();
  const { headers, signal } = t.calls[0].opts;
  assert.deepEqual(headers, { 'User-Agent': 'OpenZenBot/0.1.0', Accept: 'application/vnd.github+json' });
  assert.ok(signal);
  assert.deepEqual(Object.keys(t.calls[0].opts).sort(), ['headers', 'signal']);
});

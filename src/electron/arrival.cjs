// The desktop notice for a new weekly digest. Each poll reads GET /music/status; when the notify switch is
// on and something is unread it reads the newest digest, and shows one notice for an unread weekly one whose
// id has not been notified before. The ids already notified live in a small store (load/save, kept in the
// app's userData folder) so a restart does not repeat a notice and a digest saved while the app was closed
// still gets one. On-demand results never notify. First install (no store yet): a newest digest older than
// 24 h is recorded without a notice, a fresh one notifies. A harness that does not answer is silent.
// get(path) -> parsed JSON (throws when the harness does not answer); show({title, body, digestId});
// load() -> array of ids or null when there is no store yet; save(ids); now() -> ms.
const KEEP = 50;

// The words for n albums; the in-app notice (src/gui/music/helpers.js arrivalText) says the same.
const arrivalText = n => (n === 1 ? "This week's album is in" : `This week's ${n} albums are in`);
const STALE_MS = 24 * 3600 * 1000;

function createArrival({ get, show, load = () => null, save = () => {}, now = Date.now }) {
  let ids;  // undefined until read
  async function poll() {
    let status;
    try { status = await get('/music/status'); } catch { return; }
    if (!status?.notify || !(Number(status.unread) > 0)) return;
    let digest;
    try { digest = (await get('/music/digests?limit=1'))?.[0]; } catch { return; }
    if (!digest || digest.kind !== 'weekly' || digest.read_at) return;
    if (ids === undefined) { try { ids = load(); } catch { ids = null; } }
    const first = !Array.isArray(ids);
    if (first) ids = [];
    if (ids.includes(digest.id)) return;
    const made = Date.parse(digest.created_at);
    const stale = first && Number.isFinite(made) && now() - made > STALE_MS;
    ids = [...ids, digest.id].slice(-KEEP);
    try { save(ids); } catch { /* an unsaved id is at worst notified again after a restart */ }
    if (stale) return;
    show({ title: 'OpenZenBot', body: arrivalText((digest.cards || []).length), digestId: digest.id });
  }
  return { poll };
}
module.exports = { createArrival, arrivalText };

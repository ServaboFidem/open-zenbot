// The routines the GUI used to keep in localStorage (inside the workspace blob) move to the harness once.
// The blob's `routines` stay until the harness answers 200; then only the ones it took (or already had) leave.
// What it could not parse stays in the blob, comes back for the page to show, and is sent again next load,
// until the user makes it again on the Routines page (the harness then counts it as existing) or dismisses it.

function readBlob(storage, key) {
  try { const raw = storage.getItem(key); const blob = raw ? JSON.parse(raw) : null; return blob && typeof blob === 'object' ? blob : null; }
  catch { return null; }
}

export function legacyRoutines(storage, key) {
  const list = readBlob(storage, key)?.routines;
  return Array.isArray(list) ? list : [];
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

function keepRoutines(storage, key, keep) {
  try {
    const { routines: _gone, ...rest } = readBlob(storage, key) || {};
    storage.setItem(key, JSON.stringify(keep.length ? { ...rest, routines: keep } : rest));
  } catch { /* storage unavailable: the harness skips a routine it already has by name and bot */ }
}

// -> { imported, existing, unparsed } after a 200, or null (nothing to send, or the harness did not take them: tried again next load)
export async function importLegacyRoutines({ api, storage, key }) {
  const routines = legacyRoutines(storage, key);
  if (!routines.length) return null;
  let result;
  try { result = await api.importRoutines(routines); } catch { return null; }
  const unparsed = result?.unparsed || [];
  keepRoutines(storage, key, routines.filter(r => unparsed.some(u => same(u, r))));
  return { imported: result?.imported || [], existing: result?.existing || [], unparsed };
}

// The user dismissed the routines that could not be moved: they leave this browser for good.
export function dropLegacyRoutines(storage, key) {
  keepRoutines(storage, key, []);
}

// The words for the Routines page after an import, or '' when there is nothing to say.
export function importNotice({ imported = [], unparsed = [] } = {}) {
  const parts = [];
  if (imported.length) {
    const n = imported.length === 1 ? 'One routine' : `${imported.length} routines`;
    parts.push(`${n} from this browser moved here, paused: turn ${imported.length === 1 ? 'it' : 'each one'} on when you want it to run.`);
    for (const r of imported) if (r.note) parts.push(`${r.name}: ${r.note}`);
  }
  if (unparsed.length) {
    const n = unparsed.length === 1 ? 'One routine' : `${unparsed.length} routines`;
    parts.push(`${n} could not be moved because the schedule was not understood: ${unparsed.map(x => `${x.name || 'Unnamed'} (${x.schedule || 'no schedule'})`).join('; ')}. Create ${unparsed.length === 1 ? 'it' : 'them'} again here, with the same name and bot, or dismiss ${unparsed.length === 1 ? 'it' : 'them'} for good.`);
  }
  return parts.join(' ');
}

// Save the workspace blob without losing routines that are still waiting to be imported.
export function persistWorkspace(storage, key, data) {
  const waiting = legacyRoutines(storage, key);
  storage.setItem(key, JSON.stringify(waiting.length ? { ...data, routines: waiting } : data));
}

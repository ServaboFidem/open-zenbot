// Which approval the dialog shows. A request the user put off ("Decide later") stays pending in the
// Approvals tab and is not shown again until the page reloads.

// Pending approvals not put off, oldest first; list order on a tie (sort is stable).
export function waiting(approvals, deferred) {
  const off = new Set(deferred || []);
  return (approvals || [])
    .filter(a => a.state === 'pending' && !off.has(a.id))
    .sort((x, y) => (x.created_at || '') < (y.created_at || '') ? -1 : (x.created_at || '') > (y.created_at || '') ? 1 : 0);
}

// A list request sent before a decision can answer after it and still say "pending". `decided` maps the
// ids decided in this page to their state; such a row takes it. The same array when nothing needed it.
export function applyDecided(approvals, decided) {
  const list = approvals || [];
  const made = decided || {};
  return list.some(a => a.state === 'pending' && made[a.id]) ? list.map(a => a.state === 'pending' && made[a.id] ? { ...a, state: made[a.id] } : a) : list;
}

// List reads can answer out of order, and an old answer would drop a deferral or show a decided approval
// as pending again. start() numbers a read as it is sent; accept(n) says whether its answer may be used:
// only when no read sent later was used first. drop() refuses every read already sent (after a decision).
export function readOrder() {
  let sent = 0, used = 0;
  return {
    start() { return ++sent; },
    accept(n) { if (n <= used) return false; used = n; return true; },
    drop() { used = sent; },
  };
}

export function nextApproval(approvals, deferred) {
  return waiting(approvals, deferred)[0] || null;
}

// The put-off ids that are still pending. The same array when nothing was dropped, so a state setter
// given it does not render again.
export function keepDeferred(deferred, approvals) {
  const pending = new Set((approvals || []).filter(a => a.state === 'pending').map(a => a.id));
  const kept = (deferred || []).filter(id => pending.has(id));
  return kept.length === (deferred || []).length ? deferred : kept;
}

export function botLabel(approval, bots) {
  return (bots || []).find(b => b.id === approval.bot_id)?.name || approval.bot_id || 'A bot';
}

export function countLine(approvals, deferred) {
  const n = waiting(approvals, deferred).length;
  return n > 1 ? `1 of ${n} waiting` : '';
}

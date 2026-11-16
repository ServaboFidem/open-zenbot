// Coding agents (Claude Code, Codex) in Settings: what each card's badge says. Pure, so it is tested without the page.

// text and tone for one agent from GET /agents. tone: success | warning | danger.
export function agentStatus(a) {
  const v = a.version || '';
  if (!a.path) return { text: 'Not found', tone: 'warning' };
  if (!a.runnable) return { text: a.reason || 'Cannot run', tone: 'warning' };
  if (!a.confinable) return { text: a.reason || `${a.name} is too old to keep inside one folder`, tone: 'danger' };
  if (a.auth === 'none') return { text: `Not signed in. Run ${a.sign_in} in a terminal`, tone: 'warning' };
  if (a.usable && a.auth === 'subscription') return { text: `${v} · signed in with your subscription`, tone: 'success' };
  if (a.usable && a.auth === 'api_key') return { text: `${v} · runs bill API credits`, tone: 'warning' };
  if (a.usable) return { text: v, tone: 'success' };
  return { text: a.reason || 'Cannot be used', tone: 'warning' };
}

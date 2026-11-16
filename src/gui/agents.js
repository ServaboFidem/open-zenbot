// Coding agents (Claude Code, Codex) in Settings: what each card's badge says. Pure, so it is tested without the page.

// text (the short badge), tone and detail (the full sentence for the line under it) for one agent from GET /agents.
// tone: success | warning | danger.
export function agentStatus(a) {
  const v = a.version || '';
  if (!a.path) return { text: 'Not found', tone: 'warning', detail: a.reason || `${a.name} was not found on this computer.` };
  if (!a.runnable) return { text: 'Not ready', tone: 'warning', detail: a.reason || `${a.name} did not run.` };
  if (!a.confinable) return { text: 'Too old', tone: 'danger', detail: a.reason || `${a.name} is too old to keep inside one folder.` };
  if (a.auth === 'none') return { text: 'Not signed in', tone: 'warning', detail: `Run ${a.sign_in} in a terminal to sign in.` };
  if (a.usable && a.auth === 'subscription') return { text: `${v} · signed in with your subscription`, tone: 'success', detail: '' };
  if (a.usable && a.auth === 'api_key') return { text: `${v} · runs bill API credits`, tone: 'warning', detail: '' };
  if (a.usable) return { text: v, tone: 'success', detail: '' };
  return { text: 'Not ready', tone: 'warning', detail: a.reason || `${a.name} cannot be used yet.` };
}

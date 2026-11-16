// Pure decisions the app makes before it calls the harness. Kept out of
// app.jsx so they can be tested without React.

// Overlapping loads of the same thing, newest wins: begin() before each load returns isLatest(), true only
// while no later load has begun, so an older answer arriving last cannot overwrite a newer one.
export function latestWins() {
  let seq = 0;
  return () => { const mine = ++seq; return () => mine === seq; };
}

// A conversation in the picker: its title (or "New conversation" before the first message) and when it was last
// used, as a time today or a short date otherwise.
export function conversationLabel(c, now = new Date()) {
  const title = conversationTitle(c);
  const date = conversationWhen(c, now);
  return date ? `${title} · ${date}` : title;
}
export const conversationTitle = c => String(c?.title || '').trim() || 'New conversation';
// When a conversation was last used: a time today, a short date otherwise, '' when unknown.
export function conversationWhen(c, now = new Date()) {
  const when = c?.updated_at || c?.created_at;
  const d = when ? new Date(when) : null;
  if (!d || isNaN(d)) return '';
  return d.toDateString() === now.toDateString()
    ? `today ${d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`
    : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
// One row of the Conversations page's recent list: the bot's name, how many messages, when.
export function recentConversationMeta(c, botName, now = new Date()) {
  const n = Number(c?.exchanges) || 0;
  return [botName || c?.owner || 'Unknown bot', `${n} ${n === 1 ? 'message' : 'messages'}`, conversationWhen(c, now)].filter(Boolean).join(' · ');
}
// One card of the Design page: which version is the newest, how many there are, when it was last worked on.
export function designMeta(d, now = new Date()) {
  const n = Number(d?.versions) || 0;
  return [`Version ${Number(d?.version) || 1}`, n > 1 ? `${n} versions` : '', conversationWhen(d, now)].filter(Boolean).join(' · ');
}

// An exchange that ended without a reply still says why, instead of an empty bubble. Same words as the
// harness's reply_text (conversations.py), without its parentheses.
export function emptyReplyNote(ex) {
  if (ex?.reply) return '';
  if (ex?.outcome === 'stopped') return 'Stopped before finishing.';
  if (ex?.outcome === 'interrupted') return 'Interrupted: the app closed before this finished.';
  return '';
}

// "Remember this" lands in shared project memory, where recall and the
// Knowledge page can see it; the session id is the source.
export function rememberPayload(session) {
  return { text: session.result, namespace: 'shared', tier: 'project', source: `session:${session.id}`, entities: [] };
}

// The onboarding card: an empty shared graph, the shipped assistant present, not dismissed.
export const ONBOARDING_DISMISSED = 'openzenbot-onboarding-dismissed';
export const ASSISTANT_ID = 'personal-assistant';
export function showOnboarding({ facts, bots, dismissed }) {
  return (facts || []).length === 0 && (bots || []).some(b => b.id === ASSISTANT_ID) && !dismissed;
}

// One-time offer of the newer interview topics to an install that did the old four-topic
// interview: it knows a name but nothing about family. Dismissal is remembered like the onboarding card's.
export const NEW_TOPICS_DISMISSED = 'openzenbot-new-topics-dismissed';
export function showNewTopicsOffer({ facts, dismissed }) {
  const has = topic => (facts || []).some(f => f.source === `onboarding:${topic}`);
  return !dismissed && has('name') && !has('family');
}

// The extraction line under an import confirmation.
export const extractionEstimate = scan => `${scan.files.toLocaleString('en-US')} files · about ${(scan.extraction_calls || 0).toLocaleString('en-US')} extraction calls, run in the background`;

// The rebuild confirmation (GET /memory/rebuild): how much will be re-learned, and what is and is not cleared.
export function rebuildSummary({ facts = 0, extraction_calls: calls = 0 } = {}) {
  if (!facts) return 'There are no shared facts to rebuild.';
  return `Re-learn ${facts.toLocaleString('en-US')} shared ${facts === 1 ? 'fact' : 'facts'} · about ${calls.toLocaleString('en-US')} extraction calls, run in the background. The shared memory is cleared first; your facts and each bot's private memory stay.`;
}

// Background extraction progress (GET /profile .extraction); null when there is nothing to say.
export const WAITING_FOR_NAME = 'Waiting for your name before learning about your people — add it in Settings';
export function extractionNote(extraction) {
  if (!extraction) return null;
  const { queued = 0, done = 0, failed = 0, waiting_for_owner: waiting } = extraction;
  const parts = [];
  if (queued > 0) parts.push(`Learning from your files in the background: ${done} done, ${queued} queued${failed ? `, ${failed} failed` : ''}`);
  if (waiting) parts.push(WAITING_FOR_NAME);
  return parts.length ? parts.join('. ') : null;
}

// Poll /profile only while extraction jobs are actually moving: queued work that is not waiting on the owner's name.
export const pollExtraction = extraction => (extraction?.queued || 0) > 0 && !extraction?.waiting_for_owner;

// The count-and-confirm sentence for a directory scan.
const SKIP_LABELS = [['hidden', 'hidden'], ['no_converter', 'no converter'], ['too_large', 'too large']];
export function fileSize(bytes) {
  if (bytes < 1_000_000) return `${Math.round(bytes / 1000)} KB`;
  if (bytes < 10_000_000) return `${(bytes / 1_000_000).toFixed(1)} MB`;
  return `${Math.round(bytes / 1_000_000)} MB`;
}
export function scanSummary(scan) {
  if (!scan.files) return `Nothing to import from ${scan.dir}.`;
  const skipped = scan.skipped || {};
  const total = SKIP_LABELS.reduce((n, [k]) => n + (skipped[k] || 0), 0);
  const why = SKIP_LABELS.filter(([k]) => skipped[k] > 0).map(([, label]) => label).join(', ');
  const skipClause = total ? ` ${total.toLocaleString('en-US')} skipped (${why}).` : '';
  return `Import ${scan.files.toLocaleString('en-US')} files (${fileSize(scan.bytes)}) from ${scan.dir}?${skipClause} One summary and one memory write per file.`;
}

// -- start-up warm-up (GET /warmup) --------------------------------------------

// Still warming: no answer yet, or the harness says pending or warming. ready, skipped and failed all open the app.
export const warming = w => !w || w.state === 'pending' || w.state === 'warming';

// 0..1 for the loading bar. Steps are coarse (starting the memory server can take 30 s), so within a step
// the bar creeps toward 90% of it on the step's typical duration, and never runs backwards across a step.
export function bootProgress(w) {
  if (!w || w.state === 'pending') return 0;
  if (w.state !== 'warming') return 1;
  const within = Math.min(0.9, Math.max(0, w.step_elapsed_s || 0) / (w.step_expected_s || 1));
  return Math.min(1, (w.done + within) / (w.total || 1));
}

// -- bot activity feed (session ledger events) ---------------------------------

const clip = (v, n) => { const s = String(v ?? '').replace(/\s+/g, ' ').trim(); return s.length > n ? `${s.slice(0, n - 1)}…` : s; };
const firstOf = (args, keys) => { for (const k of keys) { const v = args?.[k]; if (Array.isArray(v) ? v.length : v) return Array.isArray(v) ? v[0] : v; } return ''; };
const plain = v => String(v ?? '').replace(/[#*_`>]+/g, '');  // model markdown, read as a one-line preview
const argSummary = args => Object.entries(args || {}).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join(' · ');

const AGENT_NAMES = { 'claude-code': 'Claude Code', codex: 'Codex' };
const agentName = id => AGENT_NAMES[id] || 'The coding agent';

// A delegated run's ledger entries (kind "delegate": start, file, end) as rows that follow the delegate tool's row.
function delegateRow(base, d, run) {
  if (d.event === 'start') { run.name = agentName(d.agent); return { ...base, icon: 'agent', status: 'info', title: `${run.name} started`, detail: clip(d.folder, 300), meta: '' }; }
  if (d.event === 'file') return { ...base, icon: d.outside ? 'error' : 'agent', status: d.outside ? 'error' : 'info', title: clip(d.tool, 40) || 'Touched a file', detail: clip(d.path, 300), meta: d.outside ? 'This file is outside the job folder' : '' };
  if (d.event === 'end') {
    const name = run.name || 'The coding agent', n = Number.isInteger(d.changed) && d.changed > 0 ? d.changed : 0;  // the runner logs a count
    const detail = [d.seconds != null && `${Math.round(d.seconds)} s`, `${n} ${n === 1 ? 'file' : 'files'} changed`].filter(Boolean).join(' · ');
    return { ...base, icon: d.ok ? 'done' : 'error', status: d.ok ? 'ok' : 'error', title: d.ok ? `${name} finished` : `${name} stopped`, detail, meta: '' };
  }
  return { ...base, icon: 'agent', status: 'info', title: 'Coding agent', detail: clip(d.event, 200), meta: '' };
}

// What a tool call reads as in the feed. MCP tools arrive as "server.tool"; the server becomes the meta line.
export function toolLabel(name = '', args = {}) {
  const dot = name.indexOf('.');
  const meta = dot > 0 ? name.slice(0, dot) : '';
  const tool = dot > 0 ? name.slice(dot + 1) : name;
  if (tool === 'shell') return { icon: 'shell', title: 'Shell', detail: clip(args.command, 400), meta };
  if (tool === 'delegate') return { icon: 'agent', title: AGENT_NAMES[args.agent] || 'Coding agent', detail: clip(args.task, 400), meta: args.folder ? clip(args.folder, 200) : meta };
  if (tool === 'learn_page') return { icon: 'learn', title: 'Learning a page', detail: clip(firstOf(args, ['url']), 400), meta };
  if (tool === 'browser') return { icon: 'web', title: 'Browser', detail: clip([args.action, firstOf(args, ['url', 'selector', 'text'])].filter(Boolean).join(' '), 400), meta };
  if (/search/i.test(tool)) return { icon: 'search', title: 'Web search', detail: clip(firstOf(args, ['query', 'q', 'search', 'keywords', 'term']) || argSummary(args), 400), meta };
  if (/crawl|scrape|webpage|fetch|url|sitemap|youtube/i.test(tool)) return { icon: 'web', title: 'Reading a page', detail: clip(firstOf(args, ['url', 'urls', 'start_url', 'link']) || argSummary(args), 400), meta };
  return { icon: 'tool', title: tool, detail: clip(argSummary(args), 400), meta };
}

const SESSION_END = { done: 'Finished', stopped: 'Stopped', failed: 'Failed', paused: 'Paused', waiting_approval: 'Waiting for approval' };

// A session's ledger events as feed rows, oldest first. A tool_start row stays "running" until its
// tool_call lands; an approval request stays "waiting" until it is decided. Routine noise is dropped.
export function activityFeed(events = []) {
  const rows = [];
  const add = row => { rows.push(row); return row; };
  const run = {};
  for (const e of events) {
    const d = e.data || {};
    const base = { id: e.id || `${e.kind}-${rows.length}`, time: (e.ts || '').slice(11, 19), kind: e.kind };
    switch (e.kind) {
      case 'session':
        if (d.event === 'start') add({ ...base, icon: 'start', status: 'info', title: 'Task started', detail: clip(d.task, 400), meta: d.injected?.length ? `skills: ${d.injected.join(', ')}` : 'no skills injected' });
        else add({ ...base, icon: d.event === 'done' ? 'done' : 'end', status: d.event === 'done' ? 'ok' : d.event === 'failed' ? 'error' : 'info', title: SESSION_END[d.event] || clip(d.event, 40), detail: d.steps != null ? `${d.steps} ${d.steps === 1 ? 'step' : 'steps'}` : '', meta: '' });
        break;
      case 'turn': {
        const tokens = (d.prompt_tokens || 0) + (d.completion_tokens || 0);
        const meta = [d.step != null && `step ${d.step}`, tokens && `${tokens.toLocaleString('en-US')} tokens`, d.cost && `$${Number(d.cost).toFixed(4)}`].filter(Boolean).join(' · ');
        if (d.tool_calls?.length) add({ ...base, icon: 'think', status: 'info', title: 'Thinking', detail: clip(plain(d.content), 400) || `Calling ${d.tool_calls.join(', ')}`, full: d.content || '', meta });
        else add({ ...base, icon: 'reply', status: 'ok', title: 'Wrote the answer', detail: clip(plain(d.content), 200), full: d.content || '', meta });
        break;
      }
      case 'tool_start':
        add({ ...base, ...toolLabel(d.tool, d.arguments), status: 'running', tool: d.tool, arguments: d.arguments || {} });
        break;
      case 'tool_call': {
        const open = rows.findLast(r => r.kind === 'tool_start' && r.status === 'running' && r.tool === d.tool);
        const row = open || add({ ...base, kind: 'tool_start', ...toolLabel(d.tool, d.arguments), tool: d.tool, arguments: d.arguments || {} });
        Object.assign(row, { status: d.ok === false ? 'error' : 'ok', output: d.output || '', error: d.error || '', done: base.time });
        break;
      }
      case 'delegate': add(delegateRow(base, d, run)); break;
      case 'approval_request':
        add({ ...base, icon: 'approval', status: 'waiting', title: 'Waiting for approval', detail: clip(d.title || d.tool, 300), meta: d.rule ? `rule: ${d.rule}` : '', approvalId: d.approval_id });
        break;
      case 'approval_decision': {
        const row = rows.findLast(r => r.approvalId === d.approval_id);
        const ok = d.decision === 'approved';
        if (row) Object.assign(row, { status: ok ? 'ok' : 'error', title: ok ? 'Approved' : 'Rejected' });
        else add({ ...base, icon: 'approval', status: ok ? 'ok' : 'error', title: ok ? 'Approved' : 'Rejected', detail: '', meta: '' });
        break;
      }
      case 'pause': add({ ...base, icon: 'pause', status: 'waiting', title: 'Paused', detail: clip(d.reason, 300), meta: d.provider || '' }); break;
      case 'resume': add({ ...base, icon: 'start', status: 'info', title: 'Resumed', detail: clip(d.reason, 300), meta: '' }); break;
      case 'supervisor':
        if (d.ok && d.action && d.action !== 'continue') add({ ...base, icon: 'supervisor', status: 'waiting', title: d.action === 'halt' ? 'Supervisor halted' : 'Supervisor nudged', detail: d.run_state ? `Run looked ${d.run_state}` : '', meta: '' });
        break;
      case 'compaction':
        if (d.ok && !d.skipped) add({ ...base, icon: 'compact', status: 'info', title: 'Compacted context', detail: `${d.before} → ${d.after} messages`, meta: '' });
        break;
      case 'scope_change': if (d.event) add({ ...base, icon: 'supervisor', status: 'info', title: `Scope change ${d.event}`, detail: '', meta: '' }); break;
      case 'memory': if (d.event === 'remember') add({ ...base, icon: 'memory', status: 'ok', title: 'Remembered a fact', detail: '', meta: [d.namespace, d.tier].filter(Boolean).join(' · ') }); break;
      case 'learn': add({ ...base, icon: 'learn', status: d.status === 'refused' ? 'error' : 'ok', title: 'Learned from a page', detail: clip(d.url, 300), meta: d.status || '' }); break;
      case 'ingest': add({ ...base, icon: 'memory', status: d.status === 'refused' ? 'error' : 'ok', title: 'Read a file into memory', detail: clip(d.source, 300), meta: d.status || '' }); break;
      case 'error': add({ ...base, icon: 'error', status: 'error', title: 'Error', detail: clip(d.error, 400), meta: d.where || '' }); break;
      case 'end': case 'decider_score': case 'helper': break;
      default: add({ ...base, icon: 'tool', status: 'info', title: e.kind, detail: clip(d.event || d.action || d.reason || '', 200), meta: '' });
    }
  }
  return rows;
}

// One bot's direct-chat sessions: room turns run under the same bot but belong to the room.
export const directSessions = (sessions, botId) => (sessions || []).filter(s => s.bot_id === botId && !s.room_id);

// Rooms newest-activity first, archived ones split off.
export function sortRooms(rooms) {
  const byRecent = (a, b) => String(b.last_active || '').localeCompare(String(a.last_active || ''));
  const all = [...(rooms || [])].sort(byRecent);
  return { active: all.filter(r => !r.archived), archived: all.filter(r => r.archived) };
}

// What the view resets to after a room is deleted: deleting the open room goes back to the bot chat, so the
// Conversation page is never left blank. null when another room was deleted.
export const afterRoomDeleted = (openRoomId, deletedId) => openRoomId === deletedId ? { roomId: '', room: null, mode: 'bot' } : null;

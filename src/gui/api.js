// Thin client for the zen-agents harness. One function per route, JSON in
// and out. No state beyond the base URL: the provider key passes through
// setProvider once and is never kept here or in localStorage.
import { createMusicApi } from './music-api.js';

export const DEFAULT_HARNESS = 'http://127.0.0.1:8765';

export function harnessUrl(win = typeof window === 'undefined' ? {} : window) {
  const fromBridge = win?.openzenbot?.harnessUrl;
  return String(fromBridge || DEFAULT_HARNESS).replace(/\/+$/, '');
}

export class HarnessError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

export function createApi({ baseUrl = harnessUrl(), fetch: fetchImpl, EventSource: SourceImpl } = {}) {
  const doFetch = fetchImpl || ((...a) => globalThis.fetch(...a));
  const Source = SourceImpl || globalThis.EventSource;
  const base = baseUrl.replace(/\/+$/, '');
  const qs = params => {
    const clean = Object.entries(params || {}).filter(([, v]) => v !== undefined && v !== null && v !== '');
    return clean.length ? '?' + new URLSearchParams(clean.map(([k, v]) => [k, String(v)])).toString() : '';
  };
  async function call(method, path, body, params) {
    const init = { method, headers: { Accept: 'application/json' } };
    if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    let res;
    try { res = await doFetch(base + path + qs(params), init); }
    catch (e) { throw new HarnessError(0, `The harness at ${base} did not answer. Start it with "uv run python -m src.harness.main".`); }
    if (!res.ok) {
      let detail = '';
      try { const j = await res.json(); detail = typeof j?.detail === 'string' ? j.detail : JSON.stringify(j?.detail ?? j); } catch { detail = await res.text().catch(() => ''); }
      throw new HarnessError(res.status, detail || `HTTP ${res.status}`);
    }
    if (res.status === 204) return null;
    return res.json();
  }
  const get = (path, params) => call('GET', path, undefined, params);
  const post = (path, body, params) => call('POST', path, body ?? {}, params);
  const put = (path, body) => call('PUT', path, body);
  const patch = (path, body) => call('PATCH', path, body);
  const del = path => call('DELETE', path);
  const enc = encodeURIComponent;

  // One EventSource per stream; `end` closes it. readyState 2 is CLOSED: the browser gave up
  // (a 404 after a harness restart). Anything else is a reconnect in progress.
  function stream(path, onEvent) {
    const src = new Source(base + path);
    src.onmessage = m => {
      let e; try { e = JSON.parse(m.data); } catch { return; }
      onEvent(e);
      if (e.kind === 'end') src.close();
    };
    src.onerror = () => {
      if (src.readyState === 2) { onEvent({ kind: 'end', state: 'lost' }); src.close(); return; }
      onEvent({ kind: 'stream_error' });
    };
    return () => src.close();
  }

  return {
    baseUrl: base,
    call,
    music: createMusicApi(call),
    // routines (the Music digest schedule is one; a managed routine keeps managed_by on every save)
    routines: () => get('/routines'),
    saveRoutine: ({ id, name, bot, skill, enabled, schedule, managed_by = '' }) => {
      const body = { name, bot, skill, enabled, schedule, managed_by };
      return id ? put(`/routines/${enc(id)}`, body) : post('/routines', body);
    },
    deleteRoutine: id => del(`/routines/${enc(id)}`),
    runRoutine: id => post(`/routines/${enc(id)}/run`),
    importRoutines: routines => post('/routines/import', { routines }),
    routineStore: () => get('/routines/store'),
    health: () => get('/health'),
    warmup: () => get('/warmup'),
    settings: () => get('/settings'),
    setProvider: ({ kind, key, model = null, scorer = null, decision_model = null }) => post('/settings/provider', { kind, key, model, scorer, decision_model }),
    toolSettings: () => get('/settings/tools'),
    providers: () => get('/settings/providers'),
    addProvider: ({ name, kind, base_url = null, key = null, key_ref = null, no_key = false }) => post('/settings/providers', { name, kind, base_url, key, key_ref, no_key: !!no_key }),
    updateProvider: (id, changes) => patch(`/settings/providers/${enc(id)}`, changes),
    deleteProvider: id => del(`/settings/providers/${enc(id)}`),
    providerModels: (id, refresh = false) => get(`/settings/providers/${enc(id)}/models`, { refresh: refresh ? 'true' : undefined }),
    setProviderModels: (id, enabled) => put(`/settings/providers/${enc(id)}/models`, { enabled }),
    startProbe: (id, model) => post(`/settings/providers/${enc(id)}/models/probe`, { model }),
    probeEvents: (id, probeId, onEvent) => stream(`/settings/providers/${enc(id)}/models/probe/${enc(probeId)}/events`, onEvent),
    stopProbe: (id, probeId) => post(`/settings/providers/${enc(id)}/models/probe/${enc(probeId)}/stop`),
    modelRoles: () => get('/settings/models'),
    setModelRoles: roles => put('/settings/models', roles),
    setTool: (id, patch) => put(`/settings/tools/${enc(id)}`, patch),
    programPath: key => get(`/settings/tools/programs/${enc(key)}`),
    setProgramPath: (key, path) => put(`/settings/tools/programs/${enc(key)}`, { path }),
    clearProgramPath: key => del(`/settings/tools/programs/${enc(key)}`),
    agents: () => get('/agents'),
    setAgent: (key, body) => put(`/settings/agents/${enc(key)}`, body),
    allowedFolders: () => get('/settings/folders'),
    addAllowedFolder: (path, access = 'full') => post('/settings/folders', { path, access }),
    setFolderAccess: (path, access) => patch('/settings/folders', { path, access }),
    removeAllowedFolder: path => call('DELETE', '/settings/folders', undefined, { path }),
    tracingSettings: () => get('/settings/tracing'),
    setSentrySettings: dsn => put('/settings/tracing/sentry', { dsn }),
    clearSentrySettings: () => del('/settings/tracing/sentry'),
    setLangfuseSettings: body => put('/settings/tracing/langfuse', body),
    clearLangfuseSettings: () => del('/settings/tracing/langfuse'),
    profile: () => get('/profile'),
    setOwner: (name, aliases = []) => put('/profile/owner', { name, aliases }),
    // bots and tasks
    bots: () => get('/bots'),
    bot: id => get(`/bots/${enc(id)}`),
    createBot: body => post('/bots', body),
    patchBot: (id, body) => call('PATCH', `/bots/${enc(id)}`, body),
    // A task joins the bot's current conversation (or the one named); a routine passes conversation: false.
    startTask: (botId, task, { conversationId, conversation = true, protected: block } = {}) =>
      post(`/bots/${enc(botId)}/tasks`, { task, ...(conversationId ? { conversation_id: conversationId } : {}), conversation, ...(block ? { protected: block } : {}) }),
    // conversations
    conversations: botId => get(`/bots/${enc(botId)}/conversations`),
    recentConversations: (limit = 5) => get(`/conversations?limit=${enc(limit)}`),  // newest one-to-one, across bots
    conversation: id => get(`/conversations/${enc(id)}`),
    newConversation: botId => post(`/bots/${enc(botId)}/conversations`),
    selectConversation: (botId, id) => post(`/bots/${enc(botId)}/conversations/${enc(id)}/current`),
    conversationAttachmentUrl: (convId, attId) => `${base}/conversations/${enc(convId)}/attachments/${enc(attId)}`,
    sessions: () => get('/sessions'),
    session: id => get(`/sessions/${enc(id)}`),
    stopSession: id => post(`/sessions/${enc(id)}/stop`),
    resumeSession: id => post(`/sessions/${enc(id)}/resume`),
    retrySession: id => post(`/sessions/${enc(id)}/retry`),
    events: (sessionId, onEvent) => stream(`/sessions/${enc(sessionId)}/events`, onEvent),
    // onboarding
    onboardingStart: topics => post('/onboarding/start', topics ? { topics } : undefined),
    onboardingReply: (id, text) => post(`/onboarding/${enc(id)}/reply`, { text }),
    onboarding: id => get(`/onboarding/${enc(id)}`),
    // directory import
    scanDir: dir => post('/vault/scan', { dir }),
    importDir: token => post('/vault/import', { token }),
    importFile: path => post('/vault/import-file', { path }),
    importStatus: id => get(`/vault/import/${enc(id)}`),
    stopImport: id => post(`/vault/import/${enc(id)}/stop`),
    importEvents: (id, onEvent) => stream(`/vault/import/${enc(id)}/events`, onEvent),
    // scope
    scopeCheck: (sessionId, text) => post(`/sessions/${enc(sessionId)}/scope`, { text }),
    scopeConfirm: (sessionId, { task = null, constraints = null } = {}) => post(`/sessions/${enc(sessionId)}/scope/confirm`, { task, constraints }),
    scopeReject: sessionId => post(`/sessions/${enc(sessionId)}/scope/reject`),
    // approvals
    approvals: state => get('/approvals', { state }),
    decide: (id, approved) => post(`/approvals/${enc(id)}/decide`, { approved }),
    // skills and fit
    skills: () => get('/skills'),
    mcps: () => get('/mcps'),
    setMcpKeys: (name, body) => put(`/mcps/${encodeURIComponent(name)}/keys`, body),
    recheckMcp: name => post(`/mcps/${encodeURIComponent(name)}/recheck`),
    clearMcpKey: (name, key) => del(`/mcps/${encodeURIComponent(name)}/keys/${encodeURIComponent(key)}`),
    suggestFit: slug => post(`/skills/${enc(slug)}/fit`),
    fits: state => get('/fits', { state }),
    confirmFit: (fitId, botId) => post(`/fits/${enc(fitId)}/confirm`, { bot_id: botId }),
    denyFit: fitId => post(`/fits/${enc(fitId)}/deny`),
    // rooms
    rooms: () => get('/rooms'),
    createRoom: (name, members) => post('/rooms', { name, members }),
    setRoomMembers: (roomId, members) => put(`/rooms/${enc(roomId)}/members`, { members }),
    postToRoom: (roomId, text, { reply_to = null, author = 'user' } = {}) => post(`/rooms/${enc(roomId)}/posts`, { text, reply_to, author }),
    roomTurn: roomId => get(`/rooms/${enc(roomId)}/turn`),
    orchestrate: (text, { newTopic = false } = {}) => post('/orchestrate', { text, new_topic: newTopic }),
    archiveRoom: (roomId, archived) => put(`/rooms/${enc(roomId)}/archived`, { archived }),
    deleteRoom: roomId => del(`/rooms/${enc(roomId)}`),
    newRoomConversation: roomId => post(`/rooms/${enc(roomId)}/conversations`),
    selectRoomConversation: (roomId, id) => post(`/rooms/${enc(roomId)}/conversations/${enc(id)}/current`),
    attachmentUrl: (roomId, attId) => `${base}/rooms/${enc(roomId)}/attachments/${enc(attId)}`,
    // recorder
    recorderStatus: () => get('/recorder/status'),
    startRecording: name => post('/recorder/start', { name }),
    recordAction: body => post('/recorder/actions', body),
    stopRecording: () => post('/recorder/stop'),
    draftSkill: (name, use) => post('/recorder/draft', { name, use }),
    // memory
    memory: namespace => get('/memory', { namespace }),
    recall: (botId, task, sessionId) => get('/memory/recall', { bot: botId, q: task, session_id: sessionId }),
    remember: fact => post('/memory', fact),
    forget: id => del(`/memory/${enc(id)}`),
    promote: (id, body) => post(`/memory/${enc(id)}/promote`, body),
    confirmPromotion: id => post(`/memory/${enc(id)}/promote/confirm`),
    denyPromotion: id => post(`/memory/${enc(id)}/promote/deny`),
    knowledgeUi: () => get('/knowledge/ui'),
    rebuildPreview: () => get('/memory/rebuild'),
    rebuild: () => post('/memory/rebuild'),
    cacheFacts: sessionId => get('/memory/cache', { session_id: sessionId }),
    addCacheFact: (sessionId, text, entities = []) => post('/memory/cache', { text, entities }, { session_id: sessionId }),
    // vault
    upload: (name, text) => post('/vault/upload', { name, text }),
    ingest: path => post('/vault/ingest', { path }),
    vaultSync: () => get('/vault/sync'),
    rescan: () => post('/vault/rescan'),
    // ledger
    ledger: params => get('/ledger', params),
  };
}

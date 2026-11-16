// The thin client: one function per harness route, JSON in and out,
// errors carry the harness detail, the key is sent once and never kept.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createApi, harnessUrl } from '../../src/gui/api.js';

function fakeFetch(responses = {}) {
  const calls = [];
  const fetch = async (url, init = {}) => {
    calls.push({ url, method: init.method || 'GET', body: init.body ? JSON.parse(init.body) : undefined, headers: init.headers || {} });
    const key = `${init.method || 'GET'} ${new URL(url).pathname}`;
    const r = responses[key] || { status: 200, body: {} };
    return { ok: r.status < 400, status: r.status, json: async () => r.body, text: async () => JSON.stringify(r.body) };
  };
  return { fetch, calls };
}

test('allowed folder routes list, add by body and remove by query', async () => {
  const { fetch, calls } = fakeFetch();
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
  await api.allowedFolders(); await api.addAllowedFolder('D:\\Projects\\site'); await api.removeAllowedFolder('D:\\Projects\\site');
  await api.addAllowedFolder('D:\\Ref', 'read_only'); await api.setFolderAccess('D:\\Ref', 'full');
  assert.deepEqual(calls.map(c => `${c.method} ${new URL(c.url).pathname}`), ['GET /settings/folders', 'POST /settings/folders', 'DELETE /settings/folders', 'POST /settings/folders', 'PATCH /settings/folders']);
  assert.deepEqual(calls[1].body, { path: 'D:\\Projects\\site', access: 'full' });
  assert.equal(new URL(calls[2].url).searchParams.get('path'), 'D:\\Projects\\site');
  assert.deepEqual(calls[3].body, { path: 'D:\\Ref', access: 'read_only' });
  assert.deepEqual(calls[4].body, { path: 'D:\\Ref', access: 'full' });
});

test('MCP key routes put the keys on the server and clear one by name', async () => {
  const { fetch, calls } = fakeFetch();
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
  await api.setMcpKeys('pexels', { keys: { PEXELS_API_KEY: 'pk' } }); await api.clearMcpKey('pexels', 'PEXELS_API_KEY');
  assert.deepEqual(calls.map(c => `${c.method} ${new URL(c.url).pathname}`), ['PUT /mcps/pexels/keys', 'DELETE /mcps/pexels/keys/PEXELS_API_KEY']);
  assert.deepEqual(calls[0].body, { keys: { PEXELS_API_KEY: 'pk' } });
});

test('recheckMcp posts to the server\'s recheck route', async () => {
  const { fetch, calls } = fakeFetch();
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
  await api.recheckMcp('a b');
  assert.deepEqual(calls.map(c => `${c.method} ${new URL(c.url).pathname}`), ['POST /mcps/a%20b/recheck']);
});

test('harnessUrl prefers the preload bridge, then the default loopback port', () => {
  assert.equal(harnessUrl({}), 'http://127.0.0.1:8765');
  assert.equal(harnessUrl({ openzenbot: { harnessUrl: 'http://127.0.0.1:9999/' } }), 'http://127.0.0.1:9999');
});

test('routes map to the harness paths and bodies', async () => {
  const { fetch, calls } = fakeFetch({ 'POST /bots/b1/tasks': { status: 202, body: { session_id: 's1', state: 'running' } } });
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
  assert.deepEqual(await api.startTask('b1', 'do it'), { session_id: 's1', state: 'running' });
  await api.bots(); await api.createBot({ name: 'n', role: 'r', instructions: 'i', skills: [] });
  await api.session('s1'); await api.stopSession('s1'); await api.resumeSession('s1'); await api.retrySession('s1');
  await api.approvals('pending'); await api.decide('a1', true);
  await api.skills(); await api.mcps();
  await api.onboardingStart(); await api.onboardingReply('i1', 'hi'); await api.onboarding('i1');
  await api.scanDir('F:/notes'); await api.importDir('t1'); await api.importStatus('j1'); await api.stopImport('j1');
  await api.suggestFit('brief'); await api.fits(); await api.confirmFit('f1', 'b1'); await api.denyFit('f1');
  await api.rooms(); await api.createRoom('r', ['b1', 'b2']); await api.postToRoom('r1', 'hi'); await api.roomTurn('r1');
  await api.orchestrate('hi', { newTopic: true }); await api.archiveRoom('r1', true); await api.deleteRoom('r1');
  await api.recorderStatus(); await api.startRecording('login'); await api.stopRecording(); await api.draftSkill('login', 'log in');
  await api.memory(); await api.recall('b1', 'q'); await api.remember({ text: 't' }); await api.forget('m1'); await api.promote('m1');
  await api.cacheFacts('s1'); await api.addCacheFact('s1', 'fact');
  await api.scopeCheck('s1', 'new goal'); await api.scopeConfirm('s1', { task: 'x' }); await api.scopeReject('s1');
  await api.ledger({ n: 20, kind: 'session' }); await api.health(); await api.settings();
  await api.profile(); await api.knowledgeUi(); await api.setOwner('Ada', ['Ad']); await api.onboardingStart('new');
  await api.rebuildPreview(); await api.rebuild();
  const seen = calls.map(c => `${c.method} ${new URL(c.url).pathname}${new URL(c.url).search}`);
  assert.deepEqual(seen, [
    'POST /bots/b1/tasks', 'GET /bots', 'POST /bots',
    'GET /sessions/s1', 'POST /sessions/s1/stop', 'POST /sessions/s1/resume', 'POST /sessions/s1/retry',
    'GET /approvals?state=pending', 'POST /approvals/a1/decide',
    'GET /skills', 'GET /mcps',
    'POST /onboarding/start', 'POST /onboarding/i1/reply', 'GET /onboarding/i1',
    'POST /vault/scan', 'POST /vault/import', 'GET /vault/import/j1', 'POST /vault/import/j1/stop',
    'POST /skills/brief/fit', 'GET /fits', 'POST /fits/f1/confirm', 'POST /fits/f1/deny',
    'GET /rooms', 'POST /rooms', 'POST /rooms/r1/posts', 'GET /rooms/r1/turn',
    'POST /orchestrate', 'PUT /rooms/r1/archived', 'DELETE /rooms/r1',
    'GET /recorder/status', 'POST /recorder/start', 'POST /recorder/stop', 'POST /recorder/draft',
    'GET /memory', 'GET /memory/recall?bot=b1&q=q', 'POST /memory', 'DELETE /memory/m1', 'POST /memory/m1/promote',
    'GET /memory/cache?session_id=s1', 'POST /memory/cache?session_id=s1',
    'POST /sessions/s1/scope', 'POST /sessions/s1/scope/confirm', 'POST /sessions/s1/scope/reject',
    'GET /ledger?n=20&kind=session', 'GET /health', 'GET /settings',
    'GET /profile', 'GET /knowledge/ui', 'PUT /profile/owner', 'POST /onboarding/start',
    'GET /memory/rebuild', 'POST /memory/rebuild',
  ]);
  assert.deepEqual(calls[0].body, { task: 'do it', conversation: true });
  assert.deepEqual(calls.find(c => c.url.endsWith('/orchestrate')).body, { text: 'hi', new_topic: true });
  assert.equal(api.attachmentUrl('r 1', 'a1'), 'http://127.0.0.1:8765/rooms/r%201/attachments/a1');
  assert.deepEqual(calls[8].body, { approved: true });
  assert.deepEqual(calls[12].body, { text: 'hi' });
  assert.deepEqual(calls[14].body, { dir: 'F:/notes' });
  assert.deepEqual(calls[15].body, { token: 't1' });
  assert.deepEqual(calls[20].body, { bot_id: 'b1' });
  assert.deepEqual(calls[23].body, { name: 'r', members: ['b1', 'b2'] });
  assert.deepEqual(calls[24].body, { text: 'hi', reply_to: null, author: 'user' });
  assert.deepEqual(calls[32].body, { name: 'login', use: 'log in' });
});

test('startTask sends the conversation fields', async () => {
  const { fetch, calls } = fakeFetch();
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
  await api.startTask('b', 'go on', { conversationId: 'c1' });
  await api.startTask('b', 'daily digest', { conversation: false });
  const block = { task: 'Research uv', constraints: ['no tools', 'refine: shorter'] };
  await api.startTask('b', 'shorter', { conversationId: 'c1', protected: block });
  assert.deepEqual(calls[0].body, { task: 'go on', conversation_id: 'c1', conversation: true });
  assert.deepEqual(calls[1].body, { task: 'daily digest', conversation: false });
  assert.deepEqual(calls[2].body, { task: 'shorter', conversation_id: 'c1', conversation: true, protected: block });
  assert.ok(calls.every(c => new URL(c.url).pathname === '/bots/b/tasks' && c.method === 'POST'));
});

test('startTask sends design comments only when there are some', async () => {
  const { fetch, calls } = fakeFetch();
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
  const comments = [{ attachment_id: 'a2', element_id: 'hero', selector: 'body > h1', tag: 'h1', text: 'Hi', note: 'bigger' }];
  await api.startTask('b', 'See my comments on the design.', { conversationId: 'c1', designComments: comments });
  await api.startTask('b', 'go on', { conversationId: 'c1', designComments: [] });
  await api.startTask('b', 'go on', { conversationId: 'c1' });
  assert.deepEqual(calls[0].body, { task: 'See my comments on the design.', conversation_id: 'c1', conversation: true, design_comments: comments });
  assert.equal('design_comments' in calls[1].body, false);
  assert.equal('design_comments' in calls[2].body, false);
});

test('conversation endpoints hit the right paths', async () => {
  const { fetch, calls } = fakeFetch();
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
  await api.conversations('b'); await api.newConversation('b'); await api.selectConversation('b', 'c');
  await api.conversation('c'); await api.newRoomConversation('r'); await api.selectRoomConversation('r', 'c');
  assert.deepEqual(calls.map(c => `${c.method} ${new URL(c.url).pathname}`), [
    'GET /bots/b/conversations', 'POST /bots/b/conversations', 'POST /bots/b/conversations/c/current',
    'GET /conversations/c', 'POST /rooms/r/conversations', 'POST /rooms/r/conversations/c/current',
  ]);
});

test('setProvider sends the key once and the client keeps nothing', async () => {
  const { fetch, calls } = fakeFetch({ 'POST /settings/provider': { status: 200, body: { provider: 'openrouter', key_set: true } } });
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
  const out = await api.setProvider({ kind: 'openrouter', key: 'sk-or-secret', model: 'm' });
  assert.equal(out.key_set, true);
  assert.deepEqual(calls[0].body, { kind: 'openrouter', key: 'sk-or-secret', model: 'm', scorer: null, decision_model: null });
  assert.equal(JSON.stringify(api).includes('sk-or-secret'), false);
  assert.equal(Object.values(api).some(v => typeof v === 'string' && v.includes('secret')), false);
});

test('errors carry the harness detail and status', async () => {
  const { fetch } = fakeFetch({ 'POST /bots/b1/tasks': { status: 503, body: { detail: 'no provider bound; enter a provider and key in Settings' } } });
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
  await assert.rejects(api.startTask('b1', 'x'), e => e.status === 503 && /Settings/.test(e.message));
});

test('events opens one EventSource per session and closes it on end', async () => {
  const sources = [];
  class FakeSource { constructor(url) { this.url = url; this.closed = false; sources.push(this); } close() { this.closed = true; } }
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch: fakeFetch().fetch, EventSource: FakeSource });
  const got = [];
  const stop = api.events('s1', e => got.push(e));
  assert.equal(sources[0].url, 'http://127.0.0.1:8765/sessions/s1/events');
  sources[0].onmessage({ data: JSON.stringify({ kind: 'turn' }) });
  sources[0].onmessage({ data: JSON.stringify({ kind: 'end', state: 'done' }) });
  assert.deepEqual(got.map(e => e.kind), ['turn', 'end']);
  assert.equal(sources[0].closed, true);
  stop();
});

test('events ends the live view when the stream closes for good', async () => {
  const sources = [];
  class FakeSource { constructor(url) { this.url = url; this.readyState = 1; this.closed = false; sources.push(this); } close() { this.closed = true; this.readyState = 2; } }
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch: fakeFetch().fetch, EventSource: FakeSource });
  const got = [];
  api.events('s1', e => got.push(e));
  sources[0].readyState = 0; sources[0].onerror({});           // reconnecting: not the end
  sources[0].readyState = 2; sources[0].onerror({});           // closed for good (404 after a harness restart)
  assert.deepEqual(got.map(e => e.kind), ['stream_error', 'end']);
  assert.equal(got[1].state, 'lost');
  assert.equal(sources[0].closed, true);
});

test('importEvents opens the import stream and closes it on end', () => {
  const sources = [];
  class FakeSource { constructor(url) { this.url = url; this.closed = false; sources.push(this); } close() { this.closed = true; } }
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch: fakeFetch().fetch, EventSource: FakeSource });
  const got = [];
  api.importEvents('j1', e => got.push(e));
  assert.equal(sources[0].url, 'http://127.0.0.1:8765/vault/import/j1/events');
  sources[0].onmessage({ data: JSON.stringify({ kind: 'file', status: 'new' }) });
  sources[0].onmessage({ data: JSON.stringify({ kind: 'end', imported: 1 }) });
  assert.deepEqual(got.map(e => e.kind), ['file', 'end']);
  assert.equal(sources[0].closed, true);
});

test('conversation attachment urls are built like room ones', () => {
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765/', fetch: async () => {} });
  assert.equal(api.conversationAttachmentUrl('c 1', 'a/1'), 'http://127.0.0.1:8765/conversations/c%201/attachments/a%2F1');
});

test('tool settings read and change one harness tool', async () => {
  const { fetch, calls } = fakeFetch({ 'GET /settings/tools': { status: 200, body: { tools: [{ id: 'image', enabled: true }] } } });
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
  assert.deepEqual(await api.toolSettings(), { tools: [{ id: 'image', enabled: true }] });
  await api.setTool('image', { enabled: false });
  assert.deepEqual(calls.map(c => `${c.method} ${new URL(c.url).pathname}`), ['GET /settings/tools', 'PUT /settings/tools/image']);
  assert.deepEqual(calls[1].body, { enabled: false });
});

test('host program path routes read, save by body and clear one program', async () => {
  const { fetch, calls } = fakeFetch();
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
  await api.programPath('imagemagick');
  await api.setProgramPath('imagemagick', 'C:\\Program Files\\ImageMagick-7.1.2-Q16-HDRI\\magick.exe');
  await api.clearProgramPath('imagemagick');
  assert.deepEqual(calls.map(c => `${c.method} ${new URL(c.url).pathname}`), ['GET /settings/tools/programs/imagemagick',
    'PUT /settings/tools/programs/imagemagick', 'DELETE /settings/tools/programs/imagemagick']);
  assert.deepEqual(calls[1].body, { path: 'C:\\Program Files\\ImageMagick-7.1.2-Q16-HDRI\\magick.exe' });
});

test('model provider routes map to the harness paths', async () => {
  const { fetch, calls } = fakeFetch();
  const api = createApi({ baseUrl: 'http://127.0.0.1:8765', fetch });
  await api.providers(); await api.addProvider({ name: 'Groq', kind: 'openai-compatible', base_url: 'https://x/v1', key: 'k' });
  await api.updateProvider('groq', { key_ref: 'env:G' }); await api.deleteProvider('groq');
  await api.providerModels('openrouter'); await api.providerModels('openrouter', true); await api.setProviderModels('openrouter', ['a/b']);
  await api.modelRoles(); await api.setModelRoles({ chat: { provider: 'openrouter', model: 'a/b' } });
  assert.deepEqual(calls.map(c => `${c.method} ${new URL(c.url).pathname}${new URL(c.url).search}`), [
    'GET /settings/providers', 'POST /settings/providers', 'PATCH /settings/providers/groq', 'DELETE /settings/providers/groq',
    'GET /settings/providers/openrouter/models', 'GET /settings/providers/openrouter/models?refresh=true', 'PUT /settings/providers/openrouter/models',
    'GET /settings/models', 'PUT /settings/models']);
  assert.deepEqual(calls[1].body, { name: 'Groq', kind: 'openai-compatible', base_url: 'https://x/v1', key: 'k', key_ref: null, no_key: false });
  assert.deepEqual(calls[6].body, { enabled: ['a/b'] });
  calls.length = 0;  // a local server: the add form's "needs no API key" reaches the harness
  await api.addProvider({ name: 'LM Studio', kind: 'openai-compatible', base_url: 'http://localhost:1234/v1', no_key: true });
  assert.equal(calls[0].body.no_key, true);
});

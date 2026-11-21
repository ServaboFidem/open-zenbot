// The AgentMail client: one function per /agentmail route.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createMailApi } from '../../src/gui/mail-api.js';

const rec = () => {
  const seen = [];
  const api = createMailApi(async (method, path, body, params) => { seen.push({ method, path, body, params }); return {}; });
  return { api, seen };
};

test('each function hits its route with the right method, path, body and params', async () => {
  const { api, seen } = rec();
  await api.status();
  await api.saveKey('am_secret_1234');
  await api.forgetKey();
  await api.inboxes();
  await api.createInbox('zen');
  await api.chooseInbox('zen@agentmail.to');
  await api.messages();
  await api.messages('t2');
  await api.message('m1');
  assert.deepEqual(seen.map(s => [s.method, s.path, s.body, s.params]), [
    ['GET', '/agentmail/status', undefined, undefined],
    ['PUT', '/agentmail/key', { key: 'am_secret_1234' }, undefined],
    ['DELETE', '/agentmail/key', undefined, undefined],
    ['GET', '/agentmail/inboxes', undefined, undefined],
    ['POST', '/agentmail/inboxes', { username: 'zen' }, undefined],
    ['PUT', '/agentmail/inbox', { inbox_id: 'zen@agentmail.to' }, undefined],
    ['GET', '/agentmail/messages', undefined, undefined],
    ['GET', '/agentmail/messages', undefined, { page_token: 't2' }],
    ['GET', '/agentmail/messages/m1', undefined, undefined],
  ]);
});

test('message ids are quoted and the key never reaches a path or params', async () => {
  const { api, seen } = rec();
  await api.message('a/b c?');
  await api.saveKey('am_secret_1234');
  assert.equal(seen[0].path, '/agentmail/messages/a%2Fb%20c%3F');
  for (const s of seen) {
    assert.ok(!s.path.includes('am_secret_1234'));
    assert.ok(!JSON.stringify(s.params ?? {}).includes('am_secret_1234'));
  }
});

test('every POST carries a JSON body', async () => {
  const { api, seen } = rec();
  await api.createInbox('zen');
  for (const s of seen.filter(s => s.method === 'POST')) assert.equal(typeof s.body, 'object', s.path);
});

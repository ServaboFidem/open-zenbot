// The /agentmail routes, one function per route. Built on createApi's `call(method, path, body, params)`.
// The key goes in a request body only, never a path or query; the harness never sends it back.
const enc = encodeURIComponent;

export function createMailApi(call) {
  return {
    status: () => call('GET', '/agentmail/status', undefined, undefined),
    saveKey: key => call('PUT', '/agentmail/key', { key }),
    forgetKey: () => call('DELETE', '/agentmail/key'),
    inboxes: () => call('GET', '/agentmail/inboxes'),
    createInbox: username => call('POST', '/agentmail/inboxes', { username }),
    chooseInbox: inboxId => call('PUT', '/agentmail/inbox', { inbox_id: inboxId }),
    // pageToken: the next_page_token the last page carried; omit for the newest page
    messages: pageToken => call('GET', '/agentmail/messages', undefined, pageToken ? { page_token: pageToken } : undefined),
    message: id => call('GET', `/agentmail/messages/${enc(id)}`),
  };
}

// The /telnyx routes, one function per route. Built on createApi's `call(method, path, body, params)`.
// The key goes in a request body only, never a path or query; the harness never sends it back. No route takes a
// destination or a message: the phone number is saved by the desktop app (window.openzenbot.telnyxTexts).
export function createTelnyxApi(call) {
  return {
    status: () => call('GET', '/telnyx/status'),
    saveKey: key => call('PUT', '/telnyx/key', { key }),
    forgetKey: () => call('DELETE', '/telnyx/key'),
    numbers: () => call('GET', '/telnyx/numbers'),
    chooseNumber: id => call('PUT', '/telnyx/number', { id }),
    test: () => call('POST', '/telnyx/test', {}),
  };
}

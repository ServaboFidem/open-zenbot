// The /speakers routes, one function per route. Built on createApi's `call(method, path, body, params)`.

export function createSpeakersApi(call) {
  return {
    status: () => call('GET', '/speakers/status', undefined, undefined),
    // only the switches that change, e.g. { dlna: true }
    saveSwitches: changes => call('PUT', '/speakers/settings', changes, undefined),
    // a speaker's key from the lists, or null for no default
    setDefault: key => call('PUT', '/speakers/default', { key }, undefined),
    // the body is required (a cross-site page cannot send one without a preflight)
    lookAgain: () => call('POST', '/speakers/look', {}, undefined),
  };
}

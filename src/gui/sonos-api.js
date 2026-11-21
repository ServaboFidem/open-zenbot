// The /sonos routes, one function per route. Built on createApi's `call(method, path, body, params)`.

export function createSonosApi(call) {
  return {
    status: () => call('GET', '/sonos/status', undefined, undefined),
    saveSettings: (enabled, maxVolume) => call('PUT', '/sonos/settings', { enabled, max_volume: maxVolume }, undefined),
    // a look with the slower subnet scan; the body is required (a cross-site page cannot send one without a preflight)
    lookAgain: () => call('POST', '/sonos/discover', {}, undefined),
  };
}

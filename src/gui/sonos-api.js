// The /sonos routes, one function per route. Built on createApi's `call(method, path, body, params)`.

export function createSonosApi(call) {
  return {
    status: () => call('GET', '/sonos/status', undefined, undefined),
    saveSettings: (enabled, maxVolume) => call('PUT', '/sonos/settings', { enabled, max_volume: maxVolume }, undefined),
    // a look with the slower subnet scan; the body is required (a cross-site page cannot send one without a preflight)
    lookAgain: () => call('POST', '/sonos/discover', {}, undefined),
    // the Sonos page: each room with what its group plays and its volume, the Favorites, and the controls
    rooms: () => call('GET', '/sonos/rooms', undefined, undefined),
    favorites: () => call('GET', '/sonos/favorites', undefined, undefined),
    play: (rooms, name) => call('POST', '/sonos/play', { rooms, name }, undefined),
    control: (room, action) => call('POST', '/sonos/control', { room, action }, undefined),
    volume: (room, level) => call('POST', '/sonos/volume', { room, level }, undefined),
  };
}

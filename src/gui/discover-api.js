// The /discover routes, one function per route. Built on createApi's `call(method, path, body, params)`.
// Every POST carries a JSON body, an empty one where the route needs nothing: the harness refuses a POST without.
const enc = encodeURIComponent;
// A picture's address for <img>: base is the harness origin, item.image the route the harness gave; '' for none.
const pictureSrc = (base, item) => (item && item.image ? `${String(base || '').replace(/\/+$/, '')}${item.image}` : '');

export function createDiscoverApi(call) {
  return {
    status: () => call('GET', '/discover/status', undefined, undefined),
    saveSettings: settings => call('PUT', '/discover/settings', settings),
    // the key goes in the body only, never the address
    saveFindLocalKey: key => call('PUT', '/discover/findlocal/key', { key }),
    forgetFindLocalKey: () => call('DELETE', '/discover/findlocal/key'),
    // before: a digest id or time to page back from (the harness lists newest first); omit for the newest
    digests: before => call('GET', '/discover/digests', undefined, before ? { before } : undefined),
    digest: id => call('GET', `/discover/digests/${enc(id)}`),
    run: () => call('POST', '/discover/run', {}),
    dismiss: id => call('POST', `/discover/cards/${enc(id)}/dismiss`, {}),
    // times: { start, end } from addTimes(card)
    add: (id, times) => call('POST', `/discover/cards/${enc(id)}/add`, { start: times.start, end: times.end }),
    // the card's picture as an address for <img>: base is the harness origin; '' when the card has none
    imageSrc: pictureSrc,
    // the pool of every event read (All Events tab); times: { start, end } from addTimes(event)
    events: () => call('GET', '/discover/events'),
    event: id => call('GET', `/discover/events/${enc(id)}`),
    addEvent: (id, times) => call('POST', `/discover/events/${enc(id)}/add`, { start: times.start, end: times.end }),
    refresh: () => call('POST', '/discover/refresh', {}),
    eventImageSrc: pictureSrc,
  };
}

// The /calendar routes, one function per route. Built on createApi's `call(method, path, body, params)`.
// The Google client secret passes through saveCredentials once; the harness never sends it back.
const enc = encodeURIComponent;

export function createCalendarApi(call) {
  return {
    status: () => call('GET', '/calendar/status', undefined, undefined),
    // start and end are day keys (YYYY-MM-DD), end exclusive; the harness takes nothing else
    events: (start, end) => call('GET', '/calendar/events', undefined, { start, end }),
    create: body => call('POST', '/calendar/events', body),
    update: (id, body) => call('PUT', `/calendar/events/${enc(id)}`, body),
    remove: id => call('DELETE', `/calendar/events/${enc(id)}`),
    calendars: () => call('GET', '/calendar/calendars'),
    // patch: any of visible, selected
    setCalendar: (id, patch) => call('PUT', `/calendar/calendars/${enc(id)}`, patch),
    saveCredentials: (clientId, clientSecret) => call('PUT', '/calendar/google/credentials', { client_id: clientId, client_secret: clientSecret }),
    clearCredentials: () => call('DELETE', '/calendar/google/credentials'),
    connect: write => call('POST', '/calendar/google/connect', { write: !!write }),
    disconnect: () => call('POST', '/calendar/google/disconnect', {}),
    pushPreview: destination => call('GET', '/calendar/push/preview', undefined, { destination }),
    setPush: (on, destination) => call('PUT', '/calendar/push', { on, destination }),
    sync: () => call('POST', '/calendar/sync', {}),
  };
}

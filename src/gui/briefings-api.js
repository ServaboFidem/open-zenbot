// The /briefings routes, one function per route. Built on createApi's `call(method, path, body, params)`.

export function createBriefingsApi(call) {
  return {
    list: () => call('GET', '/briefings', undefined, undefined),
    save: (kind, body) => call('PUT', `/briefings/${encodeURIComponent(kind)}`, body, undefined),
    remove: kind => call('DELETE', `/briefings/${encodeURIComponent(kind)}`, undefined, undefined),
    // the body is required (a cross-site page cannot send one without a preflight)
    play: kind => call('POST', `/briefings/${encodeURIComponent(kind)}/play`, {}, undefined),
  };
}

// The /music routes, one function per route. Built on createApi's `call(method, path, body, params)`.
// The Discogs token passes through connect/saveToken once; nothing here keeps it, and the harness
// never sends it back (status carries only whether one is saved and its last four characters).
const enc = encodeURIComponent;

export function createMusicApi(call) {
  return {
    status: () => call('GET', '/music/status', undefined, undefined),
    connect: (username, token) => call('POST', '/music/connect', token ? { username, token } : { username }),
    saveToken: token => call('PUT', '/music/token', { token }),
    deleteToken: () => call('DELETE', '/music/token'),
    // any of enabled, username, focus, count, era_mix (0-1), obscurity (0-1), notify, digest (true recreates the routine)
    settings: patch => call('PUT', '/music/settings', patch),
    scan: () => call('POST', '/music/scan', {}),
    map: () => call('GET', '/music/map'),
    genre: name => call('GET', `/music/genres/${enc(name)}`),
    digests: ({ before, limit } = {}) => call('GET', '/music/digests', undefined, { before, limit }),
    digest: id => call('GET', `/music/digests/${enc(id)}`),
    // passed | owned | listened | null. "wanted" is set only by want(), after Discogs accepts it.
    react: (cardId, reaction) => {
      if (reaction === 'wanted') return Promise.reject(new Error('"wanted" is set by adding the album to the Discogs wantlist.'));
      return call('PUT', `/music/cards/${enc(cardId)}/reaction`, { reaction });
    },
    // 200 {ok: true, card} or 200 {ok: false, error: kind}
    want: cardId => call('POST', `/music/cards/${enc(cardId)}/want`, {}),
    unwant: cardId => call('DELETE', `/music/cards/${enc(cardId)}/want`),
    discover: genre => call('POST', '/music/discover', { genre }),
    markRead: digestId => call('POST', `/music/digests/${enc(digestId)}/read`, {}),
  };
}

// <img src> for a card's cover: the harness serves it, so the page's origin is the harness base.
export const coverSrc = (base, coverUrl) => (coverUrl ? String(base).replace(/\/+$/, '') + coverUrl : null);

// Words for a failed wantlist add (the error kind the harness returns).
const WANT_NOTES = {
  not_found: 'Discogs could not find this release to add.',
  private: 'Discogs would not let this account change the wantlist.',
  token_invalid: 'Discogs rejected the saved token. Paste a fresh one in Music settings.',
  token_other_user: 'The saved token belongs to a different Discogs account.',
  rate_limited: 'Discogs is asking for a pause. Try again in a minute.',
  network: 'Could not reach Discogs. Check your connection and try again.',
};
export const wantNote = kind => WANT_NOTES[kind] || 'Could not add this to your Discogs wantlist.';

// What the connection state means for the person, and whether it needs a token fix.
const CONNECTION_NOTES = {
  not_found: 'Discogs has no collection under that username.',
  private: 'This collection is private. Add a personal access token, or make it public in Discogs.',
  token_invalid: 'Discogs rejected the saved token.',
  token_other_user: 'The token belongs to a different Discogs account than the username.',
  rate_limited: 'Discogs is asking for a pause. Try again in a minute.',
  network: 'Could not reach Discogs.',
};
export const connectionNote = state => CONNECTION_NOTES[state] || '';

// The Sonos settings section's decisions. Pure, so they test without a DOM (tests/gui/sonos.test.mjs).

export const OFF_LINE = 'Sonos is off. ZenBot will not look for speakers or control them.';
export const NONE_LINE = 'No Sonos found on this network. If you have one, check that this computer is on the same network and that the firewall lets the harness find devices, then press Look again.';
export const LOOKING_LINE = 'Looking for speakers…';
export const FAILED_LINE = 'Could not look for speakers. Try Look again.';
export const HELP_LINE = 'ZenBot can play your Sonos Favorites and playlists. Add music to Favorites in the Sonos app to make it playable.';
// Same sentence and rule as the harness (src/sonos/settings.py check_volume).
export const BAD_VOLUME = 'The highest volume is a whole number from 0 to 100.';
export const POLL_MS = 3000;

/** The line under the Sonos heading: off, looking, the rooms found, or why there are none. */
export function statusLine(status) {
  if (!status) return '';
  if (status.enabled === false) return OFF_LINE;
  if (status.looking) return LOOKING_LINE;
  const rooms = Array.isArray(status.rooms) ? status.rooms : [];
  if (rooms.length) return `Found ${rooms.length} ${rooms.length === 1 ? 'room' : 'rooms'}: ${rooms.map(r => r.name).join(', ')}`;
  if (status.error === 'network' || status.error === 'other') return FAILED_LINE;
  return NONE_LINE;
}

/** '' when the text is a whole number from 0 to 100, else the harness's sentence. */
export function volumeError(text) {
  const t = typeof text === 'string' ? text.trim() : '';
  return /^\d{1,3}$/.test(t) && Number(t) <= 100 ? '' : BAD_VOLUME;
}

export const canLookAgain = (status, online) => !!online && status?.enabled === true && status?.looking !== true;

export const shouldPoll = status => status?.looking === true;

// The Wi-Fi speakers panel's decisions. Pure, so they test without a DOM (tests/gui/wifi-speakers.test.mjs).

export const PROTOCOL_NAMES = { airplay: 'AirPlay', chromecast: 'Chromecast', dlna: 'DLNA' };
export const PROTOCOLS = Object.keys(PROTOCOL_NAMES);
export const NETWORK_LINE = 'Turning a speaker type on makes ZenBot listen on your home network. Windows may ask whether to allow it.';
export const HELP_LINE = 'Speakers ZenBot can find on your home network, and the one it will use by default.';
export const LOOKING_LINE = 'Looking for speakers…';
export const FAILED_LINE = 'Could not look for speakers. Try Look again.';
export const POLL_MS = 3000;
export const RETRY_MS = 5000;

const protocols = status => PROTOCOLS.map(p => status?.protocols?.[p]).filter(Boolean);

/** The line under the heading: which speaker is the default, and why it may not be reachable now. */
export function defaultLine(status) {
  const d = status?.default;
  if (!d) return 'No default speaker.';
  const type = PROTOCOL_NAMES[d.protocol] || d.protocol;
  const line = `Default speaker: ${d.name || 'a speaker no longer listed'} (${type})`;
  if (!d.protocol_on) return `${line} · ${type} is off`;
  if (!d.found) return `${line} · Not found on the last look`;
  return line;
}

/** The line under one speaker type: off, looking, failed, none found, or '' when it lists speakers. */
export function protocolLine(p, name) {
  if (!p?.on) return `Off. Turn on to look for ${name} speakers.`;
  if (p.looking) return LOOKING_LINE;
  if (p.error) return FAILED_LINE;
  if (!p.speakers?.length) return `No ${name} speakers found. Check that this computer is on the same network as them, then press Look again.`;
  return '';
}

/** The small text beside a speaker's name. */
export const speakerDetail = s => [s?.model, s?.also_sonos ? 'Also in Sonos' : ''].filter(Boolean).join(' · ');

export const canLookAgain = (status, online) => {
  const on = protocols(status).filter(p => p.on);
  return !!online && on.length > 0 && on.some(p => !p.looking);
};

export const shouldPoll = status => protocols(status).some(p => p.looking);

/** How long until the next status read: while a look runs, POLL_MS; after a failed first read, RETRY_MS; else none. */
export const nextRead = (status, failed) => (shouldPoll(status) ? POLL_MS : failed && !status ? RETRY_MS : null);

/** The newest look of any type that is on, as the harness wrote it, or ''. */
export const lastLook = status => protocols(status).filter(p => p.on && p.last_look_at).map(p => p.last_look_at).sort().pop() || '';

/** The radios are off only when the harness is, or while a switch or a look is being sent. Not while a default is
 * saving: a disabled radio loses keyboard focus, and arrowing through a list saves each step. */
export const radiosDisabled = (busy, online) => !online || (busy !== '' && busy !== 'default');

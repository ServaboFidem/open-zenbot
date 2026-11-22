// The Wi-Fi speakers panel's decisions. Pure, so they test without a DOM (tests/gui/wifi-speakers.test.mjs).

export const PROTOCOL_NAMES = { airplay: 'AirPlay', chromecast: 'Chromecast', dlna: 'DLNA' };
export const PROTOCOLS = Object.keys(PROTOCOL_NAMES);
export const NETWORK_LINE = 'Turning a speaker type on makes ZenBot listen on your home network. Windows may ask whether to allow it.';
export const HELP_LINE = 'Speakers ZenBot can find on your home network, and the one it will use by default.';
export const LOOKING_LINE = 'Looking for speakers…';
export const FAILED_LINE = 'Could not look for speakers. Try Look again.';
export const POLL_MS = 3000;
export const RETRY_MS = 5000;
export const IDLE_MS = 30000;  // while a type is on: the harness looks again every ten minutes on its own

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

/** How long until the next status read: while a look runs, POLL_MS; after a failed first read, RETRY_MS; while a type
 * is on, IDLE_MS (so a look the harness ran on its own shows); else none. */
export const nextRead = (status, failed) => {
  if (shouldPoll(status)) return POLL_MS;
  if (failed && !status) return RETRY_MS;
  return protocols(status).some(p => p.on) ? IDLE_MS : null;
};

/** The newest look of any type that is on, as the harness wrote it, or ''. */
export const lastLook = status => protocols(status).filter(p => p.on && p.last_look_at).map(p => p.last_look_at).sort().pop() || '';

/** The radios are off only when the harness is, or while a switch or a look is being sent. Not while a default is
 * saving: a disabled radio loses keyboard focus, and arrowing through a list saves each step. */
export const radiosDisabled = (busy, online) => !online || (busy !== '' && busy !== 'default');

/** Default picks, one write at a time: a pick made while one is out waits, and only the newest waiting pick is sent,
 * so the harness saves them in the order they were made. `done({status})` or `done({error})` hears the last write once
 * the queue is empty; after an error the saved default is whatever an earlier write left, so read the status again. */
export function createPickQueue(send, done) {
  let out = false;
  let waiting = null;  // { key } while a pick waits
  async function go(key) {
    out = true;
    let result;
    try { result = { status: await send(key) }; } catch (error) { result = { error }; }
    out = false;
    if (waiting) { const next = waiting.key; waiting = null; go(next); return; }
    done(result);
  }
  return {
    pick(key) { if (out) waiting = { key }; else go(key); },
    pending: () => out || waiting !== null,
  };
}

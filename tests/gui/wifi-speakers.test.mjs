// The Wi-Fi speakers panel's decisions: the default line, each type's line, Look again, polling.
import test from 'node:test';
import assert from 'node:assert/strict';
import { NETWORK_LINE, POLL_MS, PROTOCOL_NAMES, canLookAgain, defaultLine, lastLook, nextRead, protocolLine, shouldPoll, speakerDetail } from '../../src/gui/wifi-speakers.js';

const proto = (over = {}) => ({ on: false, looking: false, last_look_at: '', error: '', speakers: [], ...over });
const status = (protocols = {}, def = null) => ({ default: def, protocols: { airplay: proto(), chromecast: proto(), dlna: proto(), ...protocols } });

test('the names and the network sentence', () => {
  assert.deepEqual(PROTOCOL_NAMES, { airplay: 'AirPlay', chromecast: 'Chromecast', dlna: 'DLNA' });
  assert.match(NETWORK_LINE, /listen on your home network/);
  assert.equal(POLL_MS, 3000);
});

test('the default line: none, found, missing, its type off', () => {
  assert.equal(defaultLine(status()), 'No default speaker.');
  const d = { key: 'airplay:A', name: 'Kitchen', protocol: 'airplay', found: true, protocol_on: true };
  assert.equal(defaultLine(status({}, d)), 'Default speaker: Kitchen (AirPlay)');
  assert.equal(defaultLine(status({}, { ...d, found: false })), 'Default speaker: Kitchen (AirPlay) · Not found on the last look');
  assert.equal(defaultLine(status({}, { ...d, found: false, protocol_on: false })), 'Default speaker: Kitchen (AirPlay) · AirPlay is off');
});

test("each type's line: off, looking, none found, failed, listed", () => {
  assert.equal(protocolLine(proto(), 'DLNA'), 'Off. Turn on to look for DLNA speakers.');
  assert.equal(protocolLine(proto({ on: true, looking: true }), 'DLNA'), 'Looking for speakers…');
  assert.match(protocolLine(proto({ on: true, last_look_at: 'x' }), 'DLNA'), /^No DLNA speakers found/);
  assert.equal(protocolLine(proto({ on: true, error: 'network' }), 'DLNA'), 'Could not look for speakers. Try Look again.');
  assert.equal(protocolLine(proto({ on: true, speakers: [{ key: 'dlna:D', name: 'Hall' }] }), 'DLNA'), '');
});

test('a speaker shows its model and whether it is also in Sonos', () => {
  assert.equal(speakerDetail({ model: 'SYMFONISK', also_sonos: true }), 'SYMFONISK · Also in Sonos');
  assert.equal(speakerDetail({ model: '', also_sonos: false }), '');
});

test('Look again: online, a type on, and one of those not already looking', () => {
  assert.equal(canLookAgain(status(), true), false);
  assert.equal(canLookAgain(status({ dlna: proto({ on: true }) }), false), false);
  assert.equal(canLookAgain(status({ dlna: proto({ on: true }) }), true), true);
  assert.equal(canLookAgain(status({ dlna: proto({ on: true, looking: true }) }), true), false);
  assert.equal(canLookAgain(status({ dlna: proto({ on: true, looking: true }), airplay: proto({ on: true }) }), true), true);
  assert.equal(canLookAgain(null, true), false);
});

test('polls while any type looks; retries a failed first read', () => {
  assert.equal(shouldPoll(status()), false);
  assert.equal(shouldPoll(status({ airplay: proto({ on: true, looking: true }) })), true);
  assert.equal(nextRead(status({ airplay: proto({ on: true, looking: true }) }), false), POLL_MS);
  assert.equal(nextRead(null, true), 5000);
  assert.equal(nextRead(status(), false), null);
});

test('the newest look of any type that is on', () => {
  assert.equal(lastLook(status()), '');
  assert.equal(lastLook(status({ airplay: proto({ on: true, last_look_at: '2026-11-21T10:00:00Z' }),
    dlna: proto({ on: true, last_look_at: '2026-11-21T11:00:00Z' }) })), '2026-11-21T11:00:00Z');
});

// The Sonos settings section's decisions, without the page.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BAD_VOLUME, FAILED_LINE, LOOKING_LINE, NONE_LINE, OFF_LINE, POLL_MS, canLookAgain, shouldPoll, statusLine, volumeError }
  from '../../src/gui/sonos.js';

const rooms = names => names.map(name => ({ name, group: name, model: 'Sonos One', state: '', title: '' }));
const st = over => ({ enabled: true, max_volume: 60, rooms: [], looking: false, last_look_at: '', error: '', ...over });

test('statusLine says what the harness knows', () => {
  assert.equal(statusLine(st({ enabled: false, rooms: rooms(['Kitchen']) })), OFF_LINE);
  assert.equal(statusLine(st({ looking: true })), LOOKING_LINE);
  assert.equal(statusLine(st({ rooms: rooms(['Kitchen']) })), 'Found 1 room: Kitchen');
  assert.equal(statusLine(st({ rooms: rooms(['Kitchen', 'Living Room', 'Office', 'Patio']) })),
    'Found 4 rooms: Kitchen, Living Room, Office, Patio');
  assert.equal(statusLine(st({ error: 'none_found' })), NONE_LINE);
  assert.equal(statusLine(st({})), NONE_LINE);
  assert.equal(statusLine(st({ error: 'network' })), FAILED_LINE);
  assert.equal(statusLine(st({ error: 'other' })), FAILED_LINE);
  assert.equal(statusLine(null), '');
});

test('volumeError takes a whole number from 0 to 100 only', () => {
  for (const ok of ['60', '0', '100', ' 45 ']) assert.equal(volumeError(ok), '', ok);
  for (const bad of ['101', '-1', '5.5', '', 'abc', '1e2', null]) assert.equal(volumeError(bad), BAD_VOLUME, String(bad));
  assert.equal(BAD_VOLUME, 'The highest volume is a whole number from 0 to 100.');
});

test('Look again only when online, on and not already looking', () => {
  assert.equal(canLookAgain(st({}), true), true);
  assert.equal(canLookAgain(st({}), false), false);
  assert.equal(canLookAgain(st({ enabled: false }), true), false);
  assert.equal(canLookAgain(st({ looking: true }), true), false);
  assert.equal(canLookAgain(null, true), false);
});

test('the section polls only while a look is under way', () => {
  assert.equal(POLL_MS, 3000);
  assert.equal(shouldPoll(st({ looking: true })), true);
  assert.equal(shouldPoll(st({})), false);
  assert.equal(shouldPoll(null), false);
});

test('nextRead keeps polling a look, retries a failed first read, and otherwise waits', async () => {
  const { nextRead, RETRY_MS } = await import('../../src/gui/sonos.js');
  assert.equal(nextRead(st({ looking: true }), false), POLL_MS);
  assert.equal(nextRead(st({ looking: true }), true), POLL_MS);  // a failed poll does not end the polling
  assert.equal(nextRead(null, true), RETRY_MS);                  // the first read failed: try again
  assert.equal(nextRead(null, false), null);
  assert.equal(nextRead(st({}), true), null);                    // a settled status stays until something changes
  assert.ok(RETRY_MS >= POLL_MS);
});

test('a status read that started before a save finished is thrown away', async () => {
  const { freshRead } = await import('../../src/gui/sonos.js');
  assert.equal(freshRead(2, 2), true);
  assert.equal(freshRead(1, 2), false);
});

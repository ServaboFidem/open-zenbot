import test from 'node:test';
import assert from 'node:assert/strict';
import { FINDLOCAL_ERRORS, FINDLOCAL_FAILED, KEY_HINT, URL_HINT, bodyFrom, canSave, changed, cityName, cityOptions, draftFrom, findLocalLine, nextRunLine, toggleCategory, zoneLine } from '../../src/gui/discovery-settings.js';

const status = (o = {}, f = {}) => ({
  settings: { enabled: false, city: 'Columbus', state: 'OH', country: 'US', source_url: 'https://www.eventbrite.com/d/oh--columbus/events/', place: 'oh--columbus', categories: ['EventbriteCategory/103'], interests: 'jazz', ...o },
  categories: [{ tag: 'EventbriteCategory/103', name: 'Music' }],
  findlocal: { city: '', timezone: '', key_set: false, key_hint: '', last_refresh: '', last_error: '', ready: false, cities: [], ...f },
});

test('the URL hint says where to find the address', () => {
  assert.equal(URL_HINT, 'Open eventbrite.com, search your city, and copy the address of the results page.');
});
test('draftFrom copies the settings, and not by reference', () => {
  const s = status(); const d = draftFrom(s);
  assert.deepEqual(d, { enabled: false, city: 'Columbus', state: 'OH', source_url: 'https://www.eventbrite.com/d/oh--columbus/events/', categories: ['EventbriteCategory/103'], interests: 'jazz', findlocal_city: '' });
  d.categories.push('x'); assert.deepEqual(s.settings.categories, ['EventbriteCategory/103']);
});
test('draftFrom copes with a missing status', () => {
  assert.deepEqual(draftFrom(null), { enabled: false, city: '', state: '', source_url: '', categories: [], interests: '', findlocal_city: '' });
});
test('changed is false untouched and true after any field changes', () => {
  const s = status();
  assert.equal(changed(draftFrom(s), s), false);
  for (const [k, v] of [['enabled', true], ['city', 'Dayton'], ['state', 'KY'], ['source_url', 'x'], ['interests', 'rock'], ['categories', []], ['findlocal_city', 'columbus-oh']]) {
    assert.equal(changed({ ...draftFrom(s), [k]: v }, s), true, k);
  }
});
test('the order of ticked categories is not a change', () => {
  const s = status({ categories: ['a', 'b'] });
  assert.equal(changed({ ...draftFrom(s), categories: ['b', 'a'] }, s), false);
});
test('toggleCategory adds and removes without touching the old list', () => {
  const a = ['a']; const b = toggleCategory(a, 'b', true);
  assert.deepEqual(b, ['a', 'b']); assert.deepEqual(a, ['a']);
  assert.deepEqual(toggleCategory(b, 'a', false), ['b']);
  assert.deepEqual(toggleCategory(a, 'a', true), ['a']);
});
test('canSave refuses switching on with no address', () => {
  const r = canSave({ ...draftFrom(null), enabled: true, source_url: '   ' });
  assert.deepEqual(r, { ok: false, why: 'Paste the address of your city’s Eventbrite page first.' });
});
test('canSave refuses interests over 2,000 characters and allows exactly 2,000', () => {
  assert.deepEqual(canSave({ ...draftFrom(null), interests: 'x'.repeat(2001) }), { ok: false, why: 'Keep your interests under 2,000 characters.' });
  assert.equal(canSave({ ...draftFrom(null), interests: 'x'.repeat(2000) }).ok, true);
});
test('canSave allows an empty form with discovery off, and a full one on', () => {
  assert.equal(canSave(draftFrom(null)).ok, true);
  assert.equal(canSave({ ...draftFrom(status()), enabled: true }).ok, true);
});
test('nextRunLine says when the weekly run is, or nothing', () => {
  assert.equal(nextRunLine(null), '');
  assert.equal(nextRunLine({ enabled: false, next_run: null }), '');
  assert.equal(nextRunLine({ enabled: true, next_run: 'not a date' }), '');
  assert.match(nextRunLine({ enabled: true, next_run: '2026-11-22T09:00:00-05:00' }), /^Next weekly run: .+\./);
});

test('the draft takes the FindLocal city from status.findlocal', () => {
  assert.equal(draftFrom(status({}, { city: 'columbus-oh' })).findlocal_city, 'columbus-oh');
});
test('cityName turns a slug into words', () => {
  assert.equal(cityName('columbia-mo'), 'Columbia Mo');
  assert.equal(cityName('new-york'), 'New York');
  assert.equal(cityName('austin'), 'Austin');
});
test('cityOptions is sorted by name', () => {
  const slugs = Array.from({ length: 134 }, (_, i) => `c${String(133 - i).padStart(3, '0')}-tx`);
  const o = cityOptions(status({}, { cities: slugs }));
  assert.equal(o.length, 134);
  assert.deepEqual(o[0], { slug: 'c000-tx', name: 'C000 Tx' });
  assert.deepEqual(o.map(x => x.name), [...o.map(x => x.name)].sort());
  assert.deepEqual(cityOptions(null), []);
});
test('bodyFrom adds the FindLocal city and the zone', () => {
  const b = bodyFrom({ ...draftFrom(status({}, { city: 'columbus-oh' })) }, 'America/New_York');
  assert.equal(b.findlocal_city, 'columbus-oh'); assert.equal(b.timezone, 'America/New_York'); assert.equal(b.country, 'US');
});
test('zoneLine names the zone, or nothing', () => {
  assert.equal(zoneLine(status({}, { timezone: 'America/New_York' })), 'Event times are read as America/New_York.');
  assert.equal(zoneLine(status()), '');
  assert.equal(zoneLine(null), '');
});
test('KEY_HINT says where to get a key', () => {
  assert.equal(KEY_HINT, 'Make a free account at findlocal.community, then copy a key from Dashboard > Keys.');
});
test('findLocalLine is empty without a city or a key', () => {
  assert.equal(findLocalLine(status({}, { city: 'columbus-oh' })), '');
  assert.equal(findLocalLine(status({}, { key_set: true })), '');
  assert.equal(findLocalLine(null), '');
});
test('findLocalLine gives the sentence for each error, and a default', () => {
  const on = { city: 'columbus-oh', key_set: true };
  const sentences = {
    key: 'FindLocal did not accept the key. Check it here.',
    refused: 'FindLocal is turning requests away for now. The free plan allows 100 requests a month.',
    network: 'Could not reach FindLocal.',
    source_changed: 'FindLocal sent an answer OpenZenBot could not use.',
    provider: 'FindLocal sent an answer OpenZenBot could not use.',
  };
  for (const [k, v] of Object.entries(sentences)) { assert.equal(findLocalLine(status({}, { ...on, last_error: k })), v, k); assert.equal(FINDLOCAL_ERRORS[k], v); }
  assert.equal(findLocalLine(status({}, { ...on, last_error: 'weird' })), 'The last FindLocal read did not finish.');
  assert.equal(FINDLOCAL_FAILED, 'The last FindLocal read did not finish.');
});
test('findLocalLine says when it last read, or that it has not', () => {
  const on = { city: 'columbus-oh', key_set: true };
  assert.match(findLocalLine(status({}, { ...on, last_refresh: '2026-11-18T14:30:00Z' })), /^Last read: .+\.$/);
  assert.equal(findLocalLine(status({}, on)), 'Not read yet. The weekly run reads it.');
});

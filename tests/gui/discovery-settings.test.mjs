import test from 'node:test';
import assert from 'node:assert/strict';
import { URL_HINT, canSave, changed, draftFrom, nextRunLine, toggleCategory } from '../../src/gui/discovery-settings.js';

const status = (o = {}) => ({
  settings: { enabled: false, city: 'Columbus', state: 'OH', country: 'US', source_url: 'https://www.eventbrite.com/d/oh--columbus/events/', place: 'oh--columbus', categories: ['EventbriteCategory/103'], interests: 'jazz', ...o },
  categories: [{ tag: 'EventbriteCategory/103', name: 'Music' }],
});

test('the URL hint says where to find the address', () => {
  assert.equal(URL_HINT, 'Open eventbrite.com, search your city, and copy the address of the results page.');
});
test('draftFrom copies the settings, and not by reference', () => {
  const s = status(); const d = draftFrom(s);
  assert.deepEqual(d, { enabled: false, city: 'Columbus', state: 'OH', source_url: 'https://www.eventbrite.com/d/oh--columbus/events/', categories: ['EventbriteCategory/103'], interests: 'jazz' });
  d.categories.push('x'); assert.deepEqual(s.settings.categories, ['EventbriteCategory/103']);
});
test('draftFrom copes with a missing status', () => {
  assert.deepEqual(draftFrom(null), { enabled: false, city: '', state: '', source_url: '', categories: [], interests: '' });
});
test('changed is false untouched and true after any field changes', () => {
  const s = status();
  assert.equal(changed(draftFrom(s), s), false);
  for (const [k, v] of [['enabled', true], ['city', 'Dayton'], ['state', 'KY'], ['source_url', 'x'], ['interests', 'rock'], ['categories', []]]) {
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

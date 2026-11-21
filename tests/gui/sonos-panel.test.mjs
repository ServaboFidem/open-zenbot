// The Sonos page's decisions, without the page.
import test from 'node:test';
import assert from 'node:assert/strict';
import { ROUTINE_BOT, ROUTINE_SKILL, canMakeRoutine, canPlay, favKey, groupsOf, initialPick, matchFavorites, orderPicked,
  routineDraft, showsSonos, toggleAction, togglePick, volumeMax } from '../../src/gui/sonos-panel.js';

const row = (name, group = name, over = {}) => ({ name, group, lead: group === name, model: 'Sonos One', state: 'stopped',
  title: '', artist: '', volume: 20, ...over });
const ROWS = [row('Kitchen'), row('Living Room', 'Living Room', { state: 'playing', title: 'So What', artist: 'Miles Davis' }),
  row('Patio', 'Living Room', { state: 'playing', title: 'So What', artist: 'Miles Davis' })];

test('the Sonos entry shows only while Sonos is on', () => {
  assert.equal(showsSonos({ enabled: true, rooms: [] }), true);
  assert.equal(showsSonos({ enabled: false }), false);
  assert.equal(showsSonos(null), false);
});

test('groupsOf puts each group together, lead first, groups by lead name', () => {
  const groups = groupsOf([ROWS[2], ROWS[0], ROWS[1]]);
  assert.deepEqual(groups.map(g => [g.lead, g.rooms.map(r => r.name)]), [['Kitchen', ['Kitchen']], ['Living Room', ['Living Room', 'Patio']]]);
  assert.deepEqual([groups[1].state, groups[1].title, groups[1].artist], ['playing', 'So What', 'Miles Davis']);
  assert.deepEqual(groupsOf(null), []);
});

test('initialPick ticks the playing group, else the first speaker', () => {
  assert.deepEqual(initialPick(ROWS), ['Living Room', 'Patio']);
  assert.deepEqual(initialPick([row('Kitchen'), row('Office')]), ['Kitchen']);
  assert.deepEqual(initialPick([]), []);
});

test('togglePick adds and removes; orderPicked follows the speaker list', () => {
  assert.deepEqual(togglePick(['Kitchen'], 'Patio'), ['Kitchen', 'Patio']);
  assert.deepEqual(togglePick(['Kitchen', 'Patio'], 'Kitchen'), ['Patio']);
  assert.deepEqual(orderPicked(['Patio', 'Kitchen', 'Gone'], ROWS), ['Kitchen', 'Patio']);
});

test('canPlay needs the harness, a favorite, a speaker and nothing under way', () => {
  const ok = { online: true, favorite: { title: 'Jazz', kind: 'favorite' }, picked: ['Kitchen'], busy: '' };
  assert.equal(canPlay(ok), true);
  assert.equal(canPlay({ ...ok, online: false }), false);
  assert.equal(canPlay({ ...ok, favorite: null }), false);
  assert.equal(canPlay({ ...ok, picked: [] }), false);
  assert.equal(canPlay({ ...ok, busy: 'play' }), false);
});

test('matchFavorites searches titles, any case, every word', () => {
  const favs = [{ title: 'Morning Jazz', kind: 'favorite' }, { title: 'Dinner', kind: 'playlist' }, { title: 'Jazz Classics', kind: 'playlist' }];
  assert.deepEqual(matchFavorites(favs, 'jazz').map(f => f.title), ['Morning Jazz', 'Jazz Classics']);
  assert.deepEqual(matchFavorites(favs, 'JAZZ morn').map(f => f.title), ['Morning Jazz']);
  assert.equal(matchFavorites(favs, '  ').length, 3);
  assert.equal(favKey(favs[1]), 'playlist:Dinner');
});

test('the play button pauses a playing group and resumes any other', () => {
  assert.equal(toggleAction('playing'), 'pause');
  assert.equal(toggleAction('paused'), 'resume');
  assert.equal(toggleAction('stopped'), 'resume');
});

test('volumeMax is the ceiling, or a louder speaker\'s own volume so it can come down', () => {
  assert.equal(volumeMax(60, 30), 60);
  assert.equal(volumeMax(60, 85), 85);
  assert.equal(volumeMax(60, null), 60);
});

test('routineDraft names the favorite and speakers in the form the skill reads', () => {
  const d = routineDraft({ title: 'Morning Jazz', kind: 'favorite' }, ['Patio', 'Kitchen'], ROWS);
  assert.equal(d.name, 'Morning music: Morning Jazz in Kitchen and Patio');
  assert.equal(d.bot, ROUTINE_BOT); assert.equal(d.skill, ROUTINE_SKILL); assert.equal(d.enabled, true);
  assert.deepEqual(d.schedule, { kind: 'weekly', days: [0, 1, 2, 3, 4], time: '07:00', hours: 24 });
  assert.equal(routineDraft({ title: 'X', kind: 'playlist' }, ['Kitchen', 'Living Room', 'Patio'], ROWS).name,
    'Morning music: X in Kitchen, Living Room and Patio');
});

test('canMakeRoutine needs a pick and ZenBot carrying the skill', () => {
  const bots = [{ id: ROUTINE_BOT, skills: ['clarify-crit', ROUTINE_SKILL] }];
  const fav = { title: 'Jazz', kind: 'favorite' };
  assert.equal(canMakeRoutine(bots, fav, ['Kitchen']), true);
  assert.equal(canMakeRoutine(bots, null, ['Kitchen']), false);
  assert.equal(canMakeRoutine(bots, fav, []), false);
  assert.equal(canMakeRoutine([{ id: ROUTINE_BOT, skills: ['clarify-crit'] }], fav, ['Kitchen']), false);
  assert.equal(canMakeRoutine([], fav, ['Kitchen']), false);
});

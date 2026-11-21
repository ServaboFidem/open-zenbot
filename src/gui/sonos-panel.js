// The Sonos page's decisions (SonosPage.jsx). Pure, so they test without a DOM (tests/gui/sonos-panel.test.mjs).
// Room names, titles and artists come from the speakers and music services: shown as plain text, never as markup.

export const POLL_MS = 5000;          // how often the page reads what is playing, while it is open and visible
export const VOLUME_WAIT_MS = 350;    // a slider move is sent once it has rested this long
export const ROUTINE_BOT = 'personal-assistant';   // ZenBot
export const ROUTINE_SKILL = 'sonos-morning';      // skills/sonos-morning: reads the routine's name

export const MORNING_TITLE = 'Wake up to music';
export const MORNING_LINE = 'ZenBot can play a Favorite on your speakers every morning. Pick one and the speakers below, then make it a morning routine: you choose the days and the time.';
export const MORNING_NO_SKILL = 'Your ZenBot does not carry the Morning music skill. Add it to ZenBot on the Bots page to make morning routines here.';
export const NO_SPEAKERS = 'No Sonos speakers answer right now. Check Settings > Connected apps > Sonos, or press Look again there.';
export const NO_FAVORITES = 'There are no Sonos Favorites or playlists yet. Add some in the Sonos app, then refresh.';

/** The left bar's Sonos entry shows while Sonos is switched on (GET /sonos/status). */
export const showsSonos = status => status?.enabled === true;

const byName = (a, b) => a.localeCompare(b, undefined, { sensitivity: 'base' });

/** Rooms gathered into the groups that play together: { lead, rooms (lead first), state, title, artist }. */
export function groupsOf(rows) {
  const groups = new Map();
  for (const r of Array.isArray(rows) ? rows : []) {
    const g = groups.get(r.group) || { lead: r.group, rooms: [], state: '', title: '', artist: '' };
    g.rooms.push(r);
    if (r.lead || !g.state) Object.assign(g, { state: r.state || '', title: r.title || '', artist: r.artist || '' });
    groups.set(r.group, g);
  }
  return [...groups.values()]
    .map(g => ({ ...g, rooms: [...g.rooms].sort((a, b) => (b.lead - a.lead) || byName(a.name, b.name)) }))
    .sort((a, b) => byName(a.lead, b.lead));
}

/** The speakers ticked when the page opens: the group that is playing, else the first speaker. */
export function initialPick(rows) {
  const list = Array.isArray(rows) ? rows : [];
  const playing = groupsOf(list).find(g => g.state === 'playing');
  if (playing) return playing.rooms.map(r => r.name);
  return list.length ? [list[0].name] : [];
}

export const togglePick = (picked, name) => (picked.includes(name) ? picked.filter(n => n !== name) : [...picked, name]);

/** The picked speakers in the page's own order, leaving out any that are gone. The first one leads the group. */
export const orderPicked = (picked, rows) => (Array.isArray(rows) ? rows : []).map(r => r.name).filter(n => picked.includes(n));

export const canPlay = ({ online, favorite, picked, busy }) => !!online && !!favorite && picked.length > 0 && !busy;

export const favKey = f => `${f.kind}:${f.title}`;

/** Favorites whose title holds every word typed, any case. */
export function matchFavorites(favs, query) {
  const words = String(query ?? '').toLowerCase().split(/\s+/).filter(Boolean);
  const list = Array.isArray(favs) ? favs : [];
  return words.length ? list.filter(f => words.every(w => f.title.toLowerCase().includes(w))) : list;
}

export const toggleAction = state => (state === 'playing' ? 'pause' : 'resume');

/** The slider's top: the highest volume ZenBot may set, or a speaker's own volume when it is already louder (it can
 * still be turned down; the harness refuses turning up past the ceiling). */
export const volumeMax = (ceiling, current) => Math.max(ceiling, Number.isInteger(current) ? current : 0);

const and = names => (names.length === 1 ? names[0] : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`);

/** A new routine for the Create routine dialog: ZenBot with the Morning music skill, weekdays at 7:00, named in the
 * form the skill reads ("Morning music: <Favorite> in <speakers>"). */
export function routineDraft(favorite, picked, rows) {
  return {
    name: `Morning music: ${favorite.title} in ${and(orderPicked(picked, rows))}`,
    bot: ROUTINE_BOT, skill: ROUTINE_SKILL, enabled: true,
    schedule: { kind: 'weekly', days: [0, 1, 2, 3, 4], time: '07:00', hours: 24 },
  };
}

export const zenbotHasSkill = bots => !!(Array.isArray(bots) ? bots : []).find(b => b.id === ROUTINE_BOT)?.skills?.includes(ROUTINE_SKILL);

export const canMakeRoutine = (bots, favorite, picked) => !!favorite && picked.length > 0 && zenbotHasSkill(bots);

import test from 'node:test';
import assert from 'node:assert/strict';
import { createApi } from '../../src/gui/api.js';
import { searchSettings } from '../../src/gui/settings-index.js';
import { todayChatKey, watchToday } from '../../src/gui/morning.js';

const zen = 'personal-assistant';
const first = { current: null, conversations: [] };
const active = id => ({ current: id, conversations: [{ id }] });

test('Today belongs to a newly opened ZenBot chat and stays through the first exchange', () => {
  const key = todayChatKey(zen, first, null);
  assert.ok(key);
  const prior = { ...first, botId: zen, today: key };
  assert.equal(todayChatKey(zen, active('first-chat'), prior), key);
  assert.equal(todayChatKey(zen, active('second'), null, 'second'), 'second');
  assert.equal(todayChatKey(zen, active('second'), { botId: zen, current: 'second', today: 'second' }), 'second');
  assert.equal(todayChatKey(zen, active('first-chat'), null), null);
  assert.equal(todayChatKey(zen, active('old'), { ...prior, conversations: [{ id: 'old' }] }), null);
  assert.equal(todayChatKey(zen, active('old'), { ...prior, current: 'second', today: 'second' }), null);
  assert.equal(todayChatKey('designer', first, null), null);
  assert.equal(todayChatKey('event-scout', active('second'), null, 'second'), null);
});

test('a slow forecast leaves the agenda usable and a closed card ignores late results', async () => {
  let resolveForecast;
  const api = { agenda: async () => ({ date: '2026-11-23' }), forecast: () => new Promise(resolve => { resolveForecast = resolve; }) };
  const results = [];
  const close = watchToday(api, (source, value) => results.push([source, value]));
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(results, [['agenda', { date: '2026-11-23' }]]);
  close();
  resolveForecast({ status: 'ready' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(results.length, 1);
});

test('new cards read the latest agenda and show independent source failures', async () => {
  let title = 'Breakfast';
  const api = { agenda: async () => ({ events: [title] }), forecast: async () => { throw Error(); } };
  const open = async () => {
    const shown = {};
    const close = watchToday(api, (source, value) => { shown[source] = value; });
    await new Promise(resolve => setImmediate(resolve));
    close();
    return shown;
  };
  assert.deepEqual(await open(), { agenda: { events: ['Breakfast'] }, forecast: { status: 'unavailable' } });
  title = 'Rescheduled lunch';
  assert.deepEqual((await open()).agenda.events, ['Rescheduled lunch']);
});

test('city search encodes its query and weather saves exclude email fields', async () => {
  const requests = [];
  const api = createApi({ fetch: async (url, init) => {
    requests.push([url.replace('http://127.0.0.1:8765', ''), init.method, init.body]);
    return Response.json({});
  } });
  await api.morning.locations('A & B');
  await api.morning.saveWeather({ location: null, unit: 'F', recipient: 'other@example.com' });
  assert.deepEqual(requests, [
    ['/morning/locations?q=A+%26+B', 'GET', undefined],
    ['/morning/settings', 'PUT', '{"location":null,"unit":"F"}'],
  ]);
  assert.ok(searchSettings('weather').some(r => r.anchor === 'settings-calendar-morning-digest'));
});

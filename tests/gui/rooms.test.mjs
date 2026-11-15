// Room list and direct-chat helpers.
import test from 'node:test';
import assert from 'node:assert/strict';
import { afterRoomDeleted, directSessions, sortRooms } from '../../src/gui/actions.js';

test('direct sessions exclude room turns', () => {
  assert.deepEqual(directSessions([{ id: 'a', bot_id: 'b' }, { id: 'r', bot_id: 'b', room_id: 'x' }, { id: 'c', bot_id: 'z' }], 'b').map(s => s.id), ['a']);
});
test('rooms sort by last activity and split archived', () => {
  const { active, archived } = sortRooms([{ id: 'old', last_active: '2026-11-01T00:00:00+00:00' }, { id: 'new', last_active: '2026-11-07T00:00:00+00:00' }, { id: 'arc', archived: true, last_active: '2026-11-08T00:00:00+00:00' }]);
  assert.deepEqual(active.map(r => r.id), ['new', 'old']); assert.deepEqual(archived.map(r => r.id), ['arc']);
});
test('deleting the open room returns the conversation to the bot chat', () => {
  assert.deepEqual(afterRoomDeleted('r1', 'r1'), { roomId: '', room: null, mode: 'bot' });
  assert.equal(afterRoomDeleted('r1', 'r2'), null);
});

import { ASSISTANT_ID } from './actions.js';

// A new chat's card stays with it when its first message gives an implicit chat an id.
// Reopening a saved chat starts with no prior view, including when the record is empty.
export function todayChatKey(botId, list, previous, createdId) {
  if (botId !== ASSISTANT_ID) return null;
  if (!list.current) return 'implicit';
  if (list.current === createdId) return createdId;
  if (previous?.botId !== botId || !previous.today) return null;
  if (previous.current === list.current) return previous.today;
  if (!previous.current && !previous.conversations.some(c => c.id === list.current)) return previous.today;
  return null;
}

export function watchToday(morning, show) {
  let open = true;
  for (const source of ['agenda', 'forecast']) {
    morning[source]().catch(() => ({ status: 'unavailable' })).then(value => {
      if (open) show(source, value);
    });
  }
  return () => { open = false; };
}

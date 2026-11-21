// Reading replies aloud: the chat's speaker toggle (remembered in this window), which reply to read, and a player
// that plays one reply at a time. The harness does the work (POST /speech: the Narrator, then the speech model).

export const SPEAK_KEY = 'openzenbot.speakReplies';
export const SPEAK_MAX = 12000;  // what the Narrator reads; the rest of a long reply is never sent

export function readSpeakOn(storage) {
  try { return storage?.getItem(SPEAK_KEY) === '1'; } catch { return false; }
}

export function writeSpeakOn(storage, on) {
  try { storage?.setItem(SPEAK_KEY, on ? '1' : '0'); } catch { /* no storage: the toggle lasts this page only */ }
}

// The newest finished exchange whose session was started while the toggle was on, with a reply to read; null
// otherwise. Old replies, routine runs and replies made of attachments only are never read.
export function toSpeak(exchanges, waiting) {
  if (!Array.isArray(exchanges) || !waiting?.size) return null;
  for (let i = exchanges.length - 1; i >= 0; i--) {
    const ex = exchanges[i];
    if (!ex || !waiting.has(ex.session_id) || ex.outcome === 'pending') continue;
    const text = typeof ex.reply === 'string' ? ex.reply.trim() : '';
    if (text) return { sessionId: ex.session_id, text: text.slice(0, SPEAK_MAX) };
  }
  return null;
}

// One reply at a time: playing a new one stops the last and frees its object URL.
export function createPlayer({ Audio, URL } = {}) {
  let audio = null; let url = '';
  function stop() {
    if (audio) { try { audio.pause(); } catch { /* already gone */ } audio = null; }
    if (url) { URL.revokeObjectURL(url); url = ''; }
  }
  function play(blob) {
    stop();
    url = URL.createObjectURL(blob);
    audio = new Audio(url);
    const done = audio;
    audio.onended = () => { if (audio === done) stop(); };
    return Promise.resolve(audio.play());
  }
  return { play, stop };
}

// Fetches a reading and plays it, unless it was cancelled (a new message, a newer reply, the toggle off, another
// chat) while on its way. `onError(item, error)` hears a reading that could not be fetched or played.
export function createReader({ speak, player, onError }) {
  let seq = 0;
  return {
    cancel() { seq++; player.stop(); },
    async read(item, isOn) {
      const mine = ++seq;
      player.stop();  // a newer reply: the last one stops now, not when this one arrives
      let blob;
      try { blob = await speak(item.text); } catch (e) { if (mine === seq) onError(item, e); return; }
      if (mine !== seq || !isOn()) return;
      try { await player.play(blob); } catch { if (mine === seq) onError(item, new Error('The reply could not be played.')); }
    },
  };
}

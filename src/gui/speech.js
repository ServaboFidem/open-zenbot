// Reading replies aloud: the chat's speaker toggle (remembered in this window), which reply to read, and a player
// that plays one reply at a time. The harness does the work (POST /speech: the Narrator, then the speech model).

export const SPEAK_KEY = 'openzenbot.speakReplies';

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
    if (text) return { sessionId: ex.session_id, text };
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
    return Promise.resolve(audio.play()).catch(() => {});
  }
  return { play, stop };
}

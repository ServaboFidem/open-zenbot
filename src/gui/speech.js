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

export const FRAME_MAX = 64000000;  // the harness never sends a frame over this
export const END_MISSING = 'The speech ended early.';

// Reads POST /speech's body: frames of a 4-byte big-endian length and that many bytes of one WAV file; a frame of
// length 0 is the end mark. `push` takes the chunks as they arrive and answers the WAVs completed by each.
export function createFrameParser() {
  let buf = new Uint8Array(0);
  const parser = {
    ended: false,
    push(chunk) {
      const out = [];
      if (parser.ended || !chunk?.length) return out;
      const joined = new Uint8Array(buf.length + chunk.length);
      joined.set(buf); joined.set(chunk, buf.length);
      buf = joined;
      for (;;) {
        if (buf.length < 4) break;
        const n = ((buf[0] * 256 + buf[1]) * 256 + buf[2]) * 256 + buf[3];
        if (n > FRAME_MAX) throw new Error('The speech was not in the expected form.');
        if (n === 0) { parser.ended = true; buf = new Uint8Array(0); break; }
        if (buf.length < 4 + n) break;
        out.push(buf.slice(4, 4 + n));
        buf = buf.slice(4 + n);
      }
      return out;
    },
  };
  return parser;
}

// A queue of pieces: one plays at once when nothing is playing, otherwise after the piece before it. A piece that
// cannot play is skipped. `stop` clears the queue, stops the sound and frees every object URL.
export function createPlayer({ Audio, URL } = {}) {
  let queue = []; let audio = null; let url = '';
  function free() { if (url) { URL.revokeObjectURL(url); url = ''; } audio = null; }
  function next() {
    if (audio || !queue.length) return;
    const piece = queue.shift();
    url = piece;
    const a = new Audio(piece); audio = a;
    const done = () => { if (audio === a) { free(); next(); } };
    a.onended = done; a.onerror = done;
    try { Promise.resolve(a.play()).catch(done); } catch { done(); }
  }
  return {
    enqueue(blob) { queue.push(URL.createObjectURL(blob)); next(); },
    stop() {
      for (const u of queue) URL.revokeObjectURL(u);
      queue = [];
      if (audio) { try { audio.pause(); } catch { /* already gone */ } }
      free();
    },
    playing() { return audio !== null || queue.length > 0; },
  };
}

// Fetches a reading piece by piece and queues each piece, unless it was cancelled (a new message, a newer reply,
// the toggle off, another chat) while on its way; cancelling aborts the request, which stops the harness making
// more. `onError(item, error, { played })` hears a reading that failed; an abort is never an error.
export function createReader({ speak, player, onError }) {
  let seq = 0; let controller = null;
  function abort() { if (controller) { controller.abort(); controller = null; } }
  return {
    cancel() { seq++; abort(); player.stop(); },
    async read(item, isOn) {
      const mine = ++seq;
      abort();
      player.stop();  // a newer reply: the last one stops now, not when this one arrives
      const own = new AbortController(); controller = own;
      let played = false;
      try {
        for await (const blob of speak(item.text, own.signal)) {
          if (mine !== seq) return;
          if (!isOn()) { own.abort(); return; }
          player.enqueue(blob); played = true;
        }
      } catch (e) {
        if (mine === seq && !own.signal.aborted && e?.name !== 'AbortError') onError(item, e, { played });
      }
    },
  };
}

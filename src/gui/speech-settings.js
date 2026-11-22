// The Read aloud card's decisions for the local voice (Kokoro): the status line, the action, the voice groups.
export const LOCAL = 'kokoro';
export const LOCAL_MODEL = 'kokoro-v1.0';
export const LOCAL_LABEL = 'Kokoro (free, no key)';
export const NOTE = "Kokoro makes the speech on this computer. The Narrator still rewrites each reply first, on its own model or the chat model, so the reply's text still goes to that model's provider.";
export const REMOVE_ASK = 'Remove the downloaded voice? Read aloud will need it again.';
export const POLL_MS = 2000;
export const ERROR_LINES = {
  network: 'The download could not reach GitHub. Check the connection and try again.',
  refused: 'GitHub refused the download for now. Try again later.',
  source_changed: 'The downloaded file was not the expected one, so it was not kept.',
  disk: 'There is not enough free space, or the file could not be written.',
  provider: 'The download failed. Try again.',
};
const TOTAL_BYTES = 353_719_767;

/** Whole megabytes (1 MB = 1,000,000 bytes), rounded. */
export const mb = bytes => Math.round((Number(bytes) || 0) / 1_000_000);

export function localLine(local) {
  const l = local || {};
  if (l.state === 'downloading') return `Downloading… ${mb(l.done)} of ${mb(l.total || TOTAL_BYTES)} MB`;
  if (l.state === 'failed') return ERROR_LINES[l.error] || ERROR_LINES.provider;
  if (l.state === 'ready') return 'Downloaded.';
  return `The voice is not downloaded yet (${mb(TOTAL_BYTES)} MB).`;
}

export function localAction(local) {
  const s = local && local.state;
  if (s === 'downloading') return null;
  if (s === 'failed') return 'retry';
  if (s === 'ready') return 'remove';
  return 'download';
}

/** The voices in groups by accent then gender, in the order given; an empty group is dropped. */
export function voiceGroups(voices) {
  const groups = [];
  for (const accent of ['American', 'British']) {
    for (const gender of ['female', 'male']) {
      const list = (voices || []).filter(v => v.accent === accent && v.gender === gender);
      if (list.length) groups.push({ label: `${accent}, ${gender}`, voices: list });
    }
  }
  return groups;
}

/** What the hidden live region says: the status line, except while downloading, when it says only that it is
 *  downloading, so a screen reader hears the start, the end and a failure but not every 2 s progress tick. */
export function liveLine(local) {
  return local && local.state === 'downloading' ? 'Downloading…' : localLine(local);
}

/** The card's error line is cleared whenever this changes: the model's state or the picked voice. */
export const errorKey = (local, voice) => `${(local && local.state) || ''}|${voice || ''}`;

/** A guard that lets one call through at a time (a fast double click sends one request). `run` is called with the
 *  function given to the guard; a second call while the first is out answers undefined and runs nothing. */
export function oneAtATime(run) {
  let out = false;
  return async fn => {
    if (out) return undefined;
    out = true;
    try { return await run(fn); } finally { out = false; }
  };
}

/** Whether the card says the saved provider cannot speak: keyed on the saved tool, never on the pick in the select,
 *  and never for Kokoro, whose own line says to download it. */
export const showsNoSpeakerAlert = t => !!t && !!t.enabled && !t.active && t.provider !== LOCAL;

export const canSample = (local, busy) => !!local && local.state === 'ready' && !busy;
export const shouldPoll = local => !!local && local.state === 'downloading';

/** The voice when the local list has it, else the local default. */
export function voiceFor(local, voice) {
  const l = local || {};
  return (l.voices || []).some(v => v.name === voice) ? voice : l.default_voice;
}

const isLocal = choice => (choice || '').startsWith(`${LOCAL}::`);
const savedChoice = t => (t.provider && t.model ? `${t.provider}::${t.model}` : '');

/** The card's form for a saved tool: the provider model's free-text voice and the Kokoro picker's voice are kept apart. */
export const speechForm = t => ({ choice: savedChoice(t), voice: t.provider === LOCAL ? '' : (t.voice || ''), localVoice: t.provider === LOCAL ? t.voice : undefined });

/** The body of PUT /settings/tools/speech for the form: only what changed, never one side's voice on the other's model. */
export function speechPatch(t, form) {
  if (isLocal(form.choice)) return { provider: LOCAL, model: LOCAL_MODEL, voice: voiceFor(t.local, form.localVoice) };
  const patch = {};
  if (form.choice !== savedChoice(t)) {
    const at = form.choice.indexOf('::');
    if (at > 0) { patch.provider = form.choice.slice(0, at); patch.model = form.choice.slice(at + 2); }
  }
  const v = typeof form.voice === 'string' ? form.voice.trim() : '';
  if (v && v !== t.voice) patch.voice = v;
  return patch;
}

/** Whether Save is enabled. */
export function speechDirty(t, form) {
  if (form.choice !== savedChoice(t)) return true;
  if (isLocal(form.choice)) return voiceFor(t.local, form.localVoice) !== t.voice;
  const v = typeof form.voice === 'string' ? form.voice.trim() : '';
  return !!v && v !== t.voice;
}

/** Play a sample: a blob that arrives after stop() or after a newer play() is dropped. */
export function createSampler({ fetchSample, player }) {
  let gen = 0;
  return {
    async play(voice, onFail) {
      const mine = ++gen;
      const blob = await fetchSample(voice);
      if (mine !== gen) return false;
      player.stop();
      player.enqueue(blob, onFail);
      return true;
    },
    stop() { gen += 1; player.stop(); },
  };
}

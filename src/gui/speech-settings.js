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

export const canSample = (local, busy) => !!local && local.state === 'ready' && !busy;
export const shouldPoll = local => !!local && local.state === 'downloading';

/** The voice when the local list has it, else the local default. */
export function voiceFor(local, voice) {
  const l = local || {};
  return (l.voices || []).some(v => v.name === voice) ? voice : l.default_voice;
}

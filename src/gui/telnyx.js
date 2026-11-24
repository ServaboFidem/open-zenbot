// Telnyx's decisions for the settings section. Pure, so they test without a DOM (tests/gui/telnyx.test.mjs).

/** The line under the Telnyx heading in Settings (also in the settings search). */
export const TELNYX_LINE = 'Text reminders of your calendar to your phone through Telnyx. ZenBot sends at most 30 texts a day, and web links in events are sent as [link].';
export const REGISTER_LINE = 'US carriers block texts from unregistered numbers. Register your number for 10DLC or toll-free verification in the Telnyx portal first.';
export const REGISTER_URL = 'https://support.telnyx.com/en/articles/6325731-register-for-10dlc-messaging';

// Same choices as the desktop app (src/electron/telnyx-texts.cjs) and the harness (src/telnyx/settings.py LEADS).
export const LEADS = [10, 15, 30, 60, 120];
export const leadLabel = m => (m % 60 === 0 ? `${m / 60} hour${m === 60 ? '' : 's'}` : `${m} minutes`);

// One sentence per error kind the harness records on a text (src/telnyx/client.py, service.py).
export const ERRORS = {
  network: 'The last text could not reach Telnyx.',
  key: 'Telnyx refused the saved key. Save a new one.',
  refused: 'Telnyx was busy and did not take the last text.',
  invalid: "Telnyx refused the last text. Check the number's messaging setup and registration.",
  provider: 'Telnyx answered the last text in a way ZenBot could not read. It may not have been sent.',
  uncertain: 'The last text may not have been sent. ZenBot will not send it again.',
  interrupted: 'ZenBot stopped while a text was being sent. It may not have gone, and will not be sent again.',
};
const FALLBACK = 'Something went wrong. Try again.';

function when(seconds) {
  const at = new Date(seconds * 1000);
  return Number.isNaN(at.getTime()) ? '' : at.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

/** One line on where Telnyx stands: what is missing, else where texts go, when the last one went, and trouble. */
export function statusLine(status) {
  if (!status) return '';
  if (!status.key_set) return 'Save a Telnyx key to start.';
  if (!status.number) return 'Choose the number ZenBot sends from.';
  const texts = status.texts;
  if (!texts || !texts.phone_hint) return 'Set your phone number and which texts to send.';
  if (!texts.before && !texts.rundown) return 'Texts are off.';
  const sent = status.last_sent_at ? `Last text sent ${when(status.last_sent_at)}.` : 'Nothing sent yet.';
  const parts = [`Texts go to the phone ending ${texts.phone_hint}.`, sent];
  if (status.last_error) parts.push(ERRORS[status.last_error] || ERRORS.provider);
  if (status.skipped_today > 0) {
    parts.push(`${status.sent_today} texts were sent today; ${status.skipped_today} more were skipped.`);
  }
  return parts.join(' ');
}

/** A test text needs a saved key, a sending number and a phone saved in the desktop app. */
export const canTest = status => !!(status?.key_set && status.number && status.texts?.phone_hint);

/** The harness's sentence from a failed call (HarnessError carries it as `message`); never raw JSON. */
export function errorLine(err) {
  const text = typeof err?.message === 'string' ? err.message.trim() : '';
  return !text || /^[[{]/.test(text) ? FALLBACK : text;
}

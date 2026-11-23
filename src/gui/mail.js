// AgentMail's decisions for the page and the settings section. Pure, so they test without a DOM
// (tests/gui/mail.test.mjs).

// Same sentence and rule as the harness (src/mail/settings.py check_username).
export const BAD_NAME = 'A name is 1 to 64 lowercase letters, digits, dots, dashes or underscores, starting with a letter or digit.';
const NAME = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const FALLBACK = 'Something went wrong. Try again.';

/** The AgentMail entry shows in the navigation only once a key is saved and an inbox chosen. */
export const showsNav = status => status?.enabled === true;

/** "Ann Lee <ann@example.com>" → "Ann Lee"; a bare or bracketed address → the address. */
export function senderName(from) {
  if (typeof from !== 'string') return '';
  const text = from.trim();
  const m = text.match(/^(.*?)\s*<([^>]*)>\s*$/);
  if (!m) return text;
  const name = m[1].trim().replace(/^"(.*)"$/, '$1').trim();
  return name || m[2].trim();
}

const sameDay = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();

/** A clock time for today, "Yesterday", else a short date (with the year when not this year); '' when unreadable. */
export function timeLabel(iso, now = new Date()) {
  if (typeof iso !== 'string' || !iso) return '';
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return '';
  if (sameDay(at, now)) return at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  if (sameDay(at, yesterday)) return 'Yesterday';
  const opts = { month: 'short', day: 'numeric' };
  if (at.getFullYear() !== now.getFullYear()) opts.year = 'numeric';
  return at.toLocaleDateString([], opts);
}

/** '' when the inbox name is one the harness takes, else the harness's sentence. */
export const usernameError = text => (typeof text === 'string' && NAME.test(text.trim().toLowerCase()) ? '' : BAD_NAME);

/** The harness's sentence from a failed call (HarnessError carries it as `message`); never raw JSON. */
export function errorLine(err) {
  const text = typeof err?.message === 'string' ? err.message.trim() : '';
  return !text || /^[[{]/.test(text) ? FALLBACK : text;
}

/** The line under the AgentMail heading in Settings (also in the settings search). */
export const MAIL_LINE = "Read mail at ZenBot’s address. Incoming messages stay private from bots. To receive a daily morning digest, set a recipient in Settings → Calendar.";

// The inbox radios only mark a pick (arrow keys move it); "Use this inbox" makes the change, and only for an
// active inbox in the list that is not ZenBot's already.
export const canUseInbox = (picked, current, inboxes) => !!picked && picked !== current && Array.isArray(inboxes)
  && inboxes.some(i => i.inbox_id === picked && !i.deactivated);

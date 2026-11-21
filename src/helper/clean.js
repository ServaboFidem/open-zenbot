// Text and link hygiene, mirroring src/events/eventbrite.py (`_clean`, `_link`).
//
// Everything on a Facebook page is untrusted text written by strangers. Every
// field the helper reports is cleaned: line breaks become one space, other
// control characters are dropped, the value is trimmed and cut to its cap.
// Links are kept only when they are https on exactly www.facebook.com with an
// expected path shape. (Written with ASCII escapes only: no literal control
// characters appear in this file.)

export const TITLE_MAX = 300;
export const TEXT_MAX = 500;
export const SUMMARY_MAX = 1000;
export const URL_MAX = 1000;
export const NAME_MAX = 200;
export const LABEL_MAX = 64;

// Characters that fold to one space: tab, LF, CR, U+2028, U+2029.
const SPACE_CODES = new Set([0x09, 0x0a, 0x0d, 0x2028, 0x2029]);

/** Text with control characters taken out, trimmed, cut to `limit`. */
export function clean(value, limit) {
  if (typeof value !== "string") return "";
  let out = "";
  for (const ch of value) {
    const code = ch.codePointAt(0);
    if (SPACE_CODES.has(code)) {
      out += " ";
    } else if (code >= 0x20 && code !== 0x7f && !(code >= 0x80 && code <= 0x9f)) {
      out += ch;
    }
  }
  return out.trim().slice(0, limit).trim();
}

/** Whether `value` holds only ASCII with no whitespace or backslash. */
function isPlainAscii(value) {
  for (const ch of value) {
    const code = ch.codePointAt(0);
    if (code > 0x7e || code <= 0x20 || code === 0x5c) return false;
  }
  return true;
}

/**
 * `value` when it is an https link on exactly www.facebook.com whose path
 * starts with `prefix`, with no stray characters; else "".
 */
export function keepFacebookUrl(value, prefix = "/") {
  if (typeof value !== "string" || value.length > URL_MAX) return "";
  if (!value.startsWith("https://www.facebook.com" + prefix)) return "";
  if (!isPlainAscii(value)) return "";
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return "";
  }
  if (parsed.protocol !== "https:") return "";
  if (parsed.hostname !== "www.facebook.com") return "";
  if (parsed.username || parsed.password || parsed.port) return "";
  if (!parsed.pathname.startsWith(prefix)) return "";
  return value;
}

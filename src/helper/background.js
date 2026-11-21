// Background schedule: the service worker (MV3 module).
//
// Rhythm, the builder's pick within polite bounds: the helper re-reads at
// most every CHECK_INTERVAL_MIN minutes (240: about four sweeps through the
// day), and sends at most SEND_GAP_MS apart. Facebook is read only from tabs
// the user already has open on a named corner: this worker never opens a
// tab, never navigates or reloads one. It receives reports, checks the page
// is still a named corner, drops repeats, and hands the rest to sender.js,
// the only file here that posts anywhere.
//
// Starting a reader: Facebook moves between pages without loading them, so a
// tab that arrives on a named corner by a click inside Facebook never gets
// the content script. When a tab's address becomes a named corner, and for
// tabs already open on one when the helper is installed or reloaded, this
// worker runs that corner's own reader files in the tab (startReader): only
// the files in READER_FILES, only on a named corner. A reader already running
// there reads again instead of running twice.
//
// The alarm below is housekeeping only (it sweeps the seen-digest cache); the
// re-read timer lives in the corner readers, next to the pages they watch.
// Both use the same CHECK_INTERVAL_MIN, enforced by the slice tests.

import { cornerForUrl } from "./corners.js";
import { machineZone, validateReport } from "./schema.js";
import { postReport } from "./sender.js";

export const ALARM_NAME = "fb-helper-sweep";
export const CHECK_INTERVAL_MIN = 240;
export const RECHECK_MS = CHECK_INTERVAL_MIN * 60 * 1000;
export const DEDUPE_TTL_MS = 24 * 60 * 60 * 1000;

/** The shared text readers, run before every corner reader. */
export const SHARED_FILE = "page-text.js";
/** Each named corner's own reader. */
export const READER_FILES = {
  events_dashboard: "corner-events.js",
  invites: "corner-invites.js",
  birthdays: "corner-birthdays.js",
};
/** Tabs to look at when the helper is installed (each is checked with cornerForUrl too). */
export const TAB_PATTERNS = [
  "https://www.facebook.com/events*",
  "https://www.facebook.com/birthdays*",
];

/** The files to run in a tab at `url`: the corner's reader after the shared file, or none off the corners. */
export function readerFiles(url) {
  const corner = cornerForUrl(url);
  return corner ? [SHARED_FILE, READER_FILES[corner]] : [];
}

/**
 * Run the corner reader for `url` in tab `tabId` through `scripting`
 * (chrome.scripting). Returns whether it ran: nothing runs off a named corner.
 */
export async function startReader(tabId, url, scripting) {
  const files = readerFiles(url);
  if (files.length === 0 || !Number.isInteger(tabId)) return false;
  await scripting.executeScript({ target: { tabId }, files });
  return true;
}

/** Digests forwarded in the last DEDUPE_TTL_MS, so a re-read stays quiet. */
export const seenDigests = new Map();

export function digestReport(report) {
  return `${report.corner}\n${report.url}\n${JSON.stringify(report.items)}`;
}

export function sweepSeen(now = Date.now()) {
  for (const [digest, at] of seenDigests) {
    if (now - at > DEDUPE_TTL_MS) seenDigests.delete(digest);
  }
  return seenDigests.size;
}

/**
 * Handle one `{type: "fb-helper-report", report}` message from a corner
 * reader. `senderUrl` is the sending tab's URL. Returns `{forwarded}`.
 * Throws (naming the shape only, never the text) on anything unexpected, and
 * forwards nothing then.
 */
export async function handleMessage(message, senderUrl, fetchImpl) {
  if (!message || message.type !== "fb-helper-report" || !message.report) {
    throw new Error("bad-message: not a report");
  }
  const report = message.report;
  if (typeof senderUrl !== "string" || senderUrl === "") {
    throw new Error("bad-message: no sender page");
  }
  if (report.url !== senderUrl) {
    throw new Error("bad-message: url mismatch");
  }
  const corner = cornerForUrl(report.url);
  if (corner === "" || corner !== report.corner) {
    throw new Error("bad-message: not a named corner");
  }
  validateReport(report);
  const digest = digestReport(report);
  const seenAt = seenDigests.get(digest);
  const now = Date.now();
  if (seenAt !== undefined && now - seenAt < DEDUPE_TTL_MS) {
    return { forwarded: false, duplicate: true };
  }
  // Facebook pages give times with no zone: the harness takes them to be in this machine's zone
  const zone = machineZone();
  await postReport(zone ? { ...report, timezone: zone } : report, fetchImpl);
  seenDigests.set(digest, Date.now());
  return { forwarded: true, duplicate: false };
}

const api = globalThis.chrome;

if (api && api.runtime && api.runtime.onMessage) {
  api.runtime.onMessage.addListener((message, sender, respond) => {
    handleMessage(message, sender && sender.tab && sender.tab.url, undefined)
      .then((result) => respond({ ok: true, ...result }))
      .catch((error) =>
        respond({ ok: false, error: String((error && error.message) || error) }),
      );
    return true;
  });
}

if (api && api.tabs && api.scripting) {
  // A tab whose address became a named corner (a click inside Facebook loads no page).
  if (api.tabs.onUpdated) {
    api.tabs.onUpdated.addListener((tabId, info) => {
      if (info && typeof info.url === "string") {
        startReader(tabId, info.url, api.scripting).catch(() => {});
      }
    });
  }
  // Tabs already open on a named corner when the helper is installed or reloaded.
  if (api.runtime && api.runtime.onInstalled && api.tabs.query) {
    api.runtime.onInstalled.addListener(() => {
      api.tabs.query({ url: TAB_PATTERNS }).then(
        (found) => {
          for (const tab of found || []) {
            startReader(tab.id, tab.url, api.scripting).catch(() => {});
          }
        },
        () => {},
      );
    });
  }
}

if (api && api.alarms) {
  if (api.alarms.onAlarm) {
    api.alarms.onAlarm.addListener((alarm) => {
      if (alarm && alarm.name === ALARM_NAME) sweepSeen();
    });
  }
  if (api.runtime && api.runtime.onInstalled) {
    api.runtime.onInstalled.addListener(() => {
      if (api.alarms.create) {
        api.alarms.create(ALARM_NAME, { periodInMinutes: CHECK_INTERVAL_MIN });
      }
    });
  }
}

// Background schedule: the service worker (MV3 module).
//
// Rhythm, the builder's pick within polite bounds: the helper re-reads at
// most every CHECK_INTERVAL_MIN minutes (240: about four sweeps through the
// day), and sends at most SEND_GAP_MS apart. Facebook is read only from tabs
// the user already has open on a named corner: this worker never opens a
// tab, never navigates one, never runs a script in one. It receives reports,
// checks the page is still a named corner, drops repeats, and hands the rest
// to sender.js, the only file here that posts anywhere.
//
// The alarm below is housekeeping only (it sweeps the seen-digest cache); the
// re-read timer lives in the corner readers, next to the pages they watch.
// Both use the same CHECK_INTERVAL_MIN, enforced by the slice tests.

import { cornerForUrl } from "./corners.js";
import { validateReport } from "./schema.js";
import { postReport } from "./sender.js";

export const ALARM_NAME = "fb-helper-sweep";
export const CHECK_INTERVAL_MIN = 240;
export const RECHECK_MS = CHECK_INTERVAL_MIN * 60 * 1000;
export const DEDUPE_TTL_MS = 24 * 60 * 60 * 1000;

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
  await postReport(report, fetchImpl);
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

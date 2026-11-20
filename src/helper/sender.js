// The only code in the helper that talks to the network.
//
// It posts validated reports to the harness on this machine and nothing
// else: the destination is fixed to http://127.0.0.1:<port>/intake/facebook
// (default port 8765, the harness's own). Any other host, any other scheme,
// any other path is refused before anything is sent. Reads from Facebook
// pages never leave the browser tab; only the cleaned report goes out, and
// only to loopback.
//
// Sends are paced SEND_GAP_MS apart, the browser-side echo of the 1 s gap in
// src/events/eventbrite.py.

import { validateReport } from "./schema.js";

export const HARNESS_HOST = "127.0.0.1";
export const HARNESS_PORT = 8765;
export const INTAKE_PATH = "/intake/facebook";
export const SEND_GAP_MS = 1000;

let lastSendAt = 0;

/** Test hook: forget the last send time. */
export function resetSendClock() {
  lastSendAt = 0;
}

/**
 * The intake URL. With no `origin` the harness default is used; otherwise
 * `origin` must be the full intake URL on exactly 127.0.0.1. Anything else
 * throws and nothing is sent.
 */
export function buildIntakeUrl(origin) {
  if (origin === undefined || origin === null || origin === "") {
    return `http://${HARNESS_HOST}:${HARNESS_PORT}${INTAKE_PATH}`;
  }
  const match = /^http:\/\/127\.0\.0\.1(?::(\d+))?(\/intake\/facebook)$/.exec(
    origin,
  );
  if (!match) {
    throw new Error("bad-origin: posts go only to http://127.0.0.1/intake/facebook");
  }
  if (match[1] !== undefined) {
    const port = Number(match[1]);
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      throw new Error("bad-origin: bad port");
    }
  }
  return origin;
}

/**
 * Validate `report` then POST it to the harness. The payload is validated
 * first, so an invalid report throws without any request going out. Returns
 * true on a 2xx answer, else throws naming the status only.
 */
export async function postReport(report, fetchImpl = globalThis.fetch, origin) {
  validateReport(report);
  const url = buildIntakeUrl(origin);
  const wait = SEND_GAP_MS - (Date.now() - lastSendAt);
  if (wait > 0) {
    await new Promise((resolve) => setTimeout(resolve, wait));
  }
  if (typeof fetchImpl !== "function") {
    throw new Error("bad-fetch: no fetch implementation");
  }
  const response = await fetchImpl(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(report),
  });
  lastSendAt = Date.now();
  if (!response || !response.ok) {
    throw new Error(`harness-refused: ${response ? response.status : "no-response"}`);
  }
  return true;
}

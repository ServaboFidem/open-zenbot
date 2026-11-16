// Where a framed page may navigate to: the harness's own origin (`harnessUrl`, the app's configured
// harness base), or http(s) on this machine. The Knowledge page frames the local memory dashboard on
// another loopback port. A harness address that does not parse allows nothing.
const LOOPBACK = new Set(['127.0.0.1', 'localhost', '[::1]']);
const WEB = new Set(['http:', 'https:']);

function allowFrameNavigation(url, harnessUrl) {
  let u, harness;
  try { u = new URL(url); harness = new URL(harnessUrl); } catch { return false; }
  if (!WEB.has(u.protocol) || !WEB.has(harness.protocol)) return false;  // never an opaque "null" origin
  return u.origin === harness.origin || LOOPBACK.has(u.hostname);
}

module.exports = { allowFrameNavigation };

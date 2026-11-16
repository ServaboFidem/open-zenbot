// Where a framed page may navigate to: http(s) on this machine only. The harness serves bot-made pages
// from loopback and the Knowledge page frames the local memory dashboard (another loopback port).
const LOOPBACK = new Set(['127.0.0.1', 'localhost', '[::1]']);

function allowFrameNavigation(url) {
  let u;
  try { u = new URL(url); } catch { return false; }
  return (u.protocol === 'http:' || u.protocol === 'https:') && LOOPBACK.has(u.hostname);
}

module.exports = { allowFrameNavigation };

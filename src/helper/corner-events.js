/* corner-events.js — observe-only reader for the events dashboard.
 *
 * Runs only on https://www.facebook.com/events (see manifest.json) and
 * re-checks that page itself before touching the DOM. It reads the listing
 * the user already has open, builds a cleaned report, and hands it to the
 * background worker. It never presses anything, never answers an invite,
 * never posts, never messages, and never posts to the network itself.
 *
 * What the page holds (seen on the live dashboard, 2026-11-20): no <time>
 * elements and no headings or articles around a card. A card is the block
 * around the links to one event, and its visible text runs date line, title,
 * venue, then the "interested" and button lines (page-text.js reads them).
 * The notification flyout links to events too, outside the main region, so
 * only the main region is read.
 */
(function () {
  "use strict";

  var CORNER = "events_dashboard";
  var ALLOWED_PATHS = ["/events", "/events/"];
  // Keep in sync with CHECK_INTERVAL_MIN in background.js (one rhythm).
  var RECHECK_MS = 240 * 60 * 1000;
  // Facebook draws the cards after the page settles, so the first reads come
  // soon after load; repeats are dropped by the background worker.
  var SETTLE_MS = [3000, 10000, 30000];
  var MAX_ITEMS = 200;
  var READER = "__ozbReader_events";

  // Loaded again in the same tab (the background starts the reader when the
  // tab arrives here without a full load): read again, never run twice.
  if (typeof globalThis[READER] === "function") {
    globalThis[READER]();
    return;
  }
  var text = globalThis.OzbPageText;
  if (!text) return;

  function isAllowed(url) {
    var parsed;
    try {
      parsed = new URL(url);
    } catch (err) {
      return false;
    }
    if (parsed.protocol !== "https:") return false;
    if (parsed.hostname !== "www.facebook.com") return false;
    if (parsed.username || parsed.password || parsed.port) return false;
    return ALLOWED_PATHS.indexOf(parsed.pathname) !== -1;
  }

  /** The event items in `root`, read against `today`. */
  function readEvents(root, today) {
    var cards = text.eventCards(root, today, MAX_ITEMS);
    var items = [];
    for (var i = 0; i < cards.length; i += 1) {
      items.push({
        kind: "event",
        id: cards[i].id,
        title: cards[i].fields.title,
        start_date: cards[i].fields.date,
        start_time: cards[i].fields.time,
        url: cards[i].url,
        venue: cards[i].fields.venue,
      });
    }
    return items;
  }

  function observe() {
    if (!isAllowed(location.href)) return;
    var root = document.querySelector('[role="main"]') || document;
    var items = readEvents(root, new Date());
    var api = typeof chrome !== "undefined" ? chrome : null;
    if (api && api.runtime && api.runtime.sendMessage) {
      api.runtime.sendMessage({
        type: "fb-helper-report",
        report: {
          helper: "fb-events-v1",
          observed_at: new Date().toISOString(),
          corner: CORNER,
          url: location.href,
          items: items,
        },
      });
    }
  }

  function settle() {
    observe();
    for (var s = 0; s < SETTLE_MS.length; s += 1) setTimeout(observe, SETTLE_MS[s]);
  }

  globalThis[READER] = settle;

  var api = typeof chrome !== "undefined" ? chrome : null;
  if (api && api.runtime && api.runtime.onMessage) {
    api.runtime.onMessage.addListener(function (message) {
      if (message && message.type === "fb-helper-observe") observe();
    });
  }

  settle();
  setInterval(observe, RECHECK_MS);
})();

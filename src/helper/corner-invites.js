/* corner-invites.js — observe-only reader for the invites page.
 *
 * Runs only on the invites pages (see manifest.json) and re-checks the page
 * itself before touching the DOM. It reads the invitations the user already
 * has open, builds a cleaned report, and hands it to the background worker.
 * It never answers an invitation, never presses anything, never posts, never
 * messages, and never posts to the network itself.
 *
 * The page (seen 2026-11-20, with no invitations on it) is drawn like the
 * events dashboard: no <time>, headings or articles. So a card is read as
 * the dashboard's is (page-text.js), and the inviter is taken from a line
 * like "Sam Rivera invited you". A card with an invitation in it has not
 * been seen yet.
 */
(function () {
  "use strict";

  var CORNER = "invites";
  var ALLOWED_PATHS = [
    "/events/invites",
    "/events/invites/",
    "/events/invitation_box",
    "/events/invitation_box/",
  ];
  // Keep in sync with CHECK_INTERVAL_MIN in background.js (one rhythm).
  var RECHECK_MS = 240 * 60 * 1000;
  var SETTLE_MS = [3000, 10000, 30000];
  var MAX_ITEMS = 200;
  var NAME_MAX = 200;
  var INVITED_BY = /^(.{1,200}?)\s+invited you\b/i;
  var INVITED_BY_AFTER = /^invited by\s+(.{1,200})$/i;
  var READER = "__ozbReader_invites";

  // Loaded again in the same tab: read again, never run twice.
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

  /** The inviter's name from a card's lines, or "". */
  function inviter(lines) {
    for (var i = 0; i < lines.length; i += 1) {
      var match = INVITED_BY.exec(lines[i]) || INVITED_BY_AFTER.exec(lines[i]);
      if (match) return text.clean(match[1], NAME_MAX);
    }
    return "";
  }

  /** The invite items in `root`, read against `today`. */
  function readInvites(root, today) {
    var cards = text.eventCards(root, today, MAX_ITEMS);
    var items = [];
    for (var i = 0; i < cards.length; i += 1) {
      items.push({
        kind: "invite",
        id: cards[i].id,
        title: cards[i].fields.title,
        start_date: cards[i].fields.date,
        start_time: cards[i].fields.time,
        url: cards[i].url,
        inviter: inviter(cards[i].lines),
      });
    }
    return items;
  }

  function observe() {
    if (!isAllowed(location.href)) return;
    var root = document.querySelector('[role="main"]') || document;
    var items = readInvites(root, new Date());
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

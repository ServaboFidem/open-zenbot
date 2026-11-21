/* corner-birthdays.js — observe-only reader for the birthdays page.
 *
 * Runs only on the birthdays page (see manifest.json) and re-checks that
 * page itself before touching the DOM. It reads the birthday list the user
 * already has open, builds a cleaned report, and hands it to the background
 * worker. It never writes a greeting, never presses anything, never posts,
 * never messages, and never posts to the network itself.
 *
 * What the page holds (seen 2026-11-20; /birthdays/ now lands on
 * /events/birthdays/): headings "Today's birthdays", "Recent birthdays" and
 * "Upcoming birthdays", each followed by one heading a person, the name
 * inside the link to their profile. A person's block then has a date line
 * ("November 23" or "November 23, 1992"), except under Today's, where it has
 * none. Month headings ("December") close the list: under them Facebook
 * shows only "A, B and 35 others", which is not read.
 */
(function () {
  "use strict";

  var CORNER = "birthdays";
  var ALLOWED_PATHS = ["/birthdays", "/birthdays/", "/events/birthdays", "/events/birthdays/"];
  // Keep in sync with CHECK_INTERVAL_MIN in background.js (one rhythm).
  var RECHECK_MS = 240 * 60 * 1000;
  var SETTLE_MS = [3000, 10000, 30000];
  var MAX_ITEMS = 200;
  var NAME_MAX = 200;
  var DATE_LINES = 4;
  var HEADINGS = 'h2, [role="heading"]';
  var SECTION = /birthdays$/i;
  var TODAY_SECTION = /^today/i;
  var MONTH_ONLY = /^(january|february|march|april|may|june|july|august|september|october|november|december)$/i;
  var PROFILE_ID = /^[0-9]{1,30}$/;
  var PROFILE_SLUG = /^\/([A-Za-z0-9.]{1,80})\/?$/;
  var READER = "__ozbReader_birthdays";

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

  /** A profile link with Facebook's tracking dropped, or "" when `href` is no profile. */
  function profileUrl(href) {
    var parsed;
    try {
      parsed = new URL(href || "", "https://www.facebook.com");
    } catch (err) {
      return "";
    }
    if (parsed.protocol !== "https:" || parsed.hostname !== "www.facebook.com") return "";
    if (parsed.username || parsed.password || parsed.port) return "";
    if (parsed.pathname === "/profile.php") {
      var id = parsed.searchParams.get("id") || "";
      return PROFILE_ID.test(id) ? "https://www.facebook.com/profile.php?id=" + id : "";
    }
    var slug = PROFILE_SLUG.exec(parsed.pathname);
    return slug ? "https://www.facebook.com/" + slug[1] : "";
  }

  function onlyHeading(node) {
    return node.querySelectorAll(HEADINGS).length === 1;
  }

  /** The birthday items in `root`, read against `today`. */
  function readBirthdays(root, today) {
    var items = [];
    var seen = {};
    var section = "";
    var heads = root.querySelectorAll(HEADINGS);
    for (var i = 0; i < heads.length && items.length < MAX_ITEMS; i += 1) {
      var name = text.clean(heads[i].textContent || "", NAME_MAX);
      if (!name) continue;
      if (SECTION.test(name)) {
        section = TODAY_SECTION.test(name) ? "today" : "dated";
        continue;
      }
      if (MONTH_ONLY.test(name)) {
        section = "";
        continue;
      }
      if (!section) continue;
      var card = text.widest(heads[i], root, onlyHeading);
      var lines = text.linesOf(card);
      var label = "";
      for (var j = 0; j < lines.length && j < DATE_LINES && !label; j += 1) {
        label = text.monthDay(lines[j]);
      }
      if (!label && section === "today") label = text.monthDayOf(today);
      if (!label) continue;
      var link = heads[i].closest("a[href]") || card.querySelector("a[href]");
      var url = link ? profileUrl(link.getAttribute("href")) : "";
      var key = url || "n:" + name + "\n" + label;
      if (seen[key]) continue;
      seen[key] = true;
      items.push({ kind: "birthday", name: name, profile_url: url, date_text: label });
    }
    return items;
  }

  function observe() {
    if (!isAllowed(location.href)) return;
    var root = document.querySelector('[role="main"]') || document;
    var items = readBirthdays(root, new Date());
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

/* corner-events.js — observe-only reader for the events dashboard.
 *
 * Runs only on https://www.facebook.com/events (see manifest.json) and
 * re-checks that page itself before touching the DOM. It reads the listing
 * the user already has open, builds a cleaned report, and hands it to the
 * background worker. It never presses anything, never answers an invite,
 * never posts, never messages, and never posts to the network itself.
 */
(function () {
  "use strict";

  var CORNER = "events_dashboard";
  var ALLOWED_PATHS = ["/events", "/events/"];
  // Keep in sync with CHECK_INTERVAL_MIN in background.js (one rhythm).
  var RECHECK_MS = 240 * 60 * 1000;
  var MAX_ITEMS = 200;
  var TITLE_MAX = 300;
  var ID_PATTERN = /\/events\/([0-9]{1,30})(?:[/?#]|$)/;

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

  function clean(value, limit) {
    if (typeof value !== "string") return "";
    var out = "";
    for (var i = 0; i < value.length; i += 1) {
      var code = value.charCodeAt(i);
      if (code === 0x09 || code === 0x0a || code === 0x0d) out += " ";
      else if (code >= 0x20 && code !== 0x7f) out += value[i];
    }
    return out.trim().slice(0, limit).trim();
  }

  function keepUrl(href) {
    if (typeof href !== "string") return "";
    var absolute;
    try {
      absolute = new URL(href, location.origin).href;
    } catch (err) {
      return "";
    }
    if (absolute.length > 1000) return "";
    if (absolute.indexOf("https://www.facebook.com/events/") !== 0) return "";
    return absolute;
  }

  function cardText(root) {
    var heading = root.querySelector(
      '[role="heading"], h1, h2, h3, h4, [data-ad-rendering-role="event_name"]',
    );
    if (heading && heading.textContent) return heading.textContent;
    var labelled = root.getAttribute("aria-label");
    return labelled || root.textContent || "";
  }

  function cardDate(root) {
    var time = root.querySelector("time[datetime]");
    if (time) return time.getAttribute("datetime") || "";
    return "";
  }

  function splitDateTime(value) {
    var match = /^(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2})/.exec(value || "");
    if (!match) return { date: "", time: "" };
    return { date: match[1], time: match[2] };
  }

  function observe() {
    if (!isAllowed(location.href)) return;
    var items = [];
    var seen = {};
    var anchors = document.querySelectorAll('a[href*="/events/"]');
    for (var i = 0; i < anchors.length && items.length < MAX_ITEMS; i += 1) {
      var href = anchors[i].getAttribute("href") || "";
      var idMatch = ID_PATTERN.exec(href);
      if (!idMatch || seen[idMatch[1]]) continue;
      var root = anchors[i].closest("article, div[role='article'], li") || anchors[i];
      var title = clean(cardText(root), TITLE_MAX);
      if (!title) continue;
      var when = splitDateTime(cardDate(root));
      if (!when.date) continue;
      var url = keepUrl(href);
      if (!url) continue;
      seen[idMatch[1]] = true;
      items.push({
        kind: "event",
        id: idMatch[1],
        title: title,
        start_date: when.date,
        start_time: when.time,
        url: url,
        venue: "",
      });
    }
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

  var api = typeof chrome !== "undefined" ? chrome : null;
  if (api && api.runtime && api.runtime.onMessage) {
    api.runtime.onMessage.addListener(function (message) {
      if (message && message.type === "fb-helper-observe") observe();
    });
  }

  observe();
  setInterval(observe, RECHECK_MS);
})();

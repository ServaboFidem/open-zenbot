/* corner-birthdays.js — observe-only reader for the birthdays page.
 *
 * Runs only on https://www.facebook.com/birthdays (see manifest.json) and
 * re-checks that page itself before touching the DOM. It reads the birthday
 * list the user already has open, builds a cleaned report, and hands it to
 * the background worker. It never writes a greeting, never presses anything,
 * never posts, never messages, and never posts to the network itself.
 */
(function () {
  "use strict";

  var CORNER = "birthdays";
  var ALLOWED_PATHS = ["/birthdays", "/birthdays/"];
  // Keep in sync with CHECK_INTERVAL_MIN in background.js (one rhythm).
  var RECHECK_MS = 240 * 60 * 1000;
  var MAX_ITEMS = 200;
  var NAME_MAX = 200;
  var LABEL_MAX = 64;
  var PROFILE_ID = /[?&]id=([0-9]{1,30})(?:[&#]|$)/;

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
    if (absolute.indexOf("https://www.facebook.com/") !== 0) return "";
    return absolute;
  }

  function profileKey(href, name) {
    var idMatch = PROFILE_ID.exec(href || "");
    if (idMatch) return "id:" + idMatch[1];
    return "n:" + name;
  }

  function dateText(root) {
    var time = root.querySelector("time");
    if (time && time.textContent) return clean(time.textContent, LABEL_MAX);
    var heading = root.querySelector('[role="heading"], h1, h2, h3, h4');
    if (heading && heading.nextElementSibling && heading.nextElementSibling.textContent) {
      return clean(heading.nextElementSibling.textContent, LABEL_MAX);
    }
    return "";
  }

  function observe() {
    if (!isAllowed(location.href)) return;
    var items = [];
    var seen = {};
    var anchors = document.querySelectorAll(
      'a[href^="/profile.php"], a[href^="https://www.facebook.com/profile.php"], a[href^="/"][data-hovercard]',
    );
    for (var i = 0; i < anchors.length && items.length < MAX_ITEMS; i += 1) {
      var name = clean(anchors[i].textContent || "", NAME_MAX);
      if (!name) continue;
      var url = keepUrl(anchors[i].getAttribute("href") || "");
      if (!url) continue;
      var key = profileKey(url, name);
      if (seen[key]) continue;
      var root = anchors[i].closest("article, div[role='article'], li") || anchors[i];
      var label = dateText(root);
      if (!label) continue;
      seen[key] = true;
      items.push({
        kind: "birthday",
        name: name,
        profile_url: url,
        date_text: label,
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

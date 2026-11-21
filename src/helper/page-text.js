/* page-text.js — shared text readers for the corner scripts.
 *
 * Loaded before each corner reader (see manifest.json and READER_FILES in
 * background.js), in the extension's own isolated world, so the page cannot
 * see or change it. It only reads text and builds values: it touches no
 * network, presses nothing and reports nothing itself.
 *
 * What Facebook's pages hold (seen 2026-11-20): no <time> elements; dates are
 * English words on their own line ("Tomorrow at 10 AM", "Sat, Nov 28 at
 * 12 PM", "Sat, Feb 13, 2027 at 6:30 PM", "Fri, Dec 11 - Dec 12", "Happening
 * now"; birthdays "November 23, 1992"). Another language is not read.
 */
(function () {
  "use strict";

  if (typeof globalThis.OzbPageText === "object" && globalThis.OzbPageText) return;

  var MAX_CLIMB = 15;
  var DATE_LINES = 3;
  var TITLE_MAX = 300;
  var ID_PATTERN = /\/events\/([0-9]{1,30})(?:[/?#]|$)/;

  var MONTHS = {
    jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6,
    jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12,
  };
  var MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December"];
  var WEEKDAYS = { sun: 0, mon: 1, tue: 2, wed: 3, thu: 4, fri: 5, sat: 6 };
  var TIME = "(\\d{1,2})(?::(\\d{2}))?\\s*([ap])\\.?\\s*m\\.?";
  var CLOCK24 = "(\\d{1,2}):(\\d{2})";
  var NOW = /^happening now\b/i;
  var RELATIVE = new RegExp("^(today|tomorrow)(?:\\s+at\\s+(?:" + TIME + "|" + CLOCK24 + "))?", "i");
  var WEEKDAY_ONLY = new RegExp(
    "^(?:this\\s+)?(sun|mon|tue|wed|thu|fri|sat)[a-z]*\\s+at\\s+(?:" + TIME + "|" + CLOCK24 + ")",
    "i",
  );
  var DATED = new RegExp(
    "^(?:(sun|mon|tue|wed|thu|fri|sat)[a-z]*\\.?,?\\s+)?" +
      "(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\\.?\\s+(\\d{1,2})" +
      "(?:,?\\s+(\\d{4}))?(?=$|[\\s,\\-\\u2013\\u2014])(?:\\s+at\\s+(?:" + TIME + "|" + CLOCK24 + "))?",
    "i",
  );
  // A birthday line: the month and day, maybe the year of birth, and nothing else.
  var MONTH_DAY = /^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:,\s*\d{4})?$/i;
  var SOCIAL = /\b(interested|going)\b|^(share|invite|more)$/i;

  /** Text on one line: tabs, line ends and the odd spaces Facebook uses made spaces, controls dropped, capped. */
  function clean(value, limit) {
    if (typeof value !== "string") return "";
    var out = "";
    for (var i = 0; i < value.length; i += 1) {
      var code = value.charCodeAt(i);
      if (code === 0x09 || code === 0x0a || code === 0x0d || code === 0xa0 ||
          code === 0x2009 || code === 0x202f) out += " ";
      else if (code >= 0x20 && code !== 0x7f) out += value[i];
    }
    return out.split(/\s+/).join(" ").trim().slice(0, limit).trim();
  }

  function pad(n) {
    return (n < 10 ? "0" : "") + n;
  }

  function dayKey(d) {
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }

  /** A local Date for year, month (1-12), day, or null when it is not a real day. */
  function realDay(year, month, day) {
    var d = new Date(year, month - 1, day);
    if (d.getFullYear() !== year || d.getMonth() !== month - 1 || d.getDate() !== day) return null;
    return d;
  }

  /** "HH:MM" from the time groups at `at` (12-hour then 24-hour), "" when there is none, null when bad. */
  function clockFrom(m, at) {
    if (m[at] !== undefined) {
      var hour = Number(m[at]);
      var minute = m[at + 1] !== undefined ? Number(m[at + 1]) : 0;
      if (hour < 1 || hour > 12 || minute > 59) return null;
      var pm = m[at + 2].toLowerCase() === "p";
      if (hour === 12) hour = pm ? 12 : 0;
      else if (pm) hour += 12;
      return pad(hour) + ":" + pad(minute);
    }
    if (m[at + 3] !== undefined) {
      var h24 = Number(m[at + 3]);
      var m24 = Number(m[at + 4]);
      if (h24 > 23 || m24 > 59) return null;
      return pad(h24) + ":" + pad(m24);
    }
    return "";
  }

  /**
   * `{date: "YYYY-MM-DD", time: "HH:MM" | ""}` for a card's date line, read
   * against `today` (a local Date), or null when the line is not a start date
   * this reader understands. Only the start is read; an end ("- Dec 12",
   * "- 3 PM") is ignored. "Happening now" is today with no time. With no
   * year, the year is the one whose date falls on the weekday the line names,
   * else this year unless that day is over a month gone, then next year.
   */
  function parseWhen(line, today) {
    var text = clean(line, 200);
    var base = new Date(today.getFullYear(), today.getMonth(), today.getDate());
    if (NOW.test(text)) return { date: dayKey(base), time: "" };
    var m = RELATIVE.exec(text);
    var time;
    if (m) {
      time = clockFrom(m, 2);
      if (time === null) return null;
      if (m[1].toLowerCase() === "tomorrow") base.setDate(base.getDate() + 1);
      return { date: dayKey(base), time: time };
    }
    m = WEEKDAY_ONLY.exec(text);
    if (m) {
      time = clockFrom(m, 2);
      if (time === null) return null;
      var ahead = (WEEKDAYS[m[1].toLowerCase()] - base.getDay() + 7) % 7;
      base.setDate(base.getDate() + ahead);
      return { date: dayKey(base), time: time };
    }
    m = DATED.exec(text);
    if (!m) return null;
    time = clockFrom(m, 5);
    if (time === null) return null;
    var month = MONTHS[m[2].toLowerCase()];
    var day = Number(m[3]);
    var found = null;
    if (m[4] !== undefined) {
      found = realDay(Number(m[4]), month, day);
    } else {
      var weekday = m[1] !== undefined ? WEEKDAYS[m[1].toLowerCase()] : -1;
      var year = base.getFullYear();
      var candidates = [realDay(year, month, day), realDay(year + 1, month, day)];
      for (var i = 0; i < candidates.length && weekday !== -1; i += 1) {
        if (candidates[i] && candidates[i].getDay() === weekday) {
          found = candidates[i];
          break;
        }
      }
      if (!found) {
        found = candidates[0];
        var monthAgo = new Date(base.getFullYear(), base.getMonth(), base.getDate() - 31);
        if (!found || found < monthAgo) found = candidates[1];
      }
    }
    if (!found) return null;
    return { date: dayKey(found), time: time };
  }

  /** "November 23" for a birthday line ("November 23", "Nov 23, 1992"), or "" when it is not one. */
  function monthDay(line) {
    var m = MONTH_DAY.exec(clean(line, 64));
    if (!m) return "";
    var month = MONTHS[m[1].toLowerCase()];
    var day = Number(m[2]);
    if (!realDay(2000, month, day)) return "";
    return MONTH_NAMES[month - 1] + " " + day;
  }

  /** "November 20" for the local Date `d`. */
  function monthDayOf(d) {
    return MONTH_NAMES[d.getMonth()] + " " + d.getDate();
  }

  /**
   * The fields of one event card from its visible text lines: the first of
   * the opening lines that is a date, the title after it and, when the next
   * line is not an "interested" or button line, the venue. Null when there is
   * no date or no title.
   */
  function cardFields(lines, today) {
    for (var i = 0; i < lines.length && i < DATE_LINES; i += 1) {
      var when = parseWhen(lines[i], today);
      if (!when) continue;
      var title = clean(lines[i + 1] || "", TITLE_MAX);
      if (!title || SOCIAL.test(title)) return null;
      var venue = clean(lines[i + 2] || "", TITLE_MAX);
      if (SOCIAL.test(venue)) venue = "";
      return { date: when.date, time: when.time, title: title, venue: venue };
    }
    return null;
  }

  function eventIdOf(anchor) {
    var match = ID_PATTERN.exec(anchor.getAttribute("href") || "");
    return match ? match[1] : "";
  }

  /** Whether every event link inside `el` points to event `id`. */
  function onlyEvent(el, id) {
    var anchors = el.querySelectorAll('a[href*="/events/"]');
    for (var i = 0; i < anchors.length; i += 1) {
      var other = eventIdOf(anchors[i]);
      if (other && other !== id) return false;
    }
    return true;
  }

  /** The widest block holding `start`, below `root`, for which `fits(block)` holds. */
  function widest(start, root, fits) {
    var card = start;
    var node = start.parentElement;
    for (var depth = 0; node && node !== root && depth < MAX_CLIMB; depth += 1) {
      if (!fits(node)) break;
      card = node;
      node = node.parentElement;
    }
    return card;
  }

  /** The widest block around `anchor`, below `root`, that links to no other event. */
  function eventCard(anchor, id, root) {
    return widest(anchor, root, function (node) {
      return onlyEvent(node, id);
    });
  }

  /** The visible text lines of `el`, each cleaned, empty ones dropped. */
  function linesOf(el) {
    var text = typeof el.innerText === "string" ? el.innerText : el.textContent || "";
    var out = [];
    var parts = text.split(/\n+/);
    for (var i = 0; i < parts.length; i += 1) {
      var line = clean(parts[i], 1000);
      if (line) out.push(line);
    }
    return out;
  }

  /**
   * The event cards in `root`, read against `today`: `{id, url, lines,
   * fields}` for each event with a readable card, in page order, at most
   * `max`. The url is the event's own page with no query.
   */
  function eventCards(root, today, max) {
    var out = [];
    var seen = {};
    var anchors = root.querySelectorAll('a[href*="/events/"]');
    for (var i = 0; i < anchors.length && out.length < max; i += 1) {
      var id = eventIdOf(anchors[i]);
      if (!id || seen[id]) continue;
      var lines = linesOf(eventCard(anchors[i], id, root));
      var fields = cardFields(lines, today);
      if (!fields) continue;
      seen[id] = true;
      out.push({ id: id, url: "https://www.facebook.com/events/" + id + "/", lines: lines, fields: fields });
    }
    return out;
  }

  globalThis.OzbPageText = {
    TITLE_MAX: TITLE_MAX,
    clean: clean,
    parseWhen: parseWhen,
    monthDay: monthDay,
    monthDayOf: monthDayOf,
    cardFields: cardFields,
    widest: widest,
    linesOf: linesOf,
    eventCards: eventCards,
  };
})();

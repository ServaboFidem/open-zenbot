// Payload schema the intake slice consumes.
//
// A report is one observation of one named corner. The intake route owns the
// mapping to pool rows; its contract is this shape and nothing else:
// unknown keys are refused so a page change cannot smuggle fields through.
// Text is already cleaned and capped by the readers (see clean.js); the
// schema re-checks lengths so an oversized value fails closed here too.

import { CORNER_IDS, cornerForUrl } from "./corners.js";
import { LABEL_MAX, NAME_MAX, TITLE_MAX } from "./clean.js";

export const HELPER_ID = "fb-events-v1";
export const MAX_ITEMS = 200;

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;
const CLOCK = /^(?:[01][0-9]|2[0-3]):[0-5][0-9]$/;
const DIGITS = /^[0-9]{1,30}$/;
// A time zone name by shape (the harness's own check): the browser's zone, sent with every report.
export const ZONE = /^[A-Za-z0-9_+\-/]{1,64}$/;

/** The browser's own time zone, or "" when it gives none of the expected shape. */
export function machineZone() {
  try {
    const zone = new Intl.DateTimeFormat().resolvedOptions().timeZone;
    return typeof zone === "string" && ZONE.test(zone) ? zone : "";
  } catch {
    return "";
  }
}

function isRealDate(value) {
  if (!ISO_DATE.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  return (
    date.getUTCFullYear() === year &&
    date.getUTCMonth() === month - 1 &&
    date.getUTCDate() === day
  );
}

function fail(why) {
  throw new Error(`bad-report: ${why}`);
}

function checkString(value, name, max, { allowEmpty = false } = {}) {
  if (typeof value !== "string") fail(`${name} is not a string`);
  if (!allowEmpty && value.length === 0) fail(`${name} is empty`);
  if (value.length > max) fail(`${name} is too long`);
  if (value !== value.trim()) fail(`${name} is not trimmed`);
}

function checkLink(value, name) {
  checkString(value, name, 1000);
  if (!value.startsWith("https://www.facebook.com/")) {
    fail(`${name} is not a facebook link`);
  }
}

function checkEvent(item) {
  const keys = new Set([
    "kind",
    "id",
    "title",
    "start_date",
    "start_time",
    "url",
    "venue",
  ]);
  for (const key of Object.keys(item)) {
    if (!keys.has(key)) fail(`unknown event key ${key}`);
  }
  if (typeof item.id !== "string" || !DIGITS.test(item.id)) {
    fail("event id is not digits");
  }
  checkString(item.title, "event title", TITLE_MAX);
  if (!isRealDate(item.start_date)) fail("event start_date is not a date");
  if (item.start_time !== "" && !CLOCK.test(item.start_time)) {
    fail("event start_time is not a clock time");
  }
  checkLink(item.url, "event url");
  checkString(item.venue, "event venue", TITLE_MAX, { allowEmpty: true });
}

function checkInvite(item) {
  const keys = new Set([
    "kind",
    "id",
    "title",
    "start_date",
    "start_time",
    "url",
    "inviter",
  ]);
  for (const key of Object.keys(item)) {
    if (!keys.has(key)) fail(`unknown invite key ${key}`);
  }
  if (typeof item.id !== "string" || !DIGITS.test(item.id)) {
    fail("invite id is not digits");
  }
  checkString(item.title, "invite title", TITLE_MAX);
  if (!isRealDate(item.start_date)) fail("invite start_date is not a date");
  if (item.start_time !== "" && !CLOCK.test(item.start_time)) {
    fail("invite start_time is not a clock time");
  }
  checkLink(item.url, "invite url");
  checkString(item.inviter, "invite inviter", NAME_MAX, { allowEmpty: true });
}

function checkBirthday(item) {
  const keys = new Set(["kind", "name", "profile_url", "date_text"]);
  for (const key of Object.keys(item)) {
    if (!keys.has(key)) fail(`unknown birthday key ${key}`);
  }
  checkString(item.name, "birthday name", NAME_MAX);
  if (item.profile_url !== "") checkLink(item.profile_url, "birthday link");
  checkString(item.date_text, "birthday date_text", LABEL_MAX);
}

const CHECKERS = {
  events_dashboard: { kinds: { event: checkEvent } },
  invites: { kinds: { invite: checkInvite } },
  birthdays: { kinds: { birthday: checkBirthday } },
};

/**
 * Throw when `report` is not a well-formed helper report; return true when
 * it is. Never logs or keeps the text: the reason names the shape only.
 */
export function validateReport(report) {
  if (typeof report !== "object" || report === null || Array.isArray(report)) {
    fail("not an object");
  }
  const topKeys = new Set(["helper", "observed_at", "corner", "url", "items", "timezone"]);
  for (const key of Object.keys(report)) {
    if (!topKeys.has(key)) fail(`unknown key ${key}`);
  }
  if (report.helper !== HELPER_ID) fail("unknown helper");
  if (typeof report.observed_at !== "string") fail("observed_at is not a string");
  if (Number.isNaN(Date.parse(report.observed_at))) {
    fail("observed_at is not a time");
  }
  if (report.timezone !== undefined && !(typeof report.timezone === "string" && ZONE.test(report.timezone))) {
    fail("timezone is not a zone name");
  }
  if (!CORNER_IDS.includes(report.corner)) fail("unknown corner");
  if (typeof report.url !== "string") fail("url is not a string");
  if (cornerForUrl(report.url) !== report.corner) {
    fail("url is not that corner");
  }
  if (!Array.isArray(report.items)) fail("items is not a list");
  if (report.items.length > MAX_ITEMS) fail("too many items");
  const checker = CHECKERS[report.corner];
  for (const item of report.items) {
    if (typeof item !== "object" || item === null || Array.isArray(item)) {
      fail("item is not an object");
    }
    const check = checker.kinds[item.kind];
    if (!check) fail(`wrong kind for ${report.corner}`);
    check(item);
  }
  return true;
}

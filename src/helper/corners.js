// Named corners the helper may read (v1).
//
// Chrome-only to start (MV3). v1 surfaces are the events dashboard, the
// invites page and the birthdays page. There is no News Feed scan: the feed
// root and every other path are refused here, and the readers re-check the
// same allowlist before touching the DOM.
//
// Discipline copied from src/events/eventbrite.py: the place is checked
// strictly (exact host, exact path), and only rows with the expected shape
// are kept downstream.

export const CORNERS = [
  { id: "events_dashboard", paths: ["/events", "/events/"] },
  {
    id: "invites",
    paths: [
      "/events/invites",
      "/events/invites/",
      "/events/invitation_box",
      "/events/invitation_box/",
    ],
  },
  { id: "birthdays", paths: ["/birthdays", "/birthdays/"] },
];

export const CORNER_IDS = CORNERS.map((corner) => corner.id);

const HOST = "www.facebook.com";

/** The corner id for `value`, or "" when it is not a named corner. */
export function cornerForUrl(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return "";
  }
  if (parsed.protocol !== "https:") return "";
  if (parsed.hostname !== HOST) return "";
  if (parsed.username || parsed.password || parsed.port) return "";
  for (const corner of CORNERS) {
    if (corner.paths.includes(parsed.pathname)) return corner.id;
  }
  return "";
}

/** Whether `value` is a page the helper may read. */
export function isAllowedUrl(value) {
  return cornerForUrl(value) !== "";
}

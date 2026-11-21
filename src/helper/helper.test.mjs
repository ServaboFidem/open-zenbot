// Slice tests for the Chrome helper scaffold (ext-scaffold).
//
// Run: node --test src/helper/helper.test.mjs
// Covers the proof gate: static manifest checks pass, the helper reads only
// named corners, posts only to 127.0.0.1, and takes no action as the user.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

import { CORNER_IDS, cornerForUrl, isAllowedUrl } from "./corners.js";
import { TITLE_MAX, clean, keepFacebookUrl } from "./clean.js";
import { HELPER_ID, MAX_ITEMS, validateReport } from "./schema.js";
import {
  HARNESS_PORT,
  SEND_GAP_MS,
  buildIntakeUrl,
  postReport,
  resetSendClock,
} from "./sender.js";
import {
  CHECK_INTERVAL_MIN,
  READER_FILES,
  RECHECK_MS,
  SHARED_FILE,
  TAB_PATTERNS,
  digestReport,
  handleMessage,
  readerFiles,
  seenDigests,
  startReader,
  sweepSeen,
} from "./background.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = (name) => fs.readFileSync(path.join(HERE, name), "utf8");
const manifest = JSON.parse(read("manifest.json"));

// Source with comments and string literals removed, so the no-action scans
// below only see code, never prose or data.
function codeOnly(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, " ")
    .replace(/(^|[^\S\n])\/\/[^\n]*/gm, "$1 ")
    .replace(
      /'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`/g,
      '""',
    );
}

function goodReport(corner, url, items) {
  return {
    helper: HELPER_ID,
    observed_at: "2026-11-20T12:00:00.000Z",
    corner,
    url,
    items,
  };
}

const goodEvent = {
  kind: "event",
  id: "123456789",
  title: "Neighborhood cleanup",
  start_date: "2026-11-29",
  start_time: "10:00",
  url: "https://www.facebook.com/events/123456789/",
  venue: "",
};

const goodInvite = {
  kind: "invite",
  id: "987654321",
  title: "Housewarming",
  start_date: "2026-12-05",
  start_time: "",
  url: "https://www.facebook.com/events/987654321/",
  inviter: "Sam Rivera",
};

const goodBirthday = {
  kind: "birthday",
  name: "Sam Rivera",
  profile_url: "https://www.facebook.com/profile.php?id=100042",
  date_text: "November 21",
};

// ---- manifest ----------------------------------------------------------------------------------

test("manifest is a Chrome MV3 observe-only scaffold", () => {
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.background.service_worker, "background.js");
  assert.equal(manifest.background.type, "module");
  assert.ok(manifest.minimum_chrome_version, "pins a minimum Chrome version");
  assert.match(
    manifest.description,
    /observe-only/i,
    "says what it will not do",
  );
});

test("manifest takes no dangerous permissions", () => {
  for (const permission of manifest.permissions || []) {
    assert.ok(
      ["alarms", "storage", "scripting"].includes(permission),
      `unexpected permission ${permission}`,
    );
  }
  const forbidden = [
    "tabs",
    "cookies",
    "history",
    "webRequest",
    "webNavigation",
    "debugger",
    "proxy",
    "nativeMessaging",
  ];
  for (const permission of forbidden) {
    assert.ok(
      !(manifest.permissions || []).includes(permission),
      `must not take ${permission}`,
    );
  }
});

test("manifest posts only to the harness on 127.0.0.1 and reaches only the corners' pages", () => {
  const entries = manifest.host_permissions || [];
  const loopback = entries.filter((entry) => entry.startsWith("http://"));
  assert.ok(loopback.length > 0, "declares where it may post");
  for (const entry of loopback) {
    assert.match(
      entry,
      /^http:\/\/127\.0\.0\.1(:\d+)?\/.*$/,
      `must post only to 127.0.0.1, found ${entry}`,
    );
  }
  // the Facebook entries let the background start a corner's reader, and are exactly the tabs it looks at
  const facebook = entries.filter((entry) => !entry.startsWith("http://"));
  assert.deepEqual(facebook, TAB_PATTERNS);
  for (const entry of facebook) {
    assert.ok(
      entry === "https://www.facebook.com/events*" || entry === "https://www.facebook.com/birthdays*",
      `only the corners' pages, found ${entry}`,
    );
  }
});

test("content scripts match only the three named corners", () => {
  assert.ok((manifest.content_scripts || []).length >= 3, "one entry a corner");
  const feedish = [
    "/home",
    "/watch",
    "/groups",
    "/marketplace",
    "/messages",
    "/notifications",
    "/reel",
    "/stories",
    "/friends",
    "/search",
    "?sk=",
  ];
  let seen = 0;
  for (const script of manifest.content_scripts) {
    for (const match of script.matches) {
      assert.ok(
        match.startsWith("https://www.facebook.com/"),
        `chrome-only to start, found ${match}`,
      );
      const afterHost = match.slice("https://www.facebook.com/".length);
      assert.ok(
        afterHost.startsWith("events") || afterHost.startsWith("birthdays"),
        `named corners only, found ${match}`,
      );
      for (const surface of feedish) {
        assert.ok(!match.includes(surface), `no feed surface ${match}`);
      }
      assert.notEqual(match, "https://www.facebook.com/*", "never the root");
      seen += 1;
    }
    assert.equal(script.run_at, "document_idle");
    assert.equal(script.js.length, 2, "the shared text readers, then one corner reader");
    assert.equal(script.js[0], SHARED_FILE);
    assert.ok(Object.values(READER_FILES).includes(script.js[1]), `a corner reader: ${script.js[1]}`);
    for (const file of script.js) {
      assert.ok(
        fs.existsSync(path.join(HERE, file)),
        `content script exists: ${file}`,
      );
    }
  }
  assert.ok(seen >= 9, `every corner pattern declared, saw ${seen}`);
});

/** Whether Chrome's match pattern `pattern` (only "*" is a wildcard) matches `url`. */
function patternMatches(pattern, url) {
  const parts = pattern.split("*").map((part) => part.split("").map((c) => (/[\w]/.test(c) ? c : "\\" + c)).join(""));
  return new RegExp("^" + parts.join(".*") + "$").test(url);
}

test("content scripts reach the corners, with a query too, and never an event page or the feed", () => {
  const reaches = (url) =>
    manifest.content_scripts.filter((script) => script.matches.some((m) => patternMatches(m, url)));
  for (const [url, file] of [
    ["https://www.facebook.com/events/", "corner-events.js"],
    ["https://www.facebook.com/events/?ref=bookmarks", "corner-events.js"],
    ["https://www.facebook.com/events?ref=bookmarks", "corner-events.js"],
    ["https://www.facebook.com/events/invites/?ref=x", "corner-invites.js"],
    ["https://www.facebook.com/birthdays/", "corner-birthdays.js"],
    ["https://www.facebook.com/events/birthdays/?acontext=x", "corner-birthdays.js"],
  ]) {
    const found = reaches(url);
    assert.equal(found.length, 1, `one reader for ${url}`);
    assert.equal(found[0].js[1], file, url);
  }
  for (const url of [
    "https://www.facebook.com/",
    "https://www.facebook.com/events/123456789/",
    "https://www.facebook.com/events/123456789/?ref=x",
    "https://www.facebook.com/events/discovery/",
    "https://www.facebook.com/eventsx",
  ]) {
    assert.equal(reaches(url).length, 0, `no reader on ${url}`);
  }
});

test("manifest loads no remote code", () => {
  const csp = manifest.content_security_policy.extension_pages;
  assert.match(csp, /script-src 'self'/);
  assert.ok(!csp.includes("http"), "no remote source in CSP");
  assert.ok(!csp.includes("unsafe-"), "no unsafe source in CSP");
  assert.ok(!manifest.externally_connectable, "no page may call into it");
  const raw = read("manifest.json");
  assert.ok(
    !/http:\/\/(?!127\.0\.0\.1)/.test(raw),
    "plain http only to 127.0.0.1",
  );
  const remote = raw.match(/https:\/\/[^"\s]+/g) || [];
  for (const url of remote) {
    assert.ok(
      url.startsWith("https://www.facebook.com/"),
      `no remote code or data host, found ${url}`,
    );
  }
});

// ---- corners -----------------------------------------------------------------------------------

test("named corners are allowed, everything else is refused", () => {
  const allowed = [
    ["https://www.facebook.com/events", "events_dashboard"],
    ["https://www.facebook.com/events/", "events_dashboard"],
    ["https://www.facebook.com/events/?ref=notif", "events_dashboard"],
    ["https://www.facebook.com/events/invites/", "invites"],
    ["https://www.facebook.com/events/invites/?ref=x", "invites"],
    ["https://www.facebook.com/events/invitation_box/", "invites"],
    ["https://www.facebook.com/birthdays", "birthdays"],
    ["https://www.facebook.com/birthdays/", "birthdays"],
    ["https://www.facebook.com/birthdays/?ref=bookmarks", "birthdays"],
    ["https://www.facebook.com/events/birthdays/", "birthdays"],
    ["https://www.facebook.com/events/birthdays/?acontext=x", "birthdays"],
  ];
  for (const [url, corner] of allowed) {
    assert.equal(cornerForUrl(url), corner, url);
    assert.equal(isAllowedUrl(url), true, url);
  }
  const refused = [
    "https://www.facebook.com/",
    "https://www.facebook.com/?sk=nf",
    "https://www.facebook.com/home",
    "https://www.facebook.com/watch",
    "https://www.facebook.com/groups/1",
    "https://www.facebook.com/messages",
    "https://www.facebook.com/events/calendar",
    "https://www.facebook.com/events/invites/extra",
    "https://www.facebook.com/birthdays/today",
    "https://www.facebook.com/events/birthdays/today",
    "http://www.facebook.com/events/",
    "https://facebook.com/events/",
    "https://m.facebook.com/events/",
    "https://www.facebook.com.evil.example/events/",
    "https://user:pass@www.facebook.com/events/",
    "https://www.facebook.com:8080/events/",
    "not a url",
    "",
  ];
  for (const url of refused) {
    assert.equal(cornerForUrl(url), "", url);
    assert.equal(isAllowedUrl(url), false, url);
  }
  assert.deepEqual(CORNER_IDS, ["events_dashboard", "invites", "birthdays"]);
});

// ---- clean -------------------------------------------------------------------------------------

test("clean strips controls, folds breaks, caps length", () => {
  assert.equal(clean("  hello\tworld\nnext ", 100), "hello world next");
  assert.equal(clean("a\u0000b\u001bc", 100), "abc");
  assert.equal(clean("a\u2028b", 100), "a b");
  assert.equal(clean(42, 10), "");
  assert.equal(clean(null, 10), "");
  assert.equal(clean("x".repeat(500), TITLE_MAX).length, TITLE_MAX);
});

test("keepFacebookUrl keeps only expected facebook links", () => {
  assert.equal(
    keepFacebookUrl(
      "https://www.facebook.com/events/123/",
      "/events/",
    ),
    "https://www.facebook.com/events/123/",
  );
  const refused = [
    "http://www.facebook.com/events/123/",
    "https://facebook.com/events/123/",
    "https://m.facebook.com/events/123/",
    "https://www.facebook.com/profile.php?id=1",
    "https://evil.example/events/123/",
    "https://www.facebook.com/events/12 3/",
    "https://www.facebook.com/events/12\\3/",
    "https://user@www.facebook.com/events/1/",
    "https://www.facebook.com:8080/events/1/",
    "notaurl",
    "",
    42,
  ];
  for (const value of refused) {
    assert.equal(keepFacebookUrl(value, "/events/"), "", String(value));
  }
  assert.equal(
    keepFacebookUrl("https://www.facebook.com/events/" + "1".repeat(2000), "/events/"),
    "",
    "overlong links refused",
  );
});

// ---- schema ------------------------------------------------------------------------------------

test("schema accepts one good report per corner", () => {
  assert.equal(
    validateReport(
      goodReport("events_dashboard", "https://www.facebook.com/events/", [goodEvent]),
    ),
    true,
  );
  assert.equal(
    validateReport(
      goodReport("invites", "https://www.facebook.com/events/invites/", [goodInvite]),
    ),
    true,
  );
  assert.equal(
    validateReport(
      goodReport("birthdays", "https://www.facebook.com/birthdays/", [goodBirthday]),
    ),
    true,
  );
  assert.equal(
    validateReport(goodReport("birthdays", "https://www.facebook.com/birthdays/", [])),
    true,
    "an empty observation is still a report",
  );
});

test("schema refuses the wrong shape with a reason, never the text", () => {
  const cases = [
    [{}, "not an object is refused"],
    [
      { ...goodReport("birthdays", "https://www.facebook.com/birthdays/", []), helper: "other" },
      "unknown helper refused",
    ],
    [
      {
        ...goodReport("birthdays", "https://www.facebook.com/birthdays/", []),
        corner: "news_feed",
      },
      "unknown corner refused",
    ],
    [
      goodReport("birthdays", "https://www.facebook.com/events/", [goodBirthday]),
      "url that is not that corner refused",
    ],
    [
      goodReport("invites", "https://www.facebook.com/events/invites/", [goodEvent]),
      "wrong kind for the corner refused",
    ],
    [
      {
        ...goodReport("birthdays", "https://www.facebook.com/birthdays/", [goodBirthday]),
        observed_at: "sometime",
      },
      "bad time refused",
    ],
    [
      {
        ...goodReport("birthdays", "https://www.facebook.com/birthdays/", [goodBirthday]),
        extra: 1,
      },
      "unknown top-level key refused",
    ],
    [
      goodReport("birthdays", "https://www.facebook.com/birthdays/", [
        { ...goodBirthday, name: "" },
      ]),
      "empty name refused",
    ],
    [
      goodReport("birthdays", "https://www.facebook.com/birthdays/", [
        { ...goodBirthday, extra: 1 },
      ]),
      "unknown item key refused",
    ],
    [
      goodReport("events_dashboard", "https://www.facebook.com/events/", [
        { ...goodEvent, title: "t".repeat(TITLE_MAX + 1) },
      ]),
      "oversized title refused",
    ],
    [
      goodReport("events_dashboard", "https://www.facebook.com/events/", [
        { ...goodEvent, id: "abc" },
      ]),
      "non-digit id refused",
    ],
    [
      goodReport("events_dashboard", "https://www.facebook.com/events/", [
        { ...goodEvent, start_date: "2026-02-30" },
      ]),
      "impossible date refused",
    ],
  ];
  for (const [report, why] of cases) {
    assert.throws(() => validateReport(report), /bad-report: /, why);
  }
  const many = Array.from({ length: MAX_ITEMS + 1 }, (_, i) => ({
    ...goodEvent,
    id: String(100000 + i),
    url: `https://www.facebook.com/events/${100000 + i}/`,
  }));
  assert.throws(
    () =>
      validateReport(goodReport("events_dashboard", "https://www.facebook.com/events/", many)),
    /bad-report: too many items/,
  );
});

// ---- sender ------------------------------------------------------------------------------------

test("sender builds only the harness intake URL on 127.0.0.1", () => {
  assert.equal(
    buildIntakeUrl(),
    `http://127.0.0.1:${HARNESS_PORT}/intake/facebook`,
  );
  assert.equal(
    buildIntakeUrl("http://127.0.0.1:9000/intake/facebook"),
    "http://127.0.0.1:9000/intake/facebook",
  );
  const refused = [
    "http://localhost:8765/intake/facebook",
    "http://[::1]:8765/intake/facebook",
    "https://127.0.0.1:8765/intake/facebook",
    "http://127.0.0.1:8765/other",
    "http://127.0.0.1:8765/",
    "http://127.0.0.2:8765/intake/facebook",
    "http://127.0.0.1:99999/intake/facebook",
    "http://evil.example/intake/facebook",
  ];
  for (const origin of refused) {
    assert.throws(() => buildIntakeUrl(origin), /bad-origin/, origin);
  }
});

test("sender validates before sending and posts JSON once", async () => {
  resetSendClock();
  const calls = [];
  const stub = async (url, init) => {
    calls.push([url, init]);
    return { ok: true, status: 200 };
  };
  const report = goodReport("birthdays", "https://www.facebook.com/birthdays/", [
    goodBirthday,
  ]);
  assert.equal(await postReport(report, stub), true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], `http://127.0.0.1:${HARNESS_PORT}/intake/facebook`);
  assert.equal(calls[0][1].method, "POST");
  assert.deepEqual(JSON.parse(calls[0][1].body), report);

  resetSendClock();
  const badCalls = [];
  await assert.rejects(
    () =>
      postReport(
        goodReport("birthdays", "https://www.facebook.com/events/", [goodBirthday]),
        async (...args) => {
          badCalls.push(args);
          return { ok: true, status: 200 };
        },
      ),
    /bad-report:/,
  );
  assert.equal(badCalls.length, 0, "an invalid report sends nothing");
});

test("sender names a refused harness answer without leaking the payload", async () => {
  resetSendClock();
  await assert.rejects(
    () =>
      postReport(
        goodReport("birthdays", "https://www.facebook.com/birthdays/", [goodBirthday]),
        async () => ({ ok: false, status: 500 }),
      ),
    /harness-refused: 500/,
  );
});

// ---- background --------------------------------------------------------------------------------

test("background owns one rhythm and never opens, navigates or reloads a tab", () => {
  assert.equal(CHECK_INTERVAL_MIN, 240, "builder's pick: about four sweeps a day");
  assert.equal(RECHECK_MS, CHECK_INTERVAL_MIN * 60 * 1000);
  assert.ok(SEND_GAP_MS >= 1000, "sends stay paced");
  const src = codeOnly(read("background.js"));
  for (const token of [
    "fetch(",
    "XMLHttpRequest",
    "chrome.tabs",
    "tabs.create",
    "tabs.update",
    "tabs.remove",
    "tabs.reload",
    "tabs.goBack",
    "tabs.goForward",
    "tabs.discard",
    "windows.create",
    "navigate",
    "func:",
    "func :",
    "args:",
    "insertCSS",
    "127.0.0.1",
  ]) {
    assert.ok(!src.includes(token), `background must not contain ${token}`);
  }
  assert.ok(read("background.js").includes("./sender.js"), "sends via sender.js");
});

test("every reader re-reads on the same single rhythm", () => {
  for (const file of ["corner-events.js", "corner-invites.js", "corner-birthdays.js"]) {
    const src = read(file);
    assert.ok(
      src.includes("240 * 60 * 1000"),
      `${file} re-reads every CHECK_INTERVAL_MIN`,
    );
  }
});

test("background forwards a fresh report once, then stays quiet", async () => {
  seenDigests.clear();
  resetSendClock();
  const calls = [];
  const stub = async () => ({ ok: true, status: 200 });
  const report = goodReport("birthdays", "https://www.facebook.com/birthdays/", [
    goodBirthday,
  ]);
  const first = await handleMessage(
    { type: "fb-helper-report", report },
    "https://www.facebook.com/birthdays/",
    stub,
  );
  assert.deepEqual(first, { forwarded: true, duplicate: false });
  const second = await handleMessage(
    { type: "fb-helper-report", report },
    "https://www.facebook.com/birthdays/",
    async () => {
      calls.push(1);
      return { ok: true, status: 200 };
    },
  );
  assert.deepEqual(second, { forwarded: false, duplicate: true });
  assert.equal(calls.length, 0, "a repeat observation sends nothing");
  assert.equal(digestReport(report).split("\n")[0], "birthdays");
  assert.ok(sweepSeen(Date.now() + 25 * 60 * 60 * 1000) === 0, "sweep clears old digests");
  seenDigests.clear();
});

test("background refuses anything off the named corners", async () => {
  seenDigests.clear();
  resetSendClock();
  const stub = async () => ({ ok: true, status: 200 });
  const report = goodReport("birthdays", "https://www.facebook.com/birthdays/", [
    goodBirthday,
  ]);
  await assert.rejects(
    handleMessage({ type: "other", report }, "https://www.facebook.com/birthdays/", stub),
    /bad-message: not a report/,
  );
  await assert.rejects(
    handleMessage({ type: "fb-helper-report", report }, "", stub),
    /bad-message: no sender page/,
  );
  await assert.rejects(
    handleMessage(
      { type: "fb-helper-report", report },
      "https://www.facebook.com/events/",
      stub,
    ),
    /bad-message: url mismatch/,
  );
  await assert.rejects(
    handleMessage(
      {
        type: "fb-helper-report",
        report: goodReport("birthdays", "https://www.facebook.com/?sk=nf", []),
      },
      "https://www.facebook.com/?sk=nf",
      stub,
    ),
    /bad-message: not a named corner/,
  );
  seenDigests.clear();
});

// ---- readers: observe only ---------------------------------------------------------------------

test("corner readers take no action and reach no network", () => {
  const readers = ["corner-events.js", "corner-invites.js", "corner-birthdays.js"];
  const shared = codeOnly(read("page-text.js"));
  const forbidden = [
    "fetch(",
    "XMLHttpRequest",
    ".click(",
    ".submit(",
    "requestSubmit",
    "eval(",
    "Function(",
    "innerHTML",
    "outerHTML",
    "document.write",
    "chrome.tabs",
    "chrome.windows",
    "sendBeacon",
    ".assign(",
    ".replace(",
    "open(",
    "postMessage",
  ];
  for (const token of forbidden) {
    assert.ok(!shared.includes(token), `page-text.js must not contain ${token}`);
  }
  assert.ok(!shared.includes("sendMessage"), "page-text.js reports nothing itself");
  for (const file of readers) {
    const raw = read(file);
    const src = codeOnly(raw);
    for (const token of forbidden) {
      assert.ok(!src.includes(token), `${file} must not contain ${token}`);
    }
    assert.ok(!raw.includes("127.0.0.1"), `${file} never addresses the harness`);
    assert.ok(!raw.includes("http://"), `${file} never posts anywhere`);
    assert.ok(
      raw.includes("isAllowed(location.href)"),
      `${file} guards on its named corner`,
    );
    assert.ok(
      raw.includes('"fb-helper-report"'),
      `${file} reports via the background worker`,
    );
    assert.ok(
      raw.includes(".runtime.sendMessage"),
      `${file} only hands the report over`,
    );
  }
});

test("readers scrape read-only and clean every field", () => {
  const shared = read("page-text.js");
  assert.ok(shared.includes("querySelectorAll"), "page-text.js reads the listing");
  assert.ok(shared.includes("textContent"), "page-text.js reads text only");
  for (const file of ["corner-events.js", "corner-invites.js", "corner-birthdays.js"]) {
    const raw = read(file);
    assert.ok(raw.includes("MAX_ITEMS"), `${file} caps the report`);
    assert.ok(raw.includes("globalThis.OzbPageText"), `${file} reads through page-text.js`);
  }
});

// ---- background: starting a reader ---------------------------------------------------------------

test("background starts only a corner's own reader, only on a named corner", async () => {
  assert.deepEqual(readerFiles("https://www.facebook.com/events/?ref=bookmarks"),
    ["page-text.js", "corner-events.js"]);
  assert.deepEqual(readerFiles("https://www.facebook.com/events/invites/"),
    ["page-text.js", "corner-invites.js"]);
  assert.deepEqual(readerFiles("https://www.facebook.com/events/birthdays/?acontext=x"),
    ["page-text.js", "corner-birthdays.js"]);
  for (const url of ["https://www.facebook.com/", "https://www.facebook.com/events/123456789/",
    "https://www.facebook.com/messages", "https://example.com/events/", "", undefined]) {
    assert.deepEqual(readerFiles(url), [], String(url));
  }
  for (const file of [SHARED_FILE, ...Object.values(READER_FILES)]) {
    assert.ok(fs.existsSync(path.join(HERE, file)), `${file} exists`);
  }
  const calls = [];
  const scripting = { executeScript: async (injection) => calls.push(injection) };
  assert.equal(await startReader(7, "https://www.facebook.com/events/", scripting), true);
  assert.deepEqual(calls, [{ target: { tabId: 7 }, files: ["page-text.js", "corner-events.js"] }]);
  assert.equal(await startReader(7, "https://www.facebook.com/", scripting), false);
  assert.equal(await startReader(7, "https://www.facebook.com/events/42/", scripting), false);
  assert.equal(await startReader(undefined, "https://www.facebook.com/events/", scripting), false);
  assert.equal(calls.length, 1, "nothing runs off the corners");
  const src = codeOnly(read("background.js"));
  assert.equal(src.split("executeScript(").length - 1, 1, "one place runs a script, startReader");
});

test("background adds the browser's time zone to what it forwards", async () => {
  const { machineZone } = await import("./schema.js");
  seenDigests.clear();
  resetSendClock();
  const bodies = [];
  const stub = async (url, init) => {
    bodies.push(JSON.parse(init.body));
    return { ok: true, status: 200 };
  };
  const report = goodReport("birthdays", "https://www.facebook.com/birthdays/", [goodBirthday]);
  await handleMessage({ type: "fb-helper-report", report }, "https://www.facebook.com/birthdays/", stub);
  assert.ok(machineZone(), "node gives a zone, as the browser does");
  assert.deepEqual(bodies[0], { ...report, timezone: machineZone() });
  assert.equal(validateReport({ ...report, timezone: "America/New_York" }), true);
  assert.throws(() => validateReport({ ...report, timezone: "not a zone" }), /timezone is not a zone name/);
  assert.throws(() => validateReport({ ...report, timezone: 5 }), /timezone is not a zone name/);
});

// ---- the events reader against the dashboard's real layout ----------------------------------------
//
// The live dashboard (seen 2026-11-20) has no <time> elements, headings or
// articles: a card is the block around one event's links, its text runs date
// line, title, venue, then social and button lines. The fake page below has
// that shape, with made-up events; the notification flyout sits outside the
// main region, as on the real page.

class FakeEl {
  constructor(tag, attrs = {}, kids = [], text = "") {
    this.tag = tag;
    this.attrs = attrs;
    this.kids = kids;
    this.text = text;
    this.parentElement = null;
    for (const kid of kids) kid.parentElement = this;
  }
  getAttribute(name) {
    return Object.hasOwn(this.attrs, name) ? this.attrs[name] : null;
  }
  get innerText() {
    if (this.text) return this.text;
    return this.kids.map((kid) => kid.innerText).filter(Boolean).join("\n");
  }
  get textContent() {
    return this.text || this.kids.map((kid) => kid.textContent).join("");
  }
  matches(selector) {
    if (selector === 'a[href*="/events/"]') {
      return this.tag === "a" && (this.attrs.href || "").includes("/events/");
    }
    if (selector === "a[href]") return this.tag === "a" && Object.hasOwn(this.attrs, "href");
    if (selector === '[role="main"]') return this.attrs.role === "main";
    if (selector === 'h2, [role="heading"]') return this.tag === "h2" || this.attrs.role === "heading";
    throw new Error(`fake page has no selector ${selector}`);
  }
  closest(selector) {
    for (let node = this; node; node = node.parentElement) {
      if (node.matches(selector)) return node;
    }
    return null;
  }
  querySelectorAll(selector) {
    const out = [];
    const walk = (node) => {
      for (const kid of node.kids) {
        if (kid.matches(selector)) out.push(kid);
        walk(kid);
      }
    };
    walk(this);
    return out;
  }
  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

const el = (tag, attrs, ...kids) => new FakeEl(tag, attrs, kids);
const txt = (text) => new FakeEl("span", {}, [], text);

/** A discover-grid card: picture link, date, title link, venue, social line, button. */
function gridCard(id, when, title, venue, social) {
  return el("div", {},
    el("a", { href: `/events/${id}/?acontext=abc` }, el("img", {})),
    el("div", {}, txt(when)),
    el("a", { href: `/events/${id}/` }, txt(title)),
    venue ? el("div", {}, txt(venue)) : el("div", {}),
    el("div", {}, txt(social)),
    el("div", { role: "button" }, txt("Interested")));
}

function fakeDashboard() {
  const yourEvents = el("div", {},
    txt("Your events"),
    el("div", {},
      el("div", {},
        el("a", { href: "/events/111111/" }, el("img", {})),
        el("div", {}, txt("Tomorrow at 10 AM")),
        el("div", {}, txt("Open Studio Night")),
        el("div", {}, txt("480 Example St, Columbus, OH 43215")),
        el("div", { role: "button" }, txt("Interested")),
        el("div", { role: "button" }, txt("Share")),
        el("div", {}, txt("6 interested · 1 going")))));
  const grid = el("div", {},
    gridCard("222222", "Sat, Nov 28 at 12 PM", "Studio Saturdays", "401 Example Ave", "45 interested · 3 going"),
    gridCard("333333", "Sat, Apr 17, 2027 - Apr 18, 2027", "Oddities Expo 2027", "Expo Center", "Pat is interested"),
    gridCard("444444", "Fri, Dec 11 - Dec 12", "WinterFest", "", "Sam is interested"),
    gridCard("555555", "Thu, Jan 21, 2027 at 6:30 PM", "A Big Play", "Example Auditorium", "908 interested · 36 going"),
    gridCard("666666", "Happening now", "Already Started", "Somewhere", "2 going"));
  const main = el("div", { role: "main" }, yourEvents, txt("Discover events"), grid);
  const flyout = el("div", { role: "navigation" },
    el("div", {},
      el("a", { href: "https://www.facebook.com/events/999999/" },
        txt("Unread Example Market posted in Example Market. 7h"))));
  return el("body", {}, flyout, main);
}

/** Run page-text.js then `file` in a sandbox on `href` with `page`, on 2026-11-20 (a Friday). */
function runReader(file, href, page) {
  const sent = [];
  const timers = { timeouts: [], intervals: [] };
  class FixedDate extends Date {
    constructor(...args) {
      if (args.length === 0) super(2026, 10, 20, 12, 0, 0);
      else super(...args);
    }
  }
  const sandbox = vm.createContext({
    URL,
    Date: FixedDate,
    location: { href, origin: "https://www.facebook.com" },
    document: page,
    chrome: { runtime: { sendMessage: (message) => sent.push(message) } },
    setTimeout: (fn, ms) => timers.timeouts.push(ms),
    setInterval: (fn, ms) => timers.intervals.push(ms),
  });
  vm.runInContext(read("page-text.js"), sandbox);
  vm.runInContext(read(file), sandbox);
  // copy what crosses out of the sandbox, so deepEqual sees this realm's objects
  const plain = (value) => JSON.parse(JSON.stringify(value));
  const shared = sandbox.OzbPageText;
  const text = {
    parseWhen: (line, day) => plain(shared.parseWhen(line, day)),
    cardFields: (lines, day) => plain(shared.cardFields(lines, day)),
    monthDay: (line) => shared.monthDay(line),
  };
  return {
    sent: () => sent.map(plain),
    timers,
    text,
    FixedDate,
    again: () => vm.runInContext(read(file), sandbox),
  };
}

const runEventsReader = (href, page) => runReader("corner-events.js", href, page);

test("events reader reads the dashboard's cards from their text", () => {
  const run = runEventsReader("https://www.facebook.com/events/?ref=bookmarks", fakeDashboard());
  const sent = run.sent();
  const timers = run.timers;
  assert.equal(sent.length, 1, "one report from the first read");
  const report = sent[0].report;
  assert.equal(sent[0].type, "fb-helper-report");
  assert.equal(report.url, "https://www.facebook.com/events/?ref=bookmarks", "the page's own address");
  assert.equal(report.corner, "events_dashboard");
  assert.deepEqual(report.items, [
    { kind: "event", id: "111111", title: "Open Studio Night", start_date: "2026-11-21",
      start_time: "10:00", url: "https://www.facebook.com/events/111111/",
      venue: "480 Example St, Columbus, OH 43215" },
    { kind: "event", id: "222222", title: "Studio Saturdays", start_date: "2026-11-28",
      start_time: "12:00", url: "https://www.facebook.com/events/222222/", venue: "401 Example Ave" },
    { kind: "event", id: "333333", title: "Oddities Expo 2027", start_date: "2027-04-17",
      start_time: "", url: "https://www.facebook.com/events/333333/", venue: "Expo Center" },
    { kind: "event", id: "444444", title: "WinterFest", start_date: "2026-12-11",
      start_time: "", url: "https://www.facebook.com/events/444444/", venue: "" },
    { kind: "event", id: "555555", title: "A Big Play", start_date: "2027-01-21",
      start_time: "18:30", url: "https://www.facebook.com/events/555555/", venue: "Example Auditorium" },
    { kind: "event", id: "666666", title: "Already Started", start_date: "2026-11-20",
      start_time: "", url: "https://www.facebook.com/events/666666/", venue: "Somewhere" },
  ], "the flyout's link is left out; links lose their query; happening now is today");
  assert.equal(validateReport(report), true, "the report passes the intake contract");
  assert.deepEqual(timers.timeouts, [3000, 10000, 30000], "reads again while the cards draw");
  assert.deepEqual(timers.intervals, [RECHECK_MS], "then on the one rhythm");
});

test("events reader reads nothing off its corner", () => {
  const run = runEventsReader("https://www.facebook.com/events/discovery/", fakeDashboard());
  assert.equal(run.sent().length, 0);
});

test("events reader turns Facebook's date lines into a start", () => {
  const { text, FixedDate } = runEventsReader("https://www.facebook.com/", fakeDashboard());
  const today = new FixedDate();
  const when = (line) => text.parseWhen(line, today);
  assert.deepEqual(when("Today at 7:30 pm"), { date: "2026-11-20", time: "19:30" });
  assert.deepEqual(when("Tomorrow"), { date: "2026-11-21", time: "" });
  assert.deepEqual(when("Saturday at 8 PM"), { date: "2026-11-21", time: "20:00" });
  assert.deepEqual(when("Friday at 12 AM"), { date: "2026-11-20", time: "00:00" });
  assert.deepEqual(when("Sat, Nov 28 at 12 PM"), { date: "2026-11-28", time: "12:00" });
  assert.deepEqual(when("Saturday, November 28 at 9:15 AM – 3 PM"), { date: "2026-11-28", time: "09:15" });
  assert.deepEqual(when("Nov 28 at 19:00"), { date: "2026-11-28", time: "19:00" });
  assert.deepEqual(when("Thu, Jan 21 at 6 PM"), { date: "2027-01-21", time: "18:00" },
    "no year: the year whose Jan 21 is a Thursday");
  assert.deepEqual(when("Jan 5"), { date: "2027-01-05", time: "" }, "no year or weekday: not months gone");
  assert.deepEqual(when("Nov 1"), { date: "2026-11-01", time: "" }, "a recent start stays this year");
  assert.deepEqual(when("Happening now"), { date: "2026-11-20", time: "" });
  for (const bad of ["Christkindlmarkt", "Feb 30, 2027", "Today at 13 PM",
    "6 interested · 1 going", ""]) {
    assert.equal(when(bad), null, `${bad} is no start`);
  }
});

test("events reader finds the date among a card's opening lines", () => {
  const { text, FixedDate } = runEventsReader("https://www.facebook.com/", fakeDashboard());
  const hook = text;
  const today = new FixedDate();
  assert.deepEqual(
    hook.cardFields(["Your events", "Sat, Nov 28 at 12 PM", "Studio Day", "Pat is interested", "Interested"], today),
    { date: "2026-11-28", time: "12:00", title: "Studio Day", venue: "" },
  );
  assert.equal(hook.cardFields(["Sat, Nov 28 at 12 PM", "Interested"], today), null, "no title");
  assert.equal(hook.cardFields(["A", "B", "C", "Sat, Nov 28 at 12 PM", "Title"], today), null,
    "a date past the opening lines is not the card's");
});

test("a reader started again in the same tab reads again, never runs twice", () => {
  const run = runEventsReader("https://www.facebook.com/events/", fakeDashboard());
  run.again();
  assert.equal(run.sent().length, 2, "the second start reads at once");
  assert.deepEqual(run.timers.timeouts, [3000, 10000, 30000, 3000, 10000, 30000], "and while the cards draw");
  assert.deepEqual(run.timers.intervals, [RECHECK_MS], "one rhythm, not two");
});

// ---- the invites reader --------------------------------------------------------------------------
//
// The invites page was seen empty (2026-11-20), drawn like the dashboard. The
// fake page uses the dashboard's card shape with an inviter line; made up.

function fakeInvites() {
  const main = el("div", { role: "main" },
    el("div", {},
      el("a", { href: "/events/777777/?ref=invite" }, el("img", {})),
      el("div", {}, txt("Sat, Dec 5 at 7 PM")),
      el("a", { href: "/events/777777/" }, txt("Housewarming")),
      el("div", {}, txt("Sam Rivera invited you")),
      el("div", { role: "button" }, txt("Going"))),
    el("div", {},
      el("div", {}, txt("Invited by Pat Example")),
      el("div", {}, txt("Tomorrow")),
      el("a", { href: "/events/888888/" }, txt("Game Night")),
      el("div", {}, txt("12 going"))),
    el("div", {},
      el("a", { href: "/events/444444/" }, txt("Fri, Dec 11 - Dec 12")),
      el("div", {}, txt("WinterFest"))));
  return el("body", {}, main);
}

test("invites reader reads each invitation's card and who sent it", () => {
  const run = runReader("corner-invites.js", "https://www.facebook.com/events/invites/", fakeInvites());
  const sent = run.sent();
  assert.equal(sent.length, 1);
  const report = sent[0].report;
  assert.equal(report.corner, "invites");
  assert.deepEqual(report.items, [
    { kind: "invite", id: "777777", title: "Housewarming", start_date: "2026-12-05", start_time: "19:00",
      url: "https://www.facebook.com/events/777777/", inviter: "Sam Rivera" },
    { kind: "invite", id: "888888", title: "Game Night", start_date: "2026-11-21", start_time: "",
      url: "https://www.facebook.com/events/888888/", inviter: "Pat Example" },
    { kind: "invite", id: "444444", title: "WinterFest", start_date: "2026-12-11", start_time: "",
      url: "https://www.facebook.com/events/444444/", inviter: "" },
  ]);
  assert.equal(validateReport(report), true);
  assert.equal(runReader("corner-invites.js", "https://www.facebook.com/events/", fakeInvites()).sent().length, 0,
    "nothing off its corner");
});

// ---- the birthdays reader ------------------------------------------------------------------------
//
// The live page (2026-11-20, at /events/birthdays/) has section headings, one
// heading a person inside the link to their profile, then a date line, except
// under Today's. Month sections list only "A, B and N others". Names made up.

function person(href, name, ...lines) {
  return el("div", {},
    el("a", { href }, el("h2", {}, txt(name))),
    ...lines.map((line) => el("div", {}, txt(line))));
}

function fakeBirthdays() {
  const main = el("div", { role: "main" },
    el("div", {},
      el("h2", {}, txt("Today's birthdays")),
      person("/profile.php?id=100042&__tn__=%2CdK", "Sam Rivera", "40 years old", "Happy birthday, Sam!")),
    el("div", {},
      el("h2", {}, txt("Recent birthdays")),
      person("/jordan.example?__tn__=x", "Jordan Example", "November 18", "Message")),
    el("div", {},
      el("h2", {}, txt("Upcoming birthdays")),
      person("/alex.example?__tn__=x", "Alex Example", "November 23, 1992", "·", "34 years old", "Message"),
      el("div", {}, txt("Alex's birthday fundraiser for Example Charity"), txt("Donate")),
      person("https://www.facebook.com/casey.example", "Casey Example", "Nov 25", "Message"),
      person("/robin.example", "Robin Example", "Message"),
      person("/alex.example", "Alex Example", "November 23, 1992")),
    el("div", {},
      el("h2", {}, txt("December")),
      person("/lee.example", "Lee Example", "December 3")));
  return el("body", {}, main);
}

test("birthdays reader reads each person's day, and today's from the section", () => {
  const run = runReader("corner-birthdays.js", "https://www.facebook.com/events/birthdays/?acontext=x",
    fakeBirthdays());
  const sent = run.sent();
  assert.equal(sent.length, 1);
  const report = sent[0].report;
  assert.equal(report.corner, "birthdays");
  assert.deepEqual(report.items, [
    { kind: "birthday", name: "Sam Rivera", profile_url: "https://www.facebook.com/profile.php?id=100042",
      date_text: "November 20" },
    { kind: "birthday", name: "Jordan Example", profile_url: "https://www.facebook.com/jordan.example",
      date_text: "November 18" },
    { kind: "birthday", name: "Alex Example", profile_url: "https://www.facebook.com/alex.example",
      date_text: "November 23" },
    { kind: "birthday", name: "Casey Example", profile_url: "https://www.facebook.com/casey.example",
      date_text: "November 25" },
  ], "no date line, a repeat and the month summaries are left out; links lose their tracking");
  assert.equal(validateReport(report), true);
  assert.deepEqual(run.timers.timeouts, [3000, 10000, 30000]);
});

test("birthdays reader reads the old address and nothing off its corner", () => {
  assert.equal(runReader("corner-birthdays.js", "https://www.facebook.com/birthdays/", fakeBirthdays())
    .sent()[0].report.items.length, 4);
  assert.equal(runReader("corner-birthdays.js", "https://www.facebook.com/events/", fakeBirthdays())
    .sent().length, 0);
  const { text } = runReader("corner-birthdays.js", "https://www.facebook.com/", fakeBirthdays());
  assert.equal(text.monthDay("November 23, 1992"), "November 23");
  assert.equal(text.monthDay("Sept 3"), "September 3");
  for (const bad of ["Feb 30", "34 years old", "November", "Message", "November 23 at 5 PM"]) {
    assert.equal(text.monthDay(bad), "", bad);
  }
});

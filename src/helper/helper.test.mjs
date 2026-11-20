// Slice tests for the Chrome helper scaffold (ext-scaffold).
//
// Run: node --test src/helper/helper.test.mjs
// Covers the proof gate: static manifest checks pass, the helper reads only
// named corners, posts only to 127.0.0.1, and takes no action as the user.

import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
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
  RECHECK_MS,
  digestReport,
  handleMessage,
  seenDigests,
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
      ["alarms", "storage"].includes(permission),
      `unexpected permission ${permission}`,
    );
  }
  const forbidden = [
    "tabs",
    "cookies",
    "history",
    "webRequest",
    "webNavigation",
    "scripting",
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

test("manifest posts only to the harness on 127.0.0.1", () => {
  assert.ok(
    (manifest.host_permissions || []).length > 0,
    "declares where it may post",
  );
  for (const entry of manifest.host_permissions) {
    assert.match(
      entry,
      /^http:\/\/127\.0\.0\.1(:\d+)?\/.*$/,
      `must post only to 127.0.0.1, found ${entry}`,
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
    for (const file of script.js) {
      assert.ok(
        fs.existsSync(path.join(HERE, file)),
        `content script exists: ${file}`,
      );
    }
  }
  assert.ok(seen >= 9, `every corner pattern declared, saw ${seen}`);
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

test("background owns one rhythm and reaches no tab or page API", () => {
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
    "windows.create",
    "scripting",
    "executeScript",
    "navigate",
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
  for (const file of ["corner-events.js", "corner-invites.js"]) {
    const raw = read(file);
    assert.ok(raw.includes("querySelectorAll"), `${file} reads the listing`);
    assert.ok(raw.includes("textContent"), `${file} reads text only`);
    assert.ok(raw.includes("MAX_ITEMS"), `${file} caps the report`);
  }
  const birthdays = read("corner-birthdays.js");
  assert.ok(birthdays.includes("querySelectorAll"), "birthdays reads the list");
  assert.ok(!codeOnly(birthdays).includes("fetch("), "birthdays posts nothing itself");
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

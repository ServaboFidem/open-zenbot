#!/usr/bin/env python3
"""
check_tells.py — the enforced sweep for the humanizer skill.

Scans a text/markdown file for mechanical AI tells and reports them with line
numbers. Two tiers:

  HARD (exit code 1 if any found): em dash, en dash, double-hyphen used as a
    dash, curly quotes/apostrophes. These must be zero before delivery.

  SOFT (reported as warnings, never fails the build): high-frequency AI
    vocabulary, plus a few book/course phrase patterns. Use judgment.

Usage:
    python3 check_tells.py FILE [FILE ...]
    python3 check_tells.py --soft-fail FILE     # also fail on soft hits
    cat draft.md | python3 check_tells.py -      # read from stdin
    python3 check_tells.py "--text=<the draft itself>"  # inline text (crew)
    python3 check_tells.py /abs/path/draft.md https://.../stored-draft.md

Exit code 0 = no hard tells. Non-zero = hard tells found (or soft, with flag).
"""

import os
import re
import sys

# --- HARD tells: must be zero ------------------------------------------------
HARD_PATTERNS = {
    "em dash (—)": re.compile(r"—"),
    "en dash (–)": re.compile(r"–"),
    "double-hyphen dash ( -- )": re.compile(r"(?<=\s)--(?=\s)|\w--\w"),
    "curly double quote (“ ”)": re.compile(r"[“”]"),
    "curly single quote / apostrophe (‘ ’)": re.compile(r"[‘’]"),
}

# --- SOFT tells: high-frequency AI vocabulary (whole-word, case-insensitive) -
AI_WORDS = [
    "delve", "tapestry", "testament", "underscore", "underscores",
    "showcase", "showcases", "showcasing", "pivotal", "intricate",
    "intricacies", "interplay", "vibrant", "boasts", "nestled",
    "groundbreaking", "realm", "landscape", "navigate", "navigating",
    "elevate", "elevates", "unlock", "unlocks", "unlocking", "embark",
    "harness", "harnessing", "seamless", "seamlessly", "robust",
    "leverage", "leveraging", "myriad", "plethora", "foster", "fostering",
    "crucial", "vital", "essential", "ever-evolving", "cutting-edge",
    "holistic", "synergy", "empower", "empowering", "transformative",
    "resonate", "resonates", "captivating", "compelling", "meticulous",
    "meticulously", "profound", "moreover", "furthermore",
]

# --- SOFT tells: phrase patterns common in books & course outlines -----------
PHRASE_PATTERNS = {
    "reflective-summary ending": re.compile(
        r"\b(in that moment|i realized|i came to understand|"
        r"little did i know|it was a lesson|made all the difference|"
        r"would carry with me|taught me that)\b", re.I),
    "course outcome boilerplate": re.compile(
        r"\b(by the end of this (course|module|lesson|section)|"
        r"learners? will be able to|students? will be able to|"
        r"in this comprehensive|whether you'?re a beginner|"
        r"take your .* to the next level|master the art of|"
        r"unlock your potential|dive deep into|deep dive)\b", re.I),
    "signposting": re.compile(
        r"\b(let'?s dive in|let'?s explore|let'?s break (this|it) down|"
        r"here'?s what you need to know|without further ado)\b", re.I),
    "significance inflation": re.compile(
        r"\b(stands as a testament|serves as a reminder|"
        r"plays a (vital|crucial|pivotal|key) role|"
        r"marking a pivotal moment|in today'?s (fast-paced|ever-evolving))\b",
        re.I),
    "stock gesture / body cliche": re.compile(
        r"(breath (she|he|they|i) didn'?t know .{0,15}holding|"
        r"(smile|grin) (tugged|played|ghosted|quirked)|"
        r"heart (pounded|hammered|raced|thudded) in (her|his|their|my) chest|"
        r"breath (caught|hitched)|"
        r"a knot in (her|his|their|my) stomach|"
        r"ran a hand through (her|his|their|my) hair|"
        r"eyes met and|let out a breath)", re.I),
    "reassurance tic": re.compile(
        r"(and that'?s (okay|ok|perfectly normal|fine)\b|"
        r"there'?s no right or wrong)", re.I),
    "rhetorical-question opener": re.compile(
        r"\b(have you ever (wondered|felt|noticed)|what if i told you|"
        r"ever wondered (what|why|how)|what makes a (great|good))\b", re.I),
    "adverb dialogue tag": re.compile(
        r"\b(said|asked|replied|muttered|whispered|shouted|murmured|"
        r"answered)\s+\w+ly\b", re.I),
    "placeholder / template residue": re.compile(
        r"(\[[^\]]{1,40}\]|insert .{0,20}here|add (detail|details|example|"
        r"examples|content) here|\b\d{4}-xx-xx\b)", re.I),
}

# --- SOFT tells: filter words (narrative distance) -- reported as a count -----
FILTER_WORDS = [
    "saw", "felt", "heard", "noticed", "watched", "realized", "realised",
    "seemed", "could see", "could hear", "could feel",
]


def resolve_source(arg):
    """Map one argument to (label, text).

    Accepts a file path, "-" (stdin), an http(s) link (an artifact's stored
    link), "--file=<path or link>", or "--text=<the text itself>". A --text
    value that is just an existing file path or a link is read as that file:
    an editor passing a path where the text belongs gets the file, not the
    path's own words.
    """
    if arg.startswith("--file="):
        return _load(arg[len("--file="):].strip())
    if arg.startswith("--text="):
        value = arg[len("--text="):]
        candidate = value.strip()
        if "\n" not in candidate and (
            _is_link(candidate) or os.path.isfile(os.path.expanduser(candidate))
        ):
            return _load(candidate)
        return "(inline text)", value
    if arg == "-":
        return "(stdin)", sys.stdin.read()
    return _load(arg)


def resolve_label(arg):
    """The report header for an argument, without reading it twice."""
    if arg.startswith("--text="):
        value = arg[len("--text="):].strip()
        if "\n" in value or not (_is_link(value) or os.path.isfile(value)):
            return "(inline text)"
        return value.split("?", 1)[0]
    if arg.startswith("--file="):
        return arg[len("--file="):].split("?", 1)[0]
    return arg.split("?", 1)[0]


def _is_link(value):
    return value.startswith(("http://", "https://"))


def _load(source):
    if _is_link(source):
        import urllib.request

        with urllib.request.urlopen(source, timeout=30) as resp:  # noqa: S310
            return source.split("?", 1)[0], resp.read().decode("utf-8", "replace")
    path = os.path.expanduser(source)
    with open(path, "r", encoding="utf-8") as f:
        return path, f.read()


def read_lines(path):
    return resolve_source(path)[1].splitlines()


def scan(path):
    lines = read_lines(path)
    hard_hits, soft_hits = [], []
    filter_count = 0

    for n, line in enumerate(lines, 1):
        for label, pat in HARD_PATTERNS.items():
            for m in pat.finditer(line):
                hard_hits.append((n, label, line.strip()[:90]))

        low = line.lower()
        for word in AI_WORDS:
            for m in re.finditer(r"\b" + re.escape(word) + r"\b", low):
                soft_hits.append((n, f"AI word: '{word}'", line.strip()[:90]))
        for label, pat in PHRASE_PATTERNS.items():
            if pat.search(line):
                soft_hits.append((n, label, line.strip()[:90]))
        for word in FILTER_WORDS:
            filter_count += len(re.findall(r"\b" + re.escape(word) + r"\b", low))

    return hard_hits, soft_hits, filter_count


def report(path, hard_hits, soft_hits, filter_count):
    print(f"\n=== {resolve_label(path)} ===")
    if hard_hits:
        print(f"\nHARD TELLS — must be fixed ({len(hard_hits)}):")
        for n, label, ctx in hard_hits:
            print(f"  line {n}: {label}")
            print(f"           > {ctx}")
    else:
        print("\nHARD TELLS: none. (dashes/quotes clean)")

    if soft_hits:
        print(f"\nSOFT TELLS — review with judgment ({len(soft_hits)}):")
        for n, label, ctx in soft_hits:
            print(f"  line {n}: {label}")
            print(f"           > {ctx}")
    else:
        print("\nSOFT TELLS: none flagged.")

    print(f"\nFILTER WORDS (saw/felt/heard/noticed/realized...): {filter_count} "
          f"occurrence(s). High counts suggest narrative distance; review in "
          f"fiction/memoir.")


def main():
    args = [a for a in sys.argv[1:]]
    soft_fail = "--soft-fail" in args
    files = [a for a in args if a != "--soft-fail"]
    if not files:
        print(__doc__)
        sys.exit(2)

    total_hard, total_soft = 0, 0
    for path in files:
        try:
            hard_hits, soft_hits, filter_count = scan(path)
        except (OSError, ValueError) as exc:  # missing file, bad link, fetch error
            print(f"ERROR: could not read {resolve_label(path)}: {exc}")
            sys.exit(2)
        report(path, hard_hits, soft_hits, filter_count)
        total_hard += len(hard_hits)
        total_soft += len(soft_hits)

    print("\n" + "-" * 50)
    print(f"TOTAL: {total_hard} hard, {total_soft} soft.")
    if total_hard:
        print("RESULT: NOT READY. Fix the hard tells and rerun.")
        sys.exit(1)
    if total_soft and soft_fail:
        print("RESULT: soft tells present (--soft-fail set).")
        sys.exit(1)
    print("RESULT: passes hard checks. Review soft tells, then deliver.")
    sys.exit(0)


if __name__ == "__main__":
    main()

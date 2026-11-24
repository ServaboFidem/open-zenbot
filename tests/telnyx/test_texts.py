"""The texts: plain, links replaced, and cut by SMS segments the way carriers count them."""

import pytest

from src.telnyx.texts import clean, cut, fits, reminder, rundown, units


def test_units():
    assert units("hello") == (True, 5)
    assert units("a{b") == (True, 4)  # an extension character counts two
    assert units("café") == (True, 4)  # é is in the GSM-7 table
    assert units("naïve") == (False, 5)
    assert units("hi 🙂") == (False, 5)  # an emoji is two UTF-16 units


def test_fits_limits():
    assert fits("a" * 160, 1) and not fits("a" * 161, 1)
    assert fits("a" * 306, 2) and not fits("a" * 307, 2)
    assert fits("ï" * 70, 1) and not fits("ï" * 71, 1)
    assert fits("ï" * 134, 2) and not fits("ï" * 135, 2)


def test_clean():
    assert clean("Lunch\n\tat noon") == "Lunch at noon"
    assert clean("See https://x.test/a?b now") == "See [link] now"
    assert clean("WWW.EVIL.TEST") == "[link]"
    assert clean("a​b") == "a b"  # a format character is no hiding place


def test_cut():
    assert cut("short", 1) == "short"
    got = cut("word " * 60, 1)
    assert got.endswith("...") and fits(got, 1) and not got.endswith(" ...")
    got = cut("a" * 400, 1, tail="; ...and 3 more")
    assert got.endswith("...; ...and 3 more") and fits(got, 1)


def test_reminder_short_and_cut():
    assert reminder("Dentist", "3:00 PM", "1 Main St") == "Reminder: Dentist at 3:00 PM - 1 Main St"
    assert reminder("Dentist", "3:00 PM", "  ") == "Reminder: Dentist at 3:00 PM"
    long = reminder("x" * 300, "3:00 PM", "")
    assert units(long) == (True, 160) and long.endswith("... at 3:00 PM")
    wide = reminder("ï" + "y" * 99, "3:00 PM", "")
    assert units(wide)[1] <= 70 and wide.endswith("... at 3:00 PM")
    assert "[link]" in reminder("Join https://meet.test/x", "3:00 PM", "")


def test_rundown_more():
    items = [f"9:30 AM Meeting {n}" for n in range(40)]
    got = rundown(items)
    assert fits(got, 2) and got.startswith("Tomorrow: 9:30 AM Meeting 0; ")
    shown = got.count("Meeting")
    assert got.endswith(f"; ...and {40 - shown} more")
    one = rundown(["9:30 AM " + "z" * 500])
    assert one.endswith("...") and fits(one, 2)
    two = rundown(["9:30 AM " + "z" * 500, "10:00 AM Lunch"])
    assert two.endswith("...; ...and 1 more") and fits(two, 2)
    assert rundown(["All day Trip", "9:30 AM Dentist", "6:00 PM Dinner"]) == \
        "Tomorrow: All day Trip; 9:30 AM Dentist; 6:00 PM Dinner"
    with pytest.raises(ValueError):
        rundown([])


def test_no_added_text_breaks_gsm():
    assert units(reminder("x" * 300, "3:00 PM", "y" * 50))[0] is True
    assert units(rundown([f"9:30 AM Meeting {n}" for n in range(40)]))[0] is True


def test_a_cut_reminder_keeps_its_time():
    got = reminder("Mom’s birthday dinner with the whole family at the new place", "7:00 PM", "Joe’s")
    assert got.startswith("Reminder: Mom's birthday dinner") and " at 7:00 PM" in got and fits(got, 1)
    assert units(got)[0] is True  # curly quotes are made plain, so the text stays GSM-7
    got = reminder("Dentist", "3:00 PM", "z" * 300)
    assert got.startswith("Reminder: Dentist at 3:00 PM - zzz") and got.endswith("...") and fits(got, 1)
    got = reminder("t" * 150, "3:00 PM", "Main St")
    assert got.endswith("... at 3:00 PM") and "Main St" not in got and fits(got, 1)


def test_typographic_punctuation_is_made_plain():
    assert clean("“Hi” ‘you’ – now—then…") == "\"Hi\" 'you' - now-then..."


def test_bare_domains_and_glued_links_become_link():
    assert clean("see evil.com/login or bit.ly/x or Xhttps://a.b") == "see [link] or [link] or X[link]"
    assert clean("Go to Example.ORG now") == "Go to [link] now"
    assert clean("Dr.Smith at St.Louis, 9.30 a.m. e.g. here") == "Dr.Smith at St.Louis, 9.30 a.m. e.g. here"

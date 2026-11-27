import math
import time

import pytest

from src.skilltuner.match import Matcher, top, words


def test_words_fold_case_split_on_punctuation_and_underscore_and_drop_short():
    assert words("PDF_to-Word: a Converter, v2!") == ["pdf", "to", "word", "converter", "v2"]


def test_words_keep_unicode_letters():
    assert words("Café résumé") == ["café", "résumé"]


def test_name_match_outranks_description_match():
    m = Matcher([
        ("Spreadsheet Tools", "charts for data", ""),
        ("Other", "spreadsheet work here", ""),
    ])
    in_name = m.score("spreadsheet", "Spreadsheet Tools", "charts for data")
    in_desc = m.score("spreadsheet", "Other", "spreadsheet work here")
    assert in_name > in_desc > 0


def test_rare_word_outranks_common_word():
    docs = [("Alpha", "common filler text", "")] * 10
    docs.append(("Beta", "zebra filler text", ""))
    m = Matcher(docs)
    common = m.score("common", "Alpha", "common filler text")
    rare = m.score("zebra", "Beta", "zebra filler text")
    assert rare > common > 0


def test_nothing_shared_gives_zero_and_no_result():
    m = Matcher([("Invoices", "bill payments", "finance")])
    assert m.score("gardening", "Invoices", "bill payments", "finance") == 0.0
    items = [("Invoices", "bill payments", "finance")]
    assert top("gardening", items, key=lambda x: x, n=5) == []


def test_category_counts_as_text():
    m = Matcher([("A", "x", "finance"), ("B", "y", "travel")])
    assert m.score("finance", "A", "x", "finance") > 0


def test_word_unseen_by_the_matcher_takes_the_highest_idf():
    # "one" is in one of two docs (df 1); "novel" is in no stored doc (df 0),
    # so it takes the highest IDF, log(1 + N / 1) with N = 2
    m = Matcher([("A", "one", ""), ("B", "two", "")])
    assert m.score("one", "Q", "one") == pytest.approx(math.log(1 + 2 / 2))
    assert m.score("novel", "Q", "novel") == pytest.approx(math.log(1 + 2 / 1))
    assert m.score("novel", "Q", "novel") > m.score("one", "Q", "one")


def test_ties_keep_input_order():
    items = [
        ("First", "report", ""),
        ("Second", "report", ""),
        ("Third", "report", ""),
    ]
    result = top("report", items, key=lambda x: x, n=10)
    assert result == items


def test_top_stops_at_n_and_puts_best_first():
    items = [
        ("Misc", "notes about budget", ""),
        ("Budget", "spending plan", ""),
        ("Plan", "budget budget", ""),
        ("Nothing", "unrelated", ""),
    ]
    result = top("budget", items, key=lambda x: x, n=2)
    assert len(result) == 2
    assert result[0][0] == "Budget"
    assert all(item[0] != "Nothing" for item in result)


def test_top_key_selects_fields_from_items():
    rows = [{"id": 1, "n": "Invoices", "d": "bill"}, {"id": 2, "n": "Bills", "d": "x"}]
    result = top(
        "invoices",
        rows,
        key=lambda r: (r["n"], r["d"], ""),
        n=5,
    )
    assert [r["id"] for r in result] == [1]


def test_three_thousand_docs_score_under_half_a_second():
    docs = [(f"Skill {i}", f"does thing {i % 50} with files", "tools") for i in range(3000)]
    docs[1234] = ("Tax Return Helper", "prepare a tax return", "finance")
    m = Matcher(docs)
    start = time.perf_counter()
    result = top("tax return", docs, key=lambda x: x, n=5)
    elapsed = time.perf_counter() - start
    assert result and result[0][0] == "Tax Return Helper"
    assert elapsed < 0.5


def test_top_uses_a_given_matcher_instead_of_building_one():
    items = [("alpha beta", "", ""), ("beta gamma", "", "")]
    # a matcher built over other documents weighs "alpha" heavily and "beta" lightly
    given = Matcher([("beta", "", "")] * 5 + [("alpha", "", "")])
    got = top("alpha beta", items, key=lambda x: x, n=2, matcher=given)
    assert got == [items[0], items[1]]
    assert given.score("alpha beta", *items[0]) > given.score("alpha beta", *items[1])

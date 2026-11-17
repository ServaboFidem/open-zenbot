from src.harness.design_html import scan

def one(html, tag):
    return next(e for e in scan(html) if e.tag == tag)

def test_offsets_are_exact_for_start_tag_and_inner():
    html = '<main>\r\n<h1 data-zen-id="t">Hi &amp; bye</h1></main>'
    h1 = one(html, "h1")
    assert html[h1.start[0]:h1.start[1]] == '<h1 data-zen-id="t">'
    assert html[h1.inner[0]:h1.inner[1]] == "Hi &amp; bye"
    assert h1.attrs == {"data-zen-id": "t"} and h1.children == 0 and h1.within == ("main",)

def test_attr_span_is_the_value_inside_its_quotes():
    html = """<IMG data-zen-id='a' SRC="images/x.png" alt=plain hidden>"""
    img = one(html, "img")
    s = img.attr_span("src")
    assert html[s[0]:s[1]] == "images/x.png" and img.inner is None
    assert img.attr_span("alt") is None and img.attr_span("hidden") is None and img.attr_span("nope") is None
    assert img.attrs["alt"] == "plain" and img.attrs["hidden"] == ""

def test_a_gt_inside_a_quoted_value_does_not_end_the_tag():
    html = '<p title="a > b" data-zen-id="p">x</p>'
    p = one(html, "p")
    assert html[p.inner[0]:p.inner[1]] == "x"

def test_script_style_and_comments_hold_no_elements():
    html = '<script>var s = "<img src=x>";</script><style>p{}</style><!-- <img src="y"> --><b>z</b>'
    assert [e.tag for e in scan(html)] == ["script", "style", "b"]

def test_children_counts_elements_but_not_br_and_unclosed_has_no_inner():
    html = '<div><p>a<br>b</p><p>c<em>d</em></p><li>open'
    ps = [e for e in scan(html) if e.tag == "p"]
    assert [p.children for p in ps] == [0, 1]
    assert one(html, "li").inner is None and one(html, "div").inner is None

def test_non_ascii_and_a_bom_keep_offsets_in_characters():
    html = '﻿<p data-zen-id="é">naïve</p>'
    p = one(html, "p")
    assert html[p.inner[0]:p.inner[1]] == "naïve"

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

def test_a_p_is_closed_by_the_next_p():
    html = '<p>a<p>b</p>'
    p1, p2 = [e for e in scan(html) if e.tag == "p"]
    assert p1.inner is None and p1.children == 0
    assert html[p2.inner[0]:p2.inner[1]] == "b" and "p" not in p2.within

def test_li_is_closed_by_the_next_li_and_the_ul_inner_is_exact():
    html = '<ul><li>a<li>b</li></ul>'
    ul = one(html, "ul")
    l1, l2 = [e for e in scan(html) if e.tag == "li"]
    assert l1.inner is None and html[l2.inner[0]:l2.inner[1]] == "b"
    assert l2.within == ("ul",) and ul.children == 2
    assert html[ul.inner[0]:ul.inner[1]] == "<li>a<li>b</li>"

def test_a_p_is_closed_by_a_following_block_element():
    html = '<section><p>a<div>b</div></section>'
    p, div = one(html, "p"), one(html, "div")
    assert p.inner is None and p.children == 0
    assert div.within == ("section",) and html[div.inner[0]:div.inner[1]] == "b"
    assert one(html, "section").children == 2

def test_table_cells_and_options_close_their_siblings():
    html = '<table><tr><td>a<td>b<tr><td>c</table><select><option>x<option>y</select>'
    tds = [e for e in scan(html) if e.tag == "td"]
    assert [t.inner for t in tds[:2]] == [None, None] and tds[1].within == ("table", "tr")
    opts = [e for e in scan(html) if e.tag == "option"]
    assert opts[1].within == ("select",)

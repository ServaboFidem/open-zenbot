import json

import pytest

from src.harness.design_parts import PartRefused
from src.harness.design_tweaks import apply, fmt, knobs, view

PAGE = '''<html><head><style data-zen-tweaks>
:root {
  --accent: #2f6feb;
  --radius: 8px;
}
</style>
<script type="application/json" data-zen-tweaks>
{"tweaks": [{"var": "--accent", "label": "Accent", "type": "color"},
 {"var": "--radius", "label": "Corner radius", "type": "range", "min": 0, "max": 24, "step": 1, "unit": "px"}]}
</script></head><body><p data-zen-id="a">Hi</p></body></html>'''

ACCENT = {"var": "--accent", "label": "Accent", "type": "color"}
RADIUS = {"var": "--radius", "label": "Radius", "type": "range", "min": 0, "max": 24,
          "step": 1, "unit": "px"}
CSS = ":root {\n  --accent: #2f6feb;\n  --radius: 8px;\n}"


def page(tweaks, css=CSS, raw=None, extra=""):
    manifest = raw if raw is not None else json.dumps({"tweaks": tweaks})
    return (f'<html><head><style data-zen-tweaks>\n{css}\n</style>{extra}'
            f'<script type="application/json" data-zen-tweaks>{manifest}</script>'
            '</head><body></body></html>')


def test_knobs_are_read_with_their_current_values():
    a, r = knobs(PAGE)
    assert (a.var, a.type, a.value) == ("--accent", "color", "#2f6feb")
    assert (r.var, r.min, r.max, r.unit, r.value) == ("--radius", 0, 24, "px", "8")
    assert PAGE[a.span[0]:a.span[1]] == "#2f6feb"


def test_apply_changes_only_the_value_spans():
    out = apply(PAGE, knobs(PAGE), {"--accent": "#C2410C", "--radius": 12})
    assert out == PAGE.replace("#2f6feb", "#c2410c").replace("8px", "12px")


def test_apply_refuses_a_name_that_is_no_knob():
    with pytest.raises(PartRefused):
        apply(PAGE, knobs(PAGE), {"--nope": 1})


def test_view_gives_numbers_for_ranges():
    v = view(knobs(PAGE))
    assert v[0]["value"] == "#2f6feb" and v[1]["value"] == 8 and v[1]["unit"] == "px"


@pytest.mark.parametrize("value", [
    "12px; color: red", "red", "#fff", float("nan"), float("inf"), 25, -1, True, None, "12"])
def test_a_bad_range_value_is_refused(value):
    with pytest.raises(PartRefused):
        fmt(knobs(PAGE)[1], value)


@pytest.mark.parametrize("value", ["red", "#fff", None, 12, "12px; color: red", "#c2410c; x", True])
def test_a_bad_colour_value_is_refused(value):
    with pytest.raises(PartRefused):
        fmt(knobs(PAGE)[0], value)


def test_good_values_are_formatted():
    accent, radius = knobs(PAGE)
    assert fmt(accent, "#C2410C") == "#c2410c"
    assert fmt(radius, 12) == "12px" and fmt(radius, 12.0) == "12px"
    assert fmt(radius, 1.5) == "1.5px"


def bad(**kw):
    return {**RADIUS, **kw}


@pytest.mark.parametrize("broken", [
    page(None, raw="not json"),
    page(None, raw='{"tweaks": "x"}'),
    page([bad(var="--Radius")]),
    page([bad(var="radius")]),
    page([bad(type="slider")]),
    page([bad(unit="vh")]),
    page([bad(min=5, max=5)]),
    page([bad(min=9, max=2)]),
    page([bad(min=True)]),
    page([bad()], css=":root {\n --radius: 8px;\n --radius: 9px;\n}"),
    page([bad()], css=":root {\n --other: 1px;\n}\n.a { --radius: 8px; }"),
    page([bad()], css=":root {\n --radius: calc(1px);\n}"),
    page([bad()], css=":root {\n --radius: 30px;\n}"),
    page([bad()], css=":root {\n --radius: 8em;\n}"),
    page([bad()], css=".a { --radius: 8px; }"),
    page([bad()], css=":root {\n --a: 1px;\n}\n:root {\n --radius: 8px;\n}"),
    page([bad(label="")]),
    page([ACCENT], css=":root {\n --accent: red;\n}"),
    page(["x", 3]),
])
def test_a_knob_that_does_not_hold_is_dropped(broken):
    assert knobs(broken) == []


def test_the_repeated_var_and_the_ninth_knob_are_dropped():
    assert [k.var for k in knobs(page([ACCENT, ACCENT, RADIUS]))] == ["--accent", "--radius"]
    names = [f"--k{i}" for i in range(9)]
    css = ":root {\n" + "".join(f" {n}: #000000;\n" for n in names) + "}"
    tweaks = [{"var": n, "label": n, "type": "color"} for n in names]
    assert [k.var for k in knobs(page(tweaks, css=css))] == names[:8]


def test_a_good_knob_survives_a_bad_neighbour():
    assert [k.var for k in knobs(page([bad(unit="vh"), ACCENT]))] == ["--accent"]


def test_a_label_is_cut_and_cleaned():
    (k,) = knobs(page([{**ACCENT, "label": "A b\x00" + "c" * 60}]))
    assert len(k.label) == 40 and k.label.startswith("Abc")


def test_two_style_elements_or_two_manifests_give_nothing():
    extra_style = "<style data-zen-tweaks>:root{--x:#000000;}</style>"
    assert knobs(page([ACCENT], extra=extra_style)) == []
    extra_script = '<script data-zen-tweaks>{"tweaks": []}</script>'
    assert knobs(page([ACCENT], extra=extra_script)) == []


def test_no_manifest_or_garbage_never_raises():
    assert knobs("") == []
    assert knobs("<style data-zen-tweaks>") == []
    assert knobs("<<<>>>") == []

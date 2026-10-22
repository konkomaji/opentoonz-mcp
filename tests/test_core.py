"""Fast unit tests (no OpenToonz needed). Run: .venv\\Scripts\\python -m pytest -q -m "not slow" """
import math
import xml.etree.ElementTree as ET

import pytest

from otmcp import curve, draw, style, tnz, vec
from otmcp import tnztree as T
from otmcp.scene import Cell, SceneDoc


def new_doc(n_cols=0):
    lv = [tnz.Level(f"L{i}", f"+drawings/x/L{i}..png") for i in range(n_cols)]
    cols = [tnz.Column(l, [1, 1, 2]) for l in lv]
    return SceneDoc.new(tnz.Scene(frames=3, levels=lv, columns=cols))


# ------------------------------------------------------------------ tnztree
def test_roundtrip_stable():
    d = new_doc(2)
    a = d.text()
    b = SceneDoc(T.parse(a)).text()
    assert a == b


@pytest.mark.parametrize("s", ["plain", "with space", 'q"uote', "back\\slash", "+drawings/a..png", "", "ünï"])
def test_fmt_unquote_roundtrip(s):
    assert T.unquote(T.fmt(s)) == s


def test_parse_comments_and_single_quote_attrs():
    r = T.parse("<!-- hi --><tnz version='71.1'><a id='3'/> tok \"q s\" </tnz>")
    tnz_ = r.find("tnz")
    assert tnz_.attrs["version"] == "71.1"
    assert tnz_.tokens() == ["tok", '"q s"']


def test_parse_rejects_mismatch():
    with pytest.raises(ValueError):
        T.parse("<a><b></a></b>")


# ------------------------------------------------------------------ scene cells / params
def test_cells_runs_roundtrip():
    d = new_doc(1)
    lid = d.levels()[0]["id"]
    cells = [Cell(lid, f) for f in ["1", "1", "1", "2", "3", "4", "4"]] + [None, Cell(lid, "9"), Cell(lid, "2a")]
    d.set_column_cells(0, cells)
    d2 = SceneDoc(T.parse(d.text()))
    got = d2.column_cells(0)
    assert [c.fid if c else None for c in got] == [c.fid if c else None for c in cells]


def test_descending_cells():
    d = new_doc(1)
    lid = d.levels()[0]["id"]
    cells = [Cell(lid, f) for f in ["5", "4", "3", "2"]]
    d.set_column_cells(0, cells)
    assert [c.fid for c in SceneDoc(T.parse(d.text())).column_cells(0)] == ["5", "4", "3", "2"]


def test_params_roundtrip_all_interps():
    d = new_doc(1)
    keys = [{"frame": 0, "value": 0.0, "interp": "ease"}, {"frame": 5, "value": 2.0, "interp": "linear"},
            {"frame": 9, "value": -1.5, "interp": "speed"}, {"frame": 12, "value": 3.0, "interp": "constant"},
            {"frame": 20, "value": 1.0, "interp": "exp"}]
    d.set_param("Col1", "x", keys)
    got = SceneDoc(T.parse(d.text())).get_params("Col1")["x"]["keys"]
    assert [(k["frame"], round(k["value"], 4), k["interp"]) for k in got] == \
           [(k["frame"], k["value"], k["interp"]) for k in keys]


def test_remove_column_renumbers():
    d = new_doc(3)
    d.set_param("Col3", "x", [{"frame": 0, "value": 1.0}])
    d.set_parent("Col3", "Col2")
    d.remove_column(1)
    ids = [p.attrs["id"] for p in d.pegbars()]
    assert "Col3" not in ids and "Col2" in ids
    assert d.get_params("Col2")["x"]["keys"][0]["value"] == 1.0
    assert d.parent_of("Col2") == "Table"  # parent was the deleted column
    assert d.verify() == []


def test_add_column_verifies():
    d = new_doc(0)
    lid = d.add_level("a", "+drawings/x/a.pli", None)
    ci = d.add_column(lid, "a")
    d.set_column_cells(ci, [Cell(lid, "1")] * 4)
    assert SceneDoc(T.parse(d.text())).verify() == []


def test_pivot_tokens():
    d = new_doc(1)
    d.set_pivot("Col1", 1.5, -2.0)
    assert d.pivot("Col1") == (1.5, -2.0)
    assert d.pegbar("Col1").find("center").tokens()[2:] == ["-1.5", "2"]


# ------------------------------------------------------------------ easing / tween
@pytest.mark.parametrize("kind", ["linear", "in", "out", "inout", "slow_in_out", "snappy", "bounce", "elastic", "back"])
def test_ease_endpoints(kind):
    assert abs(draw.ease(0, kind)) < 1e-9 and abs(draw.ease(1, kind) - 1) < 1e-9


def test_tween_endpoints():
    a = [{"type": "circle", "x": 0, "y": 0, "r": 10, "fill": "#ff0000"}]
    b = [{"type": "circle", "x": 100, "y": 50, "r": 30, "fill": "#0000ff"}]
    assert draw.tween(a, b, 0)[0]["rx"] == 10 and draw.tween(a, b, 1)[0]["rx"] == 30


def test_squash_zero_motion_noop():
    a = [{"type": "circle", "x": 10, "y": 10, "r": 10}]
    out = draw.tween_motion(a, a, 0.5, squash=1.0, arc=0.2)
    assert out[0].get("rx", 10) == 10 and out[0]["x"] == 10


# ------------------------------------------------------------------ vector geometry / svg
SHAPES = [
    {"type": "circle", "x": 100, "y": 100, "r": 50, "fill": "#abc", "shadow": True},
    {"type": "ellipse", "x": 300, "y": 100, "rx": 80, "ry": 20, "rotation": 30, "fill": "red"},
    {"type": "rect", "x": 10, "y": 300, "w": 200, "h": 100, "radius": 20, "fill": [0, 200, 0, 128]},
    {"type": "polygon", "points": [[0, 0], [50, 0], [25, 40]], "fill": "#123456"},
    {"type": "path", "points": [[0, 500], [100, 450], [200, 520]], "stroke": "#000"},
    {"type": "text", "x": 500, "y": 500, "text": "Hi O!", "size": 60, "fill": "#e00"},
]


@pytest.mark.parametrize("style_name", list(style.STYLES))
def test_build_elements_finite(style_name):
    els = vec.build_elements(SHAPES, 1080, style_name, seed=7)
    for e in els:
        for c in e.contours:
            assert c.pts and all(math.isfinite(x) and math.isfinite(y) for x, y in c.pts)


def test_svg_is_valid_xml():
    svg = vec.to_svg(vec.build_elements(SHAPES, 1080, "anime", 1), 1920, 1080, 120)
    root = ET.fromstring(svg)
    assert root.tag.endswith("svg") and len(root) > 0


def test_deterministic_seed():
    a = vec.to_svg(vec.build_elements(SHAPES, 1080, "sketch", 3), 1920, 1080, 120)
    b = vec.to_svg(vec.build_elements(SHAPES, 1080, "sketch", 3), 1920, 1080, 120)
    assert a == b


def test_clean_style_no_wobble():
    els = vec.build_elements([{"type": "polygon", "points": [[0, 0], [100, 0], [100, 100]], "fill": "#fff"}],
                             1080, "clean", 5)
    pts = els[0].contours[0].pts
    assert (0.0, 0.0) in [(round(x, 6), round(y, 6)) for x, y in pts]


def test_shadow_color_darker():
    s = style.shadow_color((200, 150, 100, 255))
    assert sum(s[:3]) < 450


# ------------------------------------------------------------------ exact interpolation (curve.py)


def test_ease_in_written_negative():
    """OpenToonz reads arrival ease as -speedIn.x; a positive value silently kills the slow-in."""
    d = new_doc(1)
    d.set_param("Col1", "x", [{"frame": 0, "value": 0.0, "interp": "ease", "ease_out": 30},
                              {"frame": 10, "value": 1.0, "interp": "ease", "ease_in": 30}])
    k = SceneDoc(T.parse(d.text())).get_params("Col1")["x"]["keys"][1]
    assert k["speed_in"][0] < 0 and k["ease_in"] == 30


def test_ease_symmetric_and_bounded():
    ks = [{"frame": 0, "value": 0.0, "type": "Ep", "speed_out": (33, 0)},
          {"frame": 24, "value": 1.0, "type": "L", "speed_in": (-33, 0)}]
    vals = [curve.value_at(ks, f) for f in range(25)]
    assert vals[0] == 0 and vals[-1] == 1
    assert all(b >= a for a, b in zip(vals, vals[1:]))
    assert abs(vals[6] - (1 - vals[18])) < 1e-9  # symmetric slow-in/slow-out


def test_exponential_and_constant():
    ks = [{"frame": 0, "value": 1.0, "type": "Exp"}, {"frame": 10, "value": 0.5, "type": "L"}]
    assert abs(curve.value_at(ks, 5) - math.sqrt(0.5)) < 1e-9
    kc = [{"frame": 0, "value": 3.0, "type": "C"}, {"frame": 10, "value": 7.0, "type": "L"}]
    assert curve.value_at(kc, 9.9) == 3.0 and curve.value_at(kc, 10) == 7.0


def test_hatch_lines_stay_inside_and_gradient_starts_in_the_dark():
    import random

    from shapely.geometry import Point

    from otmcp import vec
    region = Point(0, 0).buffer(100)
    lines = vec.hatch(region, 8, 45, random.Random(1), jitter=0.3, gradient=1.0, light_deg=135)
    assert lines and all(region.buffer(1e-6).contains(Point(p)) for c in lines for p in c.pts)
    assert lines == vec.hatch(region, 8, 45, random.Random(1), jitter=0.3, gradient=1.0, light_deg=135)
    # light from the top-left: the densest (first) lines lie on the bottom-right
    first, last = lines[0].pts, lines[-1].pts
    mid = lambda p: ((p[0][0] + p[1][0]) / 2, (p[0][1] + p[1][1]) / 2)
    assert mid(first)[0] + mid(first)[1] > mid(last)[0] + mid(last)[1]
    cross = vec.hatch(region, 8, 45, random.Random(1), cross=True)
    assert len(cross) > 1.6 * len(vec.hatch(region, 8, 45, random.Random(1)))


def test_hatch_shadow_builds_ink_strokes():
    from otmcp import vec
    els = vec.build_elements([{"type": "circle", "x": 500, "y": 500, "r": 200, "fill": "#e0a080",
                               "shadow": {"style": "hatch", "spacing": 10}}], 1080, "feature", 7)
    hatch = [e for e in els if e.fill is None and e.stroke and all(not c.closed for c in e.contours)]
    assert hatch and len(hatch[0].contours) > 5
    assert len(vec.ink_profiles(els)) == 1 + len(hatch[0].contours)

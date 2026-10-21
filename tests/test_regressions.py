"""Regression tests for every confirmed QA finding (format, vector, server reviews, 2026-10-22)."""
import math
import random
import shutil
import subprocess
import sys
import textwrap

import pytest

from otmcp import art, draw, pli, style, tnz, tnztree as T, vec
from otmcp.scene import Cell, SceneDoc


def doc1():
    lv = tnz.Level("a", "+drawings/x/a..png")
    return SceneDoc.new(tnz.Scene(frames=3, levels=[lv], columns=[tnz.Column(lv, [1, 1, 1])]))


# =========================================================== format layer
def test_attr_with_escaped_quote_parses_and_roundtrips():
    src = '<tnz version="71.1"><levelSet><folder name="My \\"Cast\\"" type="default"/></levelSet></tnz>'
    r = T.parse(src)
    again = T.parse(T.dump(r))
    assert again.find("tnz").find("levelSet").nodes()[0].attrs["name"] == 'My \\"Cast\\"'


def test_attr_set_with_quote_is_escaped_on_dump():
    r = T.parse('<tnz version="71.1"><a/></tnz>')
    r.find("tnz").nodes()[0].attrs["name"] = 'x"y'
    assert T.parse(T.dump(r)).find("tnz").nodes()[0].attrs["name"] == 'x\\"y'


def test_unquote_keeps_unknown_escapes():
    assert T.unquote('"C:\\dir\\new"') == "C:\\dir\\new"
    assert T.unquote('"a\\"b\\\\c"') == 'a"b\\c'


def test_fmt_rejects_non_finite():
    with pytest.raises(ValueError):
        T.fmt(float("nan"))


def test_truncated_tag_is_valueerror():
    with pytest.raises(ValueError):
        T.parse('<tnz version="71.1"><x')


def test_active_camera_resolution():
    d = doc1()
    cams = d.props.child("cameras")
    cam2 = T.parse("<camera><cameraSize>16 9</cameraSize><cameraRes>960 540</cameraRes></camera>").nodes()[0]
    cams.children.append(cam2)
    c1 = d.pegbar("Camera1")
    del c1.attrs["activeboth"]
    d.pegbar("Camera2", create=True).attrs["activeboth"] = "yes"
    assert d.camera_res() == (960, 540) and d.dpi() == 60


def test_set_pivot_keeps_existing_offset_relationship():
    d = doc1()
    d.pegbar("Col1").child("center").set_tokens(1.0, 0.0, -0.5, 0.0)  # center 1, offset 0.5 (GUI-moved)
    cx, cy = d.pivot("Col1")
    d.set_pivot("Col1", cx, cy)  # same pivot -> must not move the object
    assert d.pegbar("Col1").find("center").tokens() == ["1", "0", "-0.5", "0"]


def test_save_surrogateescape(tmp_path):
    d = doc1()
    d.levels()[0]["node"].children[0] = "na\udcffme"
    d.save(tmp_path / "s.tnz")  # must not raise


def test_set_param_keeps_cycle_step_and_speed_handles():
    d = doc1()
    xml = ("<x><default>0</default><cycle>enabled</cycle><keyframes><S step=\"2\">0 0 10 1.5</S>"
           "<L><prev>2 -10 -1.5</prev>3 2</L></keyframes></x>")
    peg = d.pegbar("Col1")
    peg.children.insert(len(peg.children) - 1, T.parse(xml).nodes()[0])
    keys = d.get_params("Col1")["x"]["keys"]
    d.set_param("Col1", "x", keys)
    out = " ".join(T.dump(T.Node("#", children=[d.pegbar("Col1").find("x")])).split())
    assert "<cycle> enabled </cycle>" in out and 'step="2"' in out and "0 0 10 1.5" in out


def test_tnz_builder_writes_negative_ease_in():
    lv = tnz.Level("a", "+drawings/a..png")
    col = tnz.Column(lv, [1], params={"x": [tnz.Key(0, 0.0, "ease"), tnz.Key(10, 1.0)]})
    txt = tnz.build(tnz.Scene(levels=[lv], columns=[col]))
    assert "<prev>1 -33</prev>" in txt


# =========================================================== vector pipeline
def _fake_pli(tmp_path, strokes_xy, loop=True):
    """Build a tiny v71 PLI with given strokes (16-bit coords) for patch tests."""
    def dyn16(v):
        mag = abs(v) & 0x7FFF
        return bytes([mag & 0xFF, (mag >> 8) | (0x80 if v < 0 else 0)])
    body = b""
    body += bytes([22 | (1 << 6), 2]) + (1).to_bytes(2, "little")  # IMAGE_BEGIN frame 1 (no suffix len, v71)
    for pts in strokes_xy:
        nq = (len(pts) - 1) // 2
        payload = bytes([10])
        x0, y0 = pts[0]
        payload += dyn16(x0) + dyn16(y0) + bytes([255])
        px, py = x0, y0
        for i in range(nq):
            for (x, y) in pts[1 + 2 * i:3 + 2 * i]:
                payload += dyn16(x - px) + dyn16(y - py) + bytes([255])
                px, py = x, y
        t = (23 if loop else 10) | (2 << 6)  # u16 length
        body += bytes([t]) + len(payload).to_bytes(2, "little") + payload
    head = b"PLIM" + bytes([71, 0]) + (0).to_bytes(2, "little") + (0).to_bytes(4, "little") + \
        (1).to_bytes(2, "little") + bytes([2, 1, 15])
    f = tmp_path / "t.pli"
    f.write_bytes(head + body + bytes([0]))
    return f


def test_loop_pressure_has_no_seam(tmp_path):
    pts = [(int(1280 * math.cos(2 * math.pi * i / 40)), int(1280 * math.sin(2 * math.pi * i / 40))) for i in range(40)]
    pts.append(pts[0])
    f = _fake_pli(tmp_path, [pts], loop=True)
    pli.pressure_patch(str(f), loop_var=0.4)
    _, s = pli.read(str(f))
    th = [p[2] for p in s[0].points]
    assert abs(th[0] - th[-1]) <= 0.05  # same point -> same weight (1 byte tolerance)


def test_taper_is_monotonic_without_pressure_noise(tmp_path):
    pts = [(i * 128, 0) for i in range(61)]
    f = _fake_pli(tmp_path, [pts], loop=False)
    pli.pressure_patch(str(f), taper_start=0.2, taper_end=0.2, pressure_var=0.0)
    _, s = pli.read(str(f))
    th = [p[2] for p in s[0].points]
    head = th[:12]
    assert all(b >= a - 0.02 for a, b in zip(head, head[1:]))


def test_pressure_seed_is_per_frame_stable(tmp_path):
    a = _fake_pli(tmp_path, [[(0, 0), (500, 0), (1000, 0)], [(0, 300), (500, 300), (1000, 300)]], loop=False)
    pli.pressure_patch(str(a))
    th_two = [p[2] for p in pli.read(str(a))[1][1].points]
    b = _fake_pli(tmp_path, [[(0, 0), (500, 0), (1000, 0)], [(0, 300), (500, 300), (1000, 300)],
                             [(0, 600), (500, 600), (1000, 600)]], loop=False)
    pli.pressure_patch(str(b))
    assert [p[2] for p in pli.read(str(b))[1][1].points] == th_two  # adding a later stroke changes nothing


def test_old_pli_rejected(tmp_path):
    f = tmp_path / "old.pli"
    f.write_bytes(b"PLIM" + bytes([5, 5]) + b"\x00" * 20)
    with pytest.raises(ValueError):
        pli.read(str(f))


def test_squash_keeps_user_rotation_when_not_stretched():
    a = [{"type": "ellipse", "x": 0, "y": 0, "rx": 100, "ry": 40, "rotation": 30}]
    b = [{"type": "ellipse", "x": 0, "y": 500, "rx": 100, "ry": 40, "rotation": 30}]
    for t in (0.0, 0.01, 1.0):
        out = draw.tween_motion(a, b, t, squash=1.0)
        assert out[0].get("rotation", 30) == 30 or out[0]["type"] == "svgpath"


def test_squash_preserves_area_for_any_shape():
    a = [{"type": "rect", "x": 0, "y": 0, "w": 100, "h": 50, "fill": "#fff"}]
    b = [{"type": "rect", "x": 900, "y": 0, "w": 100, "h": 50, "fill": "#fff"}]
    out = draw.tween_motion(a, b, 0.5, "slow_in_out", squash=1.0, squash_limits=(0.6, 1.6), dt=0.05)
    from shapely.geometry import Polygon
    area = Polygon(vec.shape_contours(out[0])[0].pts).area
    assert abs(area - 5000) / 5000 < 0.02


def test_bowtie_shadow_keeps_both_lobes():
    bow = [vec.Contour([(0, 0), (100, 100), (100, 0), (0, 100)], True, False)]
    assert vec._poly(bow).area == pytest.approx(5000, rel=0.01)


def test_clean_style_rect_is_exact():
    els = vec.build_elements([{"type": "rect", "x": 100, "y": 100, "w": 400, "h": 400, "fill": "#ccc"}], 1080, "clean", 1)
    xs = [p[0] for p in els[0].contours[0].pts]
    assert min(xs) == 100 and max(xs) == 500


@pytest.mark.parametrize("sh", [
    {"type": "path", "points": []}, {"type": "polygon", "points": [], "fill": "#f00"},
    {"type": "polygon", "points": [[1, 1]], "fill": "#f00"}, {"type": "path", "points": [[1, 1]]}])
def test_degenerate_shapes_do_not_crash(sh):
    els = vec.build_elements([sh], 1080, "anime", 1)
    vec.to_png(els, 64, 64)
    vec.to_svg(els, 64, 64, 120)


def test_huge_coordinates_rejected():
    with pytest.raises(ValueError):
        vec.build_elements([{"type": "path", "points": [[0, 0], [1e6, 0]]}], 1080, "anime", 1)


def test_neutral_shadow_is_not_pink():
    r, g, b, _ = style.shadow_color((255, 255, 255, 255))
    assert b >= r


def test_color_lerp_hex_and_list():
    out = draw.tween([{"type": "circle", "fill": "#ff0000", "r": 1}], [{"type": "circle", "fill": [0, 0, 255], "r": 1}], 0.5)
    assert out[0]["fill"][:3] == [128, 0, 128]
    txt = draw.tween([{"type": "text", "text": "red"}], [{"type": "text", "text": "blue"}], 0.4)
    assert txt[0]["text"] == "red"


def test_text_anchor_matches_pil_baseline():
    from PIL import ImageFont
    f = ImageFont.truetype(r"C:\Windows\Fonts\arialbd.ttf", 200)
    for anchor, text in [(a, t) for a in ("ms", "mb", "mt", "ma", "md", "mm") for t in ("H", "Hg")]:
        pil_box = f.getbbox(text, anchor=anchor)
        cs = vec._text_contours({"type": "text", "x": 0, "y": 0, "text": text, "size": 200, "anchor": anchor})
        ys = [p[1] for c in cs for p in c.pts]
        assert abs(max(ys) - pil_box[3]) < 4 and abs(min(ys) - pil_box[1]) < 4, anchor


def test_mirror_transform_is_exact():
    m = art.placement(0, 0, 1.0, 0, flip_x=True)
    out = art.transform_shape({"type": "ellipse", "x": 10, "y": 0, "rx": 30, "ry": 10, "rotation": 20}, m)
    assert out["type"] == "svgpath"


def test_arc_keeps_brush_pressure():
    a = [{"type": "brush", "points": [[0, 0, 0.2], [100, 0, 1.0]]}]
    b = [{"type": "brush", "points": [[500, 0, 0.2], [600, 0, 1.0]]}]
    out = draw.tween_motion(a, b, 0.5, arc=0.2)
    assert [p[2] for p in out[0]["points"]] == [0.2, 1.0]

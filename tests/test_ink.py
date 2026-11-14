"""Ink layers through the real OpenToonz: strokes written straight into the .pli (needs OpenToonz). pytest -m slow"""
import json

import pytest
from PIL import Image

from otmcp import learn
from otmcp import server as S
from otmcp.plidoc import PliDoc

pytestmark = pytest.mark.slow
RED, BLUE = (208, 32, 32), (32, 64, 208)


@pytest.fixture(autouse=True)
def keep_lessons():
    bak = learn.LESSONS.read_text(encoding="utf-8") if learn.LESSONS.exists() else None
    yield
    if bak is None:
        learn.LESSONS.unlink(missing_ok=True)
    else:
        learn.LESSONS.write_text(bak, encoding="utf-8")


def fresh(name, frames=4):
    S.create_scene(name, width=640, height=360, frames=frames, overwrite=True)
    return name


def shot(sc, frame, tmp_path, tag):
    S._tcomposer(S._scene_file(sc), tmp_path / tag / "p..png", (frame, frame))
    return S._flat(next((tmp_path / tag).glob("p.*.png")), (255, 255, 255, 255))


def near(a, b, tol=6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def two_lines(sc):
    return S.stroke(sc, "hand", 1, [
        {"points": [[100, 180], [320, 60], [540, 180]], "width": 12, "color": "#d02020"},
        {"points": [[100, 300], [540, 300]], "width": 20, "color": "#2040d0", "smooth": False}])


def test_strokes_render_where_they_were_drawn(tmp_path):
    sc = fresh("qa_ink_draw")
    S.add_layer(sc, "hand", mode="ink")
    with pytest.raises(RuntimeError, match="nothing is drawn yet"):  # used to hang tcomposer for two minutes
        S.preview_frame(sc, 1)
    res = two_lines(sc)
    assert res["strokes"] == [0, 1] and res["verify"] == "ok"
    S.stroke(sc, "hand", 3, [{"points": [[50, 50, 0.2], [320, 180, 1.0], [590, 310, 0.2]], "width": 30}])
    im = shot(sc, 1, tmp_path, "f1")
    assert near(im.getpixel((320, 60)), RED) and near(im.getpixel((320, 300)), BLUE)
    assert near(im.getpixel((320, 308)), BLUE) and im.getpixel((320, 314)) == (255, 255, 255)  # 20 px wide
    assert im.getpixel((320, 180)) == (255, 255, 255) and im.getpixel((20, 20)) == (255, 255, 255)
    assert shot(sc, 2, tmp_path, "f2").getpixel((320, 300)) != (255, 255, 255)  # drawing 1 is held on frame 2
    im3 = shot(sc, 3, tmp_path, "f3")
    assert max(im3.getpixel((320, 180))) < 40 and im3.getpixel((320, 300)) == (255, 255, 255)
    assert max(im3.getpixel((328, 172))) < 40 and im3.getpixel((62, 38)) == (255, 255, 255)  # thick middle, thin tip
    assert S.scene_context(sc)["layers"][0]["drawings"] == [1, 3]


def test_read_drawing_reports_strokes_in_canvas_pixels():
    sc = fresh("qa_ink_read")
    S.add_layer(sc, "hand", mode="ink")
    two_lines(sc)
    rd = S.read_drawing(sc, "hand", 1, max_points=5)
    a, b = rd["strokes"]
    assert (a["by"], a["color"], a["closed"]) == ("mcp", "#d02020", False) and abs(a["width"] - 12) < 0.1
    assert a["points"][0][:2] == [100.0, 180.0] and a["points"][-1][:2] == [540.0, 180.0] and len(a["points"]) <= 6
    assert a["points"][0][2] < 0.5 and max(p[2] for p in a["points"]) == 1.0  # tapered ends
    assert b["bbox"] == [100.0, 300.0, 540.0, 300.0] and abs(b["width"] - 20) < 0.1
    assert S.read_drawing(sc, "hand", 2)["strokes"] == []


def test_opentoonz_saves_my_file_back_unchanged(tmp_path):
    """OpenToonz loads the level and writes it again: same bytes, so nothing I write is foreign to it."""
    sc = fresh("qa_ink_resave")
    S.add_layer(sc, "hand", mode="ink")
    two_lines(sc)
    S.stroke(sc, "hand", 2, [{"points": [[300, 100], [360, 160], [300, 220], [240, 160]], "closed": True}])
    pf = S._pli_file(S._load_state(sc), "hand")
    out = tmp_path / "resaved.pli"
    S._run_script(["var l = new Level();", f"l.load({json.dumps(pf.as_posix())});",
                   f"l.save({json.dumps(out.as_posix())});"], "inktest")
    assert out.exists() and out.read_bytes() == pf.read_bytes()


def test_user_and_mcp_strokes_share_a_drawing(tmp_path):
    sc = fresh("qa_ink_shared")
    S.add_layer(sc, "hand", mode="ink")
    two_lines(sc)
    pf = S._pli_file(S._load_state(sc), "hand")
    d = PliDoc.load(str(pf))  # the user draws a green line in OpenToonz and saves
    d.append(1, [{"points": [(-40.0, -30.0, 2.0), (0.0, -34.0, 2.0), (40.0, -30.0, 2.0)],
                  "style": d.style_for((16, 128, 48, 255))}])
    d.save(str(pf))
    res = S.stroke(sc, "hand", 1, [{"points": [[300, 20], [340, 20]], "color": "#d02020"}])  # no lock, no force
    assert res["strokes"] == [3] and "warnings" not in res
    rd = S.read_drawing(sc, "hand", 1)["strokes"]
    assert [s["by"] for s in rd] == ["mcp", "mcp", "other", "mcp"] and rd[2]["color"] == "#108030"
    S.erase_stroke(sc, "hand", 1, [0, 3])
    rd = S.read_drawing(sc, "hand", 1)["strokes"]
    assert [(s["index"], s["by"]) for s in rd] == [(0, "mcp"), (1, "other")]
    im = shot(sc, 1, tmp_path, "f1")
    assert im.getpixel((320, 60)) == (255, 255, 255) and near(im.getpixel((320, 300)), BLUE)
    S.undo(sc)
    assert len(S.read_drawing(sc, "hand", 1)["strokes"]) == 4
    S.erase_stroke(sc, "hand", 1, [0, 1, 2, 3])
    assert shot(sc, 1, tmp_path, "empty").getpixel((320, 300)) == (255, 255, 255)  # an emptied drawing still renders


def test_painted_vector_layer_becomes_ink_and_keeps_its_paint(tmp_path):
    sc = fresh("qa_ink_paint", frames=2)
    S.add_layer(sc, "ball", style="clean")
    S.draw(sc, "ball", 1, [{"type": "circle", "x": 320, "y": 180, "r": 100, "fill": "#f0c020", "stroke": "#111111"}])
    assert S.ink_layer(sc, "ball") == {"layer": "ball", "mode": "ink", "drawings": [1], "verify": "ok"}
    with pytest.raises(RuntimeError, match="ink layer"):
        S.draw(sc, "ball", 1, [{"type": "circle", "x": 9, "y": 9, "r": 3}])
    S.stroke(sc, "ball", 1, [{"points": [[150, 180], [490, 180]], "width": 10, "color": "#c01010", "smooth": False}])
    im = shot(sc, 1, tmp_path, "after")
    yellow = (240, 192, 32)
    assert near(im.getpixel((320, 120)), yellow) and near(im.getpixel((320, 240)), yellow)
    assert near(im.getpixel((320, 180)), (192, 16, 16)) and near(im.getpixel((180, 180)), (192, 16, 16))
    with pytest.raises(RuntimeError, match="painted"):
        S.erase_stroke(sc, "ball", 1, [0])
    S.erase_stroke(sc, "ball", 1, [1])
    im = shot(sc, 1, tmp_path, "erased")
    assert near(im.getpixel((320, 180)), yellow) and im.getpixel((180, 180)) == (255, 255, 255)
    assert S.compile_vectors(sc)["compiled"] == []  # an ink layer is never rebuilt from shapes


def test_ink_tools_refuse_what_they_cannot_do():
    sc = fresh("qa_ink_refuse")
    S.add_layer(sc, "shapes")
    S.add_layer(sc, "hand", mode="ink")
    with pytest.raises(RuntimeError, match="ink layer"):
        S.stroke(sc, "shapes", 1, [{"points": [[0, 0], [9, 9]]}])
    for bad in ([{"points": [[5, 5]]}], [{"points": [[5, 5], [5, 5]]}], [{"points": [[0, 0], [1e9, 0]]}],
                [{"points": [[0, 0], [9, 9]], "width": 0}], []):
        with pytest.raises(RuntimeError):
            S.stroke(sc, "hand", 1, bad)
    with pytest.raises(RuntimeError):
        S.read_drawing(sc, "hand", 1)  # nothing drawn yet: no level file
    assert not S._pli_file(S._load_state(sc), "hand").exists()  # failed calls leave nothing behind
    S.stroke(sc, "hand", 1, [{"points": [[0, 0], [9, 9]]}])
    with pytest.raises(RuntimeError, match="no stroke 7"):
        S.erase_stroke(sc, "hand", 1, [7])
    assert Image is not None

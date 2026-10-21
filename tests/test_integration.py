"""Integration tests against the real OpenToonz install (slow). Run: python -m pytest -q -m slow"""
import json
import shutil

import pytest

from otmcp import learn, pli
from otmcp import server as S
from otmcp.scene import SceneDoc

pytestmark = pytest.mark.slow
SC = "qa_pytest"


@pytest.fixture(scope="module", autouse=True)
def keep_lessons():
    bak = learn.LESSONS.read_text(encoding="utf-8") if learn.LESSONS.exists() else None
    yield
    if bak is None:
        learn.LESSONS.unlink(missing_ok=True)
    else:
        learn.LESSONS.write_text(bak, encoding="utf-8")


@pytest.fixture(scope="module")
def scene():
    S.create_scene(SC, frames=12, overwrite=True)
    S.add_layer(SC, "bg", style="clean")
    S.add_layer(SC, "ball")
    S.draw(SC, "bg", 1, [{"type": "rect", "x": -50, "y": -50, "w": 2020, "h": 1180, "fill": "#cce"}])
    S.draw(SC, "ball", 1, [{"type": "circle", "x": 300, "y": 500, "r": 80, "fill": "#e44"}])
    S.draw(SC, "ball", 12, [{"type": "circle", "x": 1500, "y": 500, "r": 80, "fill": "#e44"}])
    S.inbetween(SC, "ball", 1, 12, step=2)
    yield SC


def test_compile_and_pli(scene):
    res = S.compile_vectors(scene)
    st = S._load_state(scene)
    _, strokes = pli.read(str(S._pli_file(st, "ball")))
    assert strokes and {s.frame for s in strokes} >= {1, 12}


def test_render_verified(scene):
    res = S.render(scene, "png")
    assert res["frames"] == 12 and res["verify"] == "ok"


def test_render_subrange_numbering(scene):
    res = S.render(scene, "mp4", start=5, end=8)
    assert res["frames"] == 4 and res["bytes"] > 0


def test_external_edit_detected_and_kept(scene):
    f = S._scene_file(scene)
    d = SceneDoc.load(f)
    d.set_param("Col2", "rot", [{"frame": 0, "value": 0.0}, {"frame": 11, "value": 90.0}])
    d.save(f)
    ctx = S.scene_context(scene)
    assert any("outside the MCP" in w for w in ctx.get("warnings", []))
    S.set_keyframe(scene, "ball", 1, x=10)
    keys = SceneDoc.load(f).get_params("Col2")
    assert "rot" in keys and "x" in keys  # GUI edit survived the MCP edit


def test_undo(scene):
    before = S._scene_file(scene).read_text(encoding="utf-8")
    S.set_keyframe(scene, "ball", 3, y=-100)
    S.undo(scene)
    assert S._scene_file(scene).read_text(encoding="utf-8") == before


def test_error_is_reported():
    with pytest.raises(RuntimeError):
        S.draw("qa_does_not_exist", "x", 1, [])

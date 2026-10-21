"""Server-level regressions for the 2026-10-22 QA review (needs OpenToonz). Run: pytest -m slow"""
import json
import os
import subprocess
import sys
import time

import pytest

from otmcp import learn, pli
from otmcp import server as S
from otmcp import tnztree as T
from otmcp.scene import SceneDoc

pytestmark = pytest.mark.slow
CIRCLE = [{"type": "circle", "x": 300, "y": 300, "r": 60, "fill": "#e44"}]
RECT = [{"type": "rect", "x": 900, "y": 300, "w": 200, "h": 120, "fill": "#44e"}]


@pytest.fixture(autouse=True)
def keep_lessons():
    bak = learn.LESSONS.read_text(encoding="utf-8") if learn.LESSONS.exists() else None
    yield
    if bak is None:
        learn.LESSONS.unlink(missing_ok=True)
    else:
        learn.LESSONS.write_text(bak, encoding="utf-8")


def fresh(name, frames=6):
    S.create_scene(name, frames=frames, overwrite=True)
    return name


def svg_text(sc, layer, did):
    return S._svg_file(S._load_state(sc), layer, did).read_text(encoding="utf-8")


def test_dash_in_layer_name_compiles_and_never_deletes_good_pli():
    sc = fresh("qa_r_dash")
    S.add_layer(sc, "bg"); S.draw(sc, "bg", 1, RECT); S.compile_vectors(sc)
    S.add_layer(sc, "hero-arm"); S.draw(sc, "hero-arm", 1, CIRCLE)
    res = S.compile_vectors(sc)
    assert not res.get("failed") and [c["layer"] for c in res["compiled"]] == ["hero-arm"]
    assert S._pli_file(S._load_state(sc), "bg").exists()


def test_opentoonz_resave_does_not_flip_timing():
    sc = fresh("qa_r_resave")
    S.add_layer(sc, "a"); S.draw(sc, "a", 1, CIRCLE)
    f = S._scene_file(sc)
    # emulate OpenToonz renumbering persist ids on save
    root = T.load(str(f))
    ids = sorted({int(n.attrs["id"]) for n in root.walk() if n.attrs.get("id", "").isdigit()})
    remap = {str(o): str(1000 + i) for i, o in enumerate(ids)}
    for n in root.walk():
        if n.attrs.get("id") in remap:
            n.attrs["id"] = remap[n.attrs["id"]]
    T.save(root, str(f))
    ctx = S.scene_context(sc)
    assert [l["timing"] for l in ctx["layers"]] == ["auto"]


def test_compile_protects_gui_edited_pli():
    sc = fresh("qa_r_guipli")
    S.add_layer(sc, "a"); S.draw(sc, "a", 1, CIRCLE); S.compile_vectors(sc)
    pf = S._pli_file(S._load_state(sc), "a")
    data = bytearray(pf.read_bytes()); data[-2] ^= 1; pf.write_bytes(bytes(data))  # "GUI edit"
    edited = pf.read_bytes()
    st = S._load_state(sc); st["layers"]["a"]["dirty"] = True; S._save_state(st)
    res = S.compile_vectors(sc)
    assert res.get("skipped_gui_edited") == ["a"] and pf.read_bytes() == edited


def test_undo_restores_svg_sources_and_drawing():
    sc = fresh("qa_r_undo")
    S.add_layer(sc, "a"); S.draw(sc, "a", 1, CIRCLE); S.compile_vectors(sc)
    circle_svg = svg_text(sc, "a", 1)
    S.draw(sc, "a", 1, RECT)
    S.undo(sc)
    assert svg_text(sc, "a", 1) == circle_svg
    assert S._load_state(sc)["layers"]["a"]["drawings"]["1"] == CIRCLE


def test_render_does_not_consume_undo():
    sc = fresh("qa_r_render_undo")
    S.add_layer(sc, "a"); S.draw(sc, "a", 1, CIRCLE)
    S.draw(sc, "a", 3, RECT)
    S.render(sc, "png")
    S.undo(sc)  # must undo draw(3), not the compile done by render
    assert "3" not in S._load_state(sc)["layers"]["a"]["drawings"]


def test_delete_layer_single_undo_restores_everything():
    sc = fresh("qa_r_delete")
    S.add_layer(sc, "a"); S.draw(sc, "a", 1, CIRCLE); S.draw(sc, "a", 3, RECT); S.compile_vectors(sc)
    S.delete_layer(sc, "a")
    assert not any(lv["name"] == "a" for lv in SceneDoc.load(S._scene_file(sc)).levels())
    S.undo(sc)
    st = S._load_state(sc)
    assert set(st["layers"]["a"]["drawings"]) == {"1", "3"}
    assert S._svg_file(st, "a", 3).exists() and S._pli_file(st, "a").exists()


def test_preview_out_of_range_fails_fast():
    sc = fresh("qa_r_range", frames=3)
    S.add_layer(sc, "a"); S.draw(sc, "a", 1, CIRCLE)
    t0 = time.time()
    with pytest.raises(RuntimeError, match="within"):
        S.preview_frame(sc, 50)
    assert time.time() - t0 < 30


def test_overwrite_is_undoable():
    sc = fresh("qa_r_overwrite")
    S.add_layer(sc, "a"); S.draw(sc, "a", 1, CIRCLE); S.compile_vectors(sc)
    S.create_scene(sc, overwrite=True)
    S.undo(sc)
    st = S._load_state(sc)
    assert "a" in st["layers"] and S._pli_file(st, "a").exists()


def test_scene_name_containing_missing_renders():
    sc = fresh("qa_r_missing_word", frames=2)
    S.add_layer(sc, "a"); S.draw(sc, "a", 1, CIRCLE)
    assert S.render(sc, "png")["frames"] == 2


def test_boil_toggle_on_manual_timing_remaps_cells():
    sc = fresh("qa_r_boil", frames=8)
    S.add_layer(sc, "a", boil=True); S.draw(sc, "a", 1, CIRCLE)
    S.set_exposure(sc, "a", "1x8")
    S.set_layer(sc, "a", boil=False)
    cells = SceneDoc.load(S._scene_file(sc)).column_cells(0)
    assert {c.fid for c in cells} == {"1"}
    assert S.render(sc, "png")["verify"] == "ok"


def test_case_insensitive_layer_names():
    sc = fresh("qa_r_case")
    S.add_layer(sc, "Ball")
    with pytest.raises(RuntimeError, match="already exists"):
        S.add_layer(sc, "ball")


@pytest.mark.parametrize("frame", [0, 1000, -1])
def test_drawing_id_validation(frame):
    sc = "qa_r_case"
    with pytest.raises(RuntimeError, match="1..999"):
        S.draw(sc, "Ball", frame, CIRCLE)


def test_inbetween_requires_forward_range():
    sc = fresh("qa_r_ib")
    S.add_layer(sc, "a"); S.draw(sc, "a", 1, CIRCLE); S.draw(sc, "a", 6, RECT)
    with pytest.raises(RuntimeError, match="greater"):
        S.inbetween(sc, "a", 6, 1)


def test_animate_action_settle_zero_has_unique_keys():
    sc = fresh("qa_r_settle", frames=20)
    S.add_layer(sc, "a"); S.draw(sc, "a", 1, CIRCLE)
    res = S.animate_action(sc, "a", 2, 10, {"x": 100}, settle_frames=0)
    frames = [f for f, _ in res["keys"]["x"]]
    assert len(frames) == len(set(frames)) and res["keys"]["x"][-1] == (10, 100.0)


def test_mcp_key_edit_keeps_gui_speed_handles():
    sc = fresh("qa_r_handles", frames=30)
    S.add_layer(sc, "a"); S.draw(sc, "a", 1, CIRCLE)
    f = S._scene_file(sc)
    d = SceneDoc.load(f)
    peg = d.pegbar("Col1")
    peg.children.insert(len(peg.children) - 1, T.parse(
        "<x><default>0</default><keyframes><S>0 0 12 7</S><S><prev>1 -12 -7</prev>20 1 4 1</S></keyframes></x>").nodes()[0])
    d.save(f)
    S.set_keyframe(sc, "a", 26, x=50)
    out = " ".join(T.dump(T.Node("#", children=[SceneDoc.load(f).pegbar("Col1").find("x")])).split())
    assert "0 0 12 7" in out and "20 1 4 1" in out


def test_failed_call_rolls_back_side_effects():
    sc = fresh("qa_r_rollback")
    S.add_layer(sc, "a")
    before = json.dumps(S._load_state(sc)["layers"]["a"]["drawings"])
    with pytest.raises(RuntimeError):
        S.draw_many(sc, "a", [{"frame": 1, "shapes": CIRCLE}, {"frame": 2, "shapes": [{"type": "nope"}]}])
    st = S._load_state(sc)
    assert json.dumps(st["layers"]["a"]["drawings"]) == before
    assert not S._svg_file(st, "a", 1).exists()


def test_two_processes_do_not_corrupt_a_scene(tmp_path):
    sc = fresh("qa_r_conc", frames=12)
    S.add_layer(sc, "a"); S.add_layer(sc, "b")
    code = (
        "import sys; sys.path.insert(0, r'D:\\otmcp'); from otmcp import server as S\n"
        "L = sys.argv[1]\n"
        "for f in range(1, 7): S.draw('qa_r_conc', L, f, [{'type':'circle','x':100*f,'y':300,'r':30,'fill':'#e44'}])\n")
    script = tmp_path / "w.py"
    script.write_text(code, encoding="utf-8")
    env = dict(os.environ, PYTHONPYCACHEPREFIX=r"D:\otmcp\.cache\pycache")
    ps = [subprocess.Popen([sys.executable, str(script), L], env=env, stderr=subprocess.PIPE) for L in ("a", "b")]
    errs = [p.communicate(timeout=300)[1].decode(errors="replace") for p in ps]
    assert all(p.returncode == 0 for p in ps), errs
    st = S._load_state(sc)
    assert {k for k in st["layers"]["a"]["drawings"]} == {str(i) for i in range(1, 7)}
    assert {k for k in st["layers"]["b"]["drawings"]} == {str(i) for i in range(1, 7)}
    d = SceneDoc.load(S._scene_file(sc))
    assert d.verify() == [] or all("file missing" in p for p in d.verify())

"""Studio looks as OpenToonz fx graphs."""
import io

import pytest

from otmcp import fx as F
from otmcp import server as S
from otmcp.scene import SceneDoc

SC = "qa_fx"


@pytest.fixture()
def scene():
    S.create_scene(SC, frames=2, overwrite=True)
    S.add_layer(SC, "bg", style="clean")
    S.draw(SC, "bg", 1, [{"type": "rect", "x": -50, "y": -50, "w": 2020, "h": 1180, "fill": "#bcd3e0"}])
    S.add_layer(SC, "hero")
    S.draw(SC, "hero", 1, [{"type": "circle", "x": 900, "y": 500, "r": 150, "fill": "#e07040"}])
    yield SC


def _doc(scene):
    return SceneDoc.load(S._scene_file(scene))


def _first_occurrence_is_definition(doc) -> bool:
    seen = set()
    for n in doc.root.walk():
        if F._is_fx(n) and n.attrs["id"] not in seen:
            seen.add(n.attrs["id"])
            if n.selfclose:
                return False
    return True


def test_param_encoding():
    v = float(F.param_node("blurFx", "value", 10, 120).children[0].children[0])
    assert abs(v - 10 * 53.33333 / 120) < 1e-5   # px -> 1/53.333 inch (Stage::inch)
    p = F.param_node("bodyHighLightFx", "color", "#ff8000", 120)
    assert [c.name for c in p.children] == ["red", "green", "blue", "matte"]
    assert F.param_node("noiseFx", "Animate", False, 120).children == ["0", "0"]
    assert F.param_node("mosaicFx", "shape", 1, 120).children == ["1"]


@pytest.mark.parametrize("look", list(F.LOOKS))
def test_apply_then_remove_restores_the_file(scene, look):
    before = S._scene_file(scene).read_text(encoding="utf-8")
    r = S.apply_look(scene, look)
    assert r["verify"] == "ok" and r["fx"]
    doc = _doc(scene)
    assert _first_occurrence_is_definition(doc)
    assert doc.xsheet.path("fxnodes", "xsheet").nodes()[0].selfclose        # definition moved into the chain
    S.remove_look(scene)
    assert S._scene_file(scene).read_text(encoding="utf-8") == before


def test_reapply_replaces_and_layer_targets(scene):
    S.apply_look(scene, "klaus")
    n1 = len(F.ours(_doc(scene)))
    r = S.apply_look(scene, "klaus", layers=["hero"], strength=0.5)
    assert r["replaced_nodes"] == n1 and r["layers_lit"] == 1
    assert len(F.ours(_doc(scene))) == 2 + 2                               # 2 post + 2 on the one layer
    with pytest.raises(RuntimeError):
        S.apply_look(scene, "nope")


def test_hand_made_fx_survive(scene):
    doc = _doc(scene)
    F.apply_post(doc, "mine", [("blurFx", {"value": 3}, "hand blur")], doc.dpi(), 135, 1.0)
    for n in doc.root.walk():                                               # pretend it was made in the GUI
        if n.find("name") is not None and "hand blur" in n.find("name").text():
            n.find("name").set_tokens('"Blur1"')
    doc.save(S._scene_file(scene))
    S.apply_look(scene, "comic")
    S.remove_look(scene)
    doc = _doc(scene)
    blur = [n for n in doc.root.walk() if n.name == "STD_blurFx" and not n.selfclose]
    assert len(blur) == 1 and _first_occurrence_is_definition(doc)
    out_src = doc.xsheet.path("fxnodes", "output").nodes()[0].find("ports").nodes()[0].nodes()[0]
    assert out_src.attrs["id"] == blur[0].attrs["id"]


@pytest.mark.slow
def test_fx_length_unit_is_pixels_by_render(scene):
    from PIL import Image
    S.draw(scene, "hero", 1, [{"type": "rect", "x": 800, "y": 400, "w": 300, "h": 300, "fill": "#000000",
                               "stroke": "#000000", "human": False}], force=True)
    S.set_layer(scene, "bg", style="clean")
    F.LOOKS["_cal"] = {"about": "", "post": [], "layer": [
        ("bodyHighLightFx", {"mode": "add", "color": "#ff0000", "transparency": 0.0, "blur": 0, "point": (30, 0)},
         "cal")]}
    try:
        S.apply_look(scene, "_cal", layers=["hero"])
        im = Image.open(io.BytesIO(S.preview_frame(scene, 1, max_width=1920).data)).convert("RGB")
    finally:
        F.LOOKS.pop("_cal")
    red = [x for x in range(700, 1200) if (lambda p: p[0] > 150 and p[1] < 100)(im.getpixel((x, 550)))]
    assert abs(len(red) - 30) <= 1


@pytest.mark.slow
def test_every_look_renders_and_changes_the_picture(scene):
    from PIL import Image, ImageChops, ImageStat

    def frame():
        return Image.open(io.BytesIO(S.preview_frame(scene, 1, max_width=480).data)).convert("RGB")
    base = frame()
    for look in F.LOOKS:
        S.apply_look(scene, look)
        diff = sum(ImageStat.Stat(ImageChops.difference(base, frame())).mean)
        assert diff > 0.3, look
    S.remove_look(scene)


@pytest.mark.slow
@pytest.mark.parametrize("look", list(F.LOOKS))
def test_look_survives_opentoonz_load_and_save(scene, look):
    S.apply_look(scene, look)
    n = len(F.ours(_doc(scene)))
    out = S.TMP / "qa_fx_gui.tnz"
    out.unlink(missing_ok=True)
    S._run_script([f'var s = new Scene("{str(S._scene_file(scene)).replace(chr(92), "/")}")',
                   f's.save("{str(out).replace(chr(92), "/")}")'], "qa_fx")
    doc = SceneDoc.load(out)
    assert len(F.ours(doc)) == n and doc.verify() == []
    assert F.remove(doc) == n and not F.ours(doc) and _first_occurrence_is_definition(doc)

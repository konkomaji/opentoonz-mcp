"""Character rig + gait tools through the server (fast: no OpenToonz needed until render)."""
import pytest

from otmcp import anatomy
from otmcp import server as S
from otmcp.scene import SceneDoc

SC = "qa_rig"


@pytest.fixture()
def scene():
    S.create_scene(SC, frames=60, overwrite=True)
    yield SC


def test_build_character_creates_skeleton(scene):
    r = S.build_character(scene, "kid", person="girl", x=400, ground_y=950, hair="ponytail")
    assert r["verify"] == "ok" and len(r["layers"]) == len(anatomy.PARTS)
    doc = SceneDoc.load(S._scene_file(scene))
    st = S._load_state(scene)
    ctx = S.Ctx(scene, "t", {}, mutate=False)
    col = {p: ctx.stage_id(f"kid_{p}") for p in anatomy.PARTS}
    for part, (parent, _joint) in anatomy.RIG_PARENTS.items():
        want = r["root_peg"] if parent == "root" else col[parent]
        assert doc.parent_of(col[part]) == want, part
    assert st["rigs"]["kid"]["sex"] == "f" and st["rigs"]["kid"]["age"] == 8
    with pytest.raises(RuntimeError):
        S.build_character(scene, "KID", person="boy")


def test_walk_cycle_bakes_planted_feet(scene):
    S.build_character(scene, "dad", person="man", x=300, ground_y=980)
    w = S.walk_cycle(scene, "dad", start=1, cycles=2)
    assert w["legs_reached"] and w["baked_slip_px"] < 0.5 and w["max_foot_slip_px"] < 0.5
    assert w["frames"] == [1, 53] and 105 <= w["cadence_spm"] <= 112
    keys = SceneDoc.load(S._scene_file(scene)).get_params("Peg1")
    assert keys.get("x") and keys.get("y")


def test_walk_in_place_on_twos_and_left_facing(scene):
    S.build_character(scene, "gran", person="old_woman", facing="left", hair="bun")
    w = S.walk_cycle(scene, "gran", start=5, end=40, travel=False, on=2)
    assert w["on"] == 2 and w["baked_slip_px"] is None
    assert any("walking in place" in x for x in w.get("warnings", []))
    st = S._load_state(scene)
    doc = SceneDoc.load(S._scene_file(scene))
    ctx = S.Ctx(scene, "t", {}, mutate=False)
    thigh = doc.get_params(ctx.stage_id(st["rigs"]["gran"]["parts"]["leg_near_thigh"]))["rot"]
    assert thigh["keys"] and all(k["interp"] == "constant" for k in thigh["keys"])
    assert [k["frame"] for k in thigh["keys"]][:3] == [4, 6, 8]  # 0-based frames 5, 7, 9 on twos


def test_run_cycle_left_facing_mirrors(scene):
    S.build_character(scene, "boy", person="boy", facing="left")
    r = S.run_cycle(scene, "boy", start=1, cycles=1)
    assert r["flight_s"] > 0 and r["legs_reached"] and r["baked_slip_px"] < 0.5
    with pytest.raises(RuntimeError):
        S.run_cycle(scene, "nobody")


def test_anatomy_reference_tool():
    r = S.anatomy_reference(person="teen_girl", height_px=500)
    assert r["sex"] == "female" and r["height_px"] == 500


@pytest.mark.slow
def test_walk_renders_with_feet_on_the_ground(scene):
    from PIL import Image
    S.add_layer(scene, "bg", style="clean")
    S.draw(scene, "bg", 1, [{"type": "rect", "x": -50, "y": -50, "w": 2020, "h": 1180, "fill": "#ffffff"}])
    S.build_character(scene, "dad", person="man", x=300, ground_y=980)
    S.walk_cycle(scene, "dad", start=1, cycles=1)
    import io
    for f in (1, 14):
        im = Image.open(io.BytesIO(S.preview_frame(scene, f, max_width=1920).data)).convert("L")
        W, H = im.size
        k = W / 1920
        row = int(978 * k)
        dark = [x for x in range(W) if im.getpixel((x, row)) < 128]
        assert dark, f"no foot touching the ground at frame {f}"
        below = [x for x in range(W) if im.getpixel((x, int(990 * k))) < 128]
        assert not below, f"something drawn below the ground at frame {f}"


def test_region_rig_walks_with_froude_scaling(scene):
    S.build_character(scene, "a", person="man", x=300)
    r = S.build_character(scene, "b", person="man", region="netherlands", x=900)
    assert r["region"]["key"] == "nl" and "caveat" in r
    wa = S.walk_cycle(scene, "a", cycles=1)
    wb = S.walk_cycle(scene, "b", cycles=1)
    ratio = (1.826 / 1.76) ** 0.5
    assert wb["speed_m_s"] == pytest.approx(wa["speed_m_s"] * ratio, rel=1e-3)
    assert wb["cadence_spm"] == pytest.approx(wa["cadence_spm"] / ratio, abs=0.1)
    assert wb["legs_reached"] and wb["baked_slip_px"] < 0.5
    assert "regions" in S.list_regions()


@pytest.mark.slow
def test_dress_and_hat_build_walk_and_render():
    sc = "qa_rig_dress"
    S.create_scene(sc, width=640, height=360, frames=24, overwrite=True)
    r = S.build_character(sc, "mia", person="girl", age=10, x=320, ground_y=340, px_per_m=190, outfit="dress",
                          hat="straw", hair="bob")
    assert r["verify"] == "ok" and "mia_skirt" in r["layers"] and len(r["layers"]) == len(anatomy.PARTS) + 1
    ctx = S.Ctx(sc, "t", {}, mutate=False)
    assert ctx.doc.parent_of(ctx.stage_id("mia_skirt")) == ctx.stage_id("mia_torso")
    w = S.walk_cycle(sc, "mia", start=1, end=24, travel=False)   # the skirt has no keys of its own: it hangs
    assert w["verify"] == "ok"
    S.preview_frame(sc, 7)
    with pytest.raises(RuntimeError):
        S.build_character(sc, "x", outfit="kilt")
    for bad in ({"skin_tone": 0}, {"face": {"ears": 1}}, {"hat": "crown"}, {"hair": "afro", "design": "classic"}):
        with pytest.raises(RuntimeError):
            S.build_character(sc, "x", **bad)
    robe = S.build_character(sc, "sam", person="man", x=500, ground_y=340, px_per_m=150, outfit="robe", hat="turban",
                             skin_tone=6, face={"nose": 0.8})
    assert "sam_skirt" in robe["layers"] and S.walk_cycle(sc, "sam", start=1, end=24, travel=False)["verify"] == "ok"
    classic = S.build_character(sc, "old", person="man", x=120, ground_y=340, px_per_m=150, design="classic")
    assert len(classic["layers"]) == len(anatomy.PARTS)

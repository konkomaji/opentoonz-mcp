import math

import pytest

from otmcp import anatomy, gait


def _rig(person, kind="walk", frames=60, fps=24, **kw):
    pr = anatomy.proportions(person=person)
    ppm = 340.0
    h = pr["stature_m"] * ppm
    j = anatomy.rest_joints(pr, h, 500, 900)
    n = gait.norms(kind, pr["age"], pr["sex"])
    return pr, j, gait.cycle(kind, j, n, h, ppm, fps, 900, frames, **kw)


def test_adult_proportions_match_drillis_contini():
    p = anatomy.proportions(person="man")
    for k, ref in (("shoulder", 0.818), ("hip", 0.530), ("knee", 0.285), ("fingertip", 0.377), ("ankle", 0.039)):
        assert abs(p[k] - ref) < 0.006, (k, p[k])
    assert abs(p["upper_arm"] - 0.186) < 0.003 and abs(p["forearm"] - 0.146) < 0.003
    assert abs(p["thigh"] + p["shank"] - (0.530 - 0.039)) < 1e-9


def test_heads_and_legs_grow_with_age():
    ages = [1, 3, 6, 10, 14, 18, 30]
    heads = [anatomy.proportions(a, "f")["heads"] for a in ages]
    legs = [anatomy.proportions(a, "f")["hip"] for a in ages[:4]]
    assert heads == sorted(heads) and heads[0] < 5 and heads[-1] > 7.3
    assert legs == sorted(legs)   # to 10 y; legs peak relative to stature in the pubertal spurt


def test_mass_fractions_sum_to_one_and_com_is_plausible():
    for person in ("toddler", "girl", "boy", "teen_girl", "man", "woman", "old_man", "old_woman"):
        pr = anatomy.proportions(person=person)
        mf = anatomy.mass_fractions(pr["age"], pr["sex"])
        total = sum(v["mass"] * (1 if k in ("head", "trunk") else 2) for k, v in mf.items())
        assert abs(total - 1) < 1e-3, person
        assert 0.53 < anatomy.com_height(pr) < 0.60, person
    assert anatomy.mass_fractions(1, "m")["head"]["mass"] > anatomy.mass_fractions(35, "m")["head"]["mass"]


def test_resolve_words_and_errors():
    assert anatomy.resolve(person="old_woman") == (75.0, "f")
    assert anatomy.resolve("teen", "boy") == (15.0, "m")
    assert anatomy.resolve(9, "girl") == (9.0, "f")
    with pytest.raises(ValueError):
        anatomy.resolve(person="robot")
    with pytest.raises(ValueError):
        anatomy.resolve(200, "m")
    with pytest.raises(ValueError):
        anatomy.resolve(30, "x")


def test_reference_scales_to_pixels():
    r = anatomy.reference(person="woman", height_px=600)
    assert r["height_px"] == 600 and abs(r["head_px"] - 600 / r["heads_tall"]) < 0.2
    assert r["front_widths_px"]["hip_breadth"] > r["front_widths_px"]["waist_breadth"]


def test_part_shapes_cover_every_rig_part():
    pr = anatomy.proportions(person="girl")
    j = anatomy.rest_joints(pr, 430, 500, 900)
    shapes = anatomy.part_shapes(pr, j, 430, hair="ponytail")
    assert set(shapes) == set(anatomy.PARTS) == set(anatomy.RIG_PARENTS)
    assert all(shapes[p] for p in anatomy.PARTS)


@pytest.mark.parametrize("person", ["man", "woman", "boy", "toddler", "old_woman"])
def test_walk_feet_planted_and_reachable(person):
    _, _, res = _rig(person)
    r = res["report"]
    assert r["legs_reached"] and r["max_foot_slip_px"] < 0.5
    assert 55 <= r["peak_knee_flexion_swing_deg"] <= 72
    for f in res["frames"]:                       # nothing below the floor
        for side in ("near", "far"):
            assert f["world"][f"{side}_toe_tip"][1] <= 900 + 1e-6


def test_adult_walk_matches_gait_literature():
    _, _, res = _rig("man")
    r = res["report"]
    assert 0.03 <= r["vertical_oscillation_m"] <= 0.065   # pelvis: about 4-5 cm
    assert 105 <= r["cadence_spm"] <= 120 and 0.65 <= r["step_length_m"] <= 0.85


def test_age_differences_in_walk():
    adult = _rig("man")[2]["report"]
    old = _rig("old_man")[2]["report"]
    tod = _rig("toddler")[2]["report"]
    assert old["step_length_m"] < adult["step_length_m"] and old["speed_m_s"] < adult["speed_m_s"]
    assert tod["cadence_spm"] > adult["cadence_spm"] + 30


def test_run_has_flight_and_exact_gravity():
    pr, j, res = _rig("man", "run", frames=400, fps=240)   # 240 fps: flight spans ~20 samples
    r = res["report"]
    assert r["flight_s"] > 0.05 and r["legs_reached"] and r["max_foot_slip_px"] < 0.5
    assert 0.05 <= r["vertical_oscillation_m"] <= 0.11 and 85 <= r["peak_knee_flexion_swing_deg"] <= 125
    # in flight the pelvis follows g exactly: second difference = g * px_per_m / fps^2 (canvas +y down)
    g_px = 9.81 * 340 / 240 ** 2
    T = r["cycle_frames"]
    beta = r["stance_fraction"]
    ys = [f["root"][1] for f in res["frames"]]
    checked = 0
    for k in range(1, len(ys) - 1):
        in_flight = all(not (((kk / T) + ph) % 1.0 < beta) for kk in (k - 1, k, k + 1) for ph in (0, 0.5))
        if in_flight:
            assert abs((ys[k + 1] - 2 * ys[k] + ys[k - 1]) - g_px) < 0.1 * g_px
            checked += 1
    assert checked >= 20


def test_walk_in_place_keeps_root_and_reports_pan():
    _, _, res = _rig("woman", travel=False)
    assert all(abs(f["root"][0]) < 1e-9 for f in res["frames"])
    assert res["report"]["background_pan_px_per_frame"] > 0


def test_ik_two_bone_exact():
    knee, ok = gait.ik2((0, 0), (30, 80), 50, 50)
    assert ok and abs(math.dist((0, 0), knee) - 50) < 1e-9 and abs(math.dist(knee, (30, 80)) - 50) < 1e-9
    assert knee[0] > 15                            # knee in front of the hip-ankle line


def test_region_stature_and_leg_proportion_follow_the_surveys():
    reg = anatomy.regions()["regions"]
    for key in ("us", "in_wb", "jp", "nl"):
        for sx in "mf":
            p = anatomy.proportions(30, sx, region=key)
            assert abs(p["stature_m"] - reg[key][sx]["stature_m"]) < 1e-9
    # a lower sitting-height ratio means relatively longer legs
    wb, jp = anatomy.proportions(30, "m", region="west_bengal"), anatomy.proportions(30, "m", region="japan")
    assert wb["hip"] > anatomy.proportions(30, "m")["hip"] > jp["hip"]
    assert anatomy.region_key("Kolkata") == "in_wb" and anatomy.region_key(None) is None
    with pytest.raises(ValueError):
        anatomy.region_key("atlantis")


def test_region_children_scale_and_heads_do_not_change():
    a = anatomy.proportions(8, "f")
    b = anatomy.proportions(8, "f", region="in_wb")
    assert b["stature_m"] < a["stature_m"] and b["heads"] == a["heads"]


def test_bmi_widens_soft_tissue_only():
    lean = anatomy.proportions(30, "m", bmi=19)
    heavy = anatomy.proportions(30, "m", bmi=32)
    assert heavy["waist_depth"] / lean["waist_depth"] == pytest.approx((32 / 19) ** 0.5)
    assert heavy["foot_length"] == lean["foot_length"] and heavy["hip"] == lean["hip"]
    assert anatomy.proportions(30, "f", region="jp", bmi="average")["bmi"] == 20.8
    with pytest.raises(ValueError):
        anatomy.proportions(30, "m", bmi="average")
    ref = anatomy.reference(person="woman", region="bangladesh", height_px=500)
    assert ref["region"]["key"] == "bd" and "averages" in ref["caveat"]


@pytest.mark.parametrize("mood", list(gait.MOODS))
def test_every_walk_mood_keeps_feet_planted(mood):
    pr = anatomy.proportions(person="woman")
    h = pr["stature_m"] * 340
    j = anatomy.rest_joints(pr, h, 500, 900)
    n, extra = gait.apply_mood(gait.norms("walk", 35, "f"), mood)
    r = gait.cycle("walk", j, n, h, 340, 24, 900, 60, **extra)["report"]
    assert r["legs_reached"] and r["max_foot_slip_px"] < 0.5


def test_mood_timing_matches_williams_beat_chart():
    def frames_per_step(mood):
        n, _ = gait.apply_mood(gait.norms("walk", 35, "m"), mood)
        return 24 * 60 / n["cadence"]
    assert 12 <= frames_per_step("natural") <= 14
    assert 15 <= frames_per_step("stroll") <= 18 and 19 <= frames_per_step("tired") <= 23
    assert 23 <= frames_per_step("sneak") <= 28 and 7.5 <= frames_per_step("cartoon") <= 9.5
    with pytest.raises(ValueError):
        gait.apply_mood({}, "angry")


def test_feature_design_covers_every_part_and_adds_a_skirt_for_a_dress():
    pr = anatomy.proportions(person="girl", age=10)
    j = anatomy.rest_joints(pr, 400, 300, 500)
    plain = anatomy.feature_shapes(pr, j, 400)
    assert set(plain) == set(anatomy.PARTS) and all(plain[p] for p in anatomy.PARTS)
    assert anatomy.part_order(plain) == list(anatomy.PARTS)
    dress = anatomy.feature_shapes(pr, j, 400, outfit="dress", hat="straw", hair="bob")
    order = anatomy.part_order(dress)
    assert set(dress) == set(anatomy.PARTS) | {"skirt"}
    # the skirt hangs over both legs and under the head and the near arm
    assert order.index("leg_near_toe") < order.index("skirt") < order.index("head") < order.index("arm_near_upper")
    assert anatomy.EXTRA_PARTS["skirt"] == ("torso", "hip")
    assert len(dress["head"]) > len(plain["head"])  # the hat and the shadow of its brim


def test_feature_limbs_have_no_line_across_the_joint():
    """A limb is a fill, a shadow, and an OPEN outline that leaves the upper joint free, so no seam shows."""
    pr = anatomy.proportions(person="man")
    j = anatomy.rest_joints(pr, 600, 300, 700)
    shin = anatomy.feature_shapes(pr, j, 600)["leg_near_shin"]
    fills = [sh for sh in shin if sh["type"] == "polygon"]
    lines = [sh for sh in shin if sh["type"] == "path"]
    assert fills and all(sh["stroke"] is None for sh in fills)
    assert len(lines) == 1 and lines[0]["closed"] is False
    kx, ky = j["knee"]
    assert min(abs(px - kx) + abs(py - ky) for px, py in lines[0]["points"]) > 5  # the line starts below the knee cap


def test_appeal_enlarges_a_childs_head_and_leaves_an_adults_alone():
    def head_height(person, appeal):
        pr = anatomy.proportions(person=person)
        j = anatomy.rest_joints(pr, 400, 300, 500)
        ys = [p[1] for sh in anatomy.feature_shapes(pr, j, 400, appeal=appeal)["head"] for p in sh.get("points", [])]
        return max(ys) - min(ys)
    assert head_height("girl", 1.0) > head_height("girl", 0.0) * 1.3
    assert abs(head_height("man", 1.0) - head_height("man", 0.0)) < 1e-6


@pytest.mark.parametrize("outfit", sorted(anatomy.OUTFITS))
def test_every_outfit_draws_every_part(outfit):
    for person in ("toddler", "girl", "man", "old_woman"):
        pr = anatomy.proportions(person=person)
        j = anatomy.rest_joints(pr, 400, 300, 500)
        shapes = anatomy.feature_shapes(pr, j, 400, outfit=outfit)
        assert set(shapes) - {"skirt"} == set(anatomy.PARTS) and all(shapes.values())
        assert ("skirt" in shapes) == ("skirt" in anatomy.OUTFITS[outfit])
        if "skirt" in shapes:   # the hem is where the cut says: a robe reaches the ankle, a jacket the hip
            hem = max(p[1] for p in shapes["skirt"][0]["points"])
            want = {"hip": j["hip"][1], "thigh": j["hip"][1], "knee": j["knee"][1], "ankle": j["ankle"][1]}
            length = anatomy.OUTFITS[outfit]["skirt"][1]
            assert hem > j["hip"][1] and (hem <= want[length] + 4 if length in ("knee", "ankle") else hem < j["knee"][1])


def test_skin_tones_run_from_light_to_deep_and_the_line_stays_darker():
    lum = [anatomy._luma(c) for c in anatomy.SKIN_TONES]
    assert len(lum) == 10 and lum == sorted(lum, reverse=True)
    pr = anatomy.proportions(person="woman")
    j = anatomy.rest_joints(pr, 400, 300, 500)
    for tone in range(1, 11):
        head = anatomy.feature_shapes(pr, j, 400, skin_tone=tone)["head"]
        face = next(sh for sh in head if sh.get("fill") == anatomy.SKIN_TONES[tone - 1] and sh.get("stroke"))
        assert anatomy._luma(face["stroke"]) < anatomy._luma(face["fill"]) - 0.08   # the outline still reads
    explicit = anatomy.feature_shapes(pr, j, 400, skin_tone=9, colors={"skin": "#abcdef"})["head"]
    assert any(sh.get("fill") == "#abcdef" for sh in explicit)   # a colour I give wins over the tone number


def test_face_controls_shape_one_profile_and_nothing_ties_them_to_a_region():
    import inspect
    pr = anatomy.proportions(person="man")
    j = anatomy.rest_joints(pr, 400, 300, 500)

    def profile(**face):
        head = anatomy.feature_shapes(pr, j, 400, face=face)["head"]
        return max(p[0] for sh in head if sh["type"] == "polygon" and sh.get("stroke") for p in sh["points"])
    assert profile(nose=1) > profile() > profile(nose=-1)   # the nose is the farthest point forward
    assert anatomy.feature_shapes(pr, j, 400, face={"lips": 1}) != anatomy.feature_shapes(pr, j, 400)
    assert anatomy.feature_shapes(pr, j, 400, face={"eyes": -1}) != anatomy.feature_shapes(pr, j, 400)
    assert "region" not in inspect.signature(anatomy.feature_shapes).parameters


def test_a_headscarf_or_turban_covers_the_hair():
    pr = anatomy.proportions(person="woman")
    j = anatomy.rest_joints(pr, 400, 300, 500)
    hair = anatomy.FEATURE_COLORS["hair"]
    bare = anatomy.feature_shapes(pr, j, 400, hair="long")["head"]
    assert any(sh.get("fill") == hair for sh in bare)
    for hat in ("headscarf", "turban"):
        head = anatomy.feature_shapes(pr, j, 400, hair="long", hat=hat)["head"]
        assert not any(sh.get("fill") == hair and sh["type"] == "polygon" and sh.get("stroke") for sh in head)
    afro = anatomy.feature_shapes(pr, j, 400, hair="afro")["head"]
    assert afro[0]["type"] == "circle" and afro[0]["fill"] == hair   # the mass sits behind the face


def test_shadow_tone_is_darker_and_keeps_its_colour():
    import colorsys

    def hsv(c):
        return colorsys.rgb_to_hsv(*(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)))
    base, shadow = "#f6d2b4", anatomy.kage("#f6d2b4", skin=True)
    assert hsv(shadow)[2] < hsv(base)[2] and hsv(shadow)[1] > hsv(base)[1]  # darker and richer, not greyer
    assert anatomy.kage("#ffffff") != "#ffffff" and anatomy.kage("#4b6a9c") != anatomy.kage("#4b6a9c", k=0.5)

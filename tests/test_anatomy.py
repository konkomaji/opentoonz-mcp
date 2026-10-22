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

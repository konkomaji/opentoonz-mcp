import math

import pytest

from otmcp import physics as P


def test_bounce_first_fall_is_exact_parabola():
    h, N = 400.0, 12
    s = P.bounce(h, N, 0.8, bounces=3)
    for k in range(N + 1):
        assert s[k]["h"] == pytest.approx(h * (1 - (k / N) ** 2), abs=1e-9)
    assert s[N]["contact"]


def test_bounce_heights_and_durations_scale_with_restitution():
    h, N, e = 400.0, 12, 0.8
    s = P.bounce(h, N, e, bounces=1)
    apex2 = max(x["h"] for x in s if x["frame"] > N)
    # second hop lasts 2*N*e = 19.2 frames; integer sampling can miss the exact apex by < 1 frame
    assert apex2 == pytest.approx(h * e * e, rel=0.01)
    assert len(s) - 1 == math.ceil(N + 2 * N * e)


def test_jump_apex_and_landing():
    j = P.jump(height=300, air_frames=16, crouch=40, crouch_frames=6, land_frames=8)
    air = j["y"][7:7 + 16]
    assert min(air) == pytest.approx(-300, abs=0.01)  # apex at T/2
    assert air[-1] == 0.0
    assert j["gravity"] == pytest.approx(8 * 300 / 16 ** 2)


@pytest.mark.parametrize("zeta", [0.3, 0.45, 0.6])
def test_spring_overshoot_matches_theory(zeta):
    xs = [0.0] + [100.0] * 120
    ys = P.second_order(xs, f=2.0, zeta=zeta)
    over = (max(ys) - 100) / 100
    assert over == pytest.approx(P.overshoot_ratio(zeta), abs=0.03)
    assert ys[-1] == pytest.approx(100, abs=0.5)


def test_smooth_damp_converges_without_overshoot():
    ys = P.smooth_damp([0.0] + [500.0] * 100, smooth_time_frames=8)
    assert max(ys) <= 500 + 1e-9 and ys[-1] == pytest.approx(500, abs=0.01)


def test_trauma_shake_decays_and_is_deterministic():
    a = P.trauma_shake(40, trauma=1.0, decay_per_s=1.2)
    b = P.trauma_shake(40, trauma=1.0, decay_per_s=1.2)
    assert a == b
    assert all(abs(x["x"]) < 1e-9 and abs(x["y"]) < 1e-9 for x in a[21:])  # trauma 0 after 0.83 s


def test_thin_reproduces_samples():
    samples = [(i, 100 * math.sin(i / 30)) for i in range(60)]  # gentle ease-like curve
    keys = P.thin(samples, 0.25)
    assert len(keys) < len(samples) / 2
    for f, v in samples:
        for (f0, v0), (f1, v1) in zip(keys, keys[1:]):
            if f0 <= f <= f1:
                assert abs(v0 + (v1 - v0) * (f - f0) / (f1 - f0) - v) <= 0.25 + 1e-9


def test_blink_chart_and_lipsync():
    cells = P.blink_cells("standard", {"O": 1, "H": 2, "C": 3})
    assert cells == [2, 3, 3, 2]
    m = P.text_to_mouth("hana", 4)
    assert m[0] == "open" and m.count("closed") >= 2

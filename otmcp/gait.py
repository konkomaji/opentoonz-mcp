"""Walk and run cycles for the side-view part rig, from gait biomechanics.

Model
-----
* Timing: cadence (steps/min) and speed (m/s) by age and sex give the cycle time and step length.
  Walk norms: Sutherland et al. (1988, children 1-7 y), Bohannon & Andrews (2011, adults by decade),
  Hollman et al. (2011, older adults). Run norms: recreational jogging values (cadence 160-180 spm,
  ground contact 0.25-0.30 s, vertical oscillation 6-10 cm).
* Feet: each stance foot is planted in world space and rolls heel -> flat -> ball (the three
  rockers of Perry & Burnfield, "Gait Analysis", 2010); the toe stays flat on the ground while the
  heel rises. The swing ankle follows a cubic Hermite path with a lift bump peaking early in swing,
  and the toe is never allowed below the ground.
* Pelvis height: walking is an inverted pendulum, so the hip rides as high as the stance leg allows
  (smoothed and lowered just enough that every stance foot is reachable); low at double support,
  high at midstance. Running is a spring-mass system: an exact gravity parabola in flight and a
  linear-spring (harmonic) arc in stance that matches position, velocity and acceleration (-g) at
  touchdown and take-off.
* Legs: analytic two-bone IK (hip -> knee -> ankle) with the knee forward, so planted feet do not slide.
* Arms swing opposite to the same-side leg with a short lag; the elbow flexes more on the forward swing.
All output angles are screen counter-clockwise degrees for a character facing +x (canvas +y down).
"""
from __future__ import annotations

import math

G = 9.81

# age -> walk norms: cadence m, f (steps/min), speed m, f (m/s), stance fraction, heel-strike foot pitch,
# toe-off pitch, shoulder mean, shoulder amplitude, elbow mean, elbow amplitude, trunk lean, lift / H
_WALK = [
    (1, 176, 176, 0.64, 0.64, 0.67, 2, -18, 22, 3, 75, 4, 6, 0.012),
    (3, 154, 154, 0.86, 0.86, 0.63, 10, -32, 0, 11, 22, 8, 4, 0.020),
    (7, 143, 143, 1.14, 1.14, 0.61, 16, -42, -4, 14, 20, 10, 3, 0.025),
    (10, 130, 132, 1.24, 1.24, 0.60, 18, -46, -5, 15, 20, 10, 3, 0.020),
    (15, 116, 120, 1.32, 1.30, 0.60, 20, -50, -6, 16, 20, 10, 3, 0.015),
    (25, 108, 116, 1.38, 1.34, 0.60, 20, -50, -6, 17, 20, 10, 3, 0.012),
    (45, 108, 114, 1.42, 1.36, 0.60, 19, -48, -6, 15, 20, 9, 3, 0.012),
    (65, 108, 112, 1.30, 1.24, 0.62, 15, -40, -4, 11, 22, 7, 6, 0.008),
    (80, 106, 110, 1.05, 0.98, 0.64, 10, -32, -2, 7, 25, 5, 10, 0.004),
]
# age -> run norms: cadence m, f, speed m, f, stance fraction (per foot), touchdown pitch, toe-off pitch,
# shoulder mean, shoulder amplitude, elbow mean, elbow amplitude, trunk lean, lift / H
_RUN = [
    (3, 205, 205, 1.8, 1.8, 0.44, 4, -30, 5, 20, 70, 10, 6, 0.06),
    (7, 190, 190, 2.6, 2.6, 0.38, 6, -38, 0, 28, 80, 12, 6, 0.10),
    (15, 172, 176, 3.2, 3.0, 0.37, 8, -42, -4, 32, 85, 12, 7, 0.12),
    (25, 166, 172, 3.3, 3.0, 0.37, 8, -42, -4, 32, 85, 12, 7, 0.12),
    (45, 164, 170, 3.0, 2.8, 0.38, 8, -40, -4, 28, 85, 10, 7, 0.11),
    (65, 160, 164, 2.4, 2.2, 0.40, 6, -34, -2, 22, 85, 8, 9, 0.08),
    (80, 156, 160, 1.9, 1.8, 0.43, 4, -28, -2, 16, 85, 6, 11, 0.06),
]
# Walk moods (Williams, "The Animator's Survival Kit", pp. 109-110 beat chart and the character walks;
# Blair, "Cartoon Animation"): multipliers on the measured norms. cadence x / speed x / bounce / arm swing /
# lean (deg) / lift x / stance + / crouch (fraction of leg) / double bounce / head drop (deg).
MOODS = {
    "natural": (1.00, 1.00, 1.0, 1.0, 0, 1.0, 0.00, 0.00, 0.000, 0),
    "brisk": (1.12, 1.25, 1.0, 1.3, 3, 1.3, -0.02, 0.00, 0.000, 0),
    "stroll": (0.75, 0.70, 0.8, 0.7, 0, 0.8, 0.02, 0.00, 0.000, 0),     # 16 frames a step
    "tired": (0.62, 0.50, 0.6, 0.4, 8, 0.4, 0.04, 0.02, 0.000, 12),     # 20 frames, low clearance, head down
    "proud": (0.95, 1.00, 0.8, 1.2, -4, 1.1, 0.00, 0.00, 0.000, -4),    # chest out, chin up
    "happy": (1.10, 1.10, 1.2, 1.2, 0, 1.4, 0.00, 0.00, 0.012, 0),      # double bounce
    "sneak": (0.50, 0.35, 0.6, 0.3, 12, 2.5, 0.08, 0.08, 0.000, -6),    # 24 frames, crouched, high lift
    "heavy": (0.85, 0.75, 1.5, 0.8, 2, 0.8, 0.04, 0.02, 0.000, 0),      # long contact, deep down
    "cartoon": (1.50, 1.20, 2.0, 1.5, 4, 2.0, 0.00, 0.00, 0.000, 0),    # 8 frames a step
}


def apply_mood(n: dict, mood: str) -> dict:
    """Norms adjusted for a walk mood; also returns the cycle() extras."""
    if mood not in MOODS:
        raise ValueError(f"mood must be one of {list(MOODS)}")
    ck, sk, bounce, arm, lean, lift, stance, crouch, dbl, head = MOODS[mood]
    n = dict(n, cadence=n["cadence"] * ck, speed=n["speed"] * sk, lift=n["lift"] * lift,
             stance=min(0.75, n["stance"] + stance))
    return n, {"bounce": bounce, "arm_swing": arm, "lean_add": lean, "crouch": crouch, "double_bounce": dbl,
               "head_drop": head}


_FIELDS = ("cadence", "speed", "stance", "hs_pitch", "to_pitch", "sh_mean", "sh_amp", "el_mean", "el_amp",
           "lean", "lift")


def norms(kind: str, age: float, sex: str) -> dict:
    """Gait parameters for 'walk' or 'run' at an age (years) and sex ('m'|'f'), interpolated by age."""
    tab = _WALK if kind == "walk" else _RUN
    if kind not in ("walk", "run"):
        raise ValueError("kind must be walk or run")
    a = min(max(age, tab[0][0]), tab[-1][0])
    for lo, hi in zip(tab, tab[1:]):
        if lo[0] <= a <= hi[0]:
            t = (a - lo[0]) / (hi[0] - lo[0])
            row = [x + (y - x) * t for x, y in zip(lo, hi)]
            break
    else:
        row = list(tab[-1])
    f = sex == "f"
    vals = [row[2 if f else 1], row[4 if f else 3]] + row[5:]
    return dict(zip(_FIELDS, vals))


def _smoothstep(t):
    t = min(1.0, max(0.0, t))
    return t * t * (3 - 2 * t)


def _hermite(p0, p1, m0, m1, t):
    t2, t3 = t * t, t * t * t
    return (2 * t3 - 3 * t2 + 1) * p0 + (t3 - 2 * t2 + t) * m0 + (-2 * t3 + 3 * t2) * p1 + (t3 - t2) * m1


def _rot(v, deg):
    """Rotate a canvas vector (+y down) screen-counter-clockwise by deg."""
    a = math.radians(deg)
    x, y = v
    return (x * math.cos(a) + y * math.sin(a), -x * math.sin(a) + y * math.cos(a))


def screen_angle(v) -> float:
    """Screen CCW angle (deg) of a canvas vector."""
    return math.degrees(math.atan2(-v[1], v[0]))


def ik2(hip, ankle, l1, l2):
    """Two-bone IK in the sagittal plane, knee pointing forward (+x). Returns (knee, reached)."""
    dx, dy = ankle[0] - hip[0], ankle[1] - hip[1]
    d = math.hypot(dx, dy)
    reached = d <= l1 + l2 - 1e-9
    d = min(max(d, abs(l1 - l2) + 1e-6), l1 + l2 - 1e-6)
    ux, uy = dx / (math.hypot(dx, dy) or 1e-9), dy / (math.hypot(dx, dy) or 1e-9)
    a = (l1 * l1 - l2 * l2 + d * d) / (2 * d)
    h = math.sqrt(max(l1 * l1 - a * a, 0.0))
    n = (-uy, ux) if -uy > 0 or (-uy == 0 and ux > 0) else (uy, -ux)  # perpendicular with +x component
    return (hip[0] + ux * a + n[0] * h, hip[1] + uy * a + n[1] * h), reached


class Foot:
    """Rigid foot from heel to ball (toe hinged at the ball), in rest-pose offsets from the ankle."""

    def __init__(self, heel, ball, toe):
        self.heel, self.ball, self.toe = heel, ball, toe

    def ankle_from(self, pivot_name: str, pivot_world, pitch: float):
        off = _rot(getattr(self, pivot_name), pitch)
        return (pivot_world[0] - off[0], pivot_world[1] - off[1])

    def point(self, name, ankle, pitch):
        off = _rot(getattr(self, name), pitch)
        return (ankle[0] + off[0], ankle[1] + off[1])

    def toe_tip(self, ankle, pitch, toe_abs):
        """Toe tip with the toe hinged at the ball at absolute pitch toe_abs."""
        ball = self.point("ball", ankle, pitch)
        off = _rot((self.toe[0] - self.ball[0], self.toe[1] - self.ball[1]), toe_abs)
        return (ball[0] + off[0], ball[1] + off[1])

    def ankle_on_tip(self, tip, pitch, toe_abs):
        """Ankle when the toe tip is planted at tip (toe rocker)."""
        off = _rot((self.toe[0] - self.ball[0], self.toe[1] - self.ball[1]), toe_abs)
        ball = (tip[0] - off[0], tip[1] - off[1])
        return self.ankle_from("ball", ball, pitch)


ROLL = {"walk": (0.20, 0.58), "run": (0.15, 0.50)}   # stance fraction where the foot is flat / the heel rises
MAX_BALL_PITCH, MAX_TOE_ROLL = 60.0, 40.0             # heel rise about the ball, then roll on to the toe tip


def _stance_pitch(s, n, kind):
    """Foot pitch and roll pivot at stance fraction s in [0,1]."""
    flat_at, rise_at = ROLL[kind]
    if s < flat_at:
        return n["hs_pitch"] * (1 - _smoothstep(s / flat_at)), "heel"
    if s < rise_at:
        return 0.0, "heel"
    return n["to_pitch"] * ((s - rise_at) / (1 - rise_at)) ** 1.6, "ball"


def cycle(kind: str, rest: dict, n: dict, height_px: float, px_per_m: float, fps: float, ground: float,
          frames: int, travel: bool = True, bounce: float = 1.0, arm_swing: float = 1.0, lean_add: float = 0.0,
          phase: float = 0.0, crouch: float = 0.0, double_bounce: float = 0.0, head_drop: float = 0.0) -> dict:
    """Per-frame rig motion. rest: rest_joints(); n: norms() (possibly overridden).

    Returns {frames: [{root: (dx, dy), angles: {part: local screen-CCW deg}, world: {...}}], report}."""
    T = 2 * 60.0 / n["cadence"] * fps                       # frames per cycle (two steps)
    v = n["speed"] * px_per_m / fps                         # px / frame
    beta = n["stance"]
    hip0 = rest["hip"]
    l1 = math.dist(rest["hip"], rest["knee"])
    l2 = math.dist(rest["knee"], rest["ankle"])
    L = l1 + l2
    A0 = rest["ankle"]
    foot = Foot(*[(rest[k][0] - A0[0], rest[k][1] - A0[1]) for k in ("heel", "ball", "toe")])
    lift = n["lift"] * height_px
    rise_at = ROLL[kind][1]
    s_mid = 0.42 if kind == "walk" else 0.25   # hip over the flat-foot ankle at this stance fraction
    reach_to = 0.94  # hip-ankle distance at toe-off: knee about 40 deg (Perry & Burnfield)
    report: dict = {"kind": kind, "cycle_frames": round(T, 3), "cadence_spm": round(n["cadence"], 1),
                    "speed_m_s": round(n["speed"], 3), "step_length_m": round(n["speed"] * 60 / n["cadence"], 3),
                    "stance_fraction": round(beta, 3)}

    def X(t):
        return hip0[0] + v * t

    def stance_anchor(t_hs):
        """World heel / ball of a step striking at frame t_hs: the hip passes over the flat-foot ankle at
        stance fraction s_mid, so the lead leg is about 20 deg from vertical at heel strike."""
        ank = (X(t_hs + s_mid * beta * T), A0[1])
        return {"heel": foot.point("heel", ank, 0.0), "ball": foot.point("ball", ank, 0.0)}

    def foot_state(t, leg_phase, hipfn=None):
        """dict(ankle, pitch, toe (absolute pitch), stance, loaded, pivot, target) of one foot at frame t.
        loaded: heel still down, so this leg limits how high the hip can ride. With hipfn (frame -> hip),
        a rising heel lifts further when the hip has moved out of reach (forefoot rocker, then toe tip)."""
        p = ((t / T) + leg_phase) % 1.0
        t_hs = t - p * T
        if p < beta:
            anc = stance_anchor(t_hs)
            s = p / beta
            pitch, piv = _stance_pitch(s, n, kind)
            ank = foot.ankle_from(piv, anc[piv], pitch)
            target, toe = anc[piv], max(pitch, 0.0)
            if hipfn is not None and s >= rise_at:
                hip = hipfn(t)

                # reach target eases from the hip-ankle distance at heel-rise onset down to reach_to * L,
                # so the heel peels off smoothly instead of snapping up
                t_rise = t_hs + rise_at * beta * T
                d0 = math.dist(hipfn(t_rise), foot.ankle_from("ball", anc["ball"], 0.0))
                u = _smoothstep((s - rise_at) / (1 - rise_at))
                goal = max(reach_to * L, d0 + (reach_to * L - d0) * u) if d0 > reach_to * L else reach_to * L

                def far(pp, q=0.0):
                    a_ = foot.ankle_from("ball", anc["ball"], pp) if q == 0.0 else foot.ankle_on_tip(tip, pp + q, q)
                    return math.dist(hip, a_) > goal + 1e-9

                tip = foot.toe_tip(foot.ankle_from("ball", anc["ball"], 0.0), 0.0, 0.0)
                if far(pitch):
                    if far(-MAX_BALL_PITCH):                 # toe rocker: roll on to the toe tip
                        lo, hi = 0.0, -MAX_TOE_ROLL
                        for _ in range(40):
                            mid = (lo + hi) / 2
                            lo, hi = (mid, hi) if far(-MAX_BALL_PITCH, mid) else (lo, mid)
                        toe = hi
                        pitch = -MAX_BALL_PITCH + toe
                        ank, piv, target = foot.ankle_on_tip(tip, pitch, toe), "toe_tip", tip
                    else:                                    # forefoot rocker: raise the heel about the ball
                        lo, hi = pitch, -MAX_BALL_PITCH
                        for _ in range(40):
                            mid = (lo + hi) / 2
                            lo, hi = (mid, hi) if far(mid) else (lo, mid)
                        pitch = hi
                        ank = foot.ankle_from("ball", anc["ball"], pitch)
            return {"ankle": ank, "pitch": pitch, "toe": toe, "stance": True, "loaded": s < rise_at,
                    "pivot": piv, "target": target}
        s = (p - beta) / (1 - beta)                          # swing: toe-off -> next heel strike
        t_to = t_hs + beta * T
        st0 = foot_state(t_to - 1e-6, leg_phase, hipfn)     # take-off pose and velocity from the end of stance
        stp = foot_state(t_to - 0.5, leg_phase, hipfn)
        a0, pto = st0["ankle"], st0["pitch"]
        ap = stp["ankle"]
        phs = n["hs_pitch"]
        a1 = foot.ankle_from("heel", stance_anchor(t_hs + T)["heel"], phs)
        dur = (1 - beta) * T
        x = _hermite(a0[0], a1[0], (a0[0] - ap[0]) / 0.5 * dur, 0.15 * (a1[0] - a0[0]), s)
        y = _hermite(a0[1], a1[1], (a0[1] - ap[1]) / 0.5 * dur, 0.0, s)
        y -= lift * s * (1 - s) ** 1.6 / (0.385 * 0.615 ** 1.6)   # lift bump, peak at 38% of swing
        pitch = pto + (phs - pto) * _smoothstep(min(1.0, s / 0.85))
        toe = pitch - pto * max(0.0, 1 - s / 0.3) + st0["toe"] * max(0.0, 1 - s / 0.3)
        low = max(foot.point("heel", (x, y), pitch)[1], foot.point("ball", (x, y), pitch)[1],
                  foot.toe_tip((x, y), pitch, toe)[1])
        if low > ground:                                     # never through the floor
            y -= low - ground
        if hipfn is not None:                                # late swing in flight: bend rather than overreach
            hip = hipfn(t)
            d = math.dist(hip, (x, y))
            if d > 0.995 * L:
                x, y = hip[0] + (x - hip[0]) * 0.995 * L / d, hip[1] + (y - hip[1]) * 0.995 * L / d
        # the toe leaves the ground bent up and comes back in line with the foot over 30% of swing
        return {"ankle": (x, y), "pitch": pitch, "toe": toe,
                "stance": False, "loaded": False, "pivot": None, "target": None}

    # one cycle of foot states -> pelvis height curve
    N = max(8, int(math.ceil(T)) * 4)
    ts = [k * T / N for k in range(N)]
    feet = [[foot_state(t, ph) for t in ts] for ph in (phase, phase + 0.5)]

    def lowest_hip(i, t, reach):
        """Largest canvas y (lowest hip) any loaded leg demands at sample i."""
        need = -math.inf
        for f in feet:
            fs = f[i]
            if fs["loaded"]:
                dx = fs["ankle"][0] - X(t)
                need = max(need, fs["ankle"][1] - math.sqrt(max(reach * reach - dx * dx, 0.0)))
        return need

    if kind == "walk":
        base = _fourier(_fill_periodic([lowest_hip(i, t, 0.985 * L) for i, t in enumerate(ts)]), [2, 4], T)
        top = min(base(t) for t in ts)

        def curve(t):                                        # exact periodic function of the frame
            return top + (base(t) - top) * bounce            # bounce deepens the dips below the high point
    else:
        curve = _run_pelvis(T, beta, n, px_per_m, fps, A0, L, bounce, report)
    drop = max([0.0] + [lowest_hip(i, t, 0.997 * L) - curve(t) for i, t in enumerate(ts)])
    hy = [curve(t) + drop for t in ts]                       # lowered just enough that every loaded foot reaches
    report["vertical_oscillation_m"] = round((max(hy) - min(hy)) / px_per_m, 4)
    report["mean_hip_drop_m"] = round((sum(hy) / N - hip0[1]) / px_per_m, 4)

    def hip_y(t):
        # crouch lowers the hips (bent knees, sneak); double_bounce adds two extra dips per step (happy)
        extra = crouch * L + double_bounce * L * (1 - math.cos(8 * math.pi * t / T)) / 2
        return curve(t) + drop + extra

    def hipfn(t):
        return (X(t), hip_y(t))

    out = []
    max_slip, knee_swing, knee_stance, reached_all = 0.0, 0.0, 0.0, True
    lean = n["lean"] + lean_add
    for k in range(frames):
        t = float(k)
        hip = (X(t), hip_y(t))
        ang, world = {}, {"hip": hip}
        for side, ph in (("near", phase), ("far", phase + 0.5)):
            fs = foot_state(t, ph, hipfn)
            ank, pitch = fs["ankle"], fs["pitch"]
            knee, ok = ik2(hip, ank, l1, l2)
            reached_all &= ok
            th = screen_angle((knee[0] - hip[0], knee[1] - hip[1])) + 90.0
            sh = screen_angle((ank[0] - knee[0], ank[1] - knee[1])) + 90.0
            ang[f"leg_{side}_thigh"] = th
            ang[f"leg_{side}_shin"] = sh - th
            ang[f"leg_{side}_foot"] = pitch - sh
            ang[f"leg_{side}_toe"] = fs["toe"] - pitch
            if fs["stance"]:
                knee_stance = max(knee_stance, th - sh)
                # double check by forward kinematics of the output angles: the planted point must not move
                kf = _add(hip, _rot((0.0, l1), th))
                af = _add(kf, _rot((0.0, l2), sh))
                planted = (foot.toe_tip(af, pitch, fs["toe"]) if fs["pivot"] == "toe_tip"
                           else foot.point(fs["pivot"], af, pitch))
                max_slip = max(max_slip, math.dist(planted, fs["target"]))
            else:
                knee_swing = max(knee_swing, th - sh)
            world[f"{side}_ankle"], world[f"{side}_knee"] = ank, knee
            world[f"{side}_toe_tip"] = foot.toe_tip(ank, pitch, fs["toe"])
            world[f"{side}_planted"] = fs["pivot"] if fs["stance"] else None
            world[f"{side}_step"] = math.floor(t / T + ph)
        p_near = ((t / T) + phase) % 1.0
        trunk = -lean - 1.0 * math.cos(4 * math.pi * p_near)        # a little more lean at each contact
        ang["torso"] = trunk
        ang["head"] = -trunk * 0.7 - head_drop
        for side, ph in (("near", phase), ("far", phase + 0.5)):
            p = ((t / T) + ph - 0.5 - 0.04) % 1.0                    # opposite the same-side leg, slight lag
            c = math.cos(2 * math.pi * p)
            ang[f"arm_{side}_upper"] = n["sh_mean"] + n["sh_amp"] * arm_swing * c
            ang[f"arm_{side}_fore"] = n["el_mean"] + n["el_amp"] * arm_swing * max(c, -0.3)
            ang[f"arm_{side}_hand"] = 4.0 * math.cos(2 * math.pi * (p - 0.08))
        shift = 0.0 if travel else hip[0] - hip0[0]
        if shift:
            world = {kk: (w[0] - shift, w[1]) if isinstance(w, tuple) else w for kk, w in world.items()}
        out.append({"root": (hip[0] - hip0[0] - shift, hip[1] - hip0[1]), "angles": ang, "world": world})
    report.update({"frames": frames, "max_foot_slip_px": round(max_slip, 3),
                   "peak_knee_flexion_swing_deg": round(knee_swing, 1),
                   "peak_knee_flexion_stance_deg": round(knee_stance, 1), "legs_reached": reached_all,
                   "travel_px": round(v * (frames - 1), 1) if travel else 0.0,
                   "background_pan_px_per_frame": 0.0 if travel else round(v, 3)})
    return {"frames": out, "report": report}


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1])


def _fill_periodic(ys: list[float]) -> list[float]:
    """Linear interpolation over -inf gaps (no leg loaded) of a periodic signal."""
    N = len(ys)
    known = [i for i, y in enumerate(ys) if y != -math.inf]
    if not known:
        raise ValueError("no loaded foot in the whole cycle: stance fraction too small")
    out = list(ys)
    for i in range(N):
        if out[i] == -math.inf:
            a = max([k for k in known if k < i], default=known[-1] - N)
            b = min([k for k in known if k > i], default=known[0] + N)
            t = (i - a) / (b - a)
            out[i] = ys[a % N] * (1 - t) + ys[b % N] * t
    return out


def _fourier(ys: list[float], keep: list[int], period: float):
    """Low-pass a periodic signal sampled evenly over one period: keep the mean and the listed
    harmonics, returned as an exact function of time."""
    N = len(ys)
    mean = sum(ys) / N
    terms = []
    for h in keep:
        a = sum(y * math.cos(2 * math.pi * h * i / N) for i, y in enumerate(ys)) * 2 / N
        b = sum(y * math.sin(2 * math.pi * h * i / N) for i, y in enumerate(ys)) * 2 / N
        terms.append((h, a, b))

    def f(t):
        u = 2 * math.pi * t / period
        return mean + sum(a * math.cos(h * u) + b * math.sin(h * u) for h, a, b in terms)
    return f


def _spring_stance(Ts: float, vu: float) -> tuple[float, float]:
    """Linear-spring stance arc y(tau) = y_low + C (1 - cos(w tau)), tau in [-Ts/2, Ts/2]:
    solve w, C so that the take-off velocity is vu and the take-off acceleration is -g (spring unloaded)."""
    def f(w):
        phi = w * Ts / 2
        return w * math.tan(phi) + G / vu if vu > 0 else math.cos(phi)
    lo, hi = math.pi / Ts * 1.0000001, 2 * math.pi / Ts * 0.9999999   # phi in (pi/2, pi)
    for _ in range(200):
        mid = (lo + hi) / 2
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    w = (lo + hi) / 2
    phi = w * Ts / 2
    C = vu / (w * math.sin(phi)) if vu > 0 else G / (w * w)
    return w, C


def _run_pelvis(T, beta, n, px_per_m, fps, A0, L, bounce, report):
    """Spring-mass pelvis height (canvas y) as an exact function of the frame. The caller lowers it
    until every loaded foot reaches."""
    step = T / 2 / fps                                  # seconds per step
    Ts = beta * T / fps                                 # stance seconds (one foot; one stance per step)
    tf = max(step - Ts, 0.0)
    vu = G * tf / 2                                     # take-off vertical speed (m/s)
    w, C = _spring_stance(Ts, vu)
    phi = w * Ts / 2
    rise_stance = C * (1 - math.cos(phi))
    report.update({"flight_s": round(tf, 4), "contact_s": round(Ts, 4), "spring_hz": round(w / (2 * math.pi), 3)})

    def height_m(u):
        """Pelvis height above its lowest point (m); u = seconds from midstance, period one step."""
        u = (u + step / 2) % step - step / 2
        if abs(u) <= Ts / 2:
            return C * (1 - math.cos(w * u))
        tt = abs(u) - Ts / 2                            # ballistic flight from take-off
        return rise_stance + vu * tt - 0.5 * G * tt * tt

    t_mid0 = 0.5 * beta * T                             # middle of the near-foot contact (frames)
    low = A0[1] - L * math.cos(math.radians(20))       # knee about 40 deg flexed over the planted foot
    # bounce != 1 scales the excursion (flight then reads as bounce * g; 1 keeps gravity exact)
    report["flight_gravity_m_s2"] = round(G * bounce, 3)
    return lambda t: low - bounce * height_m((t - t_mid0) / fps) * px_per_m

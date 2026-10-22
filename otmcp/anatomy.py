"""Human proportions by age and sex, from published anthropometry.

Sources (fractions of stature H unless noted):
* Adult segment lengths and joint heights: Drillis & Contini (1966), as tabulated in Winter,
  "Biomechanics and Motor Control of Human Movement" (4th ed., 2009), fig. 4.1.
* Segment masses and centre-of-mass positions: de Leva (1996), J. Biomech. 29(9), adjusting
  Zatsiorsky-Seluyanov; separate male/female tables.
* Stature by age: CDC 2000 growth charts (50th percentile) for 1-18 y; NHANES 2015-2018 adult means,
  with the age-related loss reported in longitudinal studies (about 3 cm by 75 y).
* Head height in heads-per-stature and sitting-height ratio by age: growth-standard ranges
  (head height vertex-menton; sitting height / stature), interpolated between key ages.
Child mass fractions: the head share follows Jensen (1986, 1989) regressions; the rest of the body
is rescaled from the adult table, so the child mass split is an approximation (about +/-15%).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

AGE_LABELS = {"baby": 1, "infant": 1, "toddler": 3, "child": 7, "kid": 7, "preteen": 10, "teen": 15,
              "teenager": 15, "young": 25, "young_adult": 25, "adult": 35, "middle": 50, "middle_aged": 50,
              "old": 75, "elderly": 75, "senior": 75}
SEX_LABELS = {"male": "m", "m": "m", "man": "m", "boy": "m", "female": "f", "f": "f", "woman": "f", "girl": "f"}
# word -> (age, sex) for the common person words
PERSON = {"baby": (1, None), "toddler": (3, None), "boy": (8, "m"), "girl": (8, "f"), "child": (7, None),
          "kid": (7, None), "teen_boy": (15, "m"), "teen_girl": (15, "f"), "teen": (15, None),
          "man": (35, "m"), "woman": (35, "f"), "young_man": (22, "m"), "young_woman": (22, "f"),
          "old_man": (75, "m"), "old_woman": (75, "f"), "grandfather": (75, "m"), "grandmother": (75, "f")}

# age -> (stature m male, female, heads per stature, sitting-height ratio, arm length / H, neck / head)
_GROWTH = [
    (1, 0.76, 0.74, 4.6, 0.640, 0.38, 0.25),
    (3, 0.96, 0.95, 5.2, 0.575, 0.40, 0.30),
    (6, 1.16, 1.15, 5.9, 0.550, 0.42, 0.34),
    (10, 1.38, 1.38, 6.5, 0.525, 0.43, 0.37),
    (14, 1.64, 1.60, 7.0, 0.515, 0.44, 0.39),
    (18, 1.76, 1.63, 7.4, 0.520, 0.44, 0.40),
    (30, 1.76, 1.62, 7.6, 0.520, 0.44, 0.40),
    (50, 1.75, 1.61, 7.6, 0.520, 0.44, 0.40),
    (75, 1.72, 1.58, 7.4, 0.515, 0.44, 0.40),
    (90, 1.69, 1.55, 7.3, 0.510, 0.44, 0.40),
]
# de Leva (1996): mass fraction, COM position from the proximal joint as a fraction of segment length
DE_LEVA = {
    "m": {"head": (0.0694, 0.5002), "trunk": (0.4346, 0.5138), "upper_arm": (0.0271, 0.5772),
          "forearm": (0.0162, 0.4574), "hand": (0.0061, 0.7900), "thigh": (0.1416, 0.4095),
          "shank": (0.0433, 0.4459), "foot": (0.0137, 0.4415)},
    "f": {"head": (0.0668, 0.4841), "trunk": (0.4257, 0.4964), "upper_arm": (0.0255, 0.5754),
          "forearm": (0.0138, 0.4559), "hand": (0.0056, 0.7474), "thigh": (0.1478, 0.3612),
          "shank": (0.0481, 0.4416), "foot": (0.0129, 0.4014)},
}
_HEAD_MASS = [(1, 0.18), (3, 0.145), (6, 0.12), (10, 0.10), (14, 0.085), (18, None)]  # None: adult table
# adult breadths / depths (fraction of H); front = breadth, side = depth. m, f
_ADULT_W = {
    "shoulder_breadth": (0.259, 0.240), "hip_breadth": (0.191, 0.210), "waist_breadth": (0.165, 0.150),
    "chest_depth": (0.130, 0.122), "bust_depth": (0.130, 0.145), "waist_depth": (0.112, 0.100),
    "buttock_depth": (0.135, 0.148), "thigh_depth": (0.095, 0.100), "calf_depth": (0.067, 0.066),
    "ankle_depth": (0.040, 0.037), "upper_arm_depth": (0.058, 0.054), "forearm_depth": (0.046, 0.040),
    "wrist_depth": (0.030, 0.027), "neck_depth": (0.068, 0.060), "foot_length": (0.152, 0.150),
    "foot_height": (0.039, 0.039),
}
# children: same fractions with smaller sex differences and rounder bodies (puberty blends them in)
_CHILD_W = {"shoulder_breadth": 0.235, "hip_breadth": 0.185, "waist_breadth": 0.17, "chest_depth": 0.135,
            "bust_depth": 0.135, "waist_depth": 0.13, "buttock_depth": 0.13, "thigh_depth": 0.10,
            "calf_depth": 0.07, "ankle_depth": 0.045, "upper_arm_depth": 0.06, "forearm_depth": 0.05,
            "wrist_depth": 0.035, "neck_depth": 0.07, "foot_length": 0.155, "foot_height": 0.042}


def _lerp_table(table, age, col):
    if age <= table[0][0]:
        return table[0][col]
    for a, b in zip(table, table[1:]):
        if a[0] <= age <= b[0]:
            t = (age - a[0]) / (b[0] - a[0])
            return a[col] + (b[col] - a[col]) * t
    return table[-1][col]


def resolve(age=None, sex=None, person: str | None = None) -> tuple[float, str]:
    """(age years, 'm'|'f') from an age number/label, a sex word, or a person word ('girl', 'old_man')."""
    pa, ps = (None, None)
    if person:
        key = str(person).lower().replace(" ", "_").replace("-", "_")
        if key not in PERSON:
            raise ValueError(f"person must be one of {sorted(PERSON)}")
        pa, ps = PERSON[key]
    if isinstance(age, str):
        a = age.lower().replace(" ", "_")
        if a not in AGE_LABELS:
            raise ValueError(f"age must be years or one of {sorted(AGE_LABELS)}")
        age = AGE_LABELS[a]
    age = float(age if age is not None else (pa if pa is not None else 35))
    if not 1 <= age <= 100:
        raise ValueError("age must be 1..100 years")
    s = sex if sex is not None else ps
    s = SEX_LABELS.get(str(s).lower(), None) if s is not None else "m"
    if s is None:
        raise ValueError(f"sex must be one of {sorted(SEX_LABELS)}")
    return age, s


def puberty(age: float) -> float:
    """0 before 10 y, 1 after 17 y: how much adult sexual dimorphism applies."""
    return min(1.0, max(0.0, (age - 10) / 7))


def stature_m(age: float, sex: str) -> float:
    return _lerp_table(_GROWTH, age, 1 if sex == "m" else 2)


_REGIONS: dict | None = None
_BMI_REF = [(1, 17.0), (5, 15.5), (10, 17.0), (14, 19.5), (18, 22.0), (100, 22.0)]  # neutral build by age


def regions() -> dict:
    """Population means by region (data/regions.json, compiled from national health surveys)."""
    global _REGIONS
    if _REGIONS is None:
        _REGIONS = json.loads((Path(__file__).parent / "data" / "regions.json").read_text(encoding="utf-8"))
    return _REGIONS


def region_key(region: str | None) -> str | None:
    if region in (None, ""):
        return None
    k = str(region).lower().strip().replace(" ", "_").replace("-", "_")
    d = regions()
    k = d["aliases"].get(k, k)
    if k not in d["regions"]:
        raise ValueError(f"region must be one of {sorted(d['regions'])} (or {sorted(d['aliases'])})")
    return k


def proportions(age=None, sex=None, person=None, region: str | None = None, bmi: float | str | None = None) -> dict:
    """Body proportions as fractions of stature H, plus the stature in metres.

    Heights are measured from the floor; lengths are joint-to-joint. region scales stature to that
    population's adult mean and shifts the leg length by its sitting-height ratio; bmi (a number, or
    'average' for the region's mean) widens or narrows the soft-tissue widths and depths."""
    age, sex = resolve(age, sex, person)
    H = stature_m(age, sex)
    heads = _lerp_table(_GROWTH, age, 3)
    shr = _lerp_table(_GROWTH, age, 4)
    rk = region_key(region)
    rd = regions()["regions"][rk][sex] if rk else None
    if rd:
        H *= rd["stature_m"] / stature_m(30, sex)        # same growth curve, scaled to the regional adult mean
        if rd["shr"]:
            shr += (rd["shr"] / 100 - 0.52) * min(1.0, max(0.0, (age - 6) / 12))
    arm = _lerp_table(_GROWTH, age, 5)
    neck_k = _lerp_table(_GROWTH, age, 6)
    head = 1.0 / heads
    chin = 1.0 - head
    shoulder = chin - neck_k * head * 0.95  # acromion: adult 0.818 H (Drillis-Contini)
    hip = (1.0 - shr) * (0.530 / 0.480)            # greater trochanter, scaled from the adult 0.530
    ankle = 0.039 if age >= 14 else 0.042
    leg = hip - ankle
    upper_arm, forearm, hand = arm * 0.4227, arm * 0.3318, arm * 0.2455
    p = puberty(age)
    w = {}
    for k, (m, f) in _ADULT_W.items():
        adult = m if sex == "m" else f
        w[k] = _CHILD_W[k] + (adult - _CHILD_W[k]) * p
    bmi_ref = _lerp_table(_BMI_REF, age, 1)
    if bmi == "average":
        if not rd or not rd["bmi"]:
            raise ValueError("bmi='average' needs a region with a surveyed mean BMI")
        bmi = rd["bmi"] if age >= 18 else None
    if bmi is not None:
        if not 12 <= float(bmi) <= 60:
            raise ValueError("bmi must be 12..60")
        # widths of soft tissue scale with sqrt(BMI) at a fixed height (mass ~ area); bones do not
        kw = math.sqrt(float(bmi) / bmi_ref)
        for k in w:
            if k not in ("foot_length", "foot_height", "ankle_depth", "wrist_depth"):
                w[k] *= kw
    return {
        "region": rk, "bmi": float(bmi) if bmi is not None else None,
        "age": age, "sex": sex, "stature_m": round(H, 4), "heads": round(heads, 3),
        "head": head, "chin": chin, "shoulder": shoulder, "hip": hip, "knee": ankle + leg * 0.5,
        "ankle": ankle, "thigh": leg * 0.4990, "shank": leg * 0.5010,
        "upper_arm": upper_arm, "forearm": forearm, "hand": hand,
        "elbow": shoulder - upper_arm, "wrist": shoulder - upper_arm - forearm,
        "fingertip": shoulder - arm, "crotch": hip - 0.05 * (hip / 0.53),
        "head_length": head * (0.86 if age >= 14 else 0.92 - 0.06 * (age - 1) / 13),
        "sitting_height_ratio": shr, **w,
    }


def mass_fractions(age: float, sex: str) -> dict:
    """Segment mass fractions of body mass (sum = 1) and COM positions along each segment."""
    base = {k: list(v) for k, v in DE_LEVA[sex].items()}
    hm = None
    for a, b in zip(_HEAD_MASS, _HEAD_MASS[1:]):
        if a[0] <= age < b[0]:
            hb = b[1] if b[1] is not None else base["head"][0]
            hm = a[1] + (hb - a[1]) * (age - a[0]) / (b[0] - a[0])
    if age < _HEAD_MASS[0][0]:
        hm = _HEAD_MASS[0][1]
    if hm is not None:
        rest = 1 - base["head"][0]
        k = (1 - hm) / rest
        for name in base:
            base[name][0] = hm if name == "head" else base[name][0] * k
    return {k: {"mass": round(v[0], 5), "com": v[1]} for k, v in base.items()}


def reference(age=None, sex=None, person=None, px_per_m: float | None = None, height_px: float | None = None,
              region: str | None = None, bmi: float | str | None = None) -> dict:
    """Everything an animator needs to draw this person at a given scale (px)."""
    pr = proportions(age, sex, person, region, bmi)
    H = pr["stature_m"]
    if height_px is None:
        height_px = H * (px_per_m or 340.0)
    s = height_px
    keys = ("head", "chin", "shoulder", "elbow", "wrist", "fingertip", "hip", "crotch", "knee", "ankle")
    out = {
        "age": pr["age"], "sex": "male" if pr["sex"] == "m" else "female", "stature_m": H,
        "height_px": round(s, 1), "heads_tall": pr["heads"], "head_px": round(pr["head"] * s, 1),
        "heights_from_floor_px": {k: round((pr[k] if k != "head" else 1.0) * s, 1) for k in keys},
        "segment_lengths_px": {k: round(pr[k] * s, 1) for k in ("thigh", "shank", "upper_arm", "forearm", "hand",
                                                                "foot_length", "head_length")},
        "front_widths_px": {k: round(pr[k] * s, 1) for k in ("shoulder_breadth", "waist_breadth", "hip_breadth")},
        "side_depths_px": {k: round(pr[k] * s, 1) for k in ("chest_depth", "bust_depth", "waist_depth",
                                                            "buttock_depth", "thigh_depth", "calf_depth",
                                                            "upper_arm_depth", "forearm_depth", "neck_depth")},
        "mass_fractions": mass_fractions(pr["age"], pr["sex"]),
    }
    out["com_height_px"] = round(com_height(pr) * s, 1)
    out["notes"] = _notes(pr)
    if pr["region"]:
        rd = regions()["regions"][pr["region"]]
        r = rd[pr["sex"]]
        out["region"] = {"key": pr["region"], "label": rd["label"], "adult_mean_stature_m": r["stature_m"],
                         "sd_m": r["sd_m"], "sd_assumed": r["sd_assumed"], "mean_bmi": r["bmi"],
                         "sitting_height_ratio": r["shr"], "sources": r["sources"], "survey_age_band": r["age_band"]}
        out["caveat"] = regions()["_caveat"]
    if pr["bmi"] is not None:
        out["bmi"] = pr["bmi"]
    return out


def com_height(pr: dict) -> float:
    """Whole-body centre of mass height (fraction of H) in the standing pose, from the segment table."""
    mf = mass_fractions(pr["age"], pr["sex"])
    seg = {  # proximal, distal heights
        "head": (1.0, pr["chin"]), "trunk": (pr["shoulder"] + 0.3 * pr["head"], pr["hip"]),
        "upper_arm": (pr["shoulder"], pr["elbow"]), "forearm": (pr["elbow"], pr["wrist"]),
        "hand": (pr["wrist"], pr["fingertip"]), "thigh": (pr["hip"], pr["knee"]),
        "shank": (pr["knee"], pr["ankle"]), "foot": (pr["ankle"], pr["ankle"] * 0.4),
    }
    pairs = {"upper_arm", "forearm", "hand", "thigh", "shank", "foot"}
    tot = z = 0.0
    for k, (a, b) in seg.items():
        n = 2 if k in pairs else 1
        m = mf[k]["mass"] * n
        z += m * (a + (b - a) * mf[k]["com"])
        tot += m
    return z / tot


def _notes(pr: dict) -> list[str]:
    a = pr["age"]
    n = [f"{pr['heads']:.1f} heads tall; the body midpoint is at the "
         + ("navel" if a < 3 else "hips" if a < 12 else "pubic bone") + "."]
    if a < 6:
        n.append("Short legs and arms, big head and round belly; fingertips reach the hips, not mid-thigh.")
    elif a < 12:
        n.append("Limbs lengthen; shoulders and hips still about the same width for boys and girls.")
    elif a < 18:
        n.append("Puberty: legs reach adult ratio first; shoulders (boys) and hips (girls) widen afterwards.")
    if a >= 65:
        n.append("Height loss from disc compression; thoracic kyphosis brings the head forward and the knees "
                 "soften; keep joints less flexible in motion.")
    if pr["sex"] == "f" and a >= 14:
        n.append("Female adult: hips about as wide as or wider than the shoulders; shorter forearm and hand.")
    elif pr["sex"] == "m" and a >= 14:
        n.append("Male adult: shoulders about 1.35x hip width; thicker neck.")
    return n


# ---------------------------------------------------------------------- side-view rig geometry
PARTS = ("arm_far_upper", "arm_far_fore", "arm_far_hand",
         "leg_far_thigh", "leg_far_shin", "leg_far_foot", "leg_far_toe",
         "torso", "leg_near_thigh", "leg_near_shin", "leg_near_foot", "leg_near_toe",
         "head", "arm_near_upper", "arm_near_fore", "arm_near_hand")  # back to front


def rest_joints(pr: dict, height_px: float, x: float, ground: float) -> dict:
    """Side-view standing joints (canvas px, +y down, facing +x) for a person standing at x on ground."""
    s = height_px

    def y(f):
        return ground - f * s

    old = max(0.0, (pr["age"] - 60) / 25)  # kyphosis grows after 60
    fwd = old * 0.025 * s
    foot_len = pr["foot_length"] * s
    j = {
        "hip": (x, y(pr["hip"])), "knee": (x + old * 0.006 * s, y(pr["knee"])), "ankle": (x, y(pr["ankle"])),
        "shoulder": (x - 0.005 * s + fwd * 0.6, y(pr["shoulder"])),
        "neck": (x + fwd, y(pr["shoulder"] + 0.35 * (pr["chin"] - pr["shoulder"]) + 0.012)),
        "head_top": (x + 0.015 * s + fwd * 1.4, y(1.0)), "chin": (x + 0.04 * s + fwd * 1.4, y(pr["chin"])),
    }
    j["elbow"] = (j["shoulder"][0], j["shoulder"][1] + pr["upper_arm"] * s)
    j["wrist"] = (j["elbow"][0] + 0.01 * s, j["elbow"][1] + pr["forearm"] * s)
    j["fingertip"] = (j["wrist"][0] + 0.005 * s, j["wrist"][1] + pr["hand"] * s)
    ax, ay = j["ankle"]
    j["heel"] = (ax - 0.22 * foot_len, ground)
    j["ball"] = (ax + 0.55 * foot_len, ground)
    j["toe"] = (ax + 0.78 * foot_len, ground)
    return j


def _capsule(a, b, wa, wb, mid_w=None, mid_t=0.4, n=6):
    """Closed outline of a limb from joint a to b with round ends (widths wa, wb; optional bulge)."""
    ax, ay = a
    bx, by = b
    L = math.hypot(bx - ax, by - ay) or 1e-6
    ux, uy = (bx - ax) / L, (by - ay) / L
    nx, ny = -uy, ux
    stations = [(0.0, wa), (1.0, wb)]
    if mid_w:
        stations.insert(1, (mid_t, mid_w))
    left = [(ax + ux * L * t + nx * w / 2, ay + uy * L * t + ny * w / 2) for t, w in stations]
    right = [(ax + ux * L * t - nx * w / 2, ay + uy * L * t - ny * w / 2) for t, w in reversed(stations)]
    cap_b = [(bx + (nx * math.cos(q) + ux * math.sin(q)) * wb / 2, by + (ny * math.cos(q) + uy * math.sin(q)) * wb / 2)
             for q in [k * math.pi / n for k in range(1, n)]]
    cap_a = [(ax + (-nx * math.cos(q) - ux * math.sin(q)) * wa / 2, ay + (-ny * math.cos(q) - uy * math.sin(q)) * wa / 2)
             for q in [k * math.pi / n for k in range(1, n)]]
    pts = left + cap_b + right + cap_a
    return [[round(px, 2), round(py, 2)] for px, py in pts]


def _shade(c: str, k: float) -> str:
    c = c.lstrip("#")
    r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % (round(r * k), round(g * k), round(b * k))


DEFAULT_COLORS = {"skin": "#f1c7a5", "hair": "#3b2a20", "top": "#4f7cac", "bottom": "#3d3d4f", "shoes": "#5a3b2a",
                  "line": "#2b2220"}


def part_shapes(pr: dict, j: dict, height_px: float, colors: dict | None = None, hair: str = "short") -> dict:
    """Shapes for every rig part, in canvas px at the rest pose. Far-side parts are 18% darker."""
    s = height_px
    col = dict(DEFAULT_COLORS, **(colors or {}))
    if pr["age"] >= 65 and not (colors or {}).get("hair"):
        col["hair"] = "#b9b6b0"
    line = col["line"]
    out: dict[str, list[dict]] = {}
    female, p = pr["sex"] == "f", puberty(pr["age"])

    def sh(points, fill, interior=False):
        return {"type": "polygon", "points": points, "smooth": True, "fill": fill, "stroke": line,
                "interior": interior}

    for side, k in (("far", 0.82), ("near", 1.0)):
        top, bot, skin, shoe = (_shade(col[c], k) for c in ("top", "bottom", "skin", "shoes"))
        out[f"leg_{side}_thigh"] = [sh(_capsule(j["hip"], j["knee"], pr["thigh_depth"] * s * 1.05,
                                                pr["calf_depth"] * s * 0.95), bot)]
        out[f"leg_{side}_shin"] = [sh(_capsule(j["knee"], j["ankle"], pr["calf_depth"] * s * 0.95,
                                               pr["ankle_depth"] * s, pr["calf_depth"] * s * 1.1, 0.3), bot)]
        hx, gy = j["heel"]
        ax, ay = j["ankle"]
        bx = j["ball"][0]
        fh = pr["foot_height"] * s
        out[f"leg_{side}_foot"] = [sh([[hx, gy - 0.35 * fh], [hx + 0.1 * fh, ay - 0.35 * fh], [ax + 0.2 * fh, ay - 0.6 * fh],
                                       [bx - 0.1 * fh, gy - 0.75 * fh], [bx + 0.15 * fh, gy - 0.1 * fh],
                                       [hx + 0.2 * fh, gy]], shoe)]
        tx = j["toe"][0]
        out[f"leg_{side}_toe"] = [sh([[bx - 0.25 * fh, gy - 0.72 * fh], [tx - 0.2 * fh, gy - 0.5 * fh],
                                      [tx + 0.05 * fh, gy - 0.12 * fh], [bx - 0.25 * fh, gy]], shoe)]
        out[f"arm_{side}_upper"] = [sh(_capsule(j["shoulder"], j["elbow"], pr["upper_arm_depth"] * s * 1.15,
                                                pr["forearm_depth"] * s), top)]
        out[f"arm_{side}_fore"] = [sh(_capsule(j["elbow"], j["wrist"], pr["forearm_depth"] * s,
                                               pr["wrist_depth"] * s), skin)]
        wx, wy = j["wrist"]
        fx, fy = j["fingertip"]
        hw = pr["wrist_depth"] * s * 1.3
        out[f"arm_{side}_hand"] = [{"type": "ellipse", "x": (wx + fx) / 2, "y": (wy + fy) / 2, "rx": hw / 2,
                                    "ry": math.hypot(fx - wx, fy - wy) / 2, "fill": skin, "stroke": line}]
    # torso in profile: back line, buttock, belly, chest (bust after puberty for women)
    hx, hy = j["hip"]
    sx, sy = j["shoulder"]
    nx, ny = j["neck"]
    cd, wd, bd = pr["chest_depth"] * s, pr["waist_depth"] * s, pr["buttock_depth"] * s
    bust = (pr["bust_depth"] - pr["chest_depth"]) * s if female else 0.0
    waist_y = hy - 0.45 * (hy - sy)
    chest_y = sy + 0.30 * (hy - sy)
    back = -0.5
    torso = [[nx - 0.42 * pr["neck_depth"] * s, ny], [sx + back * cd, sy + 0.02 * s], [sx + back * cd * 1.02, chest_y],
             [hx + back * wd * 0.9, waist_y], [hx + back * bd * 1.05, hy - 0.01 * s],
             [hx - 0.25 * bd, hy + 0.06 * s], [hx + 0.45 * bd, hy + 0.045 * s],
             [hx + 0.55 * wd + (0.04 * s * (1 - p) if pr["age"] < 8 else 0), waist_y + 0.02 * s],
             [sx + 0.52 * cd + bust * 1.6, chest_y + (0.015 * s if bust else 0)],
             [sx + 0.45 * cd, sy + 0.03 * s], [nx + 0.42 * pr["neck_depth"] * s, ny]]
    out["torso"] = [sh(torso, col["top"]),
                    {"type": "polygon", "points": [[hx + back * bd * 1.05, hy - 0.035 * s], [hx - 0.25 * bd, hy + 0.06 * s],
                                                   [hx + 0.45 * bd, hy + 0.045 * s], [hx + 0.55 * wd, hy - 0.035 * s]],
                     "smooth": True, "fill": col["bottom"], "stroke": line}]
    # head in profile + neck: one outline (crown, brow, nose, lips, chin, jaw, occiput). Children have a
    # bigger cranium over a smaller face: the eye line drops from about 0.42 to 0.52 of the head height.
    tx, ty = j["head_top"]
    cx, cy = j["chin"]
    hh = cy - ty
    hl = pr["head_length"] * s
    hcx = tx - 0.02 * hl
    g = min(1.0, max(0.0, (pr["age"] - 1) / 16))      # 0 = toddler face, 1 = adult face
    fem = 1.0 if female else 0.0

    def P(fx, fy_adult, fy_child=None):
        fy = fy_adult if fy_child is None else fy_child + (fy_adult - fy_child) * g
        return [round(hcx + fx * hl, 2), round(ty + fy * hh, 2)]

    nose = 0.50 + 0.10 * g - 0.02 * fem                 # nose tip protrusion grows with age
    outline = [P(0.00, 0.00), P(0.30, 0.07, 0.10), P(0.46 - 0.02 * fem, 0.30, 0.42), P(0.44, 0.38, 0.50),
               P(nose, 0.56, 0.64), P(0.47, 0.62, 0.70), P(0.50, 0.70, 0.77), P(0.45, 0.74, 0.80),
               P(0.48, 0.78, 0.84), P(0.44, 0.92, 0.94), P(0.32, 1.00), P(0.15, 0.94), P(-0.02, 0.80, 0.84),
               P(-0.22, 0.72), P(-0.48, 0.55), P(-0.53, 0.33), P(-0.38, 0.09)]
    neck = _capsule((nx, ny + 0.01 * s), (hcx - 0.05 * hl, ty + 0.78 * hh), pr["neck_depth"] * s,
                    pr["neck_depth"] * s * 0.95)
    skin = col["skin"]
    ex, ey = P(0.32, 0.42, 0.53)
    eye_r = (0.035 + 0.02 * (1 - g)) * hl                # children's eyes are relatively larger
    mx, my = P(0.36, 0.745, 0.805)
    lx, ly = P(-0.08, 0.50, 0.58)
    head = [sh(neck, skin), sh(outline, skin),
            {"type": "ellipse", "x": lx, "y": ly, "rx": 0.08 * hl, "ry": 0.12 * hh,
             "fill": _shade(skin, 0.92), "stroke": line, "interior": True},  # ear
            {"type": "ellipse", "x": ex, "y": ey, "rx": eye_r * 0.8, "ry": eye_r, "fill": "#1d1a1a",
             "stroke": "#1d1a1a"},  # eye
            {"type": "path", "points": [P(0.30 - 0.02 * (1 - g), 0.34, 0.45), P(0.42, 0.33, 0.44)], "smooth": False,
             "closed": False, "stroke": line, "interior": True},  # brow
            {"type": "path", "points": [[mx, my], P(0.46, 0.745, 0.805)], "smooth": False, "closed": False,
             "stroke": line, "interior": True}]  # mouth
    if hair != "none":
        hc = col["hair"]
        cap = [P(-0.50, 0.58), P(-0.56, 0.30), P(-0.36, 0.04), P(0.02, -0.04), P(0.30, 0.04, 0.08),
               P(0.40, 0.16, 0.24), P(0.18, 0.18, 0.26), P(-0.05, 0.30), P(-0.22, 0.52)]
        if hair in ("long", "ponytail", "bob"):
            drop = {"long": 1.25, "bob": 0.85, "ponytail": 0.60}[hair]
            cap[0] = P(-0.50, drop)
            cap.append(P(-0.30, drop))
        head.append(sh(cap, hc))
        if hair == "ponytail":
            head.append(sh([P(-0.48, 0.30), P(-0.85, 0.45), P(-0.80, 0.95), P(-0.55, 0.55)], hc))
        if hair == "bun":
            bx_, by_ = P(-0.46, 0.12)
            head.append({"type": "circle", "x": bx_, "y": by_, "r": 0.2 * hl, "fill": hc, "stroke": line})
    out["head"] = head
    return out


RIG_PARENTS = {  # part -> (parent part or 'root', joint used as pivot)
    "torso": ("root", "hip"), "head": ("torso", "neck"),
    "arm_far_upper": ("torso", "shoulder"), "arm_far_fore": ("arm_far_upper", "elbow"),
    "arm_far_hand": ("arm_far_fore", "wrist"),
    "arm_near_upper": ("torso", "shoulder"), "arm_near_fore": ("arm_near_upper", "elbow"),
    "arm_near_hand": ("arm_near_fore", "wrist"),
    "leg_far_thigh": ("root", "hip"), "leg_far_shin": ("leg_far_thigh", "knee"),
    "leg_far_foot": ("leg_far_shin", "ankle"), "leg_far_toe": ("leg_far_foot", "ball"),
    "leg_near_thigh": ("root", "hip"), "leg_near_shin": ("leg_near_thigh", "knee"),
    "leg_near_foot": ("leg_near_shin", "ankle"), "leg_near_toe": ("leg_near_foot", "ball"),
}


def mirror_shapes(shapes: list[dict], x0: float) -> list[dict]:
    """Mirror shapes about the vertical line x = x0 (for a character facing left)."""
    out = []
    for sh in shapes:
        d = dict(sh)
        if "points" in d:
            d["points"] = [[2 * x0 - p[0], *p[1:]] for p in d["points"]]
        if "x" in d and d.get("type") in ("ellipse", "circle"):
            d["x"] = 2 * x0 - d["x"]
            if "rotation" in d:
                d["rotation"] = -d["rotation"]
        out.append(d)
    return out

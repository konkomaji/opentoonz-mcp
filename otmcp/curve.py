"""Exact port of OpenToonz keyframe interpolation (common/tparam/tdoubleparam.cpp, v1.8.0).

Keys use the file representation (0-based frames, internal units):
  {"frame", "value", "type": L|C|S|E|Ep|Exp, "speed_out": (x, y), "speed_in": (x, y), "step": int}
speed_in.x is NEGATIVE for an incoming ease (OpenToonz uses e1 = max(-speedIn.x, 0)).
"""
from __future__ import annotations

import math


def _bezier(p0, p1, p2, p3, t):
    mt = 1 - t
    return (mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0] + 3 * mt * t * t * p2[0] + t ** 3 * p3[0],
            mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1] + 3 * mt * t * t * p2[1] + t ** 3 * p3[1])


def _truncate_speeds(a_frame, b_frame, a_sp, b_sp):
    dx = b_frame - a_frame
    ax, ay = a_sp
    bx, by = b_sp
    if ax < 0:
        ax = 0.0
    if bx > 0:
        bx = 0.0
    if a_frame + ax > b_frame and ax != 0:
        k = dx / ax
        ax, ay = ax * k, ay * k
    if b_frame + bx < a_frame and bx != 0:
        k = dx / bx
        bx, by = -bx * k, -by * k
    return (ax, ay), (bx, by)


def _speed_in_out(k0, k1, frame):
    a, b = k0["frame"], k1["frame"]
    if frame <= a:
        return k0["value"]
    if frame >= b:
        return k1["value"]
    sa, sb = _truncate_speeds(a, b, tuple(k0.get("speed_out", (0, 0))), tuple(k1.get("speed_in", (0, 0))))
    p0 = (a, k0["value"])
    p3 = (b, k1["value"])
    p1 = (p0[0] + sa[0], p0[1] + sa[1])
    p2 = (p3[0] + sb[0], p3[1] + sb[1])
    lo, hi = 0.0, 1.0  # x(t) is monotonic after truncation -> bisection
    for _ in range(60):
        mid = (lo + hi) / 2
        if _bezier(p0, p1, p2, p3, mid)[0] < frame:
            lo = mid
        else:
            hi = mid
    return _bezier(p0, p1, p2, p3, (lo + hi) / 2)[1]


def _ease_in_out(k0, k1, frame, percentage):
    x3 = k1["frame"] - k0["frame"]
    if x3 <= 0:
        return k0["value"]
    x = frame - k0["frame"]
    if x <= 0:
        return k0["value"]
    if x >= x3:
        return k1["value"]
    e0 = max(k0.get("speed_out", (0, 0))[0], 0.0)
    e1 = max(-k1.get("speed_in", (0, 0))[0], 0.0)
    if percentage:
        e0 *= x3 * 0.01
        e1 *= x3 * 0.01
    if e0 + e1 >= x3:
        m = min(max((e0 + x3 - e1) / 2, 0.0), x3)
        e0, e1 = m, x3 - m
    x1, x2 = e0, x3 - e1
    if 0 < x1 - x2 < 1e-6:
        x1 = x2 = (x1 + x2) * 0.5
    v = 2 / (x3 + x2 - x1)
    if x < x1:
        val = 0.5 * (v / e0) * x * x
    elif x > x2:
        val = 1 - 0.5 * (v / e1) * (x3 - x) * (x3 - x)
    else:
        val = x * v - 0.5 * v * e0
    return (1 - val) * k0["value"] + val * k1["value"]


def _exponential(k0, k1, frame):
    a, b = k0["value"], k1["value"]
    if a <= 0 or b <= 0:
        return a + (frame - k0["frame"]) * (b - a) / (k1["frame"] - k0["frame"])
    t = (frame - k0["frame"]) / (k1["frame"] - k0["frame"])
    if b < a:
        t = 1 - t
        a, b = b, a
    return a * math.exp(t * math.log(b / a))


def value_at(keys: list[dict], frame: float, default: float = 0.0, cycle: bool = False) -> float:
    """Value at 0-based (fractional) frame, exactly as OpenToonz computes it."""
    if not keys:
        return default
    ks = sorted(keys, key=lambda k: k["frame"])
    if len(ks) == 1:
        return ks[0]["value"]
    f0, f1 = ks[0]["frame"], ks[-1]["frame"]
    offset = 0.0
    if frame < f0:
        frame = f0
    elif frame > f1:
        if not cycle:
            frame = f1
        else:
            dist, dv = f1 - f0, ks[-1]["value"] - ks[0]["value"]
            while frame >= f1:
                frame -= dist
                offset += dv
    bi = next(i for i, k in enumerate(ks) if k["frame"] >= frame)
    if ks[bi]["frame"] == frame and bi + 1 < len(ks):
        ai, bi = bi, bi + 1
    else:
        ai = bi - 1
    a, b = ks[ai], ks[bi]
    step = int(a.get("step", 1) or 1)
    if step > 1:
        rel = math.floor(b["frame"] - a["frame"])
        st = min(step, rel)
        b = dict(b, frame=a["frame"] + math.floor(rel / st) * st)
        frame = min(frame, b["frame"])
        frame = a["frame"] + math.floor(math.floor(frame - a["frame"]) / st) * st
    t = a.get("type", "L")
    if t == "C":
        v = b["value"] if frame == b["frame"] else a["value"]
    elif t == "L":
        v = a["value"] + (frame - a["frame"]) * (b["value"] - a["value"]) / (b["frame"] - a["frame"])
    elif t == "S":
        v = _speed_in_out(a, b, frame)
    elif t == "E":
        v = _ease_in_out(a, b, frame, False)
    elif t == "Ep":
        v = _ease_in_out(a, b, frame, True)
    elif t == "Exp":
        v = _exponential(a, b, frame)
    else:
        raise ValueError(f"unsupported keyframe type {t} (expressions are not evaluated)")
    return v + offset

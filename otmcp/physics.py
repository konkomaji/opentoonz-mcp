"""Physically based motion, sampled per frame (24 fps, px units, +y down).

Everything here is deterministic pure math so it can be unit-tested against closed forms.
Data (materials, charts) comes from otmcp/data/recipes.json.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def recipes() -> dict:
    return json.loads((Path(__file__).parent / "data" / "recipes.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------------ ballistics
def bounce(height: float, fall_frames: float, restitution: float, bounces: int = 4,
           start_on_ground: bool = False) -> list[dict]:
    """Ball dropped from rest at `height` px above the ground, gravity chosen so the first fall takes
    `fall_frames` (g = 2h/N^2 px/frame^2). Each bounce rises e^2 as high and lasts e times as long.

    Returns per-frame samples [{"frame": i (0-based), "h": height above ground, "v": velocity px/frame
    (+ = falling), "contact": bool}] until the last bounce lands."""
    g = 2.0 * height / fall_frames ** 2
    segs = []  # (t_start, h_apex, half_duration)
    t = 0.0
    if start_on_ground:
        segs.append((t, height, fall_frames, True))
        t += 2 * fall_frames
    else:
        segs.append((t, height, fall_frames, False))
        t += fall_frames
    h, n = height, fall_frames
    for _ in range(bounces):
        h *= restitution ** 2
        n *= restitution
        if h < 0.5:
            break
        segs.append((t, h, n, True))
        t += 2 * n
    end = t
    out = []
    i = 0
    contacts = {round(s[0]) for s in segs[1:]} | ({round(end)} if bounces else set())
    while i <= math.ceil(end):
        x = min(i, end)
        y, v = 0.0, 0.0
        for ts, ha, nn, full in segs:
            dur = 2 * nn if full else nn
            if ts <= x <= ts + dur:
                tau = (x - ts) - (nn if full else 0.0)  # time from apex
                y = max(0.0, ha - 0.5 * g * tau * tau)
                v = g * tau
                break
        out.append({"frame": i, "h": y, "v": v, "contact": i in contacts or (i == 0 and start_on_ground)})
        i += 1
    return out


def squash_for(material: dict, n_contact: int, e: float) -> float:
    """Contact squash sy for the n-th contact, fading with energy: 1-(1-s0)*e^n."""
    s0 = float(material.get("contact_squash", 0.75))
    return 1 - (1 - s0) * e ** n_contact


def jump(height: float, air_frames: int, crouch: float, crouch_frames: int = 6, land_frames: int = 8,
         takeoff_stretch: float = 1.1, land_squash: float = 0.9) -> dict:
    """Recipe jump (recipes.json 'jump'): crouch (anticipation) -> takeoff stretch -> exact parabola
    (g = 8h/T^2) -> landing squash -> recovery. Returns per-frame y offsets (px, +down) and sy."""
    T = float(air_frames)
    g = 8.0 * height / (T * T)
    ys, sy = [], []
    # anticipation: ease into crouch (ease-out power 2)
    for i in range(crouch_frames + 1):
        p = i / crouch_frames
        k = 1 - (1 - p) ** 2
        ys.append(crouch * k)
        sy.append(1 - (1 - land_squash) * k)
    # air: from the crouch line up to apex and back to ground (0)
    v0 = g * T / 2
    for i in range(1, air_frames + 1):
        t = i
        h = v0 * t - 0.5 * g * t * t
        ys.append(-max(0.0, h) if i < air_frames else 0.0)
        speed = abs(v0 - g * t) / v0
        sy.append(1 + (takeoff_stretch - 1) * speed if i < air_frames else land_squash)
    # landing recovery: squash eases back to 1 with a small overshoot
    for i in range(1, land_frames + 1):
        p = i / land_frames
        ys.append(0.0)
        sy.append(1 - (1 - land_squash) * math.exp(-4 * p) * math.cos(math.pi * p * 1.5))
    return {"y": ys, "sy": sy, "gravity": g, "frames": len(ys)}


# ------------------------------------------------------------------ springs (follow-through / overlap)
def second_order(xs: list[float], f: float, zeta: float, r: float = 0.0, fps: float = 24.0,
                 substeps: int = 8) -> list[float]:
    """t3ssel8r second-order dynamics: y + k1 y' + k2 y'' = x + k3 x'.
    f: natural frequency (Hz), zeta: damping ratio, r: initial response (r<0 anticipates).
    Semi-implicit Euler with substeps and the k2 stability clamp; starts at rest on xs[0]."""
    if not xs:
        return []
    k1 = zeta / (math.pi * f)
    k2 = 1 / ((2 * math.pi * f) ** 2)
    k3 = r * zeta / (2 * math.pi * f)
    T = 1.0 / fps / substeps
    y, yd = xs[0], 0.0
    xp = xs[0]
    out = [y]
    for i in range(1, len(xs)):
        for s in range(1, substeps + 1):
            x = xs[i - 1] + (xs[i] - xs[i - 1]) * s / substeps
            xd = (x - xp) / T
            xp = x
            k2s = max(k2, T * T / 2 + T * k1 / 2, T * k1)
            y = y + T * yd
            yd = yd + T * (x + k3 * xd - y - k1 * yd) / k2s
        out.append(y)
    return out


def overshoot_ratio(zeta: float) -> float:
    return math.exp(-zeta * math.pi / math.sqrt(1 - zeta * zeta)) if zeta < 1 else 0.0


# ------------------------------------------------------------------ camera
def smooth_damp(targets: list[float], smooth_time_frames: float, max_speed: float = float("inf")) -> list[float]:
    """Unity SmoothDamp (critically damped follow), one sample per frame."""
    if not targets:
        return []
    cur, vel = targets[0], 0.0
    out = [cur]
    omega = 2.0 / max(smooth_time_frames, 1e-6)
    for tgt in targets[1:]:
        x = omega
        exp = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)
        change = cur - tgt
        max_change = max_speed * smooth_time_frames
        change = max(-max_change, min(max_change, change))
        temp = (vel + omega * change)
        vel = (vel - omega * temp) * exp
        new = (cur - change) + (change + temp) * exp
        if (tgt - cur > 0) == (new > tgt):
            new, vel = tgt, 0.0
        cur = new
        out.append(cur)
    return out


def _fade(t):
    return t * t * t * (t * (t * 6 - 15) + 10)


def _noise1(x: float, seed: int) -> float:
    """1-D gradient (Perlin-style) noise in [-1, 1], deterministic."""
    def grad(i):
        h = (i * 374761393 + seed * 668265263) & 0xFFFFFFFF
        h = (h ^ (h >> 13)) * 1274126177 & 0xFFFFFFFF
        return ((h & 0xFFFF) / 0xFFFF) * 2 - 1
    i0 = math.floor(x)
    t = x - i0
    a, b = grad(i0) * t, grad(i0 + 1) * (t - 1)
    return 2 * (a + (b - a) * _fade(t))


def trauma_shake(frames: int, trauma: float = 1.0, max_offset: float = 24.0, max_angle: float = 2.0,
                 decay_per_s: float = 1.2, freq_hz: float = 8.0, fps: float = 24.0, seed: int = 1) -> list[dict]:
    """Squirrel Eiserloh trauma shake: amplitude = trauma^2, trauma decays linearly, Perlin noise."""
    out = []
    for i in range(frames):
        tr = max(0.0, trauma - decay_per_s * i / fps)
        a = tr * tr
        t = i / fps * freq_hz
        out.append({"frame": i, "x": max_offset * a * _noise1(t, seed),
                    "y": max_offset * a * _noise1(t, seed + 17), "rot": max_angle * a * _noise1(t, seed + 41)})
    return out


# ------------------------------------------------------------------ key thinning
def thin(samples: list[tuple[int, float]], tol: float) -> list[tuple[int, float]]:
    """Ramer-Douglas-Peucker on (frame, value): fewest linear keys reproducing every sample within tol."""
    if len(samples) <= 2:
        return list(samples)
    (f0, v0), (f1, v1) = samples[0], samples[-1]
    worst, wi = -1.0, 0
    for i in range(1, len(samples) - 1):
        f, v = samples[i]
        lin = v0 + (v1 - v0) * (f - f0) / (f1 - f0)
        d = abs(v - lin)
        if d > worst:
            worst, wi = d, i
    if worst <= tol:
        return [samples[0], samples[-1]]
    left = thin(samples[:wi + 1], tol)
    return left[:-1] + thin(samples[wi:], tol)


# ------------------------------------------------------------------ charts
def blink_cells(pattern: str, ids: dict[str, int], start_open: int = 0) -> list[int]:
    """Map a recipes.json blink chart (O/H/Q/C) to drawing ids, e.g. ids={'O':1,'H':2,'C':3}."""
    chart = recipes()["blink"][pattern]
    out = []
    for c in chart:
        if c in ids:
            out.append(ids[c])
        elif c == "Q":
            out.append(ids.get("H", ids["C"]))
        else:
            out.append(ids.get("O", ids.get("H")))
    return [ids["O"]] * start_open + out


_VOWELS = {"a": "open", "o": "open", "i": "half", "u": "half", "e": "half"}


def text_to_mouth(text: str, frames_per_syllable: int = 4, chart: str = "anime3") -> list[str]:
    """Rough text -> mouth shapes (no audio). anime3: closed/half/open per vowel, 'n'/'m'/'b'/'p' close.
    blair: AI/E/O/U/WQ/MBP/FV/L/etc/rest by letter. Holds >= 2 frames (recipes lip_sync.min_hold)."""
    hold = max(2, int(recipes()["lip_sync"].get("min_hold", 2)))
    fps = max(hold, frames_per_syllable)
    shapes = []
    for ch in text.lower():
        if chart == "anime3":
            if ch in _VOWELS:
                shapes += [_VOWELS[ch]] * fps
            elif ch in "mbpn":
                shapes += ["closed"] * hold
            elif ch in " .,!?":
                shapes += ["closed"] * (hold * 2 if ch != " " else hold)
        else:
            m = {"a": "AI", "i": "AI", "e": "E", "o": "O", "u": "U", "w": "WQ", "q": "WQ", "m": "MBP", "b": "MBP",
                 "p": "MBP", "f": "FV", "v": "FV", "l": "L"}.get(ch)
            if ch in " .,!?":
                m = "rest"
            elif m is None and ch.isalpha():
                m = "etc"
            if m:
                shapes += [m] * (fps if m in ("AI", "E", "O", "U") else hold)
    return shapes + (["closed"] if chart == "anime3" else ["rest"]) * hold

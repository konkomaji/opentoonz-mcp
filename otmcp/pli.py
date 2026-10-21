"""Minimal reader/patcher for OpenToonz .pli vector levels (format v71).

Layout (opentoonz image/pli/pli_io.cpp):
  header: 'PLIM' u8 major u8 minor u16+bytes creator u32 fileLength u16 frames
          [u8 s if v>=6.5] u8 ii u8 d
  tags:   u8 typeAndLenId (len id in top 2 bits: 0 none, 1 u8, 2 u16, 3 u32)
          (0xFF -> u16 type, len id in top 2 bits of that)  then payload.
Stroke tags (10 chain, 23 loop) payload:
  u8 maxThickness, P0.x P0.y (dyn ints), u8 thick0, then per quadratic:
  d1.x d1.y u8 thick1 d2.x d2.y u8 thick2.
Dyn ints are sign-magnitude, width set by SET_DATA_8/16/32 tags (default 16).
Coordinates are value / precisionScale (default 128, PRECISION_SCALE tag).

Thickness bytes can be rewritten in place: tag sizes and offsets never change,
so group/image tags that reference children by offset stay valid.
"""
from __future__ import annotations

import math
import random
import struct
from dataclasses import dataclass, field

SET8, SET16, SET32 = 1, 2, 3
CHAIN, LOOP, IMAGE_BEGIN, PRECISION = 10, 23, 22, 25


@dataclass
class Stroke:
    loop: bool
    points: list[tuple[float, float, float]]   # x, y, thickness (vector units)
    thick_offsets: list[int] = field(default_factory=list)  # file offsets of thickness bytes
    max_thick: int = 0
    frame: int = 0


def _dyn(buf: bytes, off: int, n: int) -> tuple[int, int]:
    if n == 1:
        b = buf[off]
        v = b & 0x7F
        return (-v if b & 0x80 else v), off + 1
    if n == 2:
        v = buf[off] | (buf[off + 1] << 8)
        neg = v & 0x8000
        v &= 0x7FFF
        return (-v if neg else v), off + 2
    v = int.from_bytes(buf[off:off + 4], "little")
    neg = v & 0x80000000
    v &= 0x7FFFFFFF
    return (-v if neg else v), off + 4


def read(path: str) -> tuple[bytearray, list[Stroke]]:
    data = bytearray(open(path, "rb").read())
    if data[:4] != b"PLIM":
        raise ValueError("not a little-endian PLI file")
    major, minor = data[4], data[5]
    off = 6
    if major > 5 or (major == 5 and minor >= 8):
        n = struct.unpack_from("<H", data, off)[0]
        off += 2 + n
    off += 4 + 2                       # fileLength, frames
    if major > 6 or (major == 6 and minor >= 5):
        off += 1
    off += 2                           # ii, d
    dynw, scale, frame = 2, 128.0, 0
    strokes: list[Stroke] = []
    while off < len(data):
        t = data[off]
        off += 1
        if t == 0xFF:
            t16 = struct.unpack_from("<H", data, off)[0]
            off += 2
            lid, ttype = t16 >> 14, t16 & 0x3FFF
        else:
            lid, ttype = t >> 6, t & 0x3F
        if lid == 0:
            ln = 0
        elif lid == 1:
            ln = data[off]
            off += 1
        elif lid == 2:
            ln = struct.unpack_from("<H", data, off)[0]
            off += 2
        else:
            ln = struct.unpack_from("<I", data, off)[0]
            off += 4
        if ttype == 0:
            break
        p = off
        if ttype == SET8:
            dynw = 1
        elif ttype == SET16:
            dynw = 2
        elif ttype == SET32:
            dynw = 4
        elif ttype == PRECISION:
            v, _ = _dyn(data, p, dynw)
            scale = float(v)
        elif ttype == IMAGE_BEGIN:
            frame = data[p] | (data[p + 1] << 8)
        elif ttype in (CHAIN, LOOP):
            mt = data[p]
            ratio = mt / 255.0
            q = p + 1
            x, q = _dyn(data, q, dynw)
            y, q = _dyn(data, q, dynw)
            s = Stroke(ttype == LOOP, [], [], mt, frame)
            s.points.append((x / scale, y / scale, data[q] * ratio))
            s.thick_offsets.append(q)
            q += 1
            nq = (ln - 2 * dynw - 2) // (4 * dynw + 2)
            for _ in range(nq):
                dx, q = _dyn(data, q, dynw)
                dy, q = _dyn(data, q, dynw)
                x, y = x + dx, y + dy
                s.points.append((x / scale, y / scale, data[q] * ratio))
                s.thick_offsets.append(q)
                q += 1
                dx, q = _dyn(data, q, dynw)
                dy, q = _dyn(data, q, dynw)
                x, y = x + dx, y + dy
                s.points.append((x / scale, y / scale, data[q] * ratio))
                s.thick_offsets.append(q)
                q += 1
            strokes.append(s)
        off += ln
    return data, strokes


def _smooth_noise(n: int, rng: random.Random, knots: int = 4) -> list[float]:
    """Low-frequency noise in [-1, 1] sampled at n points (cosine-interpolated)."""
    ks = [rng.uniform(-1, 1) for _ in range(knots + 1)]
    out = []
    for i in range(n):
        u = i / max(1, n - 1) * knots
        k = min(int(u), knots - 1)
        f = (1 - math.cos((u - k) * math.pi)) / 2
        out.append(ks[k] * (1 - f) + ks[k + 1] * f)
    return out


def pressure_patch(path: str, taper_start: float = 0.18, taper_end: float = 0.28, min_ratio: float = 0.12,
                   pressure_var: float = 0.25, loop_var: float = 0.35, seed: int = 0,
                   only_frames: set[int] | None = None, profiles: dict[int, list] | None = None,
                   report: dict | None = None) -> int:
    """Give strokes a hand-inked pressure profile by rewriting thickness bytes in place.

    Open strokes: tapered ends (brush lift) + smooth pressure wobble.
    Closed strokes: smooth thick/thin variation (no taper), like a pen going round.
    Thickness only ever goes DOWN from the stored value, so maxThickness stays valid.
    Hidden zero-width strokes (fill boundaries) are left untouched. Returns strokes patched."""
    data, strokes = read(path)
    count = 0
    # brush profiles: map the k-th visible stroke of a frame to profiles[frame][k]
    visible_idx: dict[int, int] = {}
    stroke_prof: dict[int, list] = {}
    mismatch: list[int] = []
    if profiles:
        per_frame: dict[int, list[int]] = {}
        for si, s in enumerate(strokes):
            if max(t for _, _, t in s.points) > 0:
                per_frame.setdefault(s.frame, []).append(si)
        for fr, prof in profiles.items():
            idx = per_frame.get(fr, [])
            if len(idx) != len(prof):
                mismatch.append(fr)
                continue
            for si, pr in zip(idx, prof):
                if pr:
                    stroke_prof[si] = pr
    if report is not None:
        report["profile_mismatch_frames"] = mismatch
    for si, s in enumerate(strokes):
        if only_frames is not None and s.frame not in only_frames:
            continue
        base = [t for _, _, t in s.points]
        if max(base) <= 0:
            continue
        rng = random.Random(seed * 7919 + si * 104729 + s.frame)
        # arc-length parameter along control points
        acc = [0.0]
        for a, b in zip(s.points, s.points[1:]):
            acc.append(acc[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
        total = acc[-1] or 1.0
        noise = _smooth_noise(len(s.points), rng, knots=3 if s.loop else 4)
        if si in stroke_prof:
            from .art import sample_profile
            for i, off in enumerate(s.thick_offsets):
                data[off] = max(1, min(255, round(data[off] * sample_profile(stroke_prof[si], acc[i] / total))))
            count += 1
            continue
        for i, off in enumerate(s.thick_offsets):
            u = acc[i] / total
            if s.loop:
                m = 1 - loop_var * (0.5 + 0.5 * noise[i])
            else:
                ts, te = taper_start * rng.uniform(0.7, 1.3), taper_end * rng.uniform(0.7, 1.3)
                m = 1.0
                if u < ts:
                    m = min_ratio + (1 - min_ratio) * math.sin(u / ts * math.pi / 2)
                elif u > 1 - te:
                    m = min_ratio + (1 - min_ratio) * math.sin((1 - u) / te * math.pi / 2)
                m *= 1 - pressure_var * (0.5 + 0.5 * noise[i])
            data[off] = max(1, min(255, round(data[off] * m)))
        count += 1
    open(path, "wb").write(bytes(data))
    return count

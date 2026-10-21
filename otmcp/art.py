"""Richer drawing primitives: raw SVG path data and pressure brush strokes."""
from __future__ import annotations

import math

from shapely.geometry import LineString


def _simplify(pts: list[tuple[float, float]], closed: bool, tol: float) -> list[tuple[float, float]]:
    if len(pts) < 4 or tol <= 0:
        return pts
    ring = pts + [pts[0]] if closed else pts
    out = list(LineString(ring).simplify(tol, preserve_topology=False).coords)
    if closed and len(out) > 1 and out[0] == out[-1]:
        out = out[:-1]
    return out if len(out) >= (3 if closed else 2) else pts


def svgpath_contours(d: str, transform: str | None = None, spacing: float = 4.0, tol: float = 0.35):
    """Parse SVG path data (all commands incl. arcs) into sampled contours in canvas px.

    Returns [(points, closed)]. Points are dense samples simplified to `tol` px so the
    vector level stays editable without losing the drawn shape."""
    from svgelements import Close, Matrix, Move, Path

    p = Path(d)
    if transform:
        p = p * Matrix(transform)
        p.reify()
    out = []
    for sub in p.as_subpaths():
        segs = [s for s in sub if not isinstance(s, Move)]
        if not segs:
            continue
        closed = any(isinstance(s, Close) for s in segs)
        pts: list[tuple[float, float]] = []
        for s in segs:
            if isinstance(s, Close):
                continue
            try:
                L = s.length(error=1e-3)
            except Exception:
                L = 0.0
            n = max(1, int(math.ceil(L / spacing)))
            for i in range(0 if not pts else 1, n + 1):
                q = s.point(i / n)
                if q is not None and (not pts or (abs(q.x - pts[-1][0]) > 1e-6 or abs(q.y - pts[-1][1]) > 1e-6)):
                    pts.append((float(q.x), float(q.y)))
        if closed and len(pts) > 2 and math.dist(pts[0], pts[-1]) < 1e-6:
            pts.pop()
        pts = _simplify(pts, closed, tol)
        if len(pts) >= (3 if closed else 2):
            out.append((pts, closed))
    return out


def brush_profile(points: list) -> list[tuple[float, float]]:
    """[(u, pressure)] along the stroke (u = arc-length fraction 0..1), pressure clamped 0.05..1."""
    xy = [(float(p[0]), float(p[1])) for p in points]
    pr = [float(p[2]) if len(p) > 2 else 1.0 for p in points]
    acc = [0.0]
    for a, b in zip(xy, xy[1:]):
        acc.append(acc[-1] + math.dist(a, b))
    total = acc[-1] or 1.0
    return [(a / total, max(0.05, min(1.0, q))) for a, q in zip(acc, pr)]


def sample_profile(profile: list[tuple[float, float]], u: float) -> float:
    if not profile:
        return 1.0
    if u <= profile[0][0]:
        return profile[0][1]
    for (u0, p0), (u1, p1) in zip(profile, profile[1:]):
        if u0 <= u <= u1:
            t = 0 if u1 == u0 else (u - u0) / (u1 - u0)
            return p0 + (p1 - p0) * t
    return profile[-1][1]

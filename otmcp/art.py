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


# ------------------------------------------------------------------ affine transforms of shape dicts
def _affine(a, b, c, d, e, f):
    """Matrix [[a c e],[b d f]] (SVG order): x' = a x + c y + e, y' = b x + d y + f."""
    return (a, b, c, d, e, f)


def compose(m1, m2):
    """m1 after m2."""
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (a1 * a2 + c1 * b2, b1 * a2 + d1 * b2, a1 * c2 + c1 * d2, b1 * c2 + d1 * d2,
            a1 * e2 + c1 * f2 + e1, b1 * e2 + d1 * f2 + f1)


def placement(x: float, y: float, scale: float = 1.0, rotation: float = 0.0, flip_x: bool = False,
              anchor: tuple[float, float] = (0.0, 0.0)):
    """Matrix moving `anchor` (in part space) to (x, y), scaling, rotating (deg, clockwise on screen
    because +y is down) and optionally mirroring horizontally."""
    r = math.radians(rotation)
    sx = -scale if flip_x else scale
    cr, sr = math.cos(r), math.sin(r)
    m = (cr * sx, sr * sx, -sr * scale, cr * scale, 0.0, 0.0)
    return compose((1, 0, 0, 1, x, y), compose(m, (1, 0, 0, 1, -anchor[0], -anchor[1])))


def _pt(m, x, y):
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def transform_shape(sh: dict, m) -> dict:
    """Apply an affine matrix to any shape dict exactly. Circles/ellipses/rects become svgpath when the
    matrix shears or scales non-uniformly; otherwise they keep their type (stays editable/tweenable)."""
    a, b, c, d, _, _ = m
    sx = math.hypot(a, b)
    sy = math.hypot(c, d)
    det = a * d - b * c
    # similarity without mirroring keeps primitive types; a mirror (det<0) goes through the exact matrix path
    uniform = abs(sx - sy) < 1e-9 and abs(a * c + b * d) < 1e-9 and det > 0
    rot = math.degrees(math.atan2(b, a))
    out = dict(sh)
    t = sh.get("type", "circle")
    for k in ("stroke_width", "width", "wobble"):
        if k in sh:
            out[k] = float(sh[k]) * math.sqrt(abs(det))
    if t in ("circle", "ellipse") and uniform:
        out["x"], out["y"] = _pt(m, float(sh.get("x", 0)), float(sh.get("y", 0)))
        rx = float(sh.get("rx", sh.get("r", 50)))
        ry = float(sh.get("ry", sh.get("r", 50)))
        out.pop("r", None)
        out.update(type="ellipse", rx=rx * sx, ry=ry * sx, rotation=float(sh.get("rotation", 0)) + rot)
        return out
    if t in ("polygon", "path", "line", "polyline", "brush"):
        out["points"] = [list(_pt(m, float(p[0]), float(p[1]))) + list(p[2:]) for p in sh.get("points", [])]
        out.pop("rotation", None)
        if sh.get("rotation"):
            # bake the shape's own rotation (about its centroid) before transforming
            pts = sh["points"]
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            pre = placement(cx, cy, 1.0, float(sh["rotation"]), False, (cx, cy))
            out["points"] = [list(_pt(compose(m, pre), float(p[0]), float(p[1]))) + list(p[2:]) for p in pts]
        return out
    if t == "text" and uniform:
        out["x"], out["y"] = _pt(m, float(sh.get("x", 0)), float(sh.get("y", 0)))
        out["size"] = float(sh.get("size", 64)) * sx
        out["rotation"] = float(sh.get("rotation", 0)) + rot
        return out
    # general case: convert to svgpath with an explicit matrix
    if t == "svgpath":
        prev = sh.get("transform")
        mat = "matrix(%r %r %r %r %r %r)" % m
        out["transform"] = f"{mat} {prev}" if prev else mat
        return out
    if t in ("circle", "ellipse", "rect"):
        from .vec import shape_contours, catmull
        cs = shape_contours(sh)
        dparts = []
        for cnt in cs:
            pts = catmull(cnt.pts, True, 8) if cnt.smooth else cnt.pts
            dparts.append("M " + " L ".join(f"{px:.3f} {py:.3f}" for px, py in pts) + " Z")
        out = {k: v for k, v in out.items() if k not in ("x", "y", "r", "rx", "ry", "w", "h", "radius", "rotation")}
        out.update(type="svgpath", d=" ".join(dparts), transform="matrix(%r %r %r %r %r %r)" % m)
        return out
    raise ValueError(f"cannot transform shape type {t} with a non-uniform matrix")

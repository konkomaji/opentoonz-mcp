"""Shape drawing + tweening. Produces transparent RGBA PNG frames for raster levels."""
from __future__ import annotations

import copy
import math
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFont

SS = 2  # supersampling factor for antialiasing


def color(c: Any, alpha: float | None = None) -> tuple[int, int, int, int]:
    if c is None:
        return (0, 0, 0, 0)
    if isinstance(c, (list, tuple)):
        rgba = tuple(int(v) for v in c) + ((255,) if len(c) == 3 else ())
    else:
        rgba = ImageColor.getcolor(str(c), "RGBA")
    if alpha is not None:
        rgba = rgba[:3] + (int(rgba[3] * max(0.0, min(1.0, alpha))),)
    return rgba  # type: ignore[return-value]


# ---------------------------------------------------------------- easing
def ease(t: float, kind: str = "linear") -> float:
    t = max(0.0, min(1.0, t))
    if kind in ("slow_in_out", "studio", "snappy"):
        # studio spacing chart: p = t^a / (t^a + (1-t)^a); a=2 standard, a=3 heavy/snappy
        a = 3.0 if kind == "snappy" else 2.0
        return t ** a / (t ** a + (1 - t) ** a) if 0 < t < 1 else t
    if kind == "in":
        return t * t
    if kind == "out":
        return 1 - (1 - t) ** 2
    if kind in ("inout", "in_out", "smooth"):
        return t * t * (3 - 2 * t)
    if kind == "bounce":
        n, d = 7.5625, 2.75
        if t < 1 / d:
            return n * t * t
        if t < 2 / d:
            t -= 1.5 / d
            return n * t * t + 0.75
        if t < 2.5 / d:
            t -= 2.25 / d
            return n * t * t + 0.9375
        t -= 2.625 / d
        return n * t * t + 0.984375
    if kind == "elastic":
        if t in (0.0, 1.0):
            return t
        return 2 ** (-10 * t) * math.sin((t * 10 - 0.75) * (2 * math.pi / 3)) + 1
    if kind == "back":
        c1 = 1.70158
        return 1 + (c1 + 1) * (t - 1) ** 3 + c1 * (t - 1) ** 2
    return t


def _lerp(a: Any, b: Any, t: float) -> Any:
    """Recursively interpolate numbers, lists, dicts and colors."""
    if isinstance(a, bool) or isinstance(b, bool):
        return a if t < 0.5 else b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a + (b - a) * t
    if isinstance(a, str) and isinstance(b, str):
        if a == b:
            return a
        try:
            ca, cb = color(a), color(b)
            return [round(_lerp(x, y, t)) for x, y in zip(ca, cb)]
        except ValueError:
            return a if t < 0.5 else b
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)) and len(a) == len(b):
        return [_lerp(x, y, t) for x, y in zip(a, b)]
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a) if t < 0.5 else dict(b)
        for k in set(a) & set(b):
            out[k] = _lerp(a[k], b[k], t)
        return out
    return a if t < 0.5 else b


def _center(sh: dict) -> tuple[float, float] | None:
    if "points" in sh and sh["points"]:
        pts = sh["points"]
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    if "x" in sh and "y" in sh:
        if sh.get("type") == "rect":
            return (sh["x"] + sh.get("w", 0) / 2, sh["y"] + sh.get("h", 0) / 2)
        return (sh["x"], sh["y"])
    return None


def _offset(sh: dict, dx: float, dy: float) -> None:
    if "points" in sh:
        sh["points"] = [[p[0] + dx, p[1] + dy] for p in sh["points"]]
    if "x" in sh:
        sh["x"] += dx
    if "y" in sh:
        sh["y"] += dy


def tween_motion(shapes_a: list[dict], shapes_b: list[dict], t: float, easing: str = "slow_in_out",
                 arc: float = 0.0, squash: float = 0.0, squash_limits: tuple[float, float] = (0.6, 1.6),
                 dt: float = 0.05) -> list[dict]:
    """tween() plus animation principles.

    arc: perpendicular bow of the travel path as a fraction of chord length (studio default 0.15).
    squash: 0..1 stretch circles/ellipses along their velocity, preserving area (sx*sy = 1)."""
    out = tween(shapes_a, shapes_b, t, easing)
    for i, sh in enumerate(out):
        if i >= len(shapes_a) or i >= len(shapes_b):
            continue
        ca, cb = _center(shapes_a[i]), _center(shapes_b[i])
        if not ca or not cb:
            continue
        chord = math.dist(ca, cb)
        if arc and chord > 1:
            nx, ny = -(cb[1] - ca[1]) / chord, (cb[0] - ca[0]) / chord
            e = ease(t, easing)
            bow = math.sin(math.pi * e) * arc * chord
            _offset(sh, nx * bow, ny * bow)
        if squash and sh.get("type") in ("circle", "ellipse") and chord > 1:
            e0, e1 = ease(max(0.0, t - dt), easing), ease(min(1.0, t + dt), easing)
            speed = (e1 - e0) * chord / (2 * dt * max(chord, 1))  # normalised 0..~2
            lo, hi = squash_limits
            # speed of eased progress: 1.0 = average speed; full stretch only at >=2x average
            s = max(1.0, min(hi, 1 + squash * (hi - 1) * min(1.0, max(0.0, speed - 0.6) / 1.4)))
            r = sh.get("r", None)
            rx = sh.pop("rx", r if r is not None else 50)
            ry = sh.pop("ry", r if r is not None else 50)
            sh.pop("r", None)
            sh["type"] = "ellipse"
            sh["rx"], sh["ry"] = rx * s, ry / s
            sh["rotation"] = math.degrees(math.atan2(cb[1] - ca[1], cb[0] - ca[0]))
    return out


def tween(shapes_a: list[dict], shapes_b: list[dict], t: float, easing: str = "linear") -> list[dict]:
    """Interpolate two shape lists (matched by index)."""
    e = ease(t, easing)

    def norm(sh: dict) -> dict:
        sh = dict(sh)
        if sh.get("type", "circle") in ("circle", "ellipse"):
            r = sh.pop("r", 50)
            sh.setdefault("rx", r)
            sh.setdefault("ry", r)
            sh["type"] = "ellipse"
        return sh

    shapes_a = [norm(s) for s in shapes_a]
    shapes_b = [norm(s) for s in shapes_b]
    out = []
    for i in range(max(len(shapes_a), len(shapes_b))):
        if i < len(shapes_a) and i < len(shapes_b):
            out.append(_lerp(shapes_a[i], shapes_b[i], e))
        else:
            out.append(copy.deepcopy(shapes_a[i] if i < len(shapes_a) else shapes_b[i]))
    return out


# ---------------------------------------------------------------- rendering
def _pts(points: list, s: float) -> list[tuple[float, float]]:
    return [(float(p[0]) * s, float(p[1]) * s) for p in points]


def _rotate(points: list[tuple[float, float]], cx: float, cy: float, deg: float) -> list[tuple[float, float]]:
    if not deg:
        return points
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in points]


def _ellipse_pts(cx: float, cy: float, rx: float, ry: float, n: int = 96) -> list[tuple[float, float]]:
    return [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n)]


def _bezier(points: list[tuple[float, float]], steps: int = 24) -> list[tuple[float, float]]:
    """Catmull-Rom smoothing through control points."""
    if len(points) < 3:
        return points
    p = [points[0]] + points + [points[-1]]
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for j in range(steps):
            t = j / steps
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * ((2 * p1[k]) + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                                    + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3) for k in (0, 1)))
    out.append(points[-1])
    return out  # type: ignore[return-value]


def _font(size: float, name: str | None = None):
    for candidate in ([name] if name else []) + ["arialbd.ttf", "arial.ttf", "segoeui.ttf"]:
        try:
            return ImageFont.truetype(candidate, int(size))
        except OSError:
            continue
    return ImageFont.load_default()


def draw_shape(d: ImageDraw.ImageDraw, sh: dict, s: float) -> None:
    kind = sh.get("type", "circle")
    op = sh.get("opacity")
    fill = color(sh.get("fill"), op) if sh.get("fill") is not None else None
    stroke = color(sh.get("stroke", "#000000" if fill is None else None), op) if sh.get("stroke", "#000000" if fill is None else None) else None
    width = max(0, int(round(float(sh.get("stroke_width", 4 if stroke else 0)) * s)))
    rot = float(sh.get("rotation", 0))

    if kind in ("circle", "ellipse"):
        cx, cy = float(sh.get("x", 0)) * s, float(sh.get("y", 0)) * s
        rx = float(sh.get("rx", sh.get("r", 50))) * s
        ry = float(sh.get("ry", sh.get("r", 50))) * s
        pts = _rotate(_ellipse_pts(cx, cy, rx, ry), cx, cy, rot)
        d.polygon(pts, fill=fill)
        if stroke and width:
            d.line(pts + [pts[0]], fill=stroke, width=width, joint="curve")
    elif kind == "rect":
        x, y = float(sh.get("x", 0)) * s, float(sh.get("y", 0)) * s
        w, h = float(sh.get("w", 100)) * s, float(sh.get("h", 100)) * s
        radius = float(sh.get("radius", 0)) * s
        if rot == 0:
            d.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=fill, outline=stroke, width=width)
        else:
            pts = _rotate([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], x + w / 2, y + h / 2, rot)
            d.polygon(pts, fill=fill)
            if stroke and width:
                d.line(pts + [pts[0]], fill=stroke, width=width, joint="curve")
    elif kind in ("polygon", "path", "line", "polyline"):
        pts = _pts(sh.get("points", []), s)
        if sh.get("smooth"):
            pts = _bezier(pts)
        if rot and pts:
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            pts = _rotate(pts, cx, cy, rot)
        closed = kind == "polygon" or sh.get("closed", False)
        if closed and fill and len(pts) >= 3:
            d.polygon(pts, fill=fill)
        if stroke and width and len(pts) >= 2:
            d.line(pts + ([pts[0]] if closed else []), fill=stroke, width=width, joint="curve")
            if sh.get("round_caps", True):
                r = width / 2
                for px, py in (pts[0], pts[-1]):
                    d.ellipse([px - r, py - r, px + r, py + r], fill=stroke)
    elif kind == "text":
        font = _font(float(sh.get("size", 64)) * s, sh.get("font"))
        x, y = float(sh.get("x", 0)) * s, float(sh.get("y", 0)) * s
        d.text((x, y), str(sh.get("text", "")), font=font, fill=fill or stroke or (0, 0, 0, 255),
               anchor=sh.get("anchor", "mm"), stroke_width=int(float(sh.get("outline_width", 0)) * s),
               stroke_fill=color(sh.get("outline")) if sh.get("outline") else None)
    else:
        raise ValueError(f"unknown shape type: {kind}")


def render_shapes(shapes: list[dict], width: int, height: int, bg: Any = None) -> Image.Image:
    """Render a shape list to an RGBA image (transparent unless bg given). Supersampled."""
    big = Image.new("RGBA", (width * SS, height * SS), color(bg) if bg else (0, 0, 0, 0))
    for sh in shapes:
        # each shape on own layer so opacity composes correctly
        layer = Image.new("RGBA", big.size, (0, 0, 0, 0))
        draw_shape(ImageDraw.Draw(layer), sh, SS)
        big.alpha_composite(layer)
    return big.resize((width, height), Image.LANCZOS)

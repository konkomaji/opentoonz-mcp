"""Shapes -> hand-drawn contours -> SVG (for OpenToonz vector import) and PNG preview.

Pipeline per drawing:
  shape dict -> contours (control points) -> humanize (seeded per drawing number)
  -> optional cel shadow (shape minus light-offset copy) -> ordered paint/line elements
  -> SVG at OpenToonz vector scale (1 vector unit = dpi/53.333 camera px).

Elements are emitted in anime cel order: paint (fill, no line) -> shadow tone -> line art,
so the ink line is never covered by paint and every closed line bounds its paint.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw

from . import style as styles
from .draw import color as parse_color

STAGE_INCH = 53.33333  # OpenToonz Stage::inch (vector units per inch)


@dataclass
class Contour:
    pts: list[tuple[float, float]]
    closed: bool
    smooth: bool  # True: Catmull-Rom through pts. False: straight edges (slightly bowed when humanized)


@dataclass
class Element:
    contours: list[Contour]
    fill: tuple | None
    stroke: tuple | None
    width: float = 0.0  # px
    pressure: list | None = None  # brush: [(u, p)] per contour (same profile for each)


# ------------------------------------------------------------------ geometry
def _rot(pts, cx, cy, deg):
    if not deg:
        return pts
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return [(cx + (x - cx) * c - (y - cy) * s, cy + (x - cx) * s + (y - cy) * c) for x, y in pts]


def _ellipse(cx, cy, rx, ry, rot=0.0, n=8):
    pts = [(cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n)) for i in range(n)]
    return _rot(pts, cx, cy, rot)


def catmull(pts, closed, steps=8):
    """Sample a Catmull-Rom spline through pts."""
    if len(pts) < 3:
        return list(pts)
    P = list(pts)
    n = len(P)
    out = []
    rng = range(n) if closed else range(n - 1)
    for i in rng:
        p0 = P[(i - 1) % n] if closed else P[max(i - 1, 0)]
        p1, p2 = P[i], P[(i + 1) % n]
        p3 = P[(i + 2) % n] if closed else P[min(i + 2, n - 1)]
        for j in range(steps):
            t = j / steps
            t2, t3 = t * t, t * t * t
            out.append(tuple(0.5 * (2 * p1[k] + (-p0[k] + p2[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                                    + (-p0[k] + 3 * p1[k] - 3 * p2[k] + p3[k]) * t3) for k in (0, 1)))
    if not closed:
        out.append(P[-1])
    return out


def _resample(poly, closed, spacing):
    pts = list(poly) + ([poly[0]] if closed else [])
    seg = [math.dist(a, b) for a, b in zip(pts, pts[1:])]
    total = sum(seg)
    if total == 0:
        return list(poly)
    n = min(2000, max(4 if closed else 2, round(total / spacing)))
    out, d, i, acc = [], 0.0, 0, 0.0
    step = total / (n if closed else n - 1)
    for k in range(n):
        target = k * step
        while i < len(seg) - 1 and acc + seg[i] < target:
            acc += seg[i]
            i += 1
        t = 0 if seg[i] == 0 else (target - acc) / seg[i]
        a, b = pts[i], pts[i + 1]
        out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
    return out


@lru_cache(maxsize=8)
def _font(path: str):
    from fontTools.ttLib import TTFont
    return TTFont(path)


def _text_contours(sh) -> list[Contour]:
    from fontTools.pens.basePen import BasePen
    font_file = sh.get("font_file") or r"C:\Windows\Fonts\arialbd.ttf"
    f = _font(font_file)
    gs, cmap = f.getGlyphSet(), f.getBestCmap()
    upem = f["head"].unitsPerEm
    size = float(sh.get("size", 64))
    sc = size / upem
    text = str(sh.get("text", ""))
    adv = 0.0
    glyph_contours: list[list[tuple[float, float]]] = []

    class Pen(BasePen):
        def __init__(self, ox):
            super().__init__(gs)
            self.ox, self.cur = ox, []

        def _moveTo(self, p):
            self.cur = [p]

        def _lineTo(self, p):
            self.cur.append(p)

        def _curveToOne(self, p1, p2, p3):
            p0 = self.cur[-1]
            for i in range(1, 7):
                t = i / 6
                self.cur.append(tuple((1 - t) ** 3 * p0[k] + 3 * (1 - t) ** 2 * t * p1[k] + 3 * (1 - t) * t * t * p2[k]
                                      + t ** 3 * p3[k] for k in (0, 1)))

        def _qCurveToOne(self, p1, p2):
            p0 = self.cur[-1]
            for i in range(1, 5):
                t = i / 4
                self.cur.append(tuple((1 - t) ** 2 * p0[k] + 2 * (1 - t) * t * p1[k] + t * t * p2[k] for k in (0, 1)))

        def _closePath(self):
            if len(self.cur) > 2:
                glyph_contours.append([(self.ox + x, y) for x, y in self.cur])
            self.cur = []

        _endPath = _closePath

    for ch in text:
        g = cmap.get(ord(ch))
        if g is None:
            adv += upem * 0.5
            continue
        gs[g].draw(Pen(adv))
        adv += gs[g].width
    anchor = sh.get("anchor", "mm")
    ascent, descent = f["hhea"].ascent, f["hhea"].descent  # descent is negative (font units, y up)
    ox = {"l": 0, "m": -adv / 2, "r": -adv}.get(anchor[0], -adv / 2)
    # Pillow anchors: a=ascender, d=descender, s=baseline, m=middle of a/d; t/b = top/bottom of the ink
    va = anchor[1] if len(anchor) > 1 else "m"
    oy = {"a": ascent, "d": descent, "s": 0, "m": (ascent + descent) / 2}.get(va, 0)
    if va in ("t", "b"):
        ys = [y for c in glyph_contours for _, y in c]
        oy = (max(ys) if va == "t" else min(ys)) if ys else 0
    x0, y0 = float(sh.get("x", 0)), float(sh.get("y", 0))
    out = []
    for c in glyph_contours:
        pts = [(x0 + (x + ox) * sc, y0 - (y - oy) * sc) for x, y in c]
        out.append(Contour(_rot(pts, x0, y0, float(sh.get("rotation", 0))), True, False))
    return out


def shape_contours(sh: dict) -> list[Contour]:
    t = sh.get("type", "circle")
    rot = float(sh.get("rotation", 0))
    if t in ("circle", "ellipse"):
        rx = float(sh.get("rx", sh.get("r", 50)))
        ry = float(sh.get("ry", sh.get("r", 50)))
        n = 8 if max(rx, ry) < 300 else 12
        return [Contour(_ellipse(float(sh.get("x", 0)), float(sh.get("y", 0)), rx, ry, rot, n), True, True)]
    if t == "rect":
        x, y, w, h = (float(sh.get(k, d)) for k, d in (("x", 0), ("y", 0), ("w", 100), ("h", 100)))
        r = min(float(sh.get("radius", 0)), w / 2, h / 2)
        cx, cy = x + w / 2, y + h / 2
        if r > 0:
            pts = []
            for (ax, ay, a0) in ((x + w - r, y + r, -90), (x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180)):
                for k in range(4):
                    a = math.radians(a0 + k * 30)
                    pts.append((ax + r * math.cos(a), ay + r * math.sin(a)))
            return [Contour(_rot(pts, cx, cy, rot), True, True)]
        return [Contour(_rot([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], cx, cy, rot), True, False)]
    if t in ("polygon", "path", "line", "polyline"):
        pts = [(float(p[0]), float(p[1])) for p in sh.get("points", [])]
        closed = t == "polygon" or bool(sh.get("closed", False))
        if len(pts) < (3 if closed else 2):
            return []  # degenerate: nothing drawable
        if rot and pts:
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            pts = _rot(pts, cx, cy, rot)
        return [Contour(pts, closed, bool(sh.get("smooth", t == "path")))]
    if t == "text":
        return _text_contours(sh)
    if t == "svgpath":
        from .art import svgpath_contours
        return [Contour(pts, closed, False) for pts, closed in
                svgpath_contours(str(sh.get("d", "")), sh.get("transform"))]
    if t == "brush":
        pts = [(float(p[0]), float(p[1])) for p in sh.get("points", [])]
        return [Contour(pts, False, True)] if len(pts) >= 2 else []
    raise ValueError(f"unknown shape type {t}")


# ------------------------------------------------------------------ humanize
def _noise_fn(rng: random.Random, wavelength: float):
    comps = [(rng.uniform(0, 2 * math.pi), wavelength * f, a) for f, a in ((1.0, 0.65), (0.43, 0.35))]
    return lambda s: sum(a * math.sin(2 * math.pi * s / wl + ph) for ph, wl, a in comps)


def humanize(c: Contour, rng: random.Random, amp: float, wavelength: float, overshoot_p: float,
             jitter: float = 0.015) -> Contour:
    """Hand-drawn imperfection. amp in px. Seeded per drawing, so held frames never boil."""
    if amp <= 0:  # clean / wobble 0: geometry must stay exactly as drawn
        return c
    pts = list(c.pts)
    if 2 <= len(pts) <= 16:  # jitter sparse control points only; dense samples (svgpath/text) would turn jagged
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        size = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
        j = jitter * size * (amp > 0)
        pts = [(x + rng.uniform(-j, j) * 0.5, y + rng.uniform(-j, j) * 0.5) for x, y in pts]
    if not c.smooth:
        # straight edges: densify each edge so it can bow slightly like a ruler-less line
        dense = []
        ring = pts + ([pts[0]] if c.closed else [])
        for a, b in zip(ring, ring[1:]):
            L = math.dist(a, b)
            n = max(1, round(L / (wavelength / 2)))
            for k in range(n):
                dense.append((a[0] + (b[0] - a[0]) * k / n, a[1] + (b[1] - a[1]) * k / n))
        if not c.closed:
            dense.append(ring[-1])
        sample = dense
    else:
        sample = _resample(catmull(pts, c.closed), c.closed, wavelength / 4)
    nz = _noise_fn(rng, wavelength)
    out, s = [], 0.0
    n = len(sample)
    for i, p in enumerate(sample):
        a = sample[(i - 1) % n] if (c.closed or i > 0) else p
        b = sample[(i + 1) % n] if (c.closed or i < n - 1) else p
        tx, ty = b[0] - a[0], b[1] - a[1]
        L = math.hypot(tx, ty) or 1.0
        if i:
            s += math.dist(sample[i - 1], p)
        d = amp * nz(s)
        out.append((p[0] - ty / L * d, p[1] + tx / L * d))
    if not c.closed and len(out) >= 2:
        for end in (0, -1):
            if rng.random() < overshoot_p:
                p, q = out[end], out[1 if end == 0 else -2]
                L = math.dist(p, q) or 1.0
                e = rng.uniform(1.0, 3.0)
                ext = (p[0] + (p[0] - q[0]) / L * e, p[1] + (p[1] - q[1]) / L * e)
                if end == 0:
                    out.insert(0, ext)
                else:
                    out.append(ext)
    return Contour(out, c.closed, True)


# ------------------------------------------------------------------ cel shading
def _poly(contours: list[Contour]):
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    polys = []
    for c in contours:
        if c.closed and len(c.pts) >= 3:
            pts = catmull(c.pts, True, 6) if c.smooth else c.pts
            from shapely import make_valid
            g = make_valid(Polygon(pts))  # keeps every lobe of self-intersecting shapes (buffer(0) drops some)
            parts = [x for x in getattr(g, "geoms", [g]) if x.geom_type in ("Polygon", "MultiPolygon")]
            if parts:
                p = unary_union(parts)
                if not p.is_empty:
                    polys.append(p)
    if not polys:
        return None
    # even-odd: glyph holes cancel
    acc = polys[0]
    for p in polys[1:]:
        acc = acc.symmetric_difference(p)
    return acc


def cel_shadow(contours: list[Contour], light_deg: float, depth: float, inset: float = 0.0):
    """kage region = shape - shape shifted toward the light, inset so it never covers the ink line."""
    from shapely import affinity
    shape = _poly(contours)
    if shape is None:
        return []
    a = math.radians(light_deg)
    lit = affinity.translate(shape, depth * math.cos(a), -depth * math.sin(a))
    sh = shape.buffer(-inset).difference(lit).buffer(-0.3).buffer(0.3)
    geoms = getattr(sh, "geoms", [sh])
    out = []
    for g in geoms:
        if g.is_empty or g.area < 4:
            continue
        ring = list(g.exterior.coords)[:-1]
        out.append(Contour(_simplify(ring, 0.6), True, False))
    return out


def _simplify(pts, tol):
    from shapely.geometry import LineString
    ls = LineString(pts + [pts[0]]).simplify(tol)
    return list(ls.coords)[:-1]


# ------------------------------------------------------------------ build elements
def build_elements(shapes: list[dict], canvas_h: int, style_name: str | None, seed: int) -> list[Element]:
    st = styles.get(style_name)
    px = canvas_h / 1080.0
    els: list[Element] = []
    for i, sh in enumerate(shapes):
        rng = random.Random((seed * 1000003 + i * 7919) & 0xFFFFFFFF)
        human = sh.get("human", True)
        amp = float(sh.get("wobble", st["wobble"])) * px if human else 0.0
        cs = shape_contours(sh)
        if not cs:
            continue
        lim = 100000.0 * px  # OpenToonz stores coords as int32 at 128x precision -> overflow past ~131k units
        if any(abs(x) > lim or abs(y) > lim for c in cs for x, y in c.pts):
            raise ValueError(f"shape {i} ({sh.get('type')}) has coordinates beyond +/-{lim:.0f} px")
        if sh.get("type") != "text":
            cs = [humanize(c, rng, amp, st["wavelength"] * px, st["overshoot_ends"], 0.015 if human else 0)
                  for c in cs]
        op = sh.get("opacity")
        fill = parse_color(sh["fill"], op) if sh.get("fill") is not None else None
        default_stroke = "#1a1a1a" if fill is None or sh.get("type") not in ("text",) else None
        stroke_c = sh.get("stroke", sh.get("color", default_stroke))
        if stroke_c is not None and sh.get("trace_line"):
            stroke_c = styles.trace_line_color(parse_color(fill or stroke_c))
        stroke = parse_color(stroke_c, op) if stroke_c is not None else None
        weight = st["line"] * (st["interior"] if sh.get("interior") else 1.0)
        if sh.get("type") == "brush":
            weight = float(sh.get("width", weight * 2.5))
        width = float(sh.get("stroke_width", weight)) * px if stroke else 0.0
        if width <= 0:
            stroke = None
        closed = [c for c in cs if c.closed]
        open_ = [c for c in cs if not c.closed]
        if fill and closed:
            # ink + paint in ONE element: OpenToonz gives a separate fill-less loop its own
            # transparent region that would hide the paint underneath.
            els.append(Element(closed, fill, stroke, width))
            shade = sh.get("shadow", st["shadow"] and sh.get("type") in ("circle", "ellipse", "polygon", "rect"))
            if shade:
                cfg = shade if isinstance(shade, dict) else {}
                xs = [p[0] for c in cs for p in c.pts]
                ys = [p[1] for c in cs for p in c.pts]
                size = max(max(xs) - min(xs), max(ys) - min(ys))
                depth = float(cfg.get("depth", 0.22 * size))
                light = float(cfg.get("light", 135))  # degrees, 90 = from top, 135 = top-left
                sc = parse_color(cfg["color"]) if cfg.get("color") else styles.shadow_color(fill)
                shadow = cel_shadow(cs, light, depth, inset=width / 2 + 0.5 * px)
                if shadow:
                    els.append(Element(shadow, sc, None))
            if stroke and open_:
                els.append(Element(open_, None, stroke, width))
        elif stroke:
            prof = None
            if sh.get("type") == "brush":
                from .art import brush_profile
                prof = brush_profile(sh.get("points", []))
            els.append(Element(cs, None, stroke, width, prof))
    return els


def ink_profiles(els: list[Element]) -> list:
    """Expected sequence of VISIBLE (inked) strokes in the converted .pli, with the brush pressure
    profile for each (None = use style taper). Fill-only elements produce zero-width strokes,
    which pressure patching skips, so they are not listed."""
    out = []
    for e in els:
        if e.stroke and e.width > 0:
            out += [e.pressure] * len(e.contours)
    return out


# ------------------------------------------------------------------ outputs
def _bezier_d(c: Contour, k: float, ox: float = 0.0, oy: float = 0.0) -> str:
    P = [((x + ox) * k, (y + oy) * k) for x, y in c.pts]
    n = len(P)
    if n == 0:
        return ""
    f = lambda p: f"{p[0]:.2f} {p[1]:.2f}"
    d = [f"M {f(P[0])}"]
    if not c.smooth or n < 3:
        d += [f"L {f(p)}" for p in P[1:]]
    else:
        rng = range(n) if c.closed else range(n - 1)
        for i in rng:
            p0 = P[(i - 1) % n] if c.closed else P[max(i - 1, 0)]
            p1, p2 = P[i], P[(i + 1) % n]
            p3 = P[(i + 2) % n] if c.closed else P[min(i + 2, n - 1)]
            c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
            c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
            d.append(f"C {f(c1)} {f(c2)} {f(p2)}")
    if c.closed:
        d.append("Z")
    return " ".join(d)


def _hex(c):
    return "#%02x%02x%02x" % tuple(c[:3])


def to_svg(els: list[Element], width: int, height: int, dpi: float) -> str:
    k = STAGE_INCH / dpi  # camera px -> vector units
    W, H = width * k, height * k
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.3f}" height="{H:.3f}" '
           f'viewBox="0 0 {W:.3f} {H:.3f}">']
    for e in els:
        for c in e.contours:
            d = _bezier_d(c, k)
            if not d:
                continue
            attrs = []
            if e.fill:
                attrs.append(f'fill="{_hex(e.fill)}"')
                if e.fill[3] < 255:
                    attrs.append(f'fill-opacity="{e.fill[3] / 255:.3f}"')
            else:
                attrs.append('fill="none"')
            if e.stroke:
                attrs.append(f'stroke="{_hex(e.stroke)}" stroke-width="{e.width * k:.3f}" '
                             f'stroke-linecap="round" stroke-linejoin="round"')
                if e.stroke[3] < 255:
                    attrs.append(f'stroke-opacity="{e.stroke[3] / 255:.3f}"')
            else:
                attrs.append('stroke="none"')
            out.append(f'<path d="{d}" {" ".join(attrs)}/>')
    out.append("</svg>")
    return "\n".join(out)


def to_png(els: list[Element], width: int, height: int, ss: int = 2) -> Image.Image:
    """Raster preview of the same geometry (constant line width)."""
    big = Image.new("RGBA", (width * ss, height * ss), (0, 0, 0, 0))
    for e in els:
        layer = Image.new("RGBA", big.size, (0, 0, 0, 0))
        dr = ImageDraw.Draw(layer)
        if e.fill:
            from PIL import ImageChops
            mask = Image.new("L", big.size, 0)
            md = ImageDraw.Draw(mask)
            for c in e.contours:  # even-odd so glyph holes stay open
                if len(c.pts) < 3:
                    continue
                tmp = Image.new("L", big.size, 0)
                pts = catmull(c.pts, True, 6) if c.smooth else c.pts
                ImageDraw.Draw(tmp).polygon([(x * ss, y * ss) for x, y in pts], fill=255)
                mask = ImageChops.logical_xor(mask.convert("1"), tmp.convert("1")).convert("L")
            layer.paste(Image.new("RGBA", big.size, e.fill), (0, 0), mask)
        if e.stroke:
            w = max(1, round(e.width * ss))
            for c in e.contours:
                if len(c.pts) < 2:
                    continue
                pts = catmull(c.pts, c.closed, 6) if c.smooth else c.pts + ([c.pts[0]] if c.closed else [])
                if c.smooth and c.closed:
                    pts = pts + [pts[0]]
                pts = [(x * ss, y * ss) for x, y in pts]
                dr.line(pts, fill=e.stroke, width=w, joint="curve")
                for px_, py_ in (pts[0], pts[-1]):
                    dr.ellipse([px_ - w / 2, py_ - w / 2, px_ + w / 2, py_ + w / 2], fill=e.stroke)
        big.alpha_composite(layer)
    return big.resize((width, height), Image.LANCZOS)

"""Reusable art: parts library (model sheets), SVG file import, image placement for raster/traced layers."""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

from . import art, config

WORK = config.work_dir()
PARTS_DIR = WORK / "knowledge" / "parts"


# ------------------------------------------------------------------ parts library
def _part_file(name: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name or ""):
        raise ValueError("part name may only use letters, digits, '_' and '-'")
    return PARTS_DIR / f"{name}.json"


def save_part(name: str, shapes: list[dict], anchor: tuple[float, float] = (0, 0),
              variants: dict[str, list[dict]] | None = None, notes: str = "") -> dict:
    """Store a named part (shapes in their own coordinate space) with optional named variants,
    e.g. a mouth chart {'A': [...], 'E': [...], 'closed': [...]}. Parts are global across scenes."""
    data = {"name": name, "anchor": list(anchor), "shapes": shapes, "variants": variants or {}, "notes": notes}
    PARTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _part_file(name).with_name(f"{name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    os.replace(tmp, _part_file(name))
    return {"part": name, "variants": ["default"] + list(data["variants"])}


def load_part(name: str) -> dict:
    f = _part_file(name)
    if not f.exists():
        raise ValueError(f"part '{name}' not found. Parts: {list_parts()}")
    return json.loads(f.read_text(encoding="utf-8"))


def list_parts() -> list[dict]:
    if not PARTS_DIR.exists():
        return []
    out = []
    for f in sorted(PARTS_DIR.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        out.append({"part": d["name"], "variants": ["default"] + list(d.get("variants", {})),
                    "anchor": d.get("anchor"), "notes": d.get("notes", "")})
    return out


def expand_parts(shapes: list[dict], depth: int = 0) -> list[dict]:
    """Replace {"type":"part", name, variant?, x, y, scale?, rotation?, flip?} with its transformed shapes.
    Parts may contain parts (max depth 5). Style keys on the part (e.g. stroke, fill) override."""
    if depth > 5:
        raise ValueError("parts nested deeper than 5 levels (cycle?)")
    out: list[dict] = []
    for sh in shapes:
        if sh.get("type") != "part":
            out.append(sh)
            continue
        p = load_part(str(sh.get("name")))
        v = sh.get("variant", "default")
        src = p["shapes"] if v in (None, "default") else p.get("variants", {}).get(v)
        if src is None:
            raise ValueError(f"part '{p['name']}' has no variant '{v}'. Variants: {['default'] + list(p['variants'])}")
        m = art.placement(float(sh.get("x", 0)), float(sh.get("y", 0)), float(sh.get("scale", 1.0)),
                          float(sh.get("rotation", 0.0)), bool(sh.get("flip", False)), tuple(p.get("anchor", (0, 0))))
        overrides = {k: sh[k] for k in ("fill", "stroke", "opacity", "stroke_width", "wobble", "human", "shadow")
                     if k in sh}
        for inner in expand_parts(src, depth + 1):
            out.append(dict(art.transform_shape(inner, m), **overrides))
    return out


# ------------------------------------------------------------------ SVG import
def svg_file_to_shapes(path: str, box: tuple[float, float, float, float] | None = None,
                       canvas: tuple[int, int] = (1920, 1080), human: bool = False) -> list[dict]:
    """Convert every visible shape of an SVG file into svgpath shape dicts (canvas px).
    box=(x, y, w, h): fit the artwork inside it keeping proportions (default: the whole canvas)."""
    from svgelements import SVG, Shape, Text
    from svgelements import Path as SPath

    svg = SVG.parse(path, reify=True)
    items = []
    for el in svg.elements():
        if isinstance(el, Text) or not isinstance(el, Shape):
            continue
        if el.values.get("visibility") == "hidden" or el.values.get("display") == "none":
            continue
        p = SPath(el)
        if len(p) == 0:
            continue
        d = p.d()
        if not d.strip():
            continue
        fill = None if el.fill is None or el.fill.value is None else _rgba(el.fill)
        stroke = None if el.stroke is None or el.stroke.value is None else _rgba(el.stroke)
        if fill is None and stroke is None:
            continue
        sw = float(el.stroke_width or 0) if stroke else 0.0
        items.append({"type": "svgpath", "d": d, "fill": fill, "stroke": stroke, "stroke_width": sw, "human": human})
    if not items:
        raise ValueError("no drawable shapes found in the SVG")
    bb = svg.bbox()
    if bb is None:
        return items
    x0, y0, x1, y1 = bb
    bw, bh = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    bx, by, bwid, bhgt = box or (0, 0, canvas[0], canvas[1])
    s = min(bwid / bw, bhgt / bh)
    ox = bx + (bwid - bw * s) / 2 - x0 * s
    oy = by + (bhgt - bh * s) / 2 - y0 * s
    m = (s, 0.0, 0.0, s, ox, oy)
    return [art.transform_shape(it, m) for it in items]


def _rgba(c) -> list[int]:
    return [int(c.red), int(c.green), int(c.blue), int(round(c.alpha))]


# ------------------------------------------------------------------ image placement
def place_image(src: str, canvas: tuple[int, int], box: tuple[float, float, float, float] | None = None,
                fit: str = "contain", background: str | None = None):
    """Return an RGBA canvas-sized image with src fitted into box (default: whole canvas).
    fit: contain (letterbox) | cover (crop) | stretch | none (original pixel size, centred in box)."""
    from PIL import Image
    im = Image.open(src).convert("RGBA")
    W, H = canvas
    bx, by, bw, bh = box or (0, 0, W, H)
    if fit == "stretch":
        tw, th = bw, bh
    elif fit == "none":
        tw, th = im.width, im.height
    else:
        k = (min if fit == "contain" else max)(bw / im.width, bh / im.height)
        tw, th = im.width * k, im.height * k
    im = im.resize((max(1, round(tw)), max(1, round(th))), Image.LANCZOS)
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0) if background is None else background)
    px, py = round(bx + (bw - im.width) / 2), round(by + (bh - im.height) / 2)
    out.alpha_composite(im, (px, py)) if (px >= 0 and py >= 0 and px + im.width <= W and py + im.height <= H) \
        else out.paste(im, (px, py), im)
    return out

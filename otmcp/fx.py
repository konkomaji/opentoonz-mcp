"""Studio "looks" as real OpenToonz fx graphs written into the .tnz (editable in the FX Schematic).

Serialization (OpenToonz 1.8 source: tfx.cpp TFx::saveData, fxdag.cpp, tparamset/tnotanimatableparam):
* An fx is <STD_<id> id='N'> with <params>, <ports>, <numberId>, <name>, <fxId>, <opened>.
* TOStream writes an object in full at its FIRST occurrence in the file; later occurrences are
  self-closing references <Tag id='N'/>. So an fx inserted between the xsheet node and the output
  holds the xsheet fx definition inside its Source port, and <xsheet> keeps only a reference.
* Params: double  <name><default>v</default></name>; bool/int "<name>default value</name>";
  enum "<name>index</name>"; pixel <name><red><default>..</default></red>..<matte>..</matte></name>;
  point <name><x><default>..</default></x><y>..</y></name>.
* Length params (measure "fxLength") are stored in 1/53.333 inch: px = value * dpi / 53.333.
Every node this module writes is named 'otmcp:<look>:<role>' so a look can be replaced or removed
without touching fx made by hand in the GUI.
"""
from __future__ import annotations

import math

from .tnztree import Node

STAGE_INCH = 53.33333
TAG = "otmcp:"

# (fx id, param) pairs whose values are lengths
LENGTH = {("blurFx", "value"), ("bodyHighLightFx", "point"), ("bodyHighLightFx", "blur"), ("glowFx", "value"),
          ("iwa_BloomFx", "size"), ("mosaicFx", "size"), ("mosaicFx", "distance"),
          ("iwa_BarrelDistortFx", "center")}
# param kinds; anything not listed is a double
KIND = {
    ("bodyHighLightFx", "mode"): "enum", ("bodyHighLightFx", "point"): "point", ("bodyHighLightFx", "color"): "pixel",
    ("bodyHighLightFx", "invert"): "bool", ("glowFx", "color"): "pixel", ("noiseFx", "Red"): "bool",
    ("noiseFx", "Green"): "bool", ("noiseFx", "Blue"): "bool", ("noiseFx", "Black_White"): "bool",
    ("noiseFx", "Animate"): "bool", ("saltpepperNoiseFx", "Animate"): "bool", ("iwa_BloomFx", "auto_gain"): "bool",
    ("iwa_BloomFx", "alpha_mode"): "enum", ("iwa_BloomFx", "alpha_rendering"): "bool",
    ("mosaicFx", "bg_color"): "pixel", ("mosaicFx", "shape"): "enum", ("inohsvNoiseFx", "type"): "enum",
    ("inohsvNoiseFx", "anti_alias"): "bool", ("inohsvNoiseFx", "reference"): "enum",
    ("iwa_BarrelDistortFx", "center"): "point", ("blurFx", "useSSE"): "bool",
}
BODY_MODE = {"add": 0, "subtract": 1, "multiply": 2, "lighten": 3, "darken": 4}

# A look = post chain on the whole frame (applied in order after the xsheet) + per-layer chain.
# Values: lengths in px, colours '#rrggbb', points (x, y) px. `light` in a param means: computed from
# light_angle (see _resolve). Sources for the looks: research notes on each studio's published process.
LOOKS: dict[str, dict] = {
    "ghibli": {
        "about": "Soft painted daylight: gentle bloom on highlights, a touch warmer and less saturated, fine paper grain.",
        "post": [("bloom", {"gain": 1.0, "size": 40, "decay": 1.0, "opacity": 0.25}, "bloom"),
                 ("hsvScaleFx", {"saturation_scale": 92, "value_scale": 102}, "grade"),
                 ("noiseFx", {"Intensity": 5, "Animate": False}, "paper grain")],
        "layer": [],
    },
    "klaus": {
        "about": "Klaus-style volumetric lighting on 2D characters: a soft form shadow away from the light, a warm "
                 "rim toward it, then a gentle overall grade and grain.",
        "post": [("hsvScaleFx", {"saturation_scale": 95}, "grade"),
                 ("noiseFx", {"Intensity": 4, "Animate": False}, "grain")],
        "layer": [("bodyHighLightFx", {"mode": "multiply", "color": "#6a5a86", "transparency": 0.3, "blur": 6,
                                       "point": "away:14"}, "form shadow"),
                  ("bodyHighLightFx", {"mode": "add", "color": "#ffcf8a", "transparency": 0.25, "blur": 1,
                                       "point": "toward:5"}, "rim light")],
    },
    "spider_verse": {
        "about": "Comic-print look: offset colour plates toward the frame edges (chromatic aberration), "
                 "hard tone banding and print specks.",
        "post": [("iwa_BarrelDistortFx", {"chromaticAberration": 0.006, "distortion": 0.0}, "misregistration"),
                 ("posterizeFx", {"levels": 9}, "tone banding"),
                 ("saltpepperNoiseFx", {"Intensity": 4, "Animate": False}, "print specks")],
        "layer": [],
    },
    "comic": {
        "about": "Flat printed comic: posterized tones and print specks.",
        "post": [("posterizeFx", {"levels": 7}, "tone banding"),
                 ("saltpepperNoiseFx", {"Intensity": 5, "Animate": False}, "print specks")],
        "layer": [],
    },
    "anime_satsuei": {
        "about": "Anime compositing (satsuei): strong bloom on highlights, slightly richer colour.",
        "post": [("bloom", {"gain": 1.0, "size": 50, "decay": 1.0, "opacity": 0.35}, "bloom"),
                 ("hsvScaleFx", {"saturation_scale": 108}, "grade")],
        "layer": [],
    },
    "painterly": {
        "about": "Painted texture (Arcane-like): colour mottling inside fills and coarse grain.",
        "post": [("inohsvNoiseFx", {"hue": 0.02, "saturation": 0.06, "value": 0.06, "nblur": 1.0}, "paint mottling"),
                 ("noiseFx", {"Intensity": 7, "Animate": False}, "grain")],
        "layer": [],
    },
    "cartoon_saloon": {
        "about": "Flat, graphic, print-like (Cartoon Saloon): muted palette, soft texture grain.",
        "post": [("hsvScaleFx", {"saturation_scale": 85, "value_scale": 98}, "muted palette"),
                 ("noiseFx", {"Intensity": 8, "Animate": False}, "texture grain")],
        "layer": [],
    },
}


# ---------------------------------------------------------------------- tree helpers
def _parents(root: Node) -> dict[int, Node]:
    par: dict[int, Node] = {}
    for n in root.walk():
        for c in n.children:
            if isinstance(c, Node):
                par[id(c)] = n
    return par


def _ordered(root: Node) -> list[Node]:
    return list(root.walk())


def _is_fx(n: Node) -> bool:
    """A persisted fx object (pegbar ids like 'Col1' and parent refs are names, not object ids)."""
    return n.attrs.get("id", "").isdigit() and "Fx" in n.name


def _is_def(n: Node) -> bool:
    return _is_fx(n) and not n.selfclose


def _defs(root: Node) -> dict[str, Node]:
    return {n.attrs["id"]: n for n in root.walk() if _is_def(n)}


def _ref(n: Node) -> Node:
    return Node(n.name, {"id": n.attrs["id"]}, selfclose=True)


def _replace(parent: Node, old: Node, new: Node | None) -> None:
    parent.children = [(new if c is old else c) for c in parent.children if not (c is old and new is None)]


def _port_input(fxdef: Node) -> Node | None:
    """The input a removed fx hands back to its consumers: Source, or Back for a blend."""
    ports = fxdef.find("ports")
    if ports is None:
        return None
    for want in ("Source", "Back"):
        p = ports.find(want)
        if p is not None and p.nodes():
            return p.nodes()[0]
    for p in ports.nodes():
        if p.nodes():
            return p.nodes()[0]
    return None


def _name(fxdef: Node) -> str:
    n = fxdef.find("name")
    return n.text() if n is not None else ""


# ---------------------------------------------------------------------- param encoding
def _double(v: float) -> list[Node]:
    return [Node("default", children=[_num(v)])]


def _num(v: float) -> str:
    s = f"{float(v):.6f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def _pixel(c: str) -> list[Node]:
    c = c.lstrip("#")
    rgb = [int(c[i:i + 2], 16) for i in (0, 2, 4)] + [255]
    return [Node(ch, children=_double(v)) for ch, v in zip(("red", "green", "blue", "matte"), rgb)]


def param_node(fx: str, name: str, value, dpi: float) -> Node:
    kind = KIND.get((fx, name), "double")
    if (fx, name) in LENGTH:
        k = STAGE_INCH / dpi
        value = (value[0] * k, value[1] * k) if isinstance(value, (tuple, list)) else value * k
    if kind == "bool":
        return Node(name, children=[str(int(bool(value))), str(int(bool(value)))])
    if kind == "enum":
        return Node(name, children=[str(int(value))])
    if kind == "pixel":
        return Node(name, children=_pixel(value))
    if kind == "point":
        return Node(name, children=[Node("x", children=_double(value[0])), Node("y", children=_double(value[1]))])
    return Node(name, children=_double(value))


def _resolve(fx: str, params: dict, light_angle: float, strength: float) -> dict:
    """Turn look shorthands into concrete params: 'toward:N'/'away:N' points from the light direction,
    body-highlight mode names, and strength scaling of the effect amounts."""
    out = {}
    for k, v in params.items():
        if isinstance(v, str) and v.startswith(("toward:", "away:")):
            d = float(v.split(":")[1]) * max(strength, 0.0)
            sgn = 1 if v.startswith("toward") else -1
            a = math.radians(light_angle)
            v = (sgn * d * math.cos(a), sgn * d * math.sin(a))  # OpenToonz +y up, like light_angle
        elif fx == "bodyHighLightFx" and k == "mode":
            v = BODY_MODE[v]
        elif k in ("Intensity", "gain", "chromaticAberration") or (fx == "inohsvNoiseFx" and k in
                                                                     ("hue", "saturation", "value")):
            v = v * strength
        elif k in ("saturation_scale", "value_scale"):   # percent: 100 = unchanged
            v = 100 + (v - 100) * strength
        elif fx == "bodyHighLightFx" and k == "transparency":
            v = 1 - (1 - v) * min(strength, 1.0)
        out[k] = v
    return out


# ---------------------------------------------------------------------- graph editing
def _new_fx(doc, fx: str, params: dict, inp: Node | list, label: str, dpi: float) -> Node:
    """inp: the Source input node, or [(port name, node), ...] for multi-input fx."""
    tag = f"STD_{fx}"
    same = [n for n in doc.root.walk() if n.name == tag and _is_def(n)]
    num = max([int(n.find("numberId").text()) for n in same if n.find("numberId") is not None] + [0]) + 1
    short = fx.replace("iwa_", "").replace("ino", "")
    short = short[0].upper() + short[1:]
    short = short[:-2] if short.endswith("Fx") else short
    node = Node(tag, {"id": str(doc._new_id())}, [
        Node("params", children=[param_node(fx, k, v, dpi) for k, v in params.items()]),
        Node("ports", children=[Node(pn, children=[pi]) for pn, pi in
                                (inp if isinstance(inp, list) else [("Source", inp)])]),
        Node("numberId", children=[str(num)]),
        Node("name", children=[f'"{label}"']),
        Node("fxId", children=[f'"{short}{num:02d}"']),
        Node("opened", children=["1"])])
    return node


def ours(doc, look: str | None = None) -> list[Node]:
    pre = TAG + (f"{look}:" if look else "")
    return [n for n in doc.root.walk() if _is_def(n) and n.name.startswith("STD_") and _name(n).startswith(pre)]


def normalize(root: Node) -> int:
    """Make the first occurrence of every persisted id its full definition (TOStream rule): when a
    reference comes before the definition, the two swap places. Returns the number of swaps."""
    swaps = 0
    while True:
        seen: set[str] = set()
        moved = False
        for n in _ordered(root):
            if not _is_fx(n):
                continue
            i = n.attrs["id"]
            if i in seen:
                continue
            seen.add(i)
            if n.selfclose:
                d = _defs(root).get(i)
                if d is not None:
                    par = _parents(root)
                    pd, pr = par[id(d)], par[id(n)]
                    _replace(pd, d, _ref(d))
                    _replace(pr, n, d)
                    swaps += 1
                    moved = True
                    break
        if not moved:
            return swaps


def remove(doc) -> int:
    """Delete every otmcp look node and splice its input back in (GUI-made fx are untouched).
    Definitions nested inside a removed node's ports (OpenToonz writes them there) are kept: they are
    parked, then normalize() moves each to its first remaining reference."""
    count = 0
    fxn = doc.xsheet.child("fxnodes")
    while True:
        mine = ours(doc)
        if not mine:
            break
        node = mine[0]
        fid = node.attrs["id"]
        inp = _port_input(node)
        par = _parents(doc.root)
        ports = node.find("ports")
        for port in (ports.nodes() if ports is not None else []):
            for ch in port.nodes():
                if _is_def(ch):                         # keep nested definitions alive
                    _replace(port, ch, _ref(ch))
                    fxn.children.append(Node("_parked", children=[ch]))
        par = _parents(doc.root)
        for n in _ordered(doc.root):
            if n is not node and _is_fx(n) and n.attrs["id"] == fid and n.selfclose:
                _replace(par[id(n)], n, _ref(inp) if inp is not None else None)
        parent = par[id(node)]
        if parent.name == "fxnode":
            _replace(par[id(parent)], parent, None)
        else:
            _replace(parent, node, _ref(inp) if inp is not None else None)
        count += 1
    normalize(doc.root)
    for pk in fxn.nodes("_parked"):
        if any(isinstance(c, Node) and c.selfclose for c in pk.children) or not pk.nodes():
            fxn.remove(pk)
    if fxn.nodes("_parked"):
        raise RuntimeError("an fx definition lost all its references while removing a look")
    lst = fxn.find("internal")
    if lst is not None and not lst.nodes():
        fxn.remove(lst)
    return count


def _internal(doc) -> Node:
    fxn = doc.xsheet.child("fxnodes")
    internal = fxn.find("internal")
    if internal is None:
        internal = Node("internal")
        fxn.children.insert(0, internal)
    return internal


def apply_post(doc, look: str, steps: list, dpi: float, light_angle: float, strength: float) -> list[str]:
    """Insert a chain just before the output fx (whole-frame look). Whatever fed the output before
    (the xsheet node, or fx made in the GUI) now feeds the chain."""
    out = doc.xsheet.child("fxnodes").find("output")
    if out is None or not out.nodes():
        raise ValueError("scene has no output fx")
    ports = out.nodes()[0].find("ports")
    port = ports.nodes()[0] if ports is not None and ports.nodes() else None
    if port is None or not port.nodes():
        raise ValueError("output fx has no source")
    src = port.nodes()[0]
    internal = _internal(doc)
    made, prev = [], src
    for fx, params, role in steps:
        label = f"{TAG}{look}:{role}"
        if fx == "bloom":  # iwa_Bloom outputs only the light: screen it back over the picture
            p = dict(params)
            opacity = p.pop("opacity", 0.6) * strength
            glow = _new_fx(doc, "iwa_BloomFx", _resolve("iwa_BloomFx", p, light_angle, 1.0), _ref(prev),
                           label + " light", dpi)
            internal.children.append(Node("fxnode", children=[glow]))
            node = _new_fx(doc, "inoScreenFx", {"opacity": opacity}, [("Fore", _ref(glow)), ("Back", _ref(prev))],
                           label, dpi)
        else:
            node = _new_fx(doc, fx, _resolve(fx, params, light_angle, strength), _ref(prev), label, dpi)
        internal.children.append(Node("fxnode", children=[node]))
        made.append(f"{node.name}#{node.attrs['id']} ({role})")
        prev = node
    port.children = [_ref(prev)]
    normalize(doc.root)
    return made


def apply_layer(doc, look: str, steps: list, column_fx_ids: list[str], dpi: float, light_angle: float,
                strength: float) -> list[str]:
    """Insert a chain right after each column's fx: every consumer of the column now reads the chain."""
    internal = _internal(doc)
    made = []
    defs = _defs(doc.root)
    for cid in column_fx_ids:
        col_def = defs[cid]
        consumers = [n for n in doc.root.walk() if _is_fx(n) and n.attrs["id"] == cid and n.selfclose]
        prev = None
        chain = []
        for fx, params, role in steps:
            inp = _ref(col_def) if prev is None else _ref(prev)
            node = _new_fx(doc, fx, _resolve(fx, params, light_angle, strength), inp, f"{TAG}{look}:{role}", dpi)
            internal.children.append(Node("fxnode", children=[node]))
            chain.append(node)
            made.append(f"{node.name}#{node.attrs['id']} ({role})")
            prev = node
        if prev is not None:
            par = _parents(doc.root)
            for n in consumers:
                _replace(par[id(n)], n, _ref(prev))
    normalize(doc.root)
    return made

"""In-place editing of an OpenToonz scene (.tnz) via the lossless tree.

The .tnz on disk is the single source of truth for timing, keyframes, hierarchy and
everything a user may have changed in the OpenToonz GUI. We only touch the nodes an
operation needs; all other content (fx, notes, sound, foreign levels) is preserved.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from . import tnz, tnztree as T
from .tnztree import Node, unquote

INTERP_CODE = {"linear": "L", "constant": "C", "ease": "Ep", "speed": "S", "exp": "Exp", "ease_frames": "E"}
CODE_INTERP = {v: k for k, v in INTERP_CODE.items()}
PARAM_TAGS = ["x", "y", "z", "so", "sx", "sy", "sc", "rot", "shx", "shy"]


def file_hash(path) -> str | None:
    try:
        return hashlib.sha1(open(path, "rb").read()).hexdigest()
    except FileNotFoundError:
        return None


@dataclass
class Cell:
    level_id: str
    fid: str  # "1", "12a"


class SceneDoc:
    def __init__(self, root: Node):
        self.root = root
        self.tnz = root.find("tnz")
        if self.tnz is None:
            raise ValueError("not a tnz scene (missing <tnz>)")

    # ---------------------------------------------------------------- io
    @classmethod
    def load(cls, path) -> "SceneDoc":
        return cls(T.load(str(path)))

    @classmethod
    def new(cls, scene: tnz.Scene) -> "SceneDoc":
        return cls(T.parse(tnz.build(scene)))

    def text(self) -> str:
        self._sync_framecount()
        return T.dump(self.root)

    def save(self, path) -> None:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.text())

    # ---------------------------------------------------------------- sections
    @property
    def props(self) -> Node:
        return self.tnz.child("properties", before="levelSet")

    @property
    def xsheet(self) -> Node:
        return self.tnz.child("xsheet")

    def _levels_node(self) -> Node:
        ls = self.tnz.child("levelSet", before="xsheet")
        return ls.child("levels")

    def _new_id(self) -> int:
        return T.max_id(self.root) + 1

    # ---------------------------------------------------------------- properties
    def camera_res(self) -> tuple[int, int]:
        cam = self.props.path("cameras", "camera", "cameraRes")
        if cam is None:
            return 1920, 1080
        a = cam.tokens()
        return int(float(a[0])), int(float(a[1]))

    def camera_size(self) -> tuple[float, float]:
        cam = self.props.path("cameras", "camera", "cameraSize")
        if cam is None:
            return 16.0, 9.0
        a = cam.tokens()
        return float(a[0]), float(a[1])

    def dpi(self) -> float:
        return self.camera_res()[0] / self.camera_size()[0]

    def fps(self) -> float:
        for o in self.props.child("outputs").nodes("output"):
            if o.attrs.get("name") == "main" and o.find("fps") is not None:
                return float(o.find("fps").tokens()[0])
        return 24.0

    def set_fps(self, fps: float) -> None:
        for o in self.props.child("outputs").nodes("output"):
            o.child("fps").set_tokens(float(fps))

    def set_output_range(self, frames: int) -> None:
        for o in self.props.child("outputs").nodes("output"):
            o.child("range").set_tokens(0, frames - 1)

    def output_range(self) -> int | None:
        for o in self.props.child("outputs").nodes("output"):
            if o.attrs.get("name") == "main" and o.find("range") is not None:
                a = o.find("range").tokens()
                return int(a[1]) + 1 if int(a[1]) >= 0 else None
        return None

    def bg(self) -> tuple[int, ...]:
        n = self.props.find("bgColor")
        return tuple(int(float(t)) for t in n.tokens()) if n is not None else (255, 255, 255, 0)

    def set_bg(self, rgba) -> None:
        self.props.child("bgColor").set_tokens(*[int(c) for c in rgba])

    def set_column_opacity_render(self, on: bool = True) -> None:
        self.props.child("columnColorFilterOnRender", before="noteColors").set_tokens(1 if on else 0)

    # ---------------------------------------------------------------- levels
    def levels(self) -> list[dict]:
        out = []
        for n in self._levels_node().nodes():
            if n.selfclose:
                continue
            name = unquote(n.tokens()[0]) if n.tokens() else ""
            p = n.find("path")
            info = n.find("info")
            out.append({"id": n.attrs.get("id"), "tag": n.name, "name": name,
                        "path": unquote(p.tokens()[0]) if p is not None and p.tokens() else "",
                        "dpi": float(info.attrs["dpix"]) if info is not None and "dpix" in info.attrs else None,
                        "node": n})
        return out

    def level_by_path(self, path: str) -> dict | None:
        norm = lambda s: s.replace("\\", "/").lower()
        for lv in self.levels():
            if norm(lv["path"]) == norm(path):
                return lv
        return None

    def add_level(self, name: str, path: str, dpi: float | None) -> str:
        lid = str(self._new_id())
        n = Node("level", {"id": lid})
        n.children.append(T.fmt(name))
        attrs = {"dpix": f"{dpi:.6f}", "dpiy": f"{dpi:.6f}"} if dpi else {"dpiType": "image"}
        n.children.append(Node("info", attrs, selfclose=True))
        n.children.append(Node("path", children=[T.fmt(path) if not path.startswith('"') else path]))
        # paths always quoted (contain / and .)
        n.children[-1].children = ['"' + path.replace("\\", "\\\\").replace('"', '\\"') + '"']
        self._levels_node().children.append(n)
        folder = None
        for f in self.tnz.child("levelSet").nodes("folder"):
            if f.attrs.get("type") == "default":
                folder = f
        if folder is not None:
            folder.child("levels").children.append(Node("level", {"id": lid}, selfclose=True))
        return lid

    def set_level_path(self, lid: str, path: str) -> None:
        for lv in self.levels():
            if lv["id"] == lid:
                lv["node"].child("path").children = ['"' + path.replace("\\", "\\\\") + '"']

    # ---------------------------------------------------------------- columns
    def columns(self) -> list[Node]:
        return self.xsheet.child("columns").nodes()

    def column_cells(self, ci: int) -> list[Cell | None]:
        col = self.columns()[ci]
        rows: dict[int, Cell] = {}
        cells = col.find("cells")
        if cells is None:
            return []
        for c in cells.nodes("cell"):
            toks = c.tokens()
            ref = c.nodes()
            if not ref or len(toks) < 4:
                continue
            row, n = int(toks[0]), int(toks[1])
            fid, inc = toks[2], int(toks[3])
            m = re.match(r"(\d+)(.*)", fid)
            num, letter = (int(m.group(1)), m.group(2)) if m else (0, fid)
            for i in range(n):
                f = f"{num + i * inc}{letter}" if not letter else f"{num}{letter}"
                rows[row + i] = Cell(ref[0].attrs["id"], f)
        if not rows:
            return []
        return [rows.get(r) for r in range(max(rows) + 1)]

    def set_column_cells(self, ci: int, cells: list[Cell | None]) -> None:
        col = self.columns()[ci]
        node = col.child("cells", before="fx")
        node.children = []
        r = 0
        while r < len(cells):
            c = cells[r]
            if c is None:
                r += 1
                continue
            n, inc = 1, 0
            plain = c.fid.isdigit()
            if plain and r + 1 < len(cells) and cells[r + 1] and cells[r + 1].level_id == c.level_id \
                    and cells[r + 1].fid.isdigit():
                inc = int(cells[r + 1].fid) - int(c.fid)
                n = 2
                while r + n < len(cells) and cells[r + n] and cells[r + n].level_id == c.level_id \
                        and cells[r + n].fid.isdigit() and int(cells[r + n].fid) == int(c.fid) + n * inc:
                    n += 1
            node.children.append(Node("cell", children=[str(r), str(n),
                                                         Node("level", {"id": c.level_id}, selfclose=True),
                                                         c.fid, str(inc)]))
            r += n
        if not node.children:
            col.remove(node)

    def column_level_ids(self, ci: int) -> set[str]:
        return {c.level_id for c in self.column_cells(ci) if c}

    def column_status(self, ci: int) -> int:
        s = self.columns()[ci].find("status")
        return int(s.tokens()[0]) if s is not None and s.tokens() else 0

    def set_column_visible(self, ci: int, visible: bool) -> None:
        st = self.column_status(ci)
        st = (st & ~3) if visible else (st | 3)
        self.columns()[ci].child("status").set_tokens(st)

    def set_column_opacity(self, ci: int, opacity: int) -> None:
        col = self.columns()[ci]
        if opacity >= 255:
            n = col.find("camerastand_opacity")
            if n is not None:
                col.remove(n)
        else:
            col.child("camerastand_opacity", before="cells").set_tokens(int(opacity))
            self.set_column_opacity_render(True)

    def _column_fx_ids(self) -> list[str]:
        ids = []
        for col in self.columns():
            fx = col.find("fx")
            inner = fx.nodes()[0] if fx is not None and fx.nodes() else None
            ids.append(inner.attrs.get("id") if inner is not None else None)
        return ids

    def add_column(self, level_id: str, name: str) -> int:
        cols = self.xsheet.child("columns", before="pegbars")
        ci = len(cols.nodes())
        cid, fid = str(self._new_id()), None
        col = Node("levelColumn", {"id": cid})
        col.children.append(Node("status", children=["0"]))
        fid = str(int(cid) + 1)
        fx = Node("Toonz_columnFx", {"id": fid}, [Node("params"), Node("ports"),
                                                  Node("dagNodePos", children=["-200", str(ci * 60)]),
                                                  Node("numberId", children=[str(ci + 1)]),
                                                  Node("fxId", children=['""']), Node("opened", children=["1"])])
        col.children.append(Node("fx", children=[fx]))
        cols.children.append(col)
        fxn = self.xsheet.child("fxnodes")
        term = fxn.find("terminal")
        if term is None:
            term = Node("terminal")
            fxn.children.insert(0, term)
        term.children.append(Node("fxnode", children=[Node("Toonz_columnFx", {"id": fid}, selfclose=True)]))
        peg = self.pegbar(f"Col{ci + 1}", create=True)
        if name:
            peg.child("name", before="isOpened").set_tokens(name)
        return ci

    def remove_column(self, ci: int) -> None:
        cols = self.xsheet.child("columns")
        col = cols.nodes()[ci]
        fx = col.find("fx")
        fid = fx.nodes()[0].attrs.get("id") if fx is not None and fx.nodes() else None
        cols.remove(col)
        fxn = self.xsheet.child("fxnodes")
        # drop terminal reference; fx that took this column as input keep a dangling port -> clear it
        for n in list(fxn.walk()):
            for ch in list(n.children):
                if isinstance(ch, Node) and ch.name == "fxnode" and ch.nodes() and ch.nodes()[0].attrs.get("id") == fid:
                    n.remove(ch)
                if isinstance(ch, Node) and ch.name == "Toonz_columnFx" and ch.attrs.get("id") == fid and ch.selfclose:
                    n.remove(ch)
        # stage objects: drop ColN, renumber later ones and parent refs
        pegs = self.xsheet.child("pegbars")
        victim = f"Col{ci + 1}"
        for p in pegs.nodes("pegbar"):
            if p.attrs.get("id") == victim:
                pegs.remove(p)
        ren = {f"Col{k + 1}": f"Col{k}" for k in range(ci + 1, ci + 200)}
        for p in pegs.nodes("pegbar"):
            if p.attrs.get("id") in ren:
                p.attrs["id"] = ren[p.attrs["id"]]
            par = p.find("parent")
            if par is not None:
                if par.attrs.get("id") == victim:
                    par.attrs["id"] = "Table"
                elif par.attrs.get("id") in ren:
                    par.attrs["id"] = ren[par.attrs["id"]]

    # ---------------------------------------------------------------- stage objects
    def pegbars(self) -> list[Node]:
        return self.xsheet.child("pegbars", before="fxnodes").nodes("pegbar")

    def pegbar(self, sid: str, create: bool = False) -> Node | None:
        for p in self.pegbars():
            if p.attrs.get("id") == sid:
                return p
        if not create:
            return None
        par = "None" if sid in ("Table",) else "Table"
        p = Node("pegbar", {"id": sid}, [
            Node("parent", {"id": par, "handle": "B", "parentHandle": "B"}, selfclose=True),
            Node("isOpened", children=["0"]), Node("center", children=["0", "0", "0", "0"]),
            Node("status", children=["0"]), Node("nodePos", children=["0", "0"])])
        pegs = self.xsheet.child("pegbars", before="fxnodes")
        gd = pegs.find("grid_dimension")
        idx = pegs.children.index(gd) if gd is not None else len(pegs.children)
        pegs.children.insert(idx, p)
        return p

    def active_camera(self) -> Node:
        for p in self.pegbars():
            if p.attrs.get("id", "").startswith("Camera") and (
                    p.attrs.get("activeboth") == "yes" or p.attrs.get("active") == "yes"):
                return p
        cam = self.pegbar("Camera1", create=True)
        cam.attrs["activeboth"] = "yes"
        return cam

    def stage_name(self, sid: str) -> str:
        p = self.pegbar(sid)
        n = p.find("name") if p is not None else None
        return n.text() if n is not None else ""

    def parent_of(self, sid: str) -> str:
        p = self.pegbar(sid)
        par = p.find("parent") if p is not None else None
        return par.attrs.get("id", "Table") if par is not None else "Table"

    def set_parent(self, sid: str, parent: str) -> None:
        p = self.pegbar(sid, create=True)
        par = p.find("parent")
        if par is None:
            par = Node("parent", {"handle": "B", "parentHandle": "B"}, selfclose=True)
            p.children.insert(0, par)
        par.attrs["id"] = parent

    def set_pivot(self, sid: str, cx: float, cy: float) -> None:
        """Rotation/scale pivot in inches (camera-centred, +y up) without moving the object:
        placement = T(offset+pos) R S T(-center), so offset = center keeps it in place."""
        p = self.pegbar(sid, create=True)
        p.child("center", before="status").set_tokens(float(cx), float(cy), -float(cx), -float(cy))

    def pivot(self, sid: str) -> tuple[float, float]:
        p = self.pegbar(sid)
        n = p.find("center") if p is not None else None
        t = n.tokens() if n is not None else []
        return (float(t[0]), float(t[1])) if len(t) >= 2 else (0.0, 0.0)

    def peg_ids(self) -> list[str]:
        return [p.attrs["id"] for p in self.pegbars() if p.attrs.get("id", "").startswith("Peg")]

    def add_peg(self, name: str, parent: str = "Table") -> str:
        used = {int(i[3:]) for i in self.peg_ids() if i[3:].isdigit()}
        k = 1
        while k in used:
            k += 1
        sid = f"Peg{k}"
        p = self.pegbar(sid, create=True)
        p.child("name", before="isOpened").set_tokens(name)
        self.set_parent(sid, parent)
        return sid

    # ---------------------------------------------------------------- keyframes
    def get_params(self, sid: str) -> dict[str, dict]:
        p = self.pegbar(sid)
        out: dict[str, dict] = {}
        if p is None:
            return out
        for tag in PARAM_TAGS:
            n = p.find(tag)
            if n is None:
                continue
            d = n.find("default")
            info = {"default": float(d.tokens()[0]) if d is not None and d.tokens() else 0.0, "keys": []}
            ks = n.find("keyframes")
            for k in (ks.nodes() if ks is not None else []):
                t = k.tokens()
                if len(t) < 2:
                    continue
                key = {"frame": int(float(t[0])), "interp": CODE_INTERP.get(k.name, k.name)}
                try:
                    key["value"] = float(t[1])
                except ValueError:
                    key["expression"] = unquote(t[1])
                if k.name in ("Ep", "E") and len(t) > 2:
                    key["ease_out"] = float(t[2])
                prev = k.find("prev")
                if prev is not None and len(prev.tokens()) > 1 and k.name:
                    key["ease_in"] = float(prev.tokens()[1])
                out_params = info["keys"]
                out_params.append(key)
            out[tag] = info
        return out

    def set_param(self, sid: str, tag: str, keys: list[dict], default: float | None = None) -> None:
        """keys: [{frame (0-based), value, interp, ease_in?, ease_out?}] sorted by frame."""
        p = self.pegbar(sid, create=True)
        old = p.find(tag)
        if not keys and default is None:
            if old is not None:
                p.remove(old)
            return
        keys = sorted(keys, key=lambda k: k["frame"])
        if default is None:
            default = keys[0]["value"] if keys else (1.0 if tag in ("sx", "sy", "sc") else 0.0)
        n = Node(tag, children=[Node("default", children=[T.fmt(float(default))])])
        if keys:
            kn = Node("keyframes")
            prev = None
            for k in keys:
                code = INTERP_CODE.get(k.get("interp", "linear"), "L")
                kid = Node(code)
                if prev is not None:
                    pc = INTERP_CODE.get(prev.get("interp", "linear"), "L")
                    if pc == "L":
                        kid.children.append(Node("prev", children=[T.fmt(float(k["value"]))]))
                    elif pc in ("Ep", "E"):
                        kid.children.append(Node("prev", children=[T.fmt(float(k["value"])),
                                                                   T.fmt(float(k.get("ease_in", 33.0 if pc == "Ep" else 3)))]))
                    elif pc == "S":
                        kid.children.append(Node("prev", children=[T.fmt(float(k["value"])), "-3", "0"]))
                body = [str(int(k["frame"])), T.fmt(float(k["value"]))]
                if code in ("Ep", "E"):
                    body.append(T.fmt(float(k.get("ease_out", 33.0 if code == "Ep" else 3))))
                elif code == "S":
                    body += ["3", "0"]
                kid.children += body
                kn.children.append(kid)
                prev = k
            n.children.append(kn)
        # keep tag order stable: before nodePos/camera
        if old is not None:
            p.children[p.children.index(old)] = n
        else:
            idx = len(p.children)
            for i, c in enumerate(p.children):
                if isinstance(c, Node) and c.name in ("cycle", "plasticSD", "nodePos", "camera"):
                    idx = i
                    break
            p.children.insert(idx, n)

    # ---------------------------------------------------------------- integrity
    def frame_count(self) -> int:
        m = 0
        for ci in range(len(self.columns())):
            m = max(m, len(self.column_cells(ci)))
        return m

    def _sync_framecount(self) -> None:
        self.tnz.attrs["framecount"] = str(max(self.frame_count(), 1))

    def verify(self, file_exists=None) -> list[str]:
        """Structural double-check. Returns a list of problems (empty = OK)."""
        problems = []
        lids = {lv["id"] for lv in self.levels()}
        for ci, col in enumerate(self.columns()):
            for c in self.column_cells(ci):
                if c and c.level_id not in lids:
                    problems.append(f"column {ci + 1}: cell references missing level id {c.level_id}")
                    break
            if col.name == "levelColumn" and col.find("fx") is None:
                problems.append(f"column {ci + 1}: no column fx")
            if self.pegbar(f"Col{ci + 1}") is None:
                problems.append(f"column {ci + 1}: no stage object Col{ci + 1}")
        term = self.xsheet.path("fxnodes", "terminal")
        termids = {n.nodes()[0].attrs.get("id") for n in (term.nodes("fxnode") if term is not None else []) if n.nodes()}
        for ci, fid in enumerate(self._column_fx_ids()):
            if fid and fid not in termids:
                # not fatal: column may feed another fx; check it is referenced somewhere
                refs = [n for n in self.xsheet.child("fxnodes").walk() if n.attrs.get("id") == fid]
                if not refs:
                    problems.append(f"column {ci + 1}: fx not connected (renders nothing)")
        if self.xsheet.path("fxnodes", "output") is None:
            problems.append("no output fx")
        if file_exists:
            for lv in self.levels():
                if lv["path"] and not file_exists(lv["path"]):
                    problems.append(f"level '{lv['name']}' file missing: {lv['path']}")
        for p in self.pegbars():
            par = p.find("parent")
            pid = par.attrs.get("id") if par is not None else None
            if pid and pid not in ("None", "Table") and self.pegbar(pid) is None and not pid.startswith("Col"):
                problems.append(f"{p.attrs.get('id')}: parent {pid} missing")
        return problems

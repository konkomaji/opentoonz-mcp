"""OpenToonz MCP server.

Design
------
* The .tnz scene file is the source of truth. Every tool re-reads it from disk, edits only
  the nodes it needs (otmcp.scene.SceneDoc over a lossless tree) and writes it back, so
  edits made in the OpenToonz GUI (retiming, keyframes, fx, extra levels...) survive.
* Vector layers: shapes -> hand-drawn SVG -> OpenToonz converts to native .pli (headless
  .toonzscript) -> pen-pressure taper patched into the .pli. Fully editable in the GUI.
* Context + memory: every call detects outside changes (file hashes), journals what it did,
  snapshots before writing (undo), and re-verifies the written scene (double check).

Conventions exposed to the model: pixels, origin top-left, +y down; frames 1-based;
scale 1.0 = 100%; rotation degrees counter-clockwise.
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime as _dt
import functools
import glob
import inspect
import json
import math
import os
import random
import re
import shutil
import subprocess
import time
import uuid
import zlib
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Image as McpImage

from . import anatomy, assets, config, curve, gait, learn, physics, pli, tnz, vec
from . import draw as drawlib
from . import fx as fxlib
from . import style as styles
from .scene import Cell, SceneDoc, file_hash

OT_HOME = config.opentoonz_home()
OT_STUFF = config.opentoonz_stuff()
PROJECT = OT_STUFF / "sandbox"
WORK = config.work_dir()
RESEARCH = Path(os.environ.get("OTMCP_RESEARCH", Path(__file__).resolve().parent.parent / "research"))
STATE_DIR, RENDER_DIR, SNAP_DIR = WORK / "state", WORK / "renders", WORK / "snapshots"
JOURNAL_DIR, NOTES_DIR, SVG_DIR, TMP = WORK / "journal", WORK / "notes", WORK / "svg", WORK / "tmp"
MAX_SNAPSHOTS = 30
LOCK_DIR = WORK / "locks"
INTERPS = ("ease", "linear", "constant", "speed", "exp", "ease_frames")
BOIL_BASE = 1000  # boil variant drawing ids: d + 1000*v

mcp = FastMCP("opentoonz", instructions=(
    "Drive OpenToonz to make 2D animation. ALWAYS call scene_context(scene) first in a session "
    "and after the user says they edited in OpenToonz. Read style_guide() for studio timing rules. "
    "Vector layers are real OpenToonz vector levels (editable in the GUI). Check tool results for "
    "'warnings' and 'verify' problems and fix them before rendering. Apply the 'lessons' returned by "
    "scene_context. After each render: inspect contact_sheet, record insights with learn_lesson, ask the "
    "user for feedback (feedback tool) and tune_style when a default should change. Workflow like a studio: "
    "notes/story (add_note) -> layout (layers, pegs, camera) -> key drawings -> inbetween -> timing -> "
    "camera -> validate_scene -> render."))

KEY_TAGS = {"x": "x", "y": "y", "z": "z", "rotation": "rot", "scale": "sc", "scale_x": "sx",
            "scale_y": "sy", "shear_x": "shx", "shear_y": "shy", "stack_order": "so"}
TAG_KEYS = {v: k for k, v in KEY_TAGS.items()}


# ====================================================================== helpers: paths/state
def _safe(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name)).strip("_")
    if not s:
        raise ValueError("empty name")
    return s


def _scene_file(name: str) -> Path:
    return PROJECT / "scenes" / f"{name}.tnz"


def _state_path(name: str) -> Path:
    return STATE_DIR / f"{name}.json"


def _list() -> list[str]:
    return sorted(p.stem for p in STATE_DIR.glob("*.json")) if STATE_DIR.exists() else []


def _load_state(scene: str) -> dict:
    name = _safe(scene)
    p = _state_path(name)
    if not p.exists():
        if _scene_file(name).exists():
            return _adopt(name)
        raise ValueError(f"scene '{scene}' not found. Existing: {_list()}. Use create_scene, or "
                         f"import_scene for a .tnz made in OpenToonz.")
    return json.loads(p.read_text(encoding="utf-8"))


def _save_state(st: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _state_path(st["name"]).with_name(f"{st['name']}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    os.replace(tmp, _state_path(st["name"]))


def _adopt(name: str) -> dict:
    """Create MCP state for a scene that only exists as a .tnz (made in OpenToonz)."""
    doc = SceneDoc.load(_scene_file(name))
    st = {"name": name, "style": "anime", "frames": max(doc.frame_count(), doc.output_range() or 0, 1),
          "layers": {}, "created": _now(), "tnz_hash": None, "cells_seen": {}}
    _save_state(st)
    _journal(name, "import_scene", {}, {"layers_in_file": len(doc.columns())})
    return st


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _level_path(st: dict, layer: str, mode: str) -> str:
    return f"+drawings/{st['name']}/{layer}.pli" if mode in ("vector", "traced") \
        else f"+drawings/{st['name']}/{layer}..png"


def _decode(coded: str) -> Path:
    """'+drawings/x' -> sandbox path. Absolute paths unchanged."""
    if coded.startswith("+"):
        head, _, rest = coded[1:].partition("/")
        return PROJECT / head / rest.replace("/", os.sep)
    if coded.startswith("$scenefolder"):
        return PROJECT / "scenes" / coded[len("$scenefolder"):].lstrip("\\/")
    return Path(coded)


def _level_file_exists(coded: str) -> bool:
    p = _decode(coded)
    if ".." in p.name:  # sequence: name..ext
        stem, ext = p.name.split("..", 1)
        return any(p.parent.glob(f"{stem}.*.{ext}"))
    return p.exists()


def _pli_file(st: dict, layer: str) -> Path:
    return PROJECT / "drawings" / st["name"] / f"{layer}.pli"


def _png_file(st: dict, layer: str, did: int) -> Path:
    return PROJECT / "drawings" / st["name"] / f"{layer}.{did:04d}.png"


def _svg_file(st: dict, layer: str, did: int) -> Path:
    return SVG_DIR / st["name"] / layer / f"{layer}.{did:04d}.svg"


# ====================================================================== journal / memory / snapshots
def _journal(scene: str, tool: str, args: dict, result: Any) -> None:
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    slim = {k: (v if len(json.dumps(v, default=str)) < 400 else f"<{len(json.dumps(v, default=str))} chars>")
            for k, v in (args or {}).items()}
    res = result if len(json.dumps(result, default=str)) < 600 else str(result)[:600]
    with open(JOURNAL_DIR / f"{scene}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": _now(), "tool": tool, "args": slim, "result": res}, default=str) + "\n")


def _journal_tail(scene: str, n: int = 15) -> list[dict]:
    p = JOURNAL_DIR / f"{scene}.jsonl"
    if not p.exists():
        return []
    lines = p.read_text(encoding="utf-8").splitlines()[-n:]
    return [json.loads(x) for x in lines if x.strip()]


_CALL_SNAPS: contextvars.ContextVar = contextvars.ContextVar("otmcp_call_snaps", default=None)


def _snapshot(name: str, label: str) -> Path:
    """Full pre-operation snapshot: scene, MCP state, SVG sources and drawing files of the scene."""
    # zero-padded ns sequence: lexical order == creation order even for calls in the same second
    d = SNAP_DIR / name / f"{time.time_ns():020d}_{_safe(label)}"
    d.mkdir(parents=True, exist_ok=True)
    sf = _scene_file(name)
    if sf.exists():
        shutil.copy2(sf, d / "scene.tnz")
    if _state_path(name).exists():
        shutil.copy2(_state_path(name), d / "state.json")
    for src, dst in ((SVG_DIR / name, d / "svg"), (PROJECT / "drawings" / name, d / "drawings")):
        if src.exists():
            shutil.copytree(src, dst)
    (d / "complete").write_text("1", encoding="utf-8")
    snaps = sorted(x for x in (SNAP_DIR / name).iterdir() if x.is_dir())
    for old in snaps[:-MAX_SNAPSHOTS]:
        shutil.rmtree(old, ignore_errors=True)
    lst = _CALL_SNAPS.get()
    if lst is not None:
        lst.append(d)
    return d


def _restore(name: str, d: Path) -> None:
    """Make scene, state, SVG sources and drawings exactly what they were in snapshot d."""
    if (d / "scene.tnz").exists():
        shutil.copy2(d / "scene.tnz", _scene_file(name))
    else:
        _scene_file(name).unlink(missing_ok=True)
    if (d / "state.json").exists():
        shutil.copy2(d / "state.json", _state_path(name))
    else:
        _state_path(name).unlink(missing_ok=True)
    for live, saved in ((SVG_DIR / name, d / "svg"), (PROJECT / "drawings" / name, d / "drawings")):
        shutil.rmtree(live, ignore_errors=True)
        if saved.exists():
            shutil.copytree(saved, live)


def _try_lock(fh) -> bool:
    try:
        fh.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(fh) -> None:
    fh.seek(0)
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@contextlib.contextmanager
def _scene_lock(name: str, timeout: float = 60.0):
    """Cross-process exclusive lock per scene (msvcrt on Windows, flock elsewhere)."""
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_DIR / f"{name}.lock", "a+b")
    t0 = time.time()
    while not _try_lock(fh):
        if time.time() - t0 > timeout:
            fh.close()
            raise RuntimeError(f"scene '{name}' is busy (another MCP call holds the lock)")
        time.sleep(0.05)
    try:
        yield
    finally:
        _unlock(fh)
        fh.close()


def _opentoonz_running() -> bool:
    try:
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq OpenToonz.exe", "/NH"], capture_output=True, text=True,
                           timeout=10)
        return "OpenToonz.exe" in r.stdout
    except Exception:
        return False


# ====================================================================== context / sync
class Ctx:
    """Per-call context: fresh scene doc from disk, change detection, warnings, save+verify."""

    def __init__(self, scene: str, tool: str, args: dict, mutate: bool = True):
        self.st = _load_state(scene)
        self.name = self.st["name"]
        self.tool, self.args, self.mutate = tool, args, mutate
        self.warnings: list[str] = []
        self.sf = _scene_file(self.name)
        if not self.sf.exists():
            raise ValueError(f"scene file missing: {self.sf}")
        self.doc = SceneDoc.load(self.sf)
        self._detect_external()
        if mutate:
            _snapshot(self.name, tool)  # before ANY side effect of the operation (files included)

    # ---- context awareness
    def _detect_external(self) -> None:
        h = file_hash(self.sf)
        if self.st.get("tnz_hash") and h != self.st["tnz_hash"]:
            self.warnings.append("scene .tnz was changed outside the MCP (OpenToonz GUI?) since the last MCP "
                                 "write; MCP is working from the CURRENT file, your changes are kept.")
            _journal(self.name, "external_change", {}, {"tnz": "modified"})
        # timing changed in GUI -> stop auto-exposure for that layer
        for lname, L in self.st["layers"].items():
            ci = self.col_index(lname)
            if ci is None:
                if L.get("column_seen"):
                    self.warnings.append(f"layer '{lname}' column was deleted in OpenToonz")
                continue
            sig = self._cells_sig(ci)
            if L.get("cells_sig") and sig != L["cells_sig"] and L.get("timing", "auto") == "auto":
                L["timing"] = "manual"
                self.warnings.append(f"layer '{lname}': timing was edited in OpenToonz -> switched to manual "
                                     f"timing (MCP will not re-time it; use set_exposure to change).")
            if L.get("mode") in ("vector", "traced"):
                ph = file_hash(_pli_file(self.st, lname))
                if L.get("pli_hash") and ph and ph != L["pli_hash"]:
                    if not L.get("gui_edited"):
                        self.warnings.append(f"layer '{lname}': drawings were edited in OpenToonz (vector .pli). "
                                             f"MCP will not overwrite them unless force=true.")
                    L["gui_edited"] = True

    def _cells_sig(self, ci: int) -> str:
        # frame ids only: OpenToonz renumbers persist ids (level ids) on every save
        return ",".join(c.fid if c else "-" for c in self.doc.column_cells(ci))

    # ---- mapping layers <-> columns
    def col_index(self, layer: str) -> int | None:
        L = self.st["layers"].get(layer)
        if L:
            lv = self.doc.level_by_path(L["level_path"])
            if lv:
                for ci in range(len(self.doc.columns())):
                    if lv["id"] in self.doc.column_level_ids(ci):
                        return ci
        for ci in range(len(self.doc.columns())):
            if self.doc.stage_name(f"Col{ci + 1}") == layer:
                return ci
        # an empty column renamed in the GUI: fall back to the remembered column if it is still empty
        hint = L.get("col_hint") if L else None
        if hint is not None and hint < len(self.doc.columns()) and not self.doc.column_cells(hint):
            others = [n for n in self.st["layers"] if n != layer and self.st["layers"][n].get("col_hint") == hint]
            if not others:
                return hint
        return None

    def layer(self, name: str) -> dict:
        n = _safe(name)
        if n not in self.st["layers"]:
            raise ValueError(f"layer '{name}' not found. Layers: {list(self.st['layers'])}")
        return self.st["layers"][n]

    def stage_id(self, target: str) -> str:
        t = str(target)
        if t.lower() in ("camera", "cam"):
            return self.doc.active_camera().attrs["id"]
        if t.lower() == "table":
            return "Table"
        if re.fullmatch(r"(Col|Peg|Camera)\d+", t):
            if t.startswith("Col") and not 1 <= int(t[3:]) <= len(self.doc.columns()):
                raise ValueError(f"{t} does not exist (scene has {len(self.doc.columns())} columns)")
            if not t.startswith("Col") and self.doc.pegbar(t) is None:
                raise ValueError(f"{t} does not exist")
            return t
        for pid in self.doc.peg_ids():
            if self.doc.stage_name(pid) == _safe(t):
                return pid
        ci = self.col_index(_safe(t))
        if ci is not None:
            return f"Col{ci + 1}"
        raise ValueError(f"target '{target}' not found (layer, peg, 'camera' or 'table')")

    def level_id(self, layer: str) -> str:
        L = self.layer(layer)
        lv = self.doc.level_by_path(L["level_path"])
        if lv is None:
            return self.doc.add_level(layer, L["level_path"],
                                      None if L["mode"] in ("vector", "traced") else self.doc.dpi())
        return lv["id"]

    # ---- commit
    def commit(self, result: dict) -> dict:
        if self.mutate:
            self.doc.set_output_range(self.st["frames"])
            text = self.doc.text()
            tmp = self.sf.with_name(f"{self.name}.{uuid.uuid4().hex}.tnz.tmp")
            tmp.write_text(text, encoding="utf-8", errors="surrogateescape")
            # double check: parse what we wrote before replacing the scene
            check = SceneDoc.load(tmp)
            problems = check.verify(_level_file_exists)
            fatal = [p for p in problems if "file missing" not in p]
            if fatal:
                tmp.unlink(missing_ok=True)
                raise RuntimeError(f"verification failed, scene NOT written: {fatal}")
            os.replace(tmp, self.sf)
            self.st["tnz_hash"] = file_hash(self.sf)
            for lname, L in self.st["layers"].items():
                ci = self.col_index(lname)
                if ci is not None:
                    L["cells_sig"] = self._cells_sig(ci)
                    L["column_seen"] = True
                    L["col_hint"] = ci
            _save_state(self.st)
            pending = {f"+drawings/{self.name}/{n}.pli" for n, L in self.st["layers"].items()
                       if L["mode"] in ("vector", "traced")}
            soft = [p for p in problems if "file missing" in p and not any(x in p for x in pending)]
            result["verify"] = soft if soft else "ok"
            if any(L.get("dirty") for L in self.st["layers"].values()):
                result["pending_compile"] = [n for n, L in self.st["layers"].items() if L.get("dirty")]
        elif self.warnings:
            # still record what we learned (manual timing, gui edits)
            self.st["tnz_hash"] = file_hash(self.sf)
            _save_state(self.st)
        if self.warnings:
            result["warnings"] = self.warnings
            learn.record_issues("warning", [w for w in self.warnings if "outside the MCP" not in w
                                            and "GUI is running" not in w], self.name)
        _journal(self.name, self.tool, self.args, result)
        return result


def tool(fn):
    """Register an MCP tool: per-scene lock, automatic rollback of a failed operation, error journal."""
    sig = inspect.signature(fn)

    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            bound = sig.bind_partial(*a, **k).arguments
        except TypeError:
            bound = {}
        raw = bound.get("scene") or (bound.get("name") if fn.__name__ == "create_scene" else None)
        sname = _safe(raw) if isinstance(raw, str) and re.search(r"\w", raw) else None
        snaps: list[Path] = []
        token = _CALL_SNAPS.set(snaps)
        try:
            with (_scene_lock(sname) if sname else contextlib.nullcontext()):
                try:
                    return fn(*a, **k)
                except Exception:
                    if snaps:  # roll back every side effect of this failed call
                        _restore(sname, snaps[0])
                        for d in snaps:
                            shutil.rmtree(d, ignore_errors=True)
                    raise
        except Exception as e:
            if sname and _state_path(sname).exists():
                _journal(sname, fn.__name__ + ":error", {kk: str(v)[:200] for kk, v in k.items()}, str(e))
            if not isinstance(e, ValueError):  # input mistakes are not lessons; real failures are
                learn.add("error", f"{fn.__name__}: {e}", sname, tags=[fn.__name__], advice=learn.advice_for(str(e)))
            raise RuntimeError(f"{type(e).__name__}: {e}") from e
        finally:
            _CALL_SNAPS.reset(token)
    return mcp.tool()(wrapper)


# ====================================================================== exposure / drawings
def _auto_cells(L: dict, frames: int) -> list[int]:
    ids = sorted(int(k) for k in L["drawings"])
    cells, cur = [], 0
    for row in range(1, frames + 1):
        for d in ids:
            if d <= row:
                cur = d
        cells.append(cur)
    return _boil_cycle(cells) if L.get("boil") else cells


def _boil_cycle(cells: list[int]) -> list[int]:
    """Held runs cycle the 3 boil variants on twos: d, d+1000, d+2000, d, ..."""
    out, run = [], 0
    for i, d in enumerate(cells):
        run = run + 1 if i and cells[i - 1] == d else 0
        v = (run // 2) % 3
        out.append(d + BOIL_BASE * v if d and v else d)
    return out


def _check_drawing_id(frame) -> None:
    if not isinstance(frame, int) or isinstance(frame, bool) or not 1 <= frame < BOIL_BASE:
        raise ValueError(f"drawing/frame number must be an integer 1..{BOIL_BASE - 1}, got {frame!r}")


def _apply_cells(c: Ctx, layer: str, cells: list[int]) -> None:
    ci = c.col_index(layer)
    lid = c.level_id(layer)
    if ci is None:
        ci = c.doc.add_column(lid, layer)
    c.doc.set_column_cells(ci, [Cell(lid, str(d)) if d else None for d in cells])


def _retime(c: Ctx, layer: str) -> str | None:
    L = c.layer(layer)
    if L.get("timing", "auto") == "auto":
        _apply_cells(c, layer, _auto_cells(L, c.st["frames"]))
        return None
    return (f"layer '{layer}' has manual timing; new drawings are not exposed automatically. "
            f"Use set_exposure to place them.")


def _write_drawing(c: Ctx, layer: str, did: int, shapes: list[dict], force: bool = False) -> None:
    L = c.layer(layer)
    if L["mode"] in ("vector", "traced") and L.get("gui_edited") and not force:
        raise RuntimeError(f"layer '{layer}' drawings were edited in OpenToonz; redrawing would discard those "
                           f"edits. Pass force=true to overwrite, or draw on a new layer.")
    W, H = c.doc.camera_res()
    stored = shapes
    shapes = assets.expand_parts(shapes)
    has_img = any(sh.get("type") == "image" for sh in shapes)
    if L["mode"] == "traced":
        if not (len(shapes) == 1 and has_img):
            raise ValueError("traced layers take exactly one image per drawing (use import_image)")
        src = _svg_file(c.st, layer, did).with_suffix(".png")
        src.parent.mkdir(parents=True, exist_ok=True)
        im = shapes[0]
        assets.place_image(im["src"], (W, H), tuple(im["box"]) if im.get("box") else None, im.get("fit", "contain"),
                           background="#ffffff").convert("RGB").save(src, dpi=(c.doc.dpi(), c.doc.dpi()))
        L["drawings"][str(did)] = stored
        L["dirty"] = True
        return
    if has_img and L["mode"] == "vector":
        raise ValueError("image shapes need a raster layer, or a traced layer to become vectors (import_image)")
    variants = [0, 1, 2] if L.get("boil") else [0]
    for v in variants:
        seed = zlib.crc32(f"{c.name}/{layer}/{did}/{v}".encode()) if L.get("style") != "clean" else 0
        sty = L.get("style") or c.st["style"]
        sh = shapes
        if v:
            sh = [dict(s, wobble=max(1.0, float(s.get("wobble", styles.get(sty)["wobble"])))) for s in shapes]
        els = vec.build_elements(sh, H, sty, seed)
        tid = did + BOIL_BASE * v
        if L["mode"] == "vector":
            f = _svg_file(c.st, layer, tid)
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(vec.to_svg(els, W, H, c.doc.dpi()), encoding="utf-8")
            prof = vec.ink_profiles(els)
            side = f.with_suffix(".prof.json")
            if any(prof):
                side.write_text(json.dumps(prof), encoding="utf-8")
            else:
                side.unlink(missing_ok=True)
        else:
            f = _png_file(c.st, layer, tid)
            f.parent.mkdir(parents=True, exist_ok=True)
            _raster_drawing(sh, W, H, sty, seed).save(f)
    L["drawings"][str(did)] = stored
    L["dirty"] = L["mode"] in ("vector", "traced")  # raster PNGs are final; only vector levels compile


def _raster_drawing(shapes: list[dict], W: int, H: int, sty: str, seed: int):
    """Paint shapes in order; image shapes are composited where they occur in the stack."""
    from PIL import Image
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    run: list[dict] = []

    def flush():
        if run:
            out.alpha_composite(vec.to_png(vec.build_elements(run, H, sty, seed), W, H))
            run.clear()
    for sh in shapes:
        if sh.get("type") == "image":
            flush()
            out.alpha_composite(assets.place_image(sh["src"], (W, H), tuple(sh["box"]) if sh.get("box") else None,
                                                   sh.get("fit", "contain")))
        else:
            run.append(sh)
    flush()
    return out


# ====================================================================== compile (vector) / render
def _run_script(lines: list[str], tag: str) -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    sp = TMP / f"{tag}_{int(time.time() * 1000)}.toonzscript"
    sp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run([str(config.exe("OpenToonz")), str(sp)], cwd=OT_HOME, capture_output=True, timeout=600)
    sp.unlink(missing_ok=True)


def _compile(st: dict, force: bool = False, only: list[str] | None = None) -> dict:
    todo, skipped = [], []
    for lname, L in st["layers"].items():
        if L["mode"] not in ("vector", "traced") or (only and lname not in only):
            continue
        pf = _pli_file(st, lname)
        if not (L.get("dirty") or not pf.exists() or force):
            continue
        ph = file_hash(pf)
        if L.get("pli_hash") and ph and ph != L["pli_hash"]:
            L["gui_edited"] = True  # changed on disk since our last compile (OpenToonz GUI edit)
        if L.get("gui_edited") and not force:
            skipped.append(lname)
            continue
        valid = {int(d) + BOIL_BASE * v for d in L["drawings"] for v in ((0, 1, 2) if L.get("boil") else (0,))}
        for f in (SVG_DIR / st["name"] / lname).glob(f"{lname}.*"):
            m = re.match(rf"{re.escape(lname)}\.(\d+)\.(svg|prof\.json|png)$", f.name)
            if m and int(m.group(1)) not in valid:
                f.unlink()
        ext = "png" if L["mode"] == "traced" else "svg"
        svgs = sorted((SVG_DIR / st["name"] / lname).glob(f"{lname}.*.{ext}"))
        if svgs:
            todo.append((lname, L, pf))
    if not todo:
        _save_state(st)
        return {"compiled": [], "skipped_gui_edited": skipped}
    if force and any(L.get("gui_edited") for _, L, _ in todo):
        _snapshot(st["name"], "compile_force")
    # build into a private temp folder, then swap in atomically; existing .pli files are never deleted first
    out_dir = TMP / f"compile_{uuid.uuid4().hex}"
    out_dir.mkdir(parents=True)
    lines = []
    for i, (lname, L, _pf) in enumerate(todo):
        dst = (out_dir / f"{lname}.pli").as_posix()
        if L["mode"] == "traced":
            tr = L.get("trace", {})
            src = (SVG_DIR / st["name"] / lname / f"{lname}..png").as_posix()
            kind = "CenterlineVectorizer" if tr.get("kind") == "centerline" else "OutlineVectorizer"
            lines += [f"var s{i} = new Level();", f"s{i}.load({json.dumps(src)});", f"var v{i} = new {kind}();"]
            for prop, val in _trace_props(tr).items():
                lines.append(f"v{i}.{prop} = {json.dumps(val)};")
            lines += [f"var l{i} = v{i}.vectorize(s{i});", f"l{i}.save({json.dumps(dst)});"]
        else:
            src = (SVG_DIR / st["name"] / lname / f"{lname}..svg").as_posix()
            lines += [f"var l{i} = new Level();", f"l{i}.load({json.dumps(src)});", f"l{i}.save({json.dumps(dst)});"]
    t0 = time.time()
    _run_script(lines, "compile")
    done, failed = [], []
    for lname, L, pf in todo:
        built = out_dir / f"{lname}.pli"
        if not built.exists():
            failed.append(lname)
            continue
        pf.parent.mkdir(parents=True, exist_ok=True)
        os.replace(built, pf)
        if L["mode"] == "traced":  # faithful trace: no stylised pressure
            _, strokes = pli.read(str(pf))
            L["dirty"], L["pli_hash"], L["gui_edited"] = False, file_hash(pf), False
            done.append({"layer": lname, "strokes": len(strokes), "frames": len({k.frame for k in strokes}),
                         "traced": L.get("trace", {}).get("kind", "outline")})
            continue
        s = styles.get(L.get("style") or st["style"])
        profiles = {}
        for side in (SVG_DIR / st["name"] / lname).glob(f"{lname}.*.prof.json"):
            m = re.match(rf"{re.escape(lname)}\.(\d+)\.prof\.json$", side.name)
            if m:
                profiles[int(m.group(1))] = json.loads(side.read_text(encoding="utf-8"))
        rep: dict = {}
        n = pli.pressure_patch(str(pf), s["taper_start"], s["taper_end"], s["min_ratio"], s["pressure_var"],
                               s["loop_var"], seed=zlib.crc32(lname.encode()) & 0xFFFF,
                               profiles=profiles or None, report=rep)
        if rep.get("profile_mismatch_frames"):
            learn.add("warning", f"brush pressure skipped on layer '{lname}' frames "
                                 f"{rep['profile_mismatch_frames']}: stroke count differs after OpenToonz import")
        _, strokes = pli.read(str(pf))
        frames_in_pli = sorted({sk.frame for sk in strokes})
        L["dirty"] = False
        L["pli_hash"] = file_hash(pf)
        L["gui_edited"] = False
        item = {"layer": lname, "strokes": len(strokes), "pressure_strokes": n, "frames": len(frames_in_pli)}
        if rep.get("profile_mismatch_frames"):
            item["brush_pressure_skipped_frames"] = rep["profile_mismatch_frames"]
        done.append(item)
    shutil.rmtree(out_dir, ignore_errors=True)
    _save_state(st)
    res = {"compiled": done, "seconds": round(time.time() - t0, 1)}
    if failed:
        res["failed"] = failed
    if skipped:
        res["skipped_gui_edited"] = skipped
    return res


def _trace_props(tr: dict) -> dict:
    """OpenToonz script vectorizer properties (scriptbinding_*_vectorizer.h)."""
    if tr.get("kind") == "centerline":
        keys = {"threshold": 8, "accuracy": 9, "despeckling": 5, "maxThickness": 200, "preservePaintedAreas": True}
    else:
        keys = {"accuracy": 8, "despeckling": 4, "maxColors": 12, "preservePaintedAreas": True,
                "transparentColor": "#ffffff", "toneThreshold": 128}
    return {k: tr.get(k, v) for k, v in keys.items()}


def _tcomposer(scene_file: Path, out_pattern: Path, frame_range: tuple[int, int] | None = None,
               shrink: int = 1, timeout: float | None = None) -> str:
    out_pattern.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(config.exe("tcomposer")), str(scene_file), "-o", str(out_pattern), "-nthreads", "all"]
    if frame_range:
        cmd += ["-range", str(frame_range[0]), str(frame_range[1])]
    if shrink > 1:
        cmd += ["-shrink", str(shrink)]
    TMP.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TEMP=str(TMP), TMP=str(TMP))
    n = (frame_range[1] - frame_range[0] + 1) if frame_range else 500
    try:
        r = subprocess.run(cmd, cwd=OT_HOME, capture_output=True, text=True, errors="replace", env=env,
                           timeout=timeout or 120 + 6 * n)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("tcomposer timed out (frame range outside the scene, or the scene is too heavy)") from exc
    log = (r.stdout or "") + (r.stderr or "")
    if "There were problems loading the scene" in log or "Some files may be missing" in log:
        raise RuntimeError(f"tcomposer reported missing files:\n{log[-1500:]}")
    if "completed" not in log:
        raise RuntimeError(f"tcomposer failed (exit {r.returncode}):\n{log[-2000:]}")
    return log


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    hits = glob.glob(os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\*FFmpeg*\**\ffmpeg.exe"),
                     recursive=True)
    if hits:
        return hits[0]
    raise RuntimeError("ffmpeg not found; render format='png' instead")


def _bg(c: Ctx) -> tuple[int, int, int, int]:
    b = c.doc.bg()
    return (b[0], b[1], b[2], 255)


def _flat(img_path: Path, bg):
    from PIL import Image
    im = Image.open(img_path).convert("RGBA")
    base = Image.new("RGBA", im.size, bg)
    base.alpha_composite(im)
    return base.convert("RGB")


def _check_frames(files: list[Path], fps: float, bg, start: int = 1) -> list[str]:
    """Double-check renders: blank frames, photosensitive flashing (>3 flashes/s)."""
    from PIL import ImageStat
    issues, lum, blank = [], [], []
    for f in files:
        im = _flat(f, bg).resize((96, 54))
        st = ImageStat.Stat(im.convert("L"))
        lum.append(st.mean[0])
        if st.stddev[0] < 0.5:
            blank.append(f.name)
    if blank and len(blank) == len(files):
        issues.append("ALL frames are a flat colour - nothing visible rendered")
    elif blank:
        issues.append(f"{len(blank)} flat/blank frames, e.g. {blank[:3]}")
    # motion review: pops (one frame jumps far more than its neighbours) and long dead holds
    if len(files) >= 4:
        small = [_flat(f, bg).resize((160, 90)).convert("L") for f in files]
        from PIL import ImageChops
        diffs = [ImageStat.Stat(ImageChops.difference(a, b)).mean[0] for a, b in zip(small, small[1:])]
        moving = sorted(d for d in diffs if d > 0.05)
        if moving:
            med = moving[len(moving) // 2]
            for i, d in enumerate(diffs):
                if d > max(6 * med, 4.0):
                    issues.append(f"pop at frame {start + i + 1}: image jumps {d / max(med, 0.01):.0f}x more than typical")
                    break
        still, longest = 0, 0
        for d in diffs:
            still = still + 1 if d < 0.02 else 0
            longest = max(longest, still)
        if longest + 1 > 2 * fps and len(files) > 2 * fps:
            issues.append(f"dead hold of {longest + 1} frames (>{2 * fps:.0f}); add a moving hold, blink or camera drift")
    flashes = [i for i in range(1, len(lum)) if abs(lum[i] - lum[i - 1]) > 0.2 * 255]
    win = max(1, int(fps))
    for i in range(len(lum)):
        if sum(1 for k in flashes if i <= k < i + win) > 3:
            issues.append("photosensitivity: more than 3 large luminance flashes within 1 second")
            break
    return issues


# ====================================================================== tools: context & memory
@tool
def opentoonz_info() -> dict:
    """Install paths, version, scenes, and whether the OpenToonz GUI is running."""
    problems = []
    tc = config.exe("tcomposer")
    if not tc.exists():
        problems.append(f"tcomposer not found at {tc}: set OPENTOONZ_HOME to the OpenToonz install folder")
    if not PROJECT.exists():
        problems.append(f"sandbox project not found at {PROJECT}: set OPENTOONZ_STUFF to OpenToonz's stuff folder")
    try:
        ff = _ffmpeg()
    except RuntimeError:
        ff = None
        problems.append("ffmpeg not found: mp4/gif renders unavailable (png sequences still work)")
    ver = "unknown"
    if tc.exists():
        r = subprocess.run([str(tc), "-version"], cwd=OT_HOME, capture_output=True, text=True)
        ver = (r.stdout + r.stderr).strip() or "unknown"
    return {"version": ver, "install": str(OT_HOME), "stuff": str(OT_STUFF), "project": str(PROJECT),
            "work": str(WORK), "ffmpeg": ff, "health": problems or "ok", "scenes": _list(),
            "gui_running": _opentoonz_running(), "styles": list(styles.STYLES),
            "conventions": "pixels, +y down, frames 1-based, scale 1.0=100%"}


@tool
def scene_context(scene: str) -> dict:
    """CALL FIRST. Full current state of a scene from disk: settings, layers, timing, keyframes,
    outside (GUI) changes detected, notes/memory, recent journal, warnings."""
    c = Ctx(scene, "scene_context", {"scene": scene}, mutate=False)
    W, H = c.doc.camera_res()
    dpi = c.doc.dpi()
    layers = []
    known_cols = set()
    for lname, L in c.st["layers"].items():
        ci = c.col_index(lname)
        if ci is not None:
            known_cols.add(ci)
        cells = c.doc.column_cells(ci) if ci is not None else []
        layers.append({"layer": lname, "column": None if ci is None else ci + 1, "mode": L["mode"],
                       "style": L.get("style") or c.st["style"], "timing": L.get("timing", "auto"),
                       "boil": L.get("boil", False), "gui_edited": L.get("gui_edited", False),
                       "needs_compile": L.get("dirty", False),
                       "drawings": sorted(int(k) for k in L["drawings"]),
                       "exposure": _compact([(int(x.fid) % BOIL_BASE) if x and x.fid.isdigit() else (x.fid if x else 0)
                                             for x in cells]),
                       "parent": c.doc.parent_of(f"Col{ci + 1}") if ci is not None else None,
                       "keys": _keys_px(c.doc.get_params(f"Col{ci + 1}"), dpi) if ci is not None else {}})
    foreign = []
    for ci in range(len(c.doc.columns())):
        if ci not in known_cols:
            ids = c.doc.column_level_ids(ci)
            names = [lv["name"] for lv in c.doc.levels() if lv["id"] in ids]
            foreign.append({"column": ci + 1, "name": c.doc.stage_name(f"Col{ci + 1}"), "levels": names,
                            "note": "made in OpenToonz; MCP can key/retime it via target='Col%d'" % (ci + 1)})
    notes = (NOTES_DIR / f"{c.name}.md").read_text(encoding="utf-8") if (NOTES_DIR / f"{c.name}.md").exists() else ""
    if _opentoonz_running():
        c.warnings.append("OpenToonz GUI is running. If this scene is open there: save GUI work BEFORE asking the "
                          "MCP to edit, and use File > Revert Scene after MCP edits.")
    res = {"scene": c.name, "file": str(c.sf), "size": [W, H], "fps": c.doc.fps(), "frames": c.st["frames"],
           "style": c.st["style"], "bg": c.doc.bg(), "layers": layers, "foreign_columns": foreign,
           "pegs": [{"peg": c.doc.stage_name(p), "id": p, "parent": c.doc.parent_of(p),
                     "keys": _keys_px(c.doc.get_params(p), dpi)} for p in c.doc.peg_ids()],
           "camera": _keys_px(c.doc.get_params(c.doc.active_camera().attrs["id"]), dpi),
           "notes": notes[-3000:], "recent_journal": _journal_tail(c.name, 12),
           "lessons": learn.top(10), "style_overrides": learn.style_overrides(),
           "undo_available": len(list((SNAP_DIR / c.name).glob("*"))) if (SNAP_DIR / c.name).exists() else 0}
    return c.commit(res)


def _compact(cells: list) -> str:
    """Xsheet run-length text: '1x6 2x6 0x3' (drawing x frames)."""
    out, i = [], 0
    while i < len(cells):
        j = i
        while j < len(cells) and cells[j] == cells[i]:
            j += 1
        out.append(f"{cells[i]}x{j - i}")
        i = j
    return " ".join(out)


def _keys_px(params: dict, dpi: float) -> dict:
    out = {}
    for tag, info in params.items():
        conv = (lambda v: v * dpi) if tag == "x" else (lambda v: -v * dpi) if tag == "y" else (lambda v: v)
        out[TAG_KEYS.get(tag, tag)] = [{"frame": k["frame"] + 1, "value": round(conv(k["value"]), 3),
                                        "interp": k["interp"]} if "value" in k else
                                       {"frame": k["frame"] + 1, "expression": k.get("expression")}
                                       for k in info["keys"]] or {"static": round(conv(info["default"]), 3)}
    return out


@tool
def add_note(scene: str, note: str) -> dict:
    """Save a memory note for this scene (story, character design, decisions, todo). Shown in scene_context."""
    name = _load_state(scene)["name"]
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    with open(NOTES_DIR / f"{name}.md", "a", encoding="utf-8") as f:
        f.write(f"- [{_now()}] {note}\n")
    _journal(name, "add_note", {"note": note[:200]}, "ok")
    return {"saved": True}


@tool
def get_journal(scene: str, last: int = 40) -> list[dict]:
    """Operation history of a scene (every MCP action, outside edits detected, errors)."""
    return _journal_tail(_load_state(scene)["name"], last)


@tool
def style_guide(topic: str = "rules") -> str:
    """Studio animation rules (Ghibli/Disney/anime practice) with numbers: timing, spacing, squash,
    line weight, shading, camera. topic: 'rules' (numbered defaults) | 'full' | 'styles'."""
    if topic == "styles":
        return json.dumps({k: styles.get(k) for k in styles.STYLES}, indent=1)
    p = RESEARCH / "studio_practices.md"  # optional local research notes
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    if topic == "rules":
        if "## Rules to encode" in text:
            return text[text.index("## Rules to encode"):]
        return styles.RULES
    return text[:60000] or styles.RULES


@tool
def undo(scene: str, steps: int = 1) -> dict:
    """Restore the scene (and any overwritten vector levels) to the state before the last N MCP writes."""
    name = _safe(scene)
    root = SNAP_DIR / name
    snaps = sorted(x for x in root.iterdir() if x.is_dir() and (x / "complete").exists()) if root.exists() else []
    if steps < 1 or len(snaps) < steps:
        raise ValueError(f"only {len(snaps)} undo steps available")
    target = snaps[-steps]
    _restore(name, target)  # scene, state, SVG sources and drawing files exactly as before that operation
    if _state_path(name).exists():
        st = _load_state(name)
        st["tnz_hash"] = file_hash(_scene_file(name))
        _save_state(st)
    for d in snaps[-steps:]:
        shutil.rmtree(d, ignore_errors=True)
    _journal(name, "undo", {"steps": steps}, {"restored": target.name})
    return {"restored_to_before": target.name.split("_", 1)[-1], "remaining_undo": len(snaps) - steps}


# ====================================================================== tools: scene setup
@tool
def create_scene(name: str, width: int = 1920, height: int = 1080, fps: float = 24, frames: int = 48,
                 bg_color: str = "#ffffff", style: str = "anime", overwrite: bool = False) -> dict:
    """Create a scene in the OpenToonz sandbox project.
    style: anime | feature | cartoon | sketch | clean (line weight, wobble, taper, default timing)."""
    name = _safe(name)
    if style not in styles.STYLES:
        raise ValueError(f"style must be one of {list(styles.STYLES)}")
    if (_scene_file(name).exists() or _state_path(name).exists()) and not overwrite:
        raise ValueError(f"scene '{name}' exists; call scene_context('{name}') to continue it, or overwrite=true")
    if _state_path(name).exists() or _scene_file(name).exists():
        _snapshot(name, "overwrite")  # old scene, sources and drawings stay recoverable with undo
    for d in (SVG_DIR / name, PROJECT / "drawings" / name):
        shutil.rmtree(d, ignore_errors=True)
    dpi = 120.0
    s = tnz.Scene(width=width, height=height, fps=fps, frames=frames, bg=drawlib.color(bg_color), dpi=dpi,
                  output_path=f"+outputs/{name}/{name}..png")
    doc = SceneDoc.new(s)
    _scene_file(name).parent.mkdir(parents=True, exist_ok=True)
    doc.save(_scene_file(name))
    st = {"name": name, "style": style, "frames": frames, "layers": {}, "created": _now(),
          "tnz_hash": file_hash(_scene_file(name))}
    _save_state(st)
    _journal(name, "create_scene", {"w": width, "h": height, "fps": fps, "frames": frames, "style": style}, "ok")
    return {"scene": name, "file": str(_scene_file(name)), "style": style,
            "timing_hint": f"{style}: animate on {styles.get(style)['exposure']}s by default"}


@tool
def import_scene(path: str, name: str | None = None, overwrite: bool = False) -> dict:
    """Bring an existing OpenToonz .tnz (made in the GUI) under MCP control. Copies it into the sandbox
    scenes folder if it is elsewhere. Its columns appear as 'foreign_columns' you can key/retime.
    A scene the MCP already manages is refused unless overwrite=true (its MCP layer data is reset)."""
    src = Path(path)
    name = _safe(name or src.stem)
    if _state_path(name).exists():
        if not overwrite:
            raise ValueError(f"scene '{name}' is already managed by the MCP; use scene_context('{name}') "
                             f"or pass overwrite=true to re-import it")
        _snapshot(name, "reimport")
    dst = _scene_file(name)
    if src.resolve() != dst.resolve():
        if dst.exists():
            raise ValueError(f"scene '{name}' already exists in sandbox")
        shutil.copy2(src, dst)
    SceneDoc.load(dst)  # validates parse
    _state_path(name).unlink(missing_ok=True)
    st = _adopt(name)
    return {"scene": name, "frames": st["frames"], "next": f"scene_context('{name}')"}


@tool
def set_scene_settings(scene: str, frames: int | None = None, fps: float | None = None,
                       bg_color: str | None = None, style: str | None = None) -> dict:
    """Change scene length (frames), fps, background colour, or default drawing style."""
    if style and style not in styles.STYLES:
        raise ValueError(f"style must be one of {list(styles.STYLES)}")
    if frames is not None and frames < 1:
        raise ValueError("frames must be >= 1")
    c = Ctx(scene, "set_scene_settings", {"frames": frames, "fps": fps, "bg": bg_color, "style": style})
    if frames:
        c.st["frames"] = int(frames)
        for lname in c.st["layers"]:
            _retime(c, lname)
    if fps:
        c.doc.set_fps(fps)
    if bg_color:
        c.doc.set_bg(drawlib.color(bg_color))
    if style:
        c.st["style"] = style
    return c.commit({"frames": c.st["frames"], "fps": c.doc.fps(), "bg": c.doc.bg(), "style": c.st["style"]})


@tool
def add_layer(scene: str, layer: str, mode: str = "vector", style: str | None = None, parent: str = "Table",
              boil: bool = False) -> dict:
    """Add a column with its own drawing level. Later layers are drawn ON TOP.
    mode: 'vector' (native OpenToonz vector .pli, editable in GUI, pressure lines) | 'raster' (PNG).
    style: override scene style for this layer (e.g. 'clean' for BG, 'sketch' for effects).
    parent: 'Table', a peg or another layer. boil: hand-drawn line boil on held drawings."""
    if style and style not in styles.STYLES:
        raise ValueError(f"style must be one of {list(styles.STYLES)}")
    c = Ctx(scene, "add_layer", {"layer": layer, "mode": mode, "style": style, "parent": parent, "boil": boil})
    name = _safe(layer)
    clash = [n for n in c.st["layers"] if n.lower() == name.lower()]
    if clash:  # Windows paths are case-insensitive: 'Ball' and 'ball' would share files
        raise ValueError(f"layer '{clash[0]}' already exists (names are case-insensitive)")
    if mode not in ("vector", "raster", "traced"):
        raise ValueError("mode must be vector, raster or traced")
    c.st["layers"][name] = {"mode": mode, "style": style, "drawings": {}, "timing": "auto", "boil": boil,
                            "level_path": _level_path(c.st, name, mode), "dirty": False}
    old = c.doc.level_by_path(_level_path(c.st, name, mode))
    lid = old["id"] if old else c.doc.add_level(name, _level_path(c.st, name, mode),
                                                 None if mode in ("vector", "traced") else c.doc.dpi())
    ci = c.doc.add_column(lid, name)
    if parent and parent.lower() != "table":
        c.doc.set_parent(f"Col{ci + 1}", c.stage_id(parent))
    return c.commit({"layer": name, "column": ci + 1, "mode": mode})


@tool
def add_peg(scene: str, peg: str, parent: str = "Table") -> dict:
    """Add a pegbar (invisible transform node). Parent layers to it to move groups (character rigs, BG sets)."""
    c = Ctx(scene, "add_peg", {"peg": peg, "parent": parent})
    pid = c.doc.add_peg(_safe(peg), c.stage_id(parent) if parent.lower() != "table" else "Table")
    return c.commit({"peg": _safe(peg), "id": pid})


@tool
def set_parent(scene: str, target: str, parent: str) -> dict:
    """Re-parent a layer or peg to 'Table', a peg, or another layer."""
    c = Ctx(scene, "set_parent", {"target": target, "parent": parent})
    sid = c.stage_id(target)
    pid = "Table" if parent.lower() == "table" else c.stage_id(parent)
    if sid == pid:
        raise ValueError("cannot parent to itself")
    c.doc.set_parent(sid, pid)
    return c.commit({"target": sid, "parent": pid})


@tool
def set_layer(scene: str, layer: str, visible: bool | None = None, opacity: float | None = None,
              boil: bool | None = None, style: str | None = None, timing: str | None = None) -> dict:
    """Layer properties: visible, opacity (0-1, renders), boil, style (needs redraw), timing 'auto'|'manual'."""
    if style and style not in styles.STYLES:
        raise ValueError(f"style must be one of {list(styles.STYLES)}")
    if timing is not None and timing not in ("auto", "manual"):
        raise ValueError("timing must be 'auto' or 'manual'")
    c = Ctx(scene, "set_layer", {"layer": layer, "visible": visible, "opacity": opacity, "boil": boil})
    name = _safe(layer)
    L = c.layer(name)
    ci = c.col_index(name)
    if visible is not None and ci is not None:
        c.doc.set_column_visible(ci, visible)
    if opacity is not None and ci is not None:
        c.doc.set_column_opacity(ci, round(max(0, min(1, opacity)) * 255))
    if style:
        L["style"] = style
    if timing in ("auto", "manual"):
        L["timing"] = timing
    if boil is not None and boil != L.get("boil", False):
        L["boil"] = boil
        for did, shapes in list(L["drawings"].items()):
            _write_drawing(c, name, int(did), shapes, force=True)
        if L.get("timing") == "manual" and ci is not None:
            # remap the hand-set exposure to base drawings, then re-apply (or drop) the boil cycle
            base = [(int(x.fid) % BOIL_BASE) if x and x.fid.isdigit() else 0 for x in c.doc.column_cells(ci)]
            _apply_cells(c, name, _boil_cycle(base) if boil else base)
    _retime(c, name)
    return c.commit({"layer": name, "visible": visible, "opacity": opacity, "boil": L.get("boil")})


@tool
def delete_layer(scene: str, layer: str) -> dict:
    """Delete a layer (column, level, drawings). Undo-able."""
    c = Ctx(scene, "delete_layer", {"layer": layer})
    name = _safe(layer)
    c.layer(name)
    ci = c.col_index(name)
    if ci is not None:
        c.doc.remove_column(ci)
    L = c.st["layers"].pop(name)
    lv = c.doc.level_by_path(L["level_path"])
    if lv and not any(lv["id"] in c.doc.column_level_ids(k) for k in range(len(c.doc.columns()))):
        c.doc.remove_level(lv["id"])
    shutil.rmtree(SVG_DIR / c.name / name, ignore_errors=True)
    if L["mode"] in ("vector", "traced"):
        _pli_file(c.st, name).unlink(missing_ok=True)
    else:
        for f in (PROJECT / "drawings" / c.name).glob(f"{name}.*.png"):
            f.unlink()
    return c.commit({"deleted": name, "undo": "undo() restores the layer and its drawings"})


# ====================================================================== tools: drawing
@tool
def draw(scene: str, layer: str, frame: int, shapes: list[dict], force: bool = False) -> dict:
    """Draw drawing #frame on a layer (held until the next drawing when timing is auto).

    Shapes (canvas px, origin top-left), painted in order:
      circle{x,y,r} ellipse{x,y,rx,ry,rotation} rect{x,y,w,h,radius,rotation} (x,y=top-left)
      polygon{points,smooth} path{points,smooth=true,closed} text{x,y,text,size,anchor='mm'}
      svgpath{d:'M.. C.. A.. Z', transform?} - any SVG path data (detailed art: hair, hands, folds)
      brush{points:[[x,y,pressure 0-1],...], width:px max, color} - inked stroke with per-point pressure
    Paint/ink: fill, stroke, stroke_width (px; default from style), opacity, interior=true (thinner
    interior line), trace_line=true (iro-tore coloured line), shadow=true|{light:deg,depth:px,color}
    (anime cel shadow; light 90=top, 135=top-left), human=false (perfect geometry), wobble (px).
    Colours: '#rrggbb', css names or [r,g,b,a]."""
    _check_drawing_id(frame)
    c = Ctx(scene, "draw", {"layer": layer, "frame": frame, "n_shapes": len(shapes)})
    name = _safe(layer)
    _write_drawing(c, name, int(frame), shapes, force)
    if int(frame) > c.st["frames"]:
        c.warnings.append(f"drawing {frame} is after the scene end ({c.st['frames']}); extend with set_scene_settings")
    msg = _retime(c, name)
    res = {"layer": name, "drawing": int(frame)}
    if msg:
        c.warnings.append(msg)
    return c.commit(res)


@tool
def draw_many(scene: str, layer: str, drawings: list[dict], force: bool = False) -> dict:
    """Batch draw: drawings=[{"frame": n, "shapes": [...]}, ...]. Use for frame-by-frame animation."""
    for d in drawings:
        _check_drawing_id(d.get("frame"))
    c = Ctx(scene, "draw_many", {"layer": layer, "count": len(drawings)})
    name = _safe(layer)
    for d in drawings:
        _write_drawing(c, name, int(d["frame"]), d["shapes"], force)
    msg = _retime(c, name)
    if msg:
        c.warnings.append(msg)
    return c.commit({"layer": name, "drawings": [int(d["frame"]) for d in drawings]})


@tool
def inbetween(scene: str, layer: str, start_frame: int, end_frame: int, start_shapes: list[dict] | None = None,
              end_shapes: list[dict] | None = None, easing: str = "slow_in_out", step: int | None = None,
              arc: float = 0.0, squash: float = 0.0, force: bool = False) -> dict:
    """Generate in-betweens (douga) between two key drawings (genga).

    start/end_shapes default to the drawings already on start_frame/end_frame.
    easing: slow_in_out (studio chart) | snappy | linear | in | out | bounce | elastic | back.
    step: 1 ones, 2 twos, 3 threes (default: style timing, anime=3, feature=2).
    arc: bow of the travel path (0.15 = natural arc). squash: 0-1 stretch round shapes along motion.
    Studio rule: only auto-inbetween 'tight' moves (rotation<=30deg, scale change<=25%); add a breakdown key otherwise."""
    _check_drawing_id(start_frame)
    _check_drawing_id(end_frame)
    if end_frame <= start_frame:
        raise ValueError("end_frame must be greater than start_frame")
    c = Ctx(scene, "inbetween", {"layer": layer, "start": start_frame, "end": end_frame, "easing": easing,
                                 "step": step, "arc": arc, "squash": squash})
    name = _safe(layer)
    L = c.layer(name)
    a = start_shapes or L["drawings"].get(str(start_frame))
    b = end_shapes or L["drawings"].get(str(end_frame))
    if not a or not b:
        raise ValueError("start/end key drawings missing: pass start_shapes/end_shapes or draw them first")
    sty = styles.get(L.get("style") or c.st["style"])
    step = step or sty["exposure"]
    span = max(1, end_frame - start_frame)
    made = []
    for f in list(range(start_frame, end_frame, max(1, step))) + [end_frame]:
        t = (f - start_frame) / span
        shapes = a if f == start_frame else b if f == end_frame else drawlib.tween_motion(
            a, b, t, easing, arc, squash, sty["squash"], dt=step / span / 2)
        _write_drawing(c, name, f, shapes, force)
        made.append(f)
    msg = _retime(c, name)
    if msg:
        c.warnings.append(msg)
    if len(a) != len(b):
        c.warnings.append("key drawings have different shape counts; extra shapes pop instead of morphing")
    return c.commit({"layer": name, "drawings": made, "on": step})


@tool
def set_exposure(scene: str, layer: str, cells: list[int] | str) -> dict:
    """Xsheet timing for a layer (sets timing=manual). cells[i] = drawing at frame i+1 (0 = blank),
    or run-length text '1x6 2x6 3x4 0x2' (drawing x frames). Cycle example on twos: '1x2 2x2 3x2 4x2 1x2 ...'."""
    c = Ctx(scene, "set_exposure", {"layer": layer, "cells": cells if isinstance(cells, str) else len(cells)})
    name = _safe(layer)
    L = c.layer(name)
    if isinstance(cells, str):
        lst = []
        for tok in cells.split():
            d, _, n = tok.partition("x")
            lst += [int(d)] * int(n or 1)
        cells = lst
    cells = [int(x) % BOIL_BASE for x in cells]  # accept boil variant ids shown by older contexts
    missing = sorted({x for x in cells if x and str(x) not in L["drawings"]})
    if missing:
        raise ValueError(f"drawings not on layer: {missing}. Existing: {sorted(int(k) for k in L['drawings'])}")
    L["timing"] = "manual"
    if L.get("boil"):
        cells = _boil_cycle(cells)
    _apply_cells(c, name, cells)
    if len(cells) > c.st["frames"]:
        c.st["frames"] = len(cells)
    return c.commit({"layer": name, "frames": len(cells), "timing": "manual"})


# ====================================================================== tools: keyframes / principles
def _to_internal(tag: str, v: float, dpi: float) -> float:
    if tag == "x":
        return v / dpi
    if tag == "y":
        return -v / dpi
    return v


def _set_keys(c: Ctx, sid: str, tag: str, new: list[dict], replace_range: tuple[int, int] | None = None) -> None:
    """Merge keys (frame 1-based, value px/deg/scale) into a stage object param."""
    dpi = c.doc.dpi()
    for k in new:
        if k.get("interp", "ease") not in INTERPS:
            raise ValueError(f"interp must be one of {INTERPS}, got {k.get('interp')!r}")
        if int(k["frame"]) < 1:
            raise ValueError(f"key frame must be >= 1, got {k['frame']}")
    conv = [{"frame": int(k["frame"]) - 1, "value": _to_internal(tag, float(k["value"]), dpi),
             "interp": k.get("interp", "ease"), "ease_in": k.get("ease_in", 33.0),
             "ease_out": k.get("ease_out", 33.0)} for k in new]
    rr = (replace_range[0] - 1, replace_range[1] - 1) if replace_range else None
    # node-level merge: untouched keys (GUI speed handles, expressions, step, cycle) stay byte-for-byte
    c.doc.upsert_keys(sid, tag, conv, rr)


@tool
def set_keyframe(scene: str, target: str, frame: int, x: float | None = None, y: float | None = None,
                 rotation: float | None = None, scale: float | None = None, scale_x: float | None = None,
                 scale_y: float | None = None, z: float | None = None, interp: str = "ease") -> dict:
    """Keyframe a layer, peg, or 'camera' (real OpenToonz keys, editable in GUI Function Editor).
    x/y: px offset from rest (+y down). rotation: deg CCW. scale*: 1.0=100%. z: depth.
    interp (segment starting at this key): ease | linear | constant | speed | exp.
    Camera: scale<1 zooms IN; use exp interp for trucks (natural zoom)."""
    c = Ctx(scene, "set_keyframe", {"target": target, "frame": frame, "x": x, "y": y, "rot": rotation,
                                    "sc": scale, "interp": interp})
    sid = c.stage_id(target)
    vals = {"x": x, "y": y, "rot": rotation, "sc": scale, "sx": scale_x, "sy": scale_y, "z": z}
    for tag, v in vals.items():
        if v is not None:
            _set_keys(c, sid, tag, [{"frame": frame, "value": v, "interp": interp}])
    return c.commit({"target": sid, "frame": frame, "set": [t for t, v in vals.items() if v is not None]})


def _layer_bbox(L: dict) -> tuple[float, float, float, float] | None:
    """Canvas-px bounds of all drawings on a layer (from stored shapes)."""
    xs, ys = [], []
    for shapes in L["drawings"].values():
        for sh in shapes:
            try:
                for c in vec.shape_contours(sh):
                    xs += [p[0] for p in c.pts]
                    ys += [p[1] for p in c.pts]
            except Exception:
                continue
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


@tool
def set_pivot(scene: str, target: str, x: float | str = "auto", y: float | None = None,
              anchor: str = "bottom") -> dict:
    """Set the rotation/scale pivot of a layer or peg without moving it (OpenToonz 'center').
    x,y: canvas px. x='auto': from the layer's drawing bounds; anchor 'bottom' (feet - use for squash
    on the ground) | 'center' | 'top'. Do this BEFORE squash/stretch or rotation keys."""
    c = Ctx(scene, "set_pivot", {"target": target, "x": x, "y": y, "anchor": anchor})
    sid = c.stage_id(target)
    W, H = c.doc.camera_res()
    dpi = c.doc.dpi()
    if x == "auto":
        bb = None
        for lname, L in c.st["layers"].items():
            if c.col_index(lname) is not None and f"Col{c.col_index(lname) + 1}" == sid:
                bb = _layer_bbox(L)
        if sid.startswith("Peg"):  # union of child layers
            boxes = [_layer_bbox(L) for n, L in c.st["layers"].items()
                     if c.col_index(n) is not None and c.doc.parent_of(f"Col{c.col_index(n) + 1}") == sid]
            boxes = [b for b in boxes if b]
            if boxes:
                bb = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                      max(b[3] for b in boxes))
        if not bb:
            raise ValueError("no drawings to derive pivot from; pass x and y")
        px = (bb[0] + bb[2]) / 2
        py = {"bottom": bb[3], "top": bb[1]}.get(anchor, (bb[1] + bb[3]) / 2)
    else:
        px, py = float(x), float(y if y is not None else H / 2)
    c.doc.set_pivot(sid, (px - W / 2) / dpi, -(py - H / 2) / dpi)
    return c.commit({"target": sid, "pivot_px": [round(px, 1), round(py, 1)]})


@tool
def clear_keyframes(scene: str, target: str, param: str | None = None, start: int | None = None,
                    end: int | None = None) -> dict:
    """Remove keys from a target (optionally one param: x,y,rotation,scale,scale_x,scale_y,z; optional frame range)."""
    c = Ctx(scene, "clear_keyframes", {"target": target, "param": param, "start": start, "end": end})
    if param and param not in KEY_TAGS and param not in KEY_TAGS.values():
        raise ValueError(f"param must be one of {list(KEY_TAGS)}")
    sid = c.stage_id(target)
    tags = [KEY_TAGS.get(param, param)] if param else list(KEY_TAGS.values())
    for tag in tags:
        cur = c.doc.get_params(sid).get(tag)
        if not cur:
            continue
        if start or end:
            c.doc.upsert_keys(sid, tag, [], ((start or 1) - 1, (end or 10 ** 6) - 1))
        else:
            c.doc.set_param(sid, tag, [], None)
    return c.commit({"target": sid, "cleared": tags})


@tool
def animate_action(scene: str, target: str, start: int, end: int, to: dict, from_: dict | None = None,
                   anticipation: float = 0.15, overshoot: float = 0.10, settle_frames: int = 8,
                   easing: str = "ease") -> dict:
    """Move a layer/peg/camera with animation principles baked in (Illusion of Life numbers):
    anticipation (opposite move, 15% amplitude, 25% of duration, min 3f), main move with slow-in/out,
    overshoot (10% past target) and damped settle (x0.45 swing) over settle_frames AFTER 'end'.
    to/from_: {x,y,rotation,scale,scale_x,scale_y} (px/deg/factor). from_ defaults to current value at start."""
    c = Ctx(scene, "animate_action", {"target": target, "start": start, "end": end, "to": to,
                                      "anticipation": anticipation, "overshoot": overshoot})
    sid = c.stage_id(target)
    dpi = c.doc.dpi()
    dur = end - start
    if dur < 2:
        raise ValueError("action needs at least 2 frames")
    ant = max(3, round(0.25 * dur)) if anticipation else 0
    if ant >= dur:
        ant = 0
    report = {}
    for key, v_to in to.items():
        tag = KEY_TAGS.get(key, key)
        if tag not in KEY_TAGS.values():
            raise ValueError(f"unknown param {key}")
        cur = c.doc.get_params(sid).get(tag)
        if from_ and key in from_:
            v0 = float(from_[key])
        else:
            v0 = _value_at(cur, start - 1, dpi, tag) if cur else (1.0 if tag in ("sc", "sx", "sy") else 0.0)
        d = float(v_to) - v0
        keys = [{"frame": start, "value": v0, "interp": "ease"}]
        if ant:
            keys.append({"frame": start + ant, "value": v0 - anticipation * d, "interp": "ease"})
        if overshoot and settle_frames >= 2:
            keys.append({"frame": end, "value": float(v_to) + overshoot * d, "interp": "ease"})
            half = max(1, settle_frames // 2)
            keys.append({"frame": end + half, "value": float(v_to) - overshoot * 0.45 * d, "interp": "ease"})
            keys.append({"frame": end + settle_frames, "value": float(v_to), "interp": "ease"})
        else:
            keys.append({"frame": end, "value": float(v_to), "interp": "ease"})
        _set_keys(c, sid, tag, keys, replace_range=(start, keys[-1]["frame"]))
        report[key] = [(k["frame"], round(k["value"], 2)) for k in keys]
    last = max(k[-1][0] for k in report.values())
    if last > c.st["frames"]:
        c.warnings.append(f"settle runs to frame {last}, past scene end {c.st['frames']}; extend with set_scene_settings")
    return c.commit({"target": sid, "keys": report})


def _value_at(info: dict, frame0: float, dpi: float, tag: str) -> float:
    """Exact value (tool units: px / deg / factor) at 0-based frame, using the OpenToonz evaluator."""
    conv = (lambda v: v * dpi) if tag == "x" else (lambda v: -v * dpi) if tag == "y" else (lambda v: v)
    ks = [k for k in info["keys"] if "value" in k]
    return conv(curve.value_at(ks, frame0, info["default"]))


def _plate_check(c: Ctx, x0: float, y0: float, x1: float, y1: float, parallax: dict | None = None,
                 margin: float = 0.05) -> str | None:
    """Warn only if NO layer's drawings cover the area the camera move reveals (camera rect grown by move)."""
    W, H = c.doc.camera_res()
    mx, my = margin * W, margin * H
    need = (x0 - mx * (x0 < 0), y0 - my * (y0 < 0), W + x1 + mx * (x1 > 0), H + y1 + my * (y1 > 0))
    for lname, L in c.st["layers"].items():
        r = float((parallax or {}).get(lname, 1.0))
        bb = _layer_bbox(L)
        if not bb:
            continue
        # a layer moving at ratio r only needs to cover r * move
        req = (need[0] * r if need[0] < 0 else need[0], need[1] * r if need[1] < 0 else need[1],
               W + (need[2] - W) * r, H + (need[3] - H) * r)
        if bb[0] <= req[0] + 1 and bb[1] <= req[1] + 1 and bb[2] >= req[2] - 1 and bb[3] >= req[3] - 1:
            return None
    return (f"bg plate: no layer covers the revealed area x[{need[0]:.0f},{need[2]:.0f}] y[{need[1]:.0f},"
            f"{need[3]:.0f}] px; make a BG drawing that large or the move exposes empty edges")


@tool
def camera_move(scene: str, kind: str, start: int, end: int | None = None, dx: float = 0, dy: float = 0,
                zoom: float | None = None, strength: float = 8.0, parallax: dict | None = None) -> dict:
    """Camera work (satsuei):
    kind='pan': move view by dx,dy px from start..end (eased 8f). parallax={layer: ratio} makes layers
      move at ratio of camera speed (sky 0.05, far BG 0.3, mid 0.6, char 1.0, FG 1.8).
    kind='truck': zoom to 'zoom' scale (0.75 = push in 25%) with exponential interpolation.
    kind='shake': impact shake starting at 'start', strength px (8 default), decays x0.7/frame on ones."""
    c = Ctx(scene, "camera_move", {"kind": kind, "start": start, "end": end, "dx": dx, "dy": dy, "zoom": zoom,
                                   "strength": strength, "parallax": parallax})
    cam = c.doc.active_camera().attrs["id"]
    dpi = c.doc.dpi()
    res: dict[str, Any] = {"camera": cam}
    if kind == "pan":
        if not end:
            raise ValueError("pan needs end")
        n = end - start
        speed = math.hypot(dx, dy) / max(1, n)
        if speed > 32:
            c.warnings.append(f"pan {speed:.1f}px/frame is a whip pan; add directional blur or slow it down")
        elif speed > 10:
            c.warnings.append(f"pan {speed:.1f}px/frame exceeds 10px/frame studio limit for detailed BGs (strobing)")
        for tag, d in (("x", dx), ("y", dy)):
            if not d:
                continue
            cur = c.doc.get_params(cam).get(tag)
            v0 = _value_at(cur, start - 1, dpi, tag) if cur else 0.0
            _set_keys(c, cam, tag, [{"frame": start, "value": v0, "interp": "ease"},
                                    {"frame": end, "value": v0 + d, "interp": "ease"}])
            for lname, ratio in (parallax or {}).items():
                sid = c.stage_id(lname)
                lc = c.doc.get_params(sid).get(tag)
                l0 = _value_at(lc, start - 1, dpi, tag) if lc else 0.0
                _set_keys(c, sid, tag, [{"frame": start, "value": l0, "interp": "ease"},
                                        {"frame": end, "value": l0 + (1 - float(ratio)) * d, "interp": "ease"}])
        res["speed_px_per_frame"] = round(speed, 2)
        w = _plate_check(c, min(0, -dx), min(0, -dy), max(0, dx), max(0, dy), parallax)
        if w:
            c.warnings.append(w)
    elif kind == "truck":
        if not end or zoom is None:
            raise ValueError("truck needs end and zoom")
        cur = c.doc.get_params(cam).get("sc")
        v0 = _value_at(cur, start - 1, dpi, "sc") if cur else 1.0
        _set_keys(c, cam, "sc", [{"frame": start, "value": v0, "interp": "exp"},
                                 {"frame": end, "value": zoom, "interp": "ease"}])
        if end - start < 8:
            c.warnings.append("trucks under 8 frames read as a jump cut")
    elif kind == "trauma":
        n = (end - start) if end else 24
        shake = physics.trauma_shake(n, trauma=min(1.0, strength / 24.0) ** 0.5 if strength else 1.0,
                                     max_offset=24.0, seed=start)
        for tag, key in (("x", "x"), ("y", "y"), ("rot", "rot")):
            base = _param_series(c, cam, tag, start, start)[0]
            _bake(c, cam, tag, [(start + s_["frame"], base + (s_[key] if tag != "y" else s_[key]))
                                for s_ in shake] + [(start + n, base)], 0.1)
        w = _plate_check(c, -24, -24, 24, 24, margin=0.0)
        if w:
            c.warnings.append(w)
    elif kind == "shake":
        frames = 10
        rng = random.Random(start)
        for tag in ("x", "y"):
            cur = c.doc.get_params(cam).get(tag)
            base = _value_at(cur, start - 1, dpi, tag) if cur else 0.0
            keys = [{"frame": start - 1, "value": base, "interp": "linear"}] if start > 1 else []
            amp = strength * (1.0 if tag == "y" else 0.6)
            for i in range(frames):
                keys.append({"frame": start + i, "value": base + amp * (0.7 ** i) * (1 if i % 2 == 0 else -1)
                             * rng.uniform(0.7, 1.0), "interp": "linear"})
            keys.append({"frame": start + frames, "value": base, "interp": "linear"})
            _set_keys(c, cam, tag, keys, replace_range=(start, start + frames))
        w = _plate_check(c, -strength, -strength, strength, strength, margin=0.0)
        if w:
            c.warnings.append(w)
    else:
        raise ValueError("kind must be pan, truck, shake or trauma")
    return c.commit(res)


# ====================================================================== tools: output
@tool
def import_image(scene: str, layer: str, path: str, frame: int = 1, box: list[float] | None = None,
                 fit: str = "contain", trace: str | None = None, max_colors: int = 12, accuracy: int = 8,
                 despeckling: int = 4, threshold: int = 8) -> dict:
    """Bring a picture (sketch, scanned drawing, painting, photo, AI image) into a layer.

    Raster layer (mode 'raster'): the image is placed as-is. Traced layer (add_layer mode='traced'):
    OpenToonz's own vectorizer turns it into editable vector strokes and fills on compile.
    trace: set the traced layer's method once: 'outline' (colour regions) | 'centerline' (ink lines).
    box [x,y,w,h] px (default whole canvas); fit contain | cover | stretch | none."""
    _check_drawing_id(frame)
    if fit not in ("contain", "cover", "stretch", "none"):
        raise ValueError("fit must be contain, cover, stretch or none")
    if not Path(path).exists():
        raise ValueError(f"image not found: {path}")
    c = Ctx(scene, "import_image", {"layer": layer, "path": path, "frame": frame, "trace": trace})
    name = _safe(layer)
    L = c.layer(name)
    if L["mode"] == "vector":
        raise ValueError("vector layers are drawn from shapes; use a 'raster' or 'traced' layer for images")
    if L["mode"] == "traced":
        if trace and trace not in ("outline", "centerline"):
            raise ValueError("trace must be 'outline' or 'centerline'")
        tr = L.setdefault("trace", {})
        if trace:
            tr["kind"] = trace
        tr.update({"maxColors": max_colors, "accuracy": accuracy, "despeckling": despeckling,
                   "threshold": threshold})
    shape = {"type": "image", "src": str(Path(path).resolve()), "fit": fit}
    if box:
        shape["box"] = [float(v) for v in box]
    _write_drawing(c, name, frame, [shape])
    msg = _retime(c, name)
    if msg:
        c.warnings.append(msg)
    return c.commit({"layer": name, "drawing": frame, "mode": L["mode"],
                     "next": "compile_vectors / preview_frame to build and check the trace"
                     if L["mode"] == "traced" else "preview_frame to check placement"})


@tool
def import_svg(scene: str, layer: str, path: str, frame: int = 1, box: list[float] | None = None,
               human: bool = False, force: bool = False) -> dict:
    """Import an SVG file (Inkscape/Illustrator/free assets) as editable vector shapes on a drawing.
    Every path becomes an 'svgpath' shape you can tween, recolour or humanize (human=true adds the
    hand-drawn wobble/pressure of the layer style). box [x,y,w,h] fits the art (default whole canvas)."""
    _check_drawing_id(frame)
    if not Path(path).exists():
        raise ValueError(f"svg not found: {path}")
    c = Ctx(scene, "import_svg", {"layer": layer, "path": path, "frame": frame})
    name = _safe(layer)
    W, H = c.doc.camera_res()
    shapes = assets.svg_file_to_shapes(path, tuple(box) if box else None, (W, H), human)
    _write_drawing(c, name, frame, shapes, force)
    msg = _retime(c, name)
    if msg:
        c.warnings.append(msg)
    return c.commit({"layer": name, "drawing": frame, "shapes": len(shapes)})


@tool
def save_part(name: str, shapes: list[dict], anchor: list[float] | None = None,
              variants: dict[str, list[dict]] | None = None, notes: str = "") -> dict:
    """Save a reusable part to the global model-sheet library (head, eye, hand, mouth chart, prop).
    shapes use the part's own coordinates; anchor [x,y] is its attachment/pivot point.
    variants: alternative drawings by name, e.g. {"closed": [...], "A": [...], "O": [...]}.
    Use in any drawing as {"type":"part","name":..,"variant":..,"x","y","scale","rotation","flip"}."""
    vec.build_elements(assets.expand_parts(shapes), 1080, "clean", 0)  # validate drawable before saving
    for vs in (variants or {}).values():
        vec.build_elements(assets.expand_parts(vs), 1080, "clean", 0)
    return assets.save_part(name, shapes, tuple(anchor or (0, 0)), variants, notes)


@tool
def list_parts() -> list[dict]:
    """Parts in the model-sheet library with their variants."""
    return assets.list_parts()


def _bake(c: Ctx, sid: str, tag: str, samples: list[tuple[int, float]], tol: float = 0.25) -> int:
    """Write per-frame samples (1-based frame, tool units) as the fewest linear keys that reproduce every
    frame within tol; replaces keys only inside the sampled range. Returns key count."""
    if not samples:
        return 0
    keys = physics.thin(samples, tol)
    _set_keys(c, sid, tag, [{"frame": f, "value": v, "interp": "linear"} for f, v in keys],
              replace_range=(samples[0][0], samples[-1][0]))
    return len(keys)


def _param_series(c: Ctx, sid: str, tag: str, start: int, end: int) -> list[float]:
    """Exact per-frame values (tool units) of a stage-object param, as OpenToonz evaluates them."""
    info = c.doc.get_params(sid).get(tag) or {"keys": [], "default": 1.0 if tag in ("sc", "sx", "sy") else 0.0}
    return [_value_at(info, f - 1, c.doc.dpi(), tag) for f in range(start, end + 1)]


def _pivot_warning(c: Ctx, sid: str) -> None:
    if c.doc.pivot(sid) == (0.0, 0.0) and sid.startswith(("Col", "Peg")):
        c.warnings.append(f"{sid} pivot is at the camera centre: scaling/rotation will swing the drawing "
                          f"around the screen centre. Call set_pivot first (anchor='bottom' for squash, "
                          f"or x,y at the attachment point for hair/tails).")


@tool
def physics_bounce(scene: str, target: str, start: int, height: float, fall_frames: float | None = None,
                   material: str = "rubber_ball", bounces: int = 4, travel_x: float = 0.0,
                   squash: bool = True, start_on_ground: bool = False) -> dict:
    """Physically exact bouncing (gravity parabolas, coefficient of restitution) on a layer/peg.
    The drawing must rest on the ground line; y offsets go UP from there. height px.
    fall_frames: frames for the first drop (default: real gravity for a 1.7 m character = 1080 px tall,
    i.e. 14.1*sqrt(height/1080)). material: rubber_ball | table_tennis | basketball | tennis |
    soft_rubber | bowling | balloon (sets restitution and squash). travel_x: px moved over the whole bounce,
    slowing with each contact. squash: area-preserving contact squash (set_pivot at the feet first)."""
    mats = physics.recipes()["bounce"]["materials"]
    if material not in mats:
        raise ValueError(f"material must be one of {list(mats)}")
    if height <= 0:
        raise ValueError("height must be > 0")
    c = Ctx(scene, "physics_bounce", {"target": target, "start": start, "height": height, "material": material})
    sid = c.stage_id(target)
    mat = mats[material]
    e = float(mat["e"])
    W, H = c.doc.camera_res()
    N = float(fall_frames or max(3.0, 14.1 * math.sqrt(height / H)))
    samples = physics.bounce(height, N, e, bounces, start_on_ground)
    base_y = _param_series(c, sid, "y", start, start)[0]
    ys = [(start + s_["frame"], base_y - s_["h"]) for s_ in samples]
    nkeys = _bake(c, sid, "y", ys)
    res = {"target": sid, "frames": len(samples), "first_fall_frames": round(N, 2), "restitution": e,
           "y_keys": nkeys}
    if travel_x:
        # horizontal speed drops by vx_loss at every contact
        loss = float(mat.get("vx_loss_per_bounce", 0.1))
        v, xs, x = 1.0, [], 0.0
        for s_ in samples:
            xs.append(x)
            if s_["contact"] and s_["frame"]:
                v *= (1 - loss)
            x += v
        k = travel_x / max(xs[-1], 1e-9)
        bx = _param_series(c, sid, "x", start, start)[0]
        res["x_keys"] = _bake(c, sid, "x", [(start + i, bx + xv * k) for i, xv in enumerate(xs)])
    if squash:
        _pivot_warning(c, sid)
        sy, n_contact = [], 0
        smax = float(mat.get("max_stretch", 1.2))
        vmax = max(abs(s_["v"]) for s_ in samples) or 1.0
        for s_ in samples:
            if s_["contact"] and s_["frame"]:
                val = physics.squash_for(mat, n_contact, e)
                n_contact += 1
            else:
                val = 1 + (smax - 1) * min(1.0, abs(s_["v"]) / vmax) ** 2
            sy.append((start + s_["frame"], val))
        res["squash_keys"] = _bake(c, sid, "sy", sy, 0.005)
        _bake(c, sid, "sx", [(f, 1 / v) for f, v in sy], 0.005)  # area preserved: sx*sy = 1
    end = start + samples[-1]["frame"]
    if end > c.st["frames"]:
        c.warnings.append(f"bounce ends at frame {end}, past the scene end {c.st['frames']}")
    return c.commit(res)


@tool
def physics_jump(scene: str, target: str, start: int, height: float, air_frames: int | None = None,
                 style: str = "feature") -> dict:
    """Recipe jump on a layer/peg: anticipation crouch -> takeoff stretch -> exact parabola -> landing
    squash -> recovery. style: realistic | feature | cartoon (crouch depth, stretch and squash amounts).
    air_frames default: real gravity for the scene height (1.7 m character = frame height)."""
    presets = {"realistic": (0.08, 1.04, 0.95, 8), "feature": (0.15, 1.10, 0.90, 6), "cartoon": (0.25, 1.25, 0.78, 8)}
    if style not in presets:
        raise ValueError(f"style must be one of {list(presets)}")
    c = Ctx(scene, "physics_jump", {"target": target, "start": start, "height": height, "style": style})
    sid = c.stage_id(target)
    W, H = c.doc.camera_res()
    crouch_k, stretch, land, cf = presets[style]
    T = int(air_frames or max(6, round(2 * 14.1 * math.sqrt(height / H))))
    j = physics.jump(height, T, crouch=crouch_k * 0.25 * H * 0.5, crouch_frames=cf, takeoff_stretch=stretch,
                     land_squash=land)
    by = _param_series(c, sid, "y", start, start)[0]
    _pivot_warning(c, sid)
    res = {"target": sid, "frames": j["frames"], "air_frames": T,
           "y_keys": _bake(c, sid, "y", [(start + i, by + v) for i, v in enumerate(j["y"])]),
           "sy_keys": _bake(c, sid, "sy", [(start + i, v) for i, v in enumerate(j["sy"])], 0.005)}
    _bake(c, sid, "sx", [(start + i, 1 / v) for i, v in enumerate(j["sy"])], 0.005)
    return c.commit(res)


@tool
def follow_through(scene: str, target: str, driver: str, start: int, end: int, param: str = "x",
                   target_param: str | None = None, frequency: float = 2.5, damping: float = 0.45,
                   response: float = 0.0, gain: float = 1.0, mode: str = "offset") -> dict:
    """Overlapping action / follow-through from a damped spring (second-order system).
    The target reacts to the driver's motion (driver's `param`, exact OpenToonz values).
    mode 'follow': target_param = spring(driver)  (a separate object trailing the driver).
    mode 'offset': target_param = gain * (spring(driver) - driver)  (a part PARENTED to the driver:
      hair, ears, tail, cloth; use target_param='rotation' with gain in deg per px for a swing).
    frequency Hz (hair 2-2.5, ears 3, belly 4-5, cape 1.2), damping 0.25-0.5 (overshoot 15-45%),
    response < 0 anticipates. Keep frequency <= 4 Hz on ones, <= 2.5 Hz on twos (aliasing)."""
    if mode not in ("offset", "follow"):
        raise ValueError("mode must be 'offset' or 'follow'")
    if end <= start:
        raise ValueError("end must be after start")
    if frequency <= 0 or damping <= 0:
        raise ValueError("frequency and damping must be > 0")
    c = Ctx(scene, "follow_through", {"target": target, "driver": driver, "param": param, "f": frequency,
                                      "zeta": damping, "mode": mode})
    sid, did = c.stage_id(target), c.stage_id(driver)
    tag, ttag = KEY_TAGS.get(param, param), KEY_TAGS.get(target_param or param, target_param or param)
    if tag not in KEY_TAGS.values() or ttag not in KEY_TAGS.values():
        raise ValueError(f"param must be one of {list(KEY_TAGS)}")
    xs = _param_series(c, did, tag, start, end)
    ys = physics.second_order(xs, frequency, damping, response, fps=c.doc.fps())
    if mode == "follow":
        out = ys
    else:
        base = _param_series(c, sid, ttag, start, start)[0]
        out = [base + gain * (y - x) for x, y in zip(xs, ys)]
    if frequency > 4:
        c.warnings.append("frequency above 4 Hz aliases at 24 fps; the wobble will read as jitter")
    if ttag in ("rot", "sc", "sx", "sy"):
        _pivot_warning(c, sid)
    n = _bake(c, sid, ttag, [(start + i, v) for i, v in enumerate(out)], 0.05 if ttag in ("rot",) else 0.25)
    return c.commit({"target": sid, "driver": did, "keys": n,
                     "overshoot_pct": round(100 * physics.overshoot_ratio(damping), 1)})


@tool
def camera_follow(scene: str, target: str, start: int, end: int, smooth_time: float = 7.0,
                  axis: str = "x", offset: float = 0.0) -> dict:
    """Follow a layer/peg with the camera using critically damped smoothing (game-camera SmoothDamp).
    smooth_time in frames (7 = 0.3 s); axis x | y | xy; offset px added to the followed position."""
    if axis not in ("x", "y", "xy"):
        raise ValueError("axis must be x, y or xy")
    c = Ctx(scene, "camera_follow", {"target": target, "start": start, "end": end, "axis": axis})
    sid = c.stage_id(target)
    cam = c.doc.active_camera().attrs["id"]
    res = {"camera": cam}
    for a in (("x", "y") if axis == "xy" else (axis,)):
        tgt = [v + offset for v in _param_series(c, sid, a, start, end)]
        cur = physics.smooth_damp(tgt, smooth_time)
        res[f"{a}_keys"] = _bake(c, cam, a, [(start + i, v) for i, v in enumerate(cur)])
        lo, hi = min(cur) - cur[0], max(cur) - cur[0]
        w = _plate_check(c, min(0, lo) if a == "x" else 0, min(0, lo) if a == "y" else 0,
                         max(0, hi) if a == "x" else 0, max(0, hi) if a == "y" else 0)
        if w:
            c.warnings.append(w)
    return c.commit(res)


@tool
def blink(scene: str, layer: str, start: int, open_drawing: int, closed_drawing: int,
          half_drawing: int | None = None, pattern: str = "standard") -> dict:
    """Insert a blink into a layer's xsheet at `start` (switches the layer to manual timing).
    pattern: standard (H C C H) | quick | anime_twos | physiological | sleepy | double.
    Drawings: the eye open/half/closed drawing numbers on that layer."""
    charts = physics.recipes()["blink"]
    if pattern not in charts or not isinstance(charts[pattern], list):
        raise ValueError(f"pattern must be one of {[k for k, v in charts.items() if isinstance(v, list)]}")
    c = Ctx(scene, "blink", {"layer": layer, "start": start, "pattern": pattern})
    name = _safe(layer)
    L = c.layer(name)
    ids = {"O": open_drawing, "C": closed_drawing, "H": half_drawing or closed_drawing}
    for v in ids.values():
        if str(v) not in L["drawings"]:
            raise ValueError(f"drawing {v} not on layer '{name}'")
    ci = c.col_index(name)
    cells = [int(x.fid) % BOIL_BASE if x and x.fid.isdigit() else 0 for x in c.doc.column_cells(ci)] if ci is not None else []
    cells += [open_drawing] * max(0, c.st["frames"] - len(cells))
    seq = physics.blink_cells(pattern, ids)
    for i, d in enumerate(seq):
        if start - 1 + i < len(cells):
            cells[start - 1 + i] = d
    L["timing"] = "manual"
    _apply_cells(c, name, _boil_cycle(cells) if L.get("boil") else cells)
    return c.commit({"layer": name, "frames": list(range(start, start + len(seq))), "cells": seq})


@tool
def lip_sync(scene: str, layer: str, text: str, start: int, part: str, x: float, y: float,
             scale: float = 1.0, chart: str = "anime3", frames_per_syllable: int = 4) -> dict:
    """Animate a mouth from text (no audio): each mouth shape is a VARIANT of a library part
    (anime3: closed/half/open; blair: AI E O U WQ MBP FV L etc rest). Draws one drawing per shape on
    the layer and writes the xsheet from `start` (2-frame minimum holds). Shift `start` 2 frames
    earlier than the audio (mouths lead sound)."""
    if chart not in ("anime3", "blair"):
        raise ValueError("chart must be anime3 or blair")
    p = assets.load_part(part)
    shapes_seq = physics.text_to_mouth(text, frames_per_syllable, chart)
    need = sorted(set(shapes_seq))
    missing = [v for v in need if v not in p.get("variants", {}) and v != "default"]
    if missing:
        raise ValueError(f"part '{part}' lacks mouth variants {missing} (has {list(p.get('variants', {}))})")
    c = Ctx(scene, "lip_sync", {"layer": layer, "text": text[:80], "start": start, "part": part})
    name = _safe(layer)
    L = c.layer(name)
    base = max([int(k) for k in L["drawings"]] + [0])
    ids = {}
    for i, v in enumerate(need):
        did = base + 1 + i
        _check_drawing_id(did)
        _write_drawing(c, name, did, [{"type": "part", "name": part, "variant": v, "x": x, "y": y, "scale": scale}])
        ids[v] = did
    ci = c.col_index(name)
    cells = [int(q.fid) % BOIL_BASE if q and q.fid.isdigit() else 0 for q in c.doc.column_cells(ci)] if ci is not None else []
    end = start - 1 + len(shapes_seq)
    cells += [0] * max(0, end - len(cells))
    for i, v in enumerate(shapes_seq):
        cells[start - 1 + i] = ids[v]
    L["timing"] = "manual"
    _apply_cells(c, name, cells)
    if end > c.st["frames"]:
        c.st["frames"] = end
    return c.commit({"layer": name, "frames": [start, end], "shapes": {v: ids[v] for v in need}})


# ====================================================================== tools: anatomy / character rigs
@tool
def anatomy_reference(person: str | None = None, age: float | str | None = None, sex: str | None = None,
                      height_px: float | None = None, px_per_m: float = 340.0) -> dict:
    """Body proportions for drawing a person, from anthropometric data (Drillis-Contini segment
    lengths, de Leva masses, CDC/NHANES stature, growth-standard head ratios).
    person: baby | toddler | boy | girl | child | teen | teen_boy | teen_girl | young_man | young_woman |
    man | woman | old_man | old_woman (or give age years / label and sex). Returns heights from the floor,
    segment lengths, front widths, side depths (px), heads tall, mass fractions, centre of mass, notes.
    Scale: height_px, or px_per_m (340 = 1.75 m adult about 600 px; keep one px_per_m per scene)."""
    return anatomy.reference(age, sex, person, px_per_m=px_per_m, height_px=height_px)


@tool
def build_character(scene: str, name: str, person: str | None = None, age: float | str | None = None,
                    sex: str | None = None, x: float | None = None, ground_y: float | None = None,
                    px_per_m: float = 340.0, facing: str = "right", hair: str = "short",
                    colors: dict | None = None, style: str | None = None) -> dict:
    """Build a side-view cut-out character rig with real proportions for the age and sex.
    Creates a root peg '<name>' at the hips and 16 vector layers '<name>_<part>' (torso, head, upper arm,
    forearm, hand, thigh, shin, foot, toe; far side darker and behind), parented like a skeleton with
    each pivot on its joint. Animate it with walk_cycle / run_cycle, or key the parts directly
    (rotation on a part turns it about its joint). x: hip x px (default centre); ground_y: floor line px
    (default 90% of the frame). hair: short | long | bob | ponytail | bun | none.
    colors: {skin, hair, top, bottom, shoes, line}. Every layer stays an editable OpenToonz vector level."""
    if facing not in ("right", "left"):
        raise ValueError("facing must be right or left")
    if hair not in ("short", "long", "bob", "ponytail", "bun", "none"):
        raise ValueError("hair must be short, long, bob, ponytail, bun or none")
    if style and style not in styles.STYLES:
        raise ValueError(f"style must be one of {list(styles.STYLES)}")
    if px_per_m <= 0:
        raise ValueError("px_per_m must be > 0")
    pr = anatomy.proportions(age, sex, person)
    c = Ctx(scene, "build_character", {"name": name, "person": person, "age": pr["age"], "sex": pr["sex"],
                                       "facing": facing})
    rig = _safe(name)
    rigs = c.st.setdefault("rigs", {})
    if rig.lower() in (r.lower() for r in rigs):
        raise ValueError(f"character '{rig}' already exists")
    W, H = c.doc.camera_res()
    dpi = c.doc.dpi()
    hpx = pr["stature_m"] * px_per_m
    x0 = float(x if x is not None else W / 2)
    gy = float(ground_y if ground_y is not None else 0.9 * H)
    if hpx > gy:
        c.warnings.append(f"the character ({hpx:.0f} px tall) does not fit above ground_y={gy:.0f}; "
                          f"lower px_per_m or move the ground down")
    joints = anatomy.rest_joints(pr, hpx, x0, gy)
    shapes = anatomy.part_shapes(pr, joints, hpx, colors, hair)
    if facing == "left":
        shapes = {k: anatomy.mirror_shapes(v, x0) for k, v in shapes.items()}

    def jpos(j):
        px, py = joints[j]
        return (2 * x0 - px if facing == "left" else px), py

    layers = {}
    for part in anatomy.PARTS:  # back to front: later columns draw on top
        lname = f"{rig}_{part}"
        if any(n.lower() == lname.lower() for n in c.st["layers"]):
            raise ValueError(f"layer '{lname}' already exists")
        c.st["layers"][lname] = {"mode": "vector", "style": style, "drawings": {}, "timing": "auto", "boil": False,
                                 "level_path": _level_path(c.st, lname, "vector"), "dirty": False}
        old = c.doc.level_by_path(_level_path(c.st, lname, "vector"))
        lid = old["id"] if old else c.doc.add_level(lname, _level_path(c.st, lname, "vector"), None)
        layers[part] = f"Col{c.doc.add_column(lid, lname) + 1}"
    root = c.doc.add_peg(rig, "Table")
    hx, hy = jpos("hip")
    c.doc.set_pivot(root, (hx - W / 2) / dpi, -(hy - H / 2) / dpi)
    for part, (parent, joint) in anatomy.RIG_PARENTS.items():
        sid = layers[part]
        c.doc.set_parent(sid, root if parent == "root" else layers[parent])
        px, py = jpos(joint)
        c.doc.set_pivot(sid, (px - W / 2) / dpi, -(py - H / 2) / dpi)
    for part in anatomy.PARTS:
        lname = f"{rig}_{part}"
        _write_drawing(c, lname, 1, shapes[part])
        _retime(c, lname)
    rigs[rig] = {"age": pr["age"], "sex": pr["sex"], "px_per_m": px_per_m, "height_px": hpx, "facing": facing,
                 "x": x0, "ground_y": gy, "root": rig, "joints": {k: list(v) for k, v in joints.items()},
                 "parts": {p: f"{rig}_{p}" for p in anatomy.PARTS}}
    ref = anatomy.reference(pr["age"], pr["sex"], height_px=hpx)
    return c.commit({"character": rig, "root_peg": root, "layers": [f"{rig}_{p}" for p in anatomy.PARTS],
                     "height_px": round(hpx, 1), "heads_tall": ref["heads_tall"], "stature_m": ref["stature_m"],
                     "age": pr["age"], "sex": ref["sex"], "notes": ref["notes"]})


def _rig(c: Ctx, character: str) -> dict:
    rigs = c.st.get("rigs", {})
    for k, v in rigs.items():
        if k.lower() == _safe(character).lower():
            return v
    raise ValueError(f"character '{character}' not found. Characters: {list(rigs)}")


def _gait_tool(scene: str, character: str, kind: str, start: int, end: int | None, cycles: float | None,
               speed: float | None, cadence: float | None, travel: bool, on: int, bounce: float,
               arm_swing: float, lean: float, phase: float) -> dict:
    if on not in (1, 2):
        raise ValueError("on must be 1 (ones) or 2 (twos)")
    if start < 1:
        raise ValueError("start must be >= 1")
    if speed is not None and speed <= 0 or cadence is not None and cadence <= 0:
        raise ValueError("speed and cadence must be > 0")
    c = Ctx(scene, f"{kind}_cycle", {"character": character, "start": start, "end": end, "cycles": cycles,
                                     "speed": speed, "cadence": cadence, "travel": travel, "on": on})
    rg = _rig(c, character)
    n = gait.norms(kind, rg["age"], rg["sex"])
    if speed is not None:
        if cadence is None:  # step length and cadence both grow with speed (about as its square root)
            n["cadence"] *= (speed / n["speed"]) ** 0.5
        n["speed"] = float(speed)
    if cadence is not None:
        n["cadence"] = float(cadence)
    fps = c.doc.fps()
    T = 2 * 60.0 / n["cadence"] * fps
    if end is None:
        end = start + max(1, round((cycles or 2) * T)) - 1
    if end <= start:
        raise ValueError("end must be after start")
    frames = end - start + 1
    joints = {k: tuple(v) for k, v in rg["joints"].items()}
    res = gait.cycle(kind, joints, n, rg["height_px"], rg["px_per_m"], fps, rg["ground_y"], frames,
                     travel=travel, bounce=bounce, arm_swing=arm_swing, lean_add=lean, phase=phase)
    flip = -1.0 if rg["facing"] == "left" else 1.0
    root = c.stage_id(rg["root"])
    cols = {p: c.stage_id(lname) for p, lname in rg["parts"].items()}
    step = on
    keyed = [i for i in range(0, frames, step)]

    def write(sid, tag, vals, tol):
        if step == 1:
            return _bake(c, sid, tag, [(start + i, vals[i]) for i in range(frames)], tol)
        _set_keys(c, sid, tag, [{"frame": start + i, "value": vals[i], "interp": "constant"} for i in keyed],
                  replace_range=(start, end))
        return len(keyed)

    bx = _param_series(c, root, "x", start, start)[0]
    by = _param_series(c, root, "y", start, start)[0]
    nkeys = write(root, "x", [bx + flip * f["root"][0] for f in res["frames"]], 0.25)
    nkeys += write(root, "y", [by + f["root"][1] for f in res["frames"]], 0.25)
    for part, sid in cols.items():
        nkeys += write(sid, "rot", [flip * f["angles"][part] for f in res["frames"]], 0.05)
    # double check: forward kinematics from the keys OpenToonz will actually evaluate
    rep = res["report"]
    pan = rep["background_pan_px_per_frame"]
    rep["baked_slip_px"] = _baked_slip(c, rg, res, cols, root, start, end, flip, bx, by, pan) if step == 1 else None
    if end > c.st["frames"]:
        c.warnings.append(f"the {kind} runs to frame {end}, past the scene end {c.st['frames']}; "
                          f"extend with set_scene_settings")
    if not rep["legs_reached"]:
        c.warnings.append("a leg could not reach its foot target on some frames; lower speed or raise cadence")
    if not travel:
        c.warnings.append(f"walking in place: pan the background {rep['background_pan_px_per_frame']} px/frame "
                          f"{'left' if flip > 0 else 'right'} (camera_move or keys on the BG peg)")
    rep["frames_baked"] = rep.pop("frames")
    return c.commit({**rep, "character": rg["root"], "frames": [start, end], "keys": nkeys, "on": on})


def _baked_slip(c, rg, res, cols, root, start, end, flip, bx, by, pan=0.0) -> float:
    """Largest movement (px) of a planted foot point, from the baked keys (in place: relative to the
    background moving at `pan` px/frame)."""
    j = {k: tuple(v) for k, v in rg["joints"].items()}
    l1, l2 = math.dist(j["hip"], j["knee"]), math.dist(j["knee"], j["ankle"])
    ser = {p: _param_series(c, cols[p], "rot", start, end) for p in cols}
    rx = _param_series(c, root, "x", start, end)
    ry = _param_series(c, root, "y", start, end)
    foot = gait.Foot(*[(j[k][0] - j["ankle"][0], j[k][1] - j["ankle"][1]) for k in ("heel", "ball", "toe")])
    worst = 0.0
    for side in ("near", "far"):
        prev = None
        for i, f in enumerate(res["frames"]):
            hip = (j["hip"][0] + flip * (rx[i] - bx + pan * i), j["hip"][1] + (ry[i] - by))
            th = flip * ser[f"leg_{side}_thigh"][i]
            sh = th + flip * ser[f"leg_{side}_shin"][i]
            pt = sh + flip * ser[f"leg_{side}_foot"][i]
            knee = (hip[0] + gait._rot((0.0, l1), th)[0], hip[1] + gait._rot((0.0, l1), th)[1])
            ank = (knee[0] + gait._rot((0.0, l2), sh)[0], knee[1] + gait._rot((0.0, l2), sh)[1])
            planted = f["world"].get(f"{side}_planted")
            if planted:
                piv = foot.point(planted, ank, pt) if planted != "toe_tip" else foot.toe_tip(
                    ank, pt, pt + flip * ser[f"leg_{side}_toe"][i])
                if prev and prev[0] == f["world"][f"{side}_step"] and prev[1] == planted:
                    worst = max(worst, math.dist(piv, prev[2]))
                prev = (f["world"][f"{side}_step"], planted, piv)
            else:
                prev = None
    return round(worst, 3)


@tool
def walk_cycle(scene: str, character: str, start: int = 1, end: int | None = None, cycles: float | None = None,
               speed: float | None = None, cadence: float | None = None, travel: bool = True, on: int = 1,
               bounce: float = 1.0, arm_swing: float = 1.0, lean: float = 0.0, phase: float = 0.0) -> dict:
    """Walk a build_character rig with biomechanically correct timing for its age and sex.
    Feet stay planted and roll heel -> flat -> ball -> toe; legs use two-bone IK; the hips ride an
    inverted-pendulum arc (low at contact, high at passing); arms swing opposite the legs.
    Defaults come from gait studies (e.g. adult man 108 steps/min at 1.4 m/s; toddler 154 at 0.86;
    old age shorter steps). speed m/s, cadence steps/min override them. cycles (default 2) or end.
    travel=false walks in place (pan the BG). on=2 holds every pose 2 frames (keys become constant).
    bounce, arm_swing scale the motion (1 = natural; >1 = cartoon); lean adds forward trunk lean (deg).
    Returns the self-check: cadence, step length, vertical oscillation, knee flexion, foot slip."""
    return _gait_tool(scene, character, "walk", start, end, cycles, speed, cadence, travel, on, bounce,
                      arm_swing, lean, phase)


@tool
def run_cycle(scene: str, character: str, start: int = 1, end: int | None = None, cycles: float | None = None,
              speed: float | None = None, cadence: float | None = None, travel: bool = True, on: int = 1,
              bounce: float = 1.0, arm_swing: float = 1.0, lean: float = 0.0, phase: float = 0.0) -> dict:
    """Run a build_character rig: spring-mass physics (exact gravity in the flight phase, a spring arc
    in contact, lowest at mid-stance), planted feet, high knee recovery, bent arms. Defaults by age and
    sex from jogging norms (adult man about 166 steps/min at 3.3 m/s). Same options as walk_cycle;
    bounce=1 keeps gravity exact."""
    return _gait_tool(scene, character, "run", start, end, cycles, speed, cadence, travel, on, bounce,
                      arm_swing, lean, phase)


@tool
def compile_vectors(scene: str, force: bool = False) -> dict:
    """Convert pending vector drawings to native OpenToonz .pli levels with pen-pressure lines.
    Runs automatically before preview/render/open. force=true also overwrites GUI-edited levels."""
    st = _load_state(scene)
    res = _compile(st, force)
    _journal(st["name"], "compile_vectors", {"force": force}, res)
    return res


def _prepare(scene: str) -> tuple[Ctx, dict]:
    st = _load_state(scene)
    comp = _compile(st)
    if comp.get("failed"):
        raise RuntimeError(f"OpenToonz could not build vector levels {comp['failed']}; nothing rendered. "
                           f"Check the drawings on those layers (validate_scene).")
    c = Ctx(scene, "render_prepare", {}, mutate=False)
    problems = c.doc.verify(_level_file_exists)
    used = set()
    for ci in range(len(c.doc.columns())):
        used |= c.doc.column_level_ids(ci)
    missing = [p for p in problems if "file missing" in p and any(
        f"'{lv['name']}'" in p for lv in c.doc.levels() if lv["id"] in used)]
    if missing:
        raise RuntimeError(f"cannot render, exposed levels are missing on disk: {missing}")
    c.warnings += [p for p in problems if p not in missing]
    return c, comp


@tool
def preview_frame(scene: str, frame: int = 1, max_width: int = 960) -> McpImage:
    """Render one frame through OpenToonz and return the image (check your work visually)."""
    c, _ = _prepare(scene)
    if not 1 <= frame <= c.st["frames"]:
        raise ValueError(f"frame must be within 1..{c.st['frames']}")
    out = TMP / f"prev_{c.name}_{uuid.uuid4().hex}" / "p..png"
    _tcomposer(c.sf, out, (frame, frame))
    f = next(out.parent.glob("p.*.png"))
    img = _flat(f, _bg(c))
    if img.width > max_width:
        img = img.resize((max_width, round(img.height * max_width / img.width)))
    p = out.parent / "preview.png"
    img.save(p)
    data = p.read_bytes()
    shutil.rmtree(out.parent, ignore_errors=True)
    _journal(c.name, "preview_frame", {"frame": frame}, "ok")
    return McpImage(data=data, format="png")


@tool
def contact_sheet(scene: str, frames: list[int] | None = None, thumb_width: int = 480) -> McpImage:
    """Render several frames into one labelled grid (default 6 spread over the scene) to review motion."""
    from PIL import Image, ImageDraw
    c, _ = _prepare(scene)
    n = c.st["frames"]
    frames = sorted(set(frames or [max(1, round(1 + i * (n - 1) / 5)) for i in range(6)]))
    bad = [f for f in frames if not 1 <= f <= n]
    if bad:
        raise ValueError(f"frames {bad} are outside the scene (1..{n})")
    tmp = TMP / f"cs_{c.name}_{uuid.uuid4().hex}"
    _tcomposer(c.sf, tmp / "c..png", (min(frames), max(frames)))
    W, H = c.doc.camera_res()
    th = round(thumb_width * H / W)
    cols = 3
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * thumb_width, rows * th), "#808080")
    dr = ImageDraw.Draw(sheet)
    for i, f in enumerate(frames):
        p = tmp / f"c.{f:04d}.png"
        if p.exists():
            sheet.paste(_flat(p, _bg(c)).resize((thumb_width - 4, th - 4)), ((i % cols) * thumb_width + 2,
                                                                            (i // cols) * th + 2))
        dr.rectangle([(i % cols) * thumb_width + 2, (i // cols) * th + 2, (i % cols) * thumb_width + 46,
                      (i // cols) * th + 20], fill="black")
        dr.text(((i % cols) * thumb_width + 6, (i // cols) * th + 5), f"f{f}", fill="white")
    out = tmp / "sheet.png"
    sheet.save(out)
    data = out.read_bytes()
    shutil.rmtree(tmp, ignore_errors=True)
    return McpImage(data=data, format="png")


@tool
def render(scene: str, format: str = "mp4", start: int | None = None, end: int | None = None,
           shrink: int = 1) -> dict:
    """Render with OpenToonz (tcomposer), then double-check: frame count, blank frames, flashing.
    format: mp4 | gif | png (sequence). shrink 2 = half size (faster drafts)."""
    if format not in ("mp4", "gif", "png"):
        raise ValueError("format must be mp4, gif or png")
    c, comp = _prepare(scene)
    start, end = start or 1, end or c.st["frames"]
    if not 1 <= start <= end <= c.st["frames"]:
        raise ValueError(f"need 1 <= start <= end <= {c.st['frames']}")
    frames_dir = RENDER_DIR / c.name / "frames"
    shutil.rmtree(frames_dir, ignore_errors=True)
    t0 = time.time()
    _tcomposer(c.sf, frames_dir / f"{c.name}..png", (start, end), shrink)
    files = sorted(frames_dir.glob(f"{c.name}.*.png"))
    res: dict[str, Any] = {"frames_dir": str(frames_dir), "frames": len(files), "seconds": round(time.time() - t0, 1)}
    if comp.get("compiled"):
        res["compiled"] = comp["compiled"]
    issues = []
    if len(files) != end - start + 1:
        issues.append(f"expected {end - start + 1} frames, got {len(files)}")
    issues += _check_frames(files, c.doc.fps(), _bg(c), start)
    if format != "png":
        flat = frames_dir / "_flat"
        flat.mkdir()
        for f in files:
            _flat(f, _bg(c)).save(flat / f.name)
        out = RENDER_DIR / c.name / f"{c.name}.{format}"
        base = [_ffmpeg(), "-y", "-loglevel", "error", "-framerate", str(c.doc.fps()), "-start_number", str(start),
                "-i", str(flat / f"{c.name}.%04d.png")]
        if format == "mp4":
            cmd = base + ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "17", "-preset", "slow",
                          "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", str(out)]
        elif format == "gif":
            cmd = base + ["-vf", "scale='min(800,iw)':-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
                          str(out)]
        else:
            raise ValueError("format must be mp4, gif or png")
        r = subprocess.run(cmd, capture_output=True, text=True)
        shutil.rmtree(flat, ignore_errors=True)
        if r.returncode or not out.exists() or out.stat().st_size == 0:
            raise RuntimeError(f"ffmpeg failed: {r.stderr[-1500:]}")
        res["output"] = str(out)
        res["bytes"] = out.stat().st_size
    res["verify"] = issues or "ok"
    learn.record_issues("quality", issues, c.name)
    if c.warnings:
        res["warnings"] = c.warnings
    res["next"] = "Review with contact_sheet; record what worked or failed with learn_lesson / ask user for feedback."
    _journal(c.name, "render", {"format": format, "start": start, "end": end}, res)
    return res


@tool
def open_in_opentoonz(scene: str) -> dict:
    """Open the scene in the OpenToonz GUI for hand editing (vector levels compiled first).
    After editing: SAVE in OpenToonz, then the MCP picks up your changes automatically."""
    c, comp = _prepare(scene)
    running = _opentoonz_running()
    subprocess.Popen([str(config.exe("OpenToonz")), str(c.sf)], cwd=OT_HOME,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    res = {"opened": str(c.sf), "compiled": comp.get("compiled", [])}
    if running:
        res["note"] = "OpenToonz was already running; a second window may open. Use File > Revert Scene to reload."
    _journal(c.name, "open_in_opentoonz", {}, res)
    return res


@tool
def validate_scene(scene: str, render_test: bool = True) -> dict:
    """Double-check a scene: structure, missing files, layer/column mapping, timing gaps, studio rule
    warnings (holds, pan speed), and optionally a 1-frame test render."""
    c, comp = _prepare(scene)
    issues = list(c.warnings)
    for lname, L in c.st["layers"].items():
        ci = c.col_index(lname)
        if ci is None:
            issues.append(f"layer '{lname}' has no column in the scene")
            continue
        cells = c.doc.column_cells(ci)
        if not any(cells):
            issues.append(f"layer '{lname}' is never exposed (empty xsheet column)")
        sty = styles.get(L.get("style") or c.st["style"])
        # short holds on keys (< 6 frames is below studio minimum unless animating on ones)
        runs, i = [], 0
        ids = [x.fid if x else None for x in cells]
        while i < len(ids):
            j = i
            while j < len(ids) and ids[j] == ids[i]:
                j += 1
            runs.append((ids[i], j - i))
            i = j
        if L["drawings"] and len(L["drawings"]) > 1:
            singles = sum(1 for d, n in runs if d and n == 1)
            if singles > 6 and sty["exposure"] > 1:
                issues.append(f"layer '{lname}': {singles} drawings on ones; style '{L.get('style') or c.st['style']}'"
                              f" usually animates on {sty['exposure']}s")
    test = None
    if render_test:
        try:
            out = TMP / f"val_{c.name}_{int(time.time() * 1000)}" / "v..png"
            mid = max(1, c.st["frames"] // 2)
            _tcomposer(c.sf, out, (mid, mid))
            files = list(out.parent.glob("v.*.png"))
            test = "ok" if files else "no output"
            issues += _check_frames(files, c.doc.fps(), _bg(c))
            shutil.rmtree(out.parent, ignore_errors=True)
        except Exception as e:
            test = f"FAILED: {e}"
    res = {"issues": issues or "none", "test_render": test, "compiled": comp.get("compiled", [])}
    learn.record_issues("quality", [i for i in issues if "GUI is running" not in i], c.name)
    _journal(c.name, "validate_scene", {}, res)
    return res


# ====================================================================== tools: self-improvement
@tool
def learn_lesson(lesson: str, tags: list[str] | None = None, scene: str | None = None) -> dict:
    """Record an insight from reviewing your own output (e.g. 'squash 0.8 looks rubbery; 0.4 reads better
    for a heavy ball'). Lessons are shown in every later scene_context so future work improves."""
    e = learn.add("insight", lesson, _safe(scene) if scene else None, tags)
    return {"saved": True, "times_seen": e["count"]}


@tool
def feedback(scene: str, rating: int, comment: str = "") -> dict:
    """Store the USER's rating (1-5) and comment about a result. Ranked highest in lessons/preferences.
    Ask the user for this after showing a render."""
    name = _load_state(scene)["name"]
    learn.add("preference", f"[{rating}/5] {comment}".strip(), name, ["feedback"])
    _journal(name, "feedback", {"rating": rating, "comment": comment[:300]}, "ok")
    return {"saved": True, "hint": "If the comment implies a default change, call tune_style."}


@tool
def tune_style(style: str, changes: dict, reason: str) -> dict:
    """Permanently adjust a style preset's defaults (learned from feedback). Keys: line, interior, wobble,
    wavelength, taper_start, taper_end, min_ratio, pressure_var, loop_var, exposure, squash [lo,hi],
    shadow, overshoot_ends. Applies to all future drawings using that style."""
    if style not in styles.STYLES:
        raise ValueError(f"style must be one of {list(styles.STYLES)}")
    bad = [k for k in changes if k not in styles.STYLES[style]]
    if bad:
        raise ValueError(f"unknown style keys {bad}; valid: {list(styles.STYLES[style])}")
    return {"style": style, "overrides": learn.tune_style(style, changes, reason), "now": styles.get(style)}


@tool
def knowledge(query: str | None = None, limit: int = 20) -> list[dict]:
    """Search accumulated lessons (errors seen, quality issues, insights, user preferences) with advice."""
    return learn.top(limit, query)


@tool
def list_scenes() -> list[str]:
    """Scenes known to the MCP."""
    return _list()


# ====================================================================== tools: studio looks (fx)
@tool
def list_looks() -> dict:
    """Studio looks apply_look can build from real OpenToonz fx, with what each does."""
    return {k: {"about": v["about"], "whole_frame": [r for _, _, r in v["post"]],
                "per_layer": [r for _, _, r in v["layer"]]} for k, v in fxlib.LOOKS.items()}


@tool
def apply_look(scene: str, look: str, layers: list[str] | None = None, strength: float = 1.0,
               light_angle: float = 135.0) -> dict:
    """Give the scene a studio look made of real OpenToonz fx nodes (open the FX Schematic to tweak them).
    look: ghibli | klaus | spider_verse | comic | anime_satsuei | painterly | cartoon_saloon (list_looks).
    Whole-frame fx go just before the output; per-layer fx (e.g. Klaus form shadow and rim light) go on
    `layers` (default: every layer except ones named like bg/background/sky). strength scales the
    amounts (0.5 subtle, 1 default, 1.5 strong). light_angle: degrees, 90 = light from above,
    135 = top-left. Re-applying replaces the previous look; fx you made by hand are kept."""
    if look not in fxlib.LOOKS:
        raise ValueError(f"look must be one of {list(fxlib.LOOKS)}")
    if not 0 < strength <= 3:
        raise ValueError("strength must be in (0, 3]")
    c = Ctx(scene, "apply_look", {"look": look, "layers": layers, "strength": strength,
                                  "light_angle": light_angle})
    spec = fxlib.LOOKS[look]
    removed = fxlib.remove(c.doc)
    dpi = c.doc.dpi()
    made = fxlib.apply_post(c.doc, look, spec["post"], dpi, light_angle, strength) if spec["post"] else []
    targets = []
    if spec["layer"]:
        names = [_safe(n) for n in layers] if layers else [
            n for n in c.st["layers"] if not re.search(r"(^|_)(bg|background|sky|backdrop)($|_)", n.lower())]
        fx_ids = c.doc._column_fx_ids()
        for n in names:
            c.layer(n)
            ci = c.col_index(n)
            if ci is not None and fx_ids[ci]:
                targets.append(fx_ids[ci])
        made += fxlib.apply_layer(c.doc, look, spec["layer"], targets, dpi, light_angle, strength)
    c.st["look"] = {"name": look, "strength": strength, "light_angle": light_angle}
    return c.commit({"look": look, "fx": made, "layers_lit": len(targets), "replaced_nodes": removed,
                     "about": spec["about"]})


@tool
def remove_look(scene: str) -> dict:
    """Remove the fx apply_look added (fx you made in OpenToonz stay)."""
    c = Ctx(scene, "remove_look", {})
    n = fxlib.remove(c.doc)
    c.st.pop("look", None)
    return c.commit({"removed_nodes": n})


def main() -> None:
    for d in (STATE_DIR, RENDER_DIR, SNAP_DIR, JOURNAL_DIR, NOTES_DIR, SVG_DIR, TMP):
        d.mkdir(parents=True, exist_ok=True)
    mcp.run()


if __name__ == "__main__":
    main()

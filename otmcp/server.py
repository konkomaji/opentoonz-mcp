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

import datetime as _dt
import functools
import glob
import json
import math
import os
import random
import re
import shutil
import subprocess
import time
import zlib
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP, Image as McpImage

from . import curve, draw as drawlib, learn, pli, style as styles, tnz, vec
from .scene import Cell, SceneDoc, file_hash

OT_HOME = Path(os.environ.get("OPENTOONZ_HOME", r"D:\OpenToonz"))
OT_STUFF = Path(os.environ.get("OPENTOONZ_STUFF", r"D:\OpenToonz stuff"))
PROJECT = OT_STUFF / "sandbox"
WORK = Path(os.environ.get("OTMCP_WORK", r"D:\otmcp\work"))
DOCS = Path(__file__).resolve().parent.parent / "docs"
STATE_DIR, RENDER_DIR, SNAP_DIR = WORK / "state", WORK / "renders", WORK / "snapshots"
JOURNAL_DIR, NOTES_DIR, SVG_DIR, TMP = WORK / "journal", WORK / "notes", WORK / "svg", WORK / "tmp"
MAX_SNAPSHOTS = 60
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
    tmp = _state_path(st["name"]).with_suffix(".tmp")
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
    return f"+drawings/{st['name']}/{layer}.pli" if mode == "vector" else f"+drawings/{st['name']}/{layer}..png"


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


def _snapshot(st: dict, label: str, extra_files: list[Path] = ()) -> None:
    d = SNAP_DIR / st["name"] / f"{time.strftime('%Y%m%d-%H%M%S')}_{int(time.time() * 1000) % 1000:03d}_{label}"
    d.mkdir(parents=True, exist_ok=True)
    sf = _scene_file(st["name"])
    if sf.exists():
        shutil.copy2(sf, d / "scene.tnz")
    if _state_path(st["name"]).exists():
        shutil.copy2(_state_path(st["name"]), d / "state.json")
    for f in extra_files:
        if Path(f).exists():
            (d / "files").mkdir(exist_ok=True)
            shutil.copy2(f, d / "files" / Path(f).name)
            (d / "files" / (Path(f).name + ".origin")).write_text(str(f), encoding="utf-8")
    snaps = sorted((SNAP_DIR / st["name"]).iterdir())
    for old in snaps[:-MAX_SNAPSHOTS]:
        shutil.rmtree(old, ignore_errors=True)


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
            if L.get("mode") == "vector":
                ph = file_hash(_pli_file(self.st, lname))
                if L.get("pli_hash") and ph and ph != L["pli_hash"]:
                    if not L.get("gui_edited"):
                        self.warnings.append(f"layer '{lname}': drawings were edited in OpenToonz (vector .pli). "
                                             f"MCP will not overwrite them unless force=true.")
                    L["gui_edited"] = True

    def _cells_sig(self, ci: int) -> str:
        return ",".join(f"{c.level_id}:{c.fid}" if c else "-" for c in self.doc.column_cells(ci))

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
            return self.doc.add_level(layer, L["level_path"], None if L["mode"] == "vector" else self.doc.dpi())
        return lv["id"]

    # ---- commit
    def commit(self, result: dict) -> dict:
        if self.mutate:
            _snapshot(self.st, self.tool)
            self.doc.set_output_range(self.st["frames"])
            text = self.doc.text()
            tmp = self.sf.with_suffix(".tnz.tmp")
            tmp.write_text(text, encoding="utf-8")
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
            _save_state(self.st)
            pending = {f"+drawings/{self.name}/{n}.pli" for n, L in self.st["layers"].items()
                       if L["mode"] == "vector"}
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
    """Register an MCP tool with uniform error reporting."""
    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:  # surface as structured error the model can act on
            scene = k.get("scene") or (a[0] if a else None)
            sname = _safe(scene) if isinstance(scene, str) and re.search(r"\w", scene) else None
            if sname and _state_path(sname).exists():
                _journal(sname, fn.__name__ + ":error", k, str(e))
            learn.add("error", f"{fn.__name__}: {e}", sname, tags=[fn.__name__], advice=learn.advice_for(str(e)))
            raise RuntimeError(f"{type(e).__name__}: {e}") from e
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
    if L.get("boil"):
        out, run = [], 0
        for i, d in enumerate(cells):
            run = run + 1 if i and cells[i - 1] == d else 0
            v = (run // 2) % 3
            out.append(d + BOIL_BASE * v if d and v else d)
        cells = out
    return cells


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
    if L["mode"] == "vector" and L.get("gui_edited") and not force:
        raise RuntimeError(f"layer '{layer}' drawings were edited in OpenToonz; redrawing would discard those "
                           f"edits. Pass force=true to overwrite, or draw on a new layer.")
    W, H = c.doc.camera_res()
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
            vec.to_png(els, W, H).save(f)
    L["drawings"][str(did)] = shapes
    L["dirty"] = True


# ====================================================================== compile (vector) / render
def _run_script(lines: list[str], tag: str) -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    sp = TMP / f"{tag}_{int(time.time() * 1000)}.toonzscript"
    sp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    subprocess.run([str(OT_HOME / "OpenToonz.exe"), str(sp)], cwd=OT_HOME, capture_output=True, timeout=600)
    sp.unlink(missing_ok=True)


def _compile(st: dict, force: bool = False, only: list[str] | None = None) -> dict:
    todo, skipped = [], []
    for lname, L in st["layers"].items():
        if L["mode"] != "vector" or (only and lname not in only):
            continue
        pf = _pli_file(st, lname)
        if not (L.get("dirty") or not pf.exists() or force):
            continue
        if L.get("gui_edited") and not force:
            skipped.append(lname)
            continue
        valid = {int(d) + BOIL_BASE * v for d in L["drawings"] for v in ((0, 1, 2) if L.get("boil") else (0,))}
        for f in (SVG_DIR / st["name"] / lname).glob(f"{lname}.*"):
            m = re.match(rf"{re.escape(lname)}\.(\d+)\.(svg|prof\.json)$", f.name)
            if m and int(m.group(1)) not in valid:
                f.unlink()
        svgs = sorted((SVG_DIR / st["name"] / lname).glob(f"{lname}.*.svg"))
        if svgs:
            todo.append((lname, L, pf))
    if not todo:
        return {"compiled": [], "skipped_gui_edited": skipped}
    _snapshot(st, "compile", [p for _, _, p in todo])
    lines = []
    for lname, L, pf in todo:
        pf.parent.mkdir(parents=True, exist_ok=True)
        src = (SVG_DIR / st["name"] / lname / f"{lname}..svg").as_posix()
        lines += [f'l_{lname} = new Level()', f'l_{lname}.load("{src}")', f'l_{lname}.save("{pf.as_posix()}")']
    t0 = time.time()
    for _, _, pf in todo:
        pf.unlink(missing_ok=True)
    _run_script(lines, "compile")
    done, failed = [], []
    for lname, L, pf in todo:
        # OpenToonz also drops a 'name..pli' twin next to the target; remove it
        twin = pf.with_name(pf.stem + "..pli")
        if twin.exists():
            twin.unlink()
        if not pf.exists():
            failed.append(lname)
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
    _save_state(st)
    res = {"compiled": done, "seconds": round(time.time() - t0, 1)}
    if failed:
        res["failed"] = failed
    if skipped:
        res["skipped_gui_edited"] = skipped
    return res


def _tcomposer(scene_file: Path, out_pattern: Path, frame_range: tuple[int, int] | None = None,
               shrink: int = 1) -> str:
    out_pattern.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(OT_HOME / "tcomposer.exe"), str(scene_file), "-o", str(out_pattern), "-nthreads", "all"]
    if frame_range:
        cmd += ["-range", str(frame_range[0]), str(frame_range[1])]
    if shrink > 1:
        cmd += ["-shrink", str(shrink)]
    TMP.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TEMP=str(TMP), TMP=str(TMP))
    r = subprocess.run(cmd, cwd=OT_HOME, capture_output=True, text=True, errors="replace", env=env, timeout=3600)
    log = (r.stdout or "") + (r.stderr or "")
    if "completed" not in log:
        raise RuntimeError(f"tcomposer failed (exit {r.returncode}):\n{log[-2000:]}")
    if "problems loading" in log or "missing" in log.lower():
        raise RuntimeError(f"tcomposer reported missing files:\n{log[-1500:]}")
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


def _flat(img_path: Path, bg) -> "Image":
    from PIL import Image
    im = Image.open(img_path).convert("RGBA")
    base = Image.new("RGBA", im.size, bg)
    base.alpha_composite(im)
    return base.convert("RGB")


def _check_frames(files: list[Path], fps: float, bg) -> list[str]:
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
                    issues.append(f"pop at frame {i + 2}: image jumps {d / max(med, 0.01):.0f}x more than typical")
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
    r = subprocess.run([str(OT_HOME / "tcomposer.exe"), "-version"], cwd=OT_HOME, capture_output=True, text=True)
    return {"version": (r.stdout + r.stderr).strip() or "unknown", "install": str(OT_HOME), "stuff": str(OT_STUFF),
            "project": str(PROJECT), "scenes": _list(), "gui_running": _opentoonz_running(),
            "styles": list(styles.STYLES), "conventions": "pixels, +y down, frames 1-based, scale 1.0=100%"}


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
                       "exposure": _compact([int(x.fid) if x and x.fid.isdigit() else (x.fid if x else 0) for x in cells]),
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
        return json.dumps(styles.STYLES, indent=1)
    p = DOCS / "studio_practices.md"
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    if topic == "rules" and "## Rules to encode" in text:
        return text[text.index("## Rules to encode"):]
    return text[:60000]


@tool
def undo(scene: str, steps: int = 1) -> dict:
    """Restore the scene (and any overwritten vector levels) to the state before the last N MCP writes."""
    name = _load_state(scene)["name"]
    snaps = sorted((SNAP_DIR / name).iterdir()) if (SNAP_DIR / name).exists() else []
    if len(snaps) < steps:
        raise ValueError(f"only {len(snaps)} snapshots available")
    target = snaps[-steps]
    if (target / "scene.tnz").exists():
        shutil.copy2(target / "scene.tnz", _scene_file(name))
    if (target / "state.json").exists():
        shutil.copy2(target / "state.json", _state_path(name))
    restored = []
    if (target / "files").exists():
        for o in (target / "files").glob("*.origin"):
            dst = Path(o.read_text(encoding="utf-8"))
            shutil.copy2(target / "files" / o.name[:-7], dst)
            restored.append(str(dst))
    st = _load_state(name)
    st["tnz_hash"] = file_hash(_scene_file(name))
    for lname, L in st["layers"].items():
        if L["mode"] == "vector":
            L["pli_hash"] = file_hash(_pli_file(st, lname))
            L["dirty"] = True  # svgs may be newer than restored state; recompile on demand
    _save_state(st)
    for s in snaps[-steps:]:
        shutil.rmtree(s, ignore_errors=True)
    _journal(name, "undo", {"steps": steps}, {"restored": target.name})
    return {"restored_to_before": target.name.split("_", 2)[-1], "files": restored}


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
    if _state_path(name).exists():
        _snapshot(_load_state(name), "overwrite")
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
    (JOURNAL_DIR / f"{name}.jsonl").unlink(missing_ok=True)
    _journal(name, "create_scene", {"w": width, "h": height, "fps": fps, "frames": frames, "style": style}, "ok")
    return {"scene": name, "file": str(_scene_file(name)), "style": style,
            "timing_hint": f"{style}: animate on {styles.get(style)['exposure']}s by default"}


@tool
def import_scene(path: str, name: str | None = None) -> dict:
    """Bring an existing OpenToonz .tnz (made in the GUI) under MCP control. Copies it into the sandbox
    scenes folder if it is elsewhere. Its columns appear as 'foreign_columns' you can key/retime."""
    src = Path(path)
    name = _safe(name or src.stem)
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
    c = Ctx(scene, "add_layer", {"layer": layer, "mode": mode, "style": style, "parent": parent, "boil": boil})
    name = _safe(layer)
    if name in c.st["layers"]:
        raise ValueError(f"layer '{name}' exists")
    if mode not in ("vector", "raster"):
        raise ValueError("mode must be vector or raster")
    c.st["layers"][name] = {"mode": mode, "style": style, "drawings": {}, "timing": "auto", "boil": boil,
                            "level_path": _level_path(c.st, name, mode), "dirty": False}
    lid = c.doc.add_level(name, _level_path(c.st, name, mode), None if mode == "vector" else c.doc.dpi())
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
    if L["mode"] == "vector":
        _snapshot(c.st, "delete_pli", [_pli_file(c.st, name)])
    shutil.rmtree(SVG_DIR / c.name / name, ignore_errors=True)
    return c.commit({"deleted": name})


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
    c = Ctx(scene, "draw", {"layer": layer, "frame": frame, "n_shapes": len(shapes)})
    name = _safe(layer)
    _write_drawing(c, name, int(frame), shapes, force)
    msg = _retime(c, name)
    res = {"layer": name, "drawing": int(frame)}
    if msg:
        c.warnings.append(msg)
    return c.commit(res)


@tool
def draw_many(scene: str, layer: str, drawings: list[dict], force: bool = False) -> dict:
    """Batch draw: drawings=[{"frame": n, "shapes": [...]}, ...]. Use for frame-by-frame animation."""
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
    missing = sorted({x for x in cells if x and str(x) not in L["drawings"]})
    if missing:
        raise ValueError(f"drawings not on layer: {missing}. Existing: {sorted(int(k) for k in L['drawings'])}")
    L["timing"] = "manual"
    if L.get("boil"):
        exp = []
        run = 0
        for i, d in enumerate(cells):
            run = run + 1 if i and cells[i - 1] == d else 0
            v = (run // 2) % 3
            exp.append(d + BOIL_BASE * v if d and v else d)
        cells = exp
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
    cur = c.doc.get_params(sid).get(tag, {"keys": []})["keys"]
    nf = {k["frame"] - 1 for k in new}
    keep = [k for k in cur if "value" in k and k["frame"] not in nf and
            not (replace_range and replace_range[0] - 1 <= k["frame"] <= replace_range[1] - 1)]
    merged = keep + [{"frame": k["frame"] - 1, "value": _to_internal(tag, float(k["value"]), dpi),
                      "interp": k.get("interp", "ease"), "ease_in": k.get("ease_in", 33.0),
                      "ease_out": k.get("ease_out", 33.0)} for k in new]
    c.doc.set_param(sid, tag, merged)


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
    sid = c.stage_id(target)
    tags = [KEY_TAGS.get(param, param)] if param else list(KEY_TAGS.values())
    for tag in tags:
        cur = c.doc.get_params(sid).get(tag)
        if not cur:
            continue
        if start or end:
            s0, e0 = (start or 1) - 1, (end or 10 ** 6) - 1
            keep = [k for k in cur["keys"] if not (s0 <= k["frame"] <= e0)]
            c.doc.set_param(sid, tag, keep, cur["default"])
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
        if overshoot:
            keys.append({"frame": end, "value": float(v_to) + overshoot * d, "interp": "ease"})
            half = max(2, settle_frames // 2)
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
        raise ValueError("kind must be pan, truck or shake")
    return c.commit(res)


# ====================================================================== tools: output
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
    c = Ctx(scene, "render_prepare", {}, mutate=False)
    problems = c.doc.verify(_level_file_exists)
    if problems:
        c.warnings += problems
    return c, comp


@tool
def preview_frame(scene: str, frame: int = 1, max_width: int = 960) -> McpImage:
    """Render one frame through OpenToonz and return the image (check your work visually)."""
    c, _ = _prepare(scene)
    out = TMP / f"prev_{c.name}_{int(time.time() * 1000)}" / "p..png"
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
    tmp = TMP / f"cs_{c.name}_{int(time.time() * 1000)}"
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
    c, comp = _prepare(scene)
    start, end = start or 1, end or c.st["frames"]
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
    issues += _check_frames(files, c.doc.fps(), _bg(c))
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
    subprocess.Popen([str(OT_HOME / "OpenToonz.exe"), str(c.sf)], cwd=OT_HOME,
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
    e = learn.add("preference", f"[{rating}/5] {comment}".strip(), name, ["feedback"])
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


def main() -> None:
    for d in (STATE_DIR, RENDER_DIR, SNAP_DIR, JOURNAL_DIR, NOTES_DIR, SVG_DIR, TMP):
        d.mkdir(parents=True, exist_ok=True)
    mcp.run()


if __name__ == "__main__":
    main()

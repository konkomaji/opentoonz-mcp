"""Writer for OpenToonz .tnz scene files (format 71.1, OpenToonz 1.8).

Format reverse-engineered from opentoonz v1.8.0 sources:
  toonzscene.cpp, levelset.cpp, txshsimplelevel.cpp, txsheet.cpp,
  txshlevelcolumn.cpp, fxdag.cpp, tcolumnfx.cpp, tstageobject*.cpp,
  tdoublekeyframe.cpp, tstream.cpp.

Persist objects are written as <tag id='N'>...</tag> on first use and
<tag id='N'/> as later references (shared id table across the file).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from xml.sax.saxutils import escape

# keyframe interpolation codes (TDoubleKeyframe::saveData)
INTERP = {"linear": "L", "constant": "C", "ease": "Ep", "speed": "S", "exp": "Exp"}


@dataclass
class Key:
    frame: int          # 0-based xsheet row
    value: float        # internal units (inches for x/y, degrees, scale factor)
    interp: str = "linear"   # interpolation of the segment that STARTS here
    ease_in: float = 33.0    # % (only for "ease")
    ease_out: float = 33.0


@dataclass
class Level:
    name: str            # level name, e.g. "ball"
    path: str            # coded path, e.g. "+drawings/myscene/ball..png"
    dpi: float = 120.0


@dataclass
class Column:
    level: Level
    cells: list[int]     # per row: frame number (1-based) or 0 = empty
    name: str = ""
    params: dict[str, list[Key]] = field(default_factory=dict)  # x,y,rot,sx,sy,sc,z,so,shx,shy
    parent: str = "Table"   # stage object id: "Table", "Peg1", "Col2"...
    visible: bool = True


@dataclass
class Peg:
    id: str               # "Peg1"...
    name: str = ""
    params: dict[str, list[Key]] = field(default_factory=dict)
    parent: str = "Table"


@dataclass
class Scene:
    width: int = 1920
    height: int = 1080
    fps: float = 24
    frames: int = 24
    bg: tuple[int, int, int, int] = (255, 255, 255, 255)
    dpi: float = 120.0
    levels: list[Level] = field(default_factory=list)
    columns: list[Column] = field(default_factory=list)
    pegs: list[Peg] = field(default_factory=list)
    camera: dict[str, list[Key]] = field(default_factory=dict)
    output_path: str = "+outputs/out..png"


def _num(v: float) -> str:
    return f"{v:.6g}" if isinstance(v, float) else str(v)


def _q(s: str) -> str:
    """TOStream string/filepath: quoted, backslash-escaped."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _param(tag: str, keys: list[Key]) -> str:
    keys = sorted(keys, key=lambda k: k.frame)
    out = [f"<{tag}><default>{_num(keys[0].value)}</default><keyframes>"]
    prev: Key | None = None
    for k in keys:
        code = INTERP.get(k.interp, "L")
        inner = ""
        if prev is not None:  # incoming handle, depends on previous segment type
            pc = INTERP.get(prev.interp, "L")
            if pc == "L":
                inner = f"<prev>{_num(k.value)}</prev>"
            elif pc == "Ep":
                inner = f"<prev>{_num(k.value)} {_num(k.ease_in)}</prev>"
            elif pc == "S":
                inner = f"<prev>{_num(k.value)} -3 0</prev>"
        if code == "Ep":
            body = f"{k.frame} {_num(k.value)} {_num(k.ease_out)}"
        elif code == "S":
            body = f"{k.frame} {_num(k.value)} 3 0"
        else:
            body = f"{k.frame} {_num(k.value)}"
        out.append(f"<{code}>{inner}{body}</{code}>")
        prev = k
    out.append(f"</keyframes></{tag}>")
    return "".join(out)


def _stage_params(params: dict[str, list[Key]]) -> str:
    order = ["x", "y", "z", "so", "sx", "sy", "sc", "rot", "shx", "shy"]
    return "".join(_param(t, params[t]) for t in order if params.get(t))


def _output(name: str, s: Scene) -> str:
    return (f'<output name="{name}"><range>0 {s.frames - 1}</range><step>1</step><shrink>1</shrink>'
            f'<applyShrinkToViewer>0</applyShrinkToViewer><fps>{_num(float(s.fps))}</fps>'
            f'<path>{_q(s.output_path)}</path><bpp>32</bpp><multimedia>0</multimedia>'
            f'<threadsIndex>2</threadsIndex><maxTileSizeIndex>0</maxTileSizeIndex><subcameraPrev>0</subcameraPrev>'
            f'<stereoscopic>0 0.05</stereoscopic><resquality>0</resquality><fieldprevalence>0</fieldprevalence>'
            f'<gamma>1</gamma><timestretch>25 25</timestretch><formatsProperties></formatsProperties></output>')


def build(s: Scene) -> str:
    ids = iter(range(1, 100000))
    lx, ly = s.width / s.dpi, s.height / s.dpi
    cam = (f"<cameraSize>{_num(lx)} {_num(ly)}</cameraSize><cameraRes>{s.width} {s.height}</cameraRes>"
           f"<cameraXPrevalence>1</cameraXPrevalence><interestRect>0 0 -1 -1</interestRect>")
    x = [f'<tnz framecount="{s.frames}" version="71.1">',
         "<generator>OpenToonz 1.8.0 (otmcp)</generator>",
         "<properties>",
         f"<cameras><camera>{cam}</camera></cameras>",
         f"<outputs>{_output('main', s)}{_output('preview', s)}</outputs>",
         f"<bgColor>{' '.join(str(c) for c in s.bg)}</bgColor>",
         "<markers>6 0</markers><subsampling>1 1</subsampling>",
         f"<fieldguide>16 {_num(s.width / s.height)}</fieldguide>",
         "</properties>"]

    # --- levels
    level_id: dict[int, int] = {}
    x.append("<levelSet><levels>")
    for lv in s.levels:
        i = next(ids)
        level_id[id(lv)] = i
        x.append(f"<level id='{i}'>{_q(lv.name)}<info dpix=\"{lv.dpi:.6f}\" dpiy=\"{lv.dpi:.6f}\"/>"
                 f"<path>{_q(lv.path)}</path></level>")
    x.append("</levels>")
    x.append('<folder name="Cast" type="default"><levels>')
    for lv in s.levels:
        x.append(f"<level id='{level_id[id(lv)]}'/>")
    x.append("</levels></folder></levelSet>")

    # --- xsheet columns
    x.append("<xsheet><columns>")
    colfx_ids = []
    for ci, col in enumerate(s.columns):
        lid = level_id[id(col.level)]
        # status bits are "hidden" flags: 0x1 camstand, 0x2 preview (txshcolumn.h)
        status = 0 if col.visible else 3
        x.append(f"<levelColumn id='{next(ids)}'><status>{status}</status><cells>")
        r = 0
        cells = col.cells
        while r < len(cells):
            f = cells[r]
            if not f:
                r += 1
                continue
            n, inc = 1, 0
            if r + 1 < len(cells) and cells[r + 1]:
                inc = cells[r + 1] - f
                n = 2
                while r + n < len(cells) and cells[r + n] and cells[r + n] == f + n * inc:
                    n += 1
            x.append(f"<cell>{r} {n} <level id='{lid}'/> {f} {inc}</cell>")
            r += n
        fid = next(ids)
        colfx_ids.append(fid)
        x.append(f"</cells><fx><Toonz_columnFx id='{fid}'><params></params><ports></ports>"
                 f"<dagNodePos>{-200} {ci * 60}</dagNodePos><numberId>{ci + 1}</numberId>"
                 f"<fxId>{_q('')}</fxId><opened>1</opened></Toonz_columnFx></fx></levelColumn>")
    x.append("</columns>")

    # --- pegbars / stage objects
    x.append("<pegbars>")
    x.append('<pegbar id="Table"><parent id="None" handle="B" parentHandle="B"/><isOpened>0</isOpened>'
             '<center>0 0 0 0</center><status>0</status><nodePos>0 0</nodePos></pegbar>')
    x.append('<pegbar id="Camera1" activeboth="yes"><parent id="Table" handle="B" parentHandle="B"/>'
             f'<isOpened>0</isOpened><center>0 0 0 0</center><status>0</status>{_stage_params(s.camera)}'
             f'<nodePos>0 0</nodePos><camera>{cam}</camera></pegbar>')
    for p in s.pegs:
        name = f"<name>{_q(p.name)}</name>" if p.name else ""
        x.append(f'<pegbar id="{p.id}"><parent id="{p.parent}" handle="B" parentHandle="B"/>{name}'
                 f'<isOpened>0</isOpened><center>0 0 0 0</center><status>0</status>'
                 f'{_stage_params(p.params)}<nodePos>0 0</nodePos></pegbar>')
    for ci, col in enumerate(s.columns):
        name = f"<name>{_q(col.name)}</name>" if col.name else ""
        x.append(f'<pegbar id="Col{ci + 1}"><parent id="{col.parent}" handle="B" parentHandle="B"/>{name}'
                 f'<isOpened>0</isOpened><center>0 0 0 0</center><status>0</status>'
                 f'{_stage_params(col.params)}<nodePos>0 0</nodePos></pegbar>')
    x.append("</pegbars>")

    # --- fx dag: every column -> xsheet node -> output
    x.append("<fxnodes>")
    if colfx_ids:
        x.append("<terminal>" + "".join(f"<fxnode><Toonz_columnFx id='{i}'/></fxnode>" for i in colfx_ids)
                 + "</terminal>")
    xs = next(ids)
    x.append(f"<xsheet><Toonz_xsheetFx id='{xs}'><params></params><ports></ports><numberId>1</numberId>"
             f"<fxId>{_q('')}</fxId><opened>1</opened></Toonz_xsheetFx></xsheet>")
    x.append(f"<output><Toonz_outputFx id='{next(ids)}'><params></params><ports><source>"
             f"<Toonz_xsheetFx id='{xs}'/></source></ports><numberId>1</numberId>"
             f"<fxId>{_q('')}</fxId><opened>1</opened></Toonz_outputFx></output>")
    x.append("<grid_dimension>0</grid_dimension></fxnodes>")
    x.append("</xsheet></tnz>\n")
    return "\n".join(x)


def escape_text(s: str) -> str:
    return escape(s)

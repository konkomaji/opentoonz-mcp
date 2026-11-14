# Architecture (developer guide)

This is how I structured the server, and the rules I follow when I change it. The guiding idea: the assistant may only ever touch an OpenToonz scene through a transaction it can undo, and everything it writes must be something OpenToonz itself would write.

```
MCP client ──stdio──► otmcp.server (FastMCP, 57 tools)
                          │  every scene-changing call: lock → snapshot → edit → verify → write → journal
          ┌───────────────┼──────────────────┬──────────────────┬──────────────┐
          ▼               ▼                  ▼                  ▼              ▼
   scene.SceneDoc     vec / draw / art     pli               physics        learn
   tnztree (lossless  (shapes → contours   (read .pli,       (ballistics,   (lessons, prefs,
   .tnz edit), curve  → humanize → SVG /   pressure patch)   springs,       style overrides)
   (exact OpenToonz   PNG), assets (parts,                   camera, charts)
   key evaluator)     SVG/image import)
          │               │                  ▲
          ▼               ▼                  │
   <stuff>/sandbox/   <work>/svg/<scene>/<layer>/ ──► OpenToonz.exe *.toonzscript ──► <stuff>/sandbox/drawings/<scene>/<layer>.pli
   scenes/<name>.tnz                                   (SVG load, or Outline/Centerline vectorizer for traced layers)
          └────────────────────────────────► tcomposer.exe ──► <work>/renders ──► ffmpeg (mp4/gif)
```

## Modules

| file | responsibility |
|---|---|
| `config.py` | Finds the OpenToonz install, the stuff folder and the work folder. Precedence: environment variable, then the Windows registry, then the standard path. |
| `tnztree.py` | TOStream tokenizer, parser and dumper. Raw tokens are kept, so untouched nodes round-trip exactly. Attribute escaping matches OpenToonz `escape()`. |
| `scene.py` | `SceneDoc`: levels, columns, cells (run encoding), stage objects, keyframes, `upsert_keys` (node-level merge that keeps GUI key data), pivot (offset-preserving), active-camera dpi, `verify()`. |
| `curve.py` | Exact port of OpenToonz `TDoubleParam::getValue`: L, C, Exp, E, Ep, S (Bézier with speed truncation), step and cycle. |
| `tnz.py` | Skeleton builder used only by `create_scene`. |
| `draw.py` | Colours, easing (studio `slow_in_out`), `tween`, and `tween_motion` (arc, exact area-preserving squash). |
| `art.py` | SVG path sampling (svgelements), brush pressure profiles, and an affine shape transform that keeps primitive types when it can. |
| `vec.py` | Turns shapes into contours, applies the humanize step and the cel shadow (shapely), builds ink+paint elements, and writes SVG at vector scale or a PNG preview. Also produces ink stroke profiles. |
| `pli.py` | `.pli` reader (v5.7 and later). `pressure_patch` rewrites thickness bytes in place (periodic on loops, per-frame seeds, atomic write). |
| `plidoc.py` | `.pli` stroke editor for ink layers. Keeps every tag as raw bytes, resolves GROUP and IMAGE child offsets to tag objects on load and writes them again on save, so untouched data round-trips byte for byte. Adds strokes and drawings, adds palette colours, removes strokes that no painted region depends on. Also fits a quadratic chain through points. |
| `bridge.py` | Client for the `otbridge` plugin that runs inside OpenToonz (`bridge/`): one JSON line each way over a local socket, with a timeout for a busy GUI. Returns `None` when nothing listens, so callers fall back to the files. |
| `assets.py` | Parts library (global, with variants), SVG file to shapes, image placement. |
| `physics.py` | Bounce, jump, second-order springs, SmoothDamp, trauma shake, RDP key thinning, blink and lip-sync charts. Reads `data/recipes.json`. |
| `anatomy.py` | Human proportions by age, sex and region (Drillis-Contini, de Leva, CDC/NHANES; regional means in `data/regions.json`), mass split and centre of mass, the 16-part side-view rig (joints, parents, drawn shapes, mirroring). |
| `gait.py` | Walk and run cycles: gait norms by age and sex, planted rolling feet, two-bone IK, inverted-pendulum and spring-mass pelvis, self-check report. Pure functions; `server.py` bakes the result into keys. |
| `fx.py` | Studio looks as fx graphs: param encoding (double, bool, enum, pixel, point; lengths in 1/53.333 inch), insertion before the output or after column fx, `normalize` (first occurrence = definition), and removal that splices inputs back. |
| `style.py` | Style presets plus learned overrides; colour design (kage, iro-tore, lighting); built-in rule summary. |
| `learn.py` | Lessons store with advice, ranking and `tune_style`. |
| `server.py` | The tools, plus `Ctx` (the per-call transaction), compile and render. |

## Per-call transaction

1. The `@tool` wrapper takes the per-scene lock: `msvcrt` byte-range on Windows, `flock` elsewhere.
2. `Ctx.__init__` loads the state JSON and the **current** `.tnz`, and detects outside edits:
   - tnz hash
   - fid-only cell signatures (OpenToonz renumbers persist ids on save)
   - `.pli` hashes
3. If the call changes anything, `Ctx` takes a **full snapshot first**: scene, state, `svg/<scene>` and `drawings/<scene>`.
4. The tool edits the document and the state. It may write SVG and PNG files.
5. `commit()` writes the result safely:
   - Serialize to a unique temp file.
   - Re-parse it and run `verify()`. A fatal problem aborts the write.
   - Replace the scene with `os.replace`.
   - Update the hashes and signatures, journal the call, and turn warnings into lessons.
6. On any exception the wrapper **restores the snapshot**, so a failed call leaves no trace. `undo` restores older snapshots the same way.

## Layer modes

| mode | source | built by |
|---|---|---|
| `vector` | shapes → `svg/<scene>/<layer>/<layer>.NNNN.svg` (+ `.prof.json` brush profiles) | OpenToonz SVG import, then pressure patch |
| `traced` | images → `svg/<scene>/<layer>/<layer>.NNNN.png` | OpenToonz Outline/Centerline vectorizer |
| `raster` | shapes and images → `drawings/<scene>/<layer>.NNNN.png` | written directly |
| `ink` | the `.pli` itself: `drawings/<scene>/<layer>.pli` | `plidoc` appends strokes in place; never compiled |

An ink layer has no second copy of its drawings. The level file is the only truth, so the user and the assistant can both add to one drawing, and a GUI edit never locks the layer. The state keeps only a geometry checksum of each stroke the assistant wrote, which is how `read_drawing` tells its own strokes from the user's. `ink_layer` converts a vector layer by dropping its shapes and SVG sources.

The level file of an ink layer appears with its first stroke. OpenToonz does not watch level files, so strokes written while the scene is open in the GUI show up after File > Revert Scene.

### Live mode

When the running OpenToonz has the scene open and the `otbridge` plugin is installed, its memory is the truth, not the files. `stroke`, `read_drawing` and `erase_stroke` ask `bridge.has_open(scene_file)` first and, if it is true, send the work to OpenToonz instead of touching the `.pli`:

```
stroke ──► bridge.call("stroke", level, drawing, strokes) ──► otbridge.dll (GUI thread)
                                                                  TVectorImage::addStroke + TUndoManager + repaint
```

The stroke is on screen at once and is one undo step in OpenToonz. The files change when the user saves, or on `opentoonz_live('save')`. The plugin is C++ against OpenToonz's own classes, so it is compiled per OpenToonz build and refuses to start in any other (`bridge/README.md`).

Stroke points are rounded to 1/128 of a vector unit before they are written, in both modes. The `.pli` could hold 1/16384, but the GUI cuts loaded coordinates to 1/128, and a stroke must keep its exact points for its checksum to keep matching.

Boil variants use drawing ids `d + 1000·v` (v = 1, 2). Drawing numbers are therefore limited to 1..999.

## Timing model

- `timing=auto`: each drawing holds until the next one. With boil on, held runs cycle the variants on twos.
- `timing=manual` is set by `set_exposure`, `blink`, `lip_sync`, or by detecting a GUI retime. New drawings are not exposed automatically.

## Testing

| command | scope |
|---|---|
| `pytest -m "not slow"` | unit and regression tests, no OpenToonz (CI) |
| `pytest -m slow` | renders through the local OpenToonz install |
| `scripts/gen_tool_docs.py [--check]` | regenerates `docs/TOOLS.md`, or verifies it is current |
| `scripts/release_check.py [--tag vX.Y.Z]` | version and changelog gate used by CI and the release workflow |

## Conventions

- **Tool API:** pixels, origin at top-left, +y down, 1-based frames, degrees.
- **Internal:** OpenToonz inches, +y up, 0-based frames. Conversion happens only in `server.py`.
- **Seeds:** use `zlib.crc32`, never Python `hash()`.
- **File writes:** go through a temp file plus `os.replace`.

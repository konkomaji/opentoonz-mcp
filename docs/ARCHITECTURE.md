# Architecture (developer guide)

```
Claude ──MCP stdio──► otmcp.server (FastMCP, 34 tools)
                         │
         ┌───────────────┼──────────────────────┬───────────────────┐
         ▼               ▼                      ▼                   ▼
   scene.SceneDoc    vec / draw / style      pli                  learn
   (tnztree: lossless (shapes → contours →   (read + pressure     (lessons, prefs,
    .tnz edit)        humanize → SVG/PNG)     patch .pli)          style overrides)
         │               │                      ▲
         ▼               ▼                      │
  sandbox/scenes/   work/svg/<scene>/<layer>/ ──► OpenToonz.exe *.toonzscript ──► sandbox/drawings/<scene>/<layer>.pli
     <name>.tnz                                                        │
         └──────────────────────────────► tcomposer.exe ──► work/renders ──► ffmpeg (mp4/gif)
```

## Modules
| file | responsibility |
|---|---|
| `tnztree.py` | Tokenizer, parser and dumper for TOStream. Raw tokens are kept, so nodes we don't touch round-trip exactly. |
| `scene.py` | `SceneDoc` edits a loaded tree: levels, columns, cells (run encoding), stage objects, keyframes, pivot, and the structural `verify()`. |
| `tnz.py` | Builder for a fresh scene skeleton. Only `create_scene` uses it; all other edits go through `SceneDoc`. |
| `draw.py` | Colours, easing (including the studio `slow_in_out` chart), `tween`, and `tween_motion` (arc, area-preserving squash). |
| `vec.py` | Turns shapes into contours, applies the hand-drawn wobble (seeded per drawing), builds cel shadows (shapely), produces ink+paint elements, and writes SVG at vector scale or a PNG preview. |
| `pli.py` | Minimal `.pli` reader. `pressure_patch` adds taper and pressure to the strokes. |
| `style.py` | Studio presets and colour design helpers. `get()` merges in learned overrides. |
| `learn.py` | Global lessons store (`work/knowledge`), automatic advice, and style tuning. |
| `server.py` | The tools. The `Ctx` class is the transaction around each call. |

## Per-call transaction (`server.Ctx`)
1. Load the MCP state JSON and the **current** `.tnz` from disk.
2. Detect outside edits. A tnz hash change gives a warning. A changed cell signature switches the layer to manual timing. A changed pli hash sets `gui_edited`.
3. The tool edits `SceneDoc` and the state.
4. `commit()` runs the write:
   - Snapshot the scene and state.
   - Write to `*.tnz.tmp`.
   - Re-parse the tmp file and run `verify()`. A fatal problem aborts the write.
   - Atomically replace the scene file.
   - Store the hashes and cell signatures.
   - Write the journal entry.
   - Turn warnings into lessons.

## Layer ↔ column mapping
A layer owns one level (`+drawings/<scene>/<layer>.pli` or `..png`). Its column is the one whose cells reference that level. If there is none, the stage-object name is used as a fallback. Columns that no MCP layer owns are reported as `foreign_columns`, and they can still be keyed through `ColN`.

## Vector pipeline
1. `draw` writes one SVG per drawing (plus 2 boil variants with ids d+1000, d+2000) and marks the layer dirty.
2. `_compile`, run before preview, render or open:
   - Remove orphan SVGs.
   - Run one `.toonzscript` per batch that loads `<layer>..svg` and saves `<layer>.pli`.
   - Apply `pressure_patch`.
   - Record the pli hash.
3. If the user edited a pli in the GUI, its hash changes. Compile then skips that layer, and drawing on it requires `force`.

## Timing model
- `timing=auto`: each drawing holds until the next one, up to the scene length. With boil enabled, held runs cycle variants on twos.
- `timing=manual` is set by `set_exposure` or by detecting a GUI retime. New drawings are then not exposed automatically.

## Data on disk (all under `D:\otmcp\work`)
- `state/<scene>.json`: MCP-only data (layer modes, shapes per drawing, hashes, timing mode).
- `journal/<scene>.jsonl`: every action, outside change and error.
- `notes/<scene>.md`: memory notes.
- `snapshots/<scene>/<ts>_<tool>/`: undo data (last 60).
- `svg/`: SVG drawing sources.
- `renders/`: render output.
- `tmp/`: temporary files.
- `knowledge/`: lessons and style overrides.

## Testing
- `python -m pytest -q -m "not slow"`: unit tests, no OpenToonz needed.
- `python -m pytest -q -m slow`: renders through the real OpenToonz.
- `python scripts/gen_tool_docs.py`: regenerates `docs/TOOLS.md`.

## Conventions
- Coordinates in the tool API are pixels with origin top-left and +y down. Frames are 1-based.
- Internally everything is OpenToonz inches with +y up, and frames are 0-based.
- Seeds use `zlib.crc32`, never Python `hash()`, which is randomized per process.
- All paths are on D:. Environment variables `OPENTOONZ_HOME`, `OPENTOONZ_STUFF` and `OTMCP_WORK` override them.

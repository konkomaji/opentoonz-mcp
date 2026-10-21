# OpenToonz MCP (otmcp)

MCP server that drives **OpenToonz 1.8** (`D:\OpenToonz`) to make 2D animation like a studio pipeline.

- Scenes: `D:\OpenToonz stuff\sandbox\scenes\<name>.tnz` (open/edit in OpenToonz any time)
- Work data (all on D:): `D:\otmcp\work\` — state, journal, notes, snapshots (undo), svg sources, renders, knowledge
- Research: `docs\studio_practices.md` (studio rules with sources; `style_guide` tool)

## How it works
| Part | What |
|---|---|
| `scene.py` + `tnztree.py` | Lossless parser/editor of the `.tnz` format. The file on disk is the source of truth, so GUI edits survive MCP edits. |
| `vec.py` + `pli.py` | Shapes → hand-drawn SVG (wobble, cel shadow, ink+paint) → OpenToonz converts to native vector `.pli` (headless `.toonzscript`) → pen-pressure taper patched into the `.pli`. |
| `style.py` | Studio presets (anime / feature / cartoon / sketch / clean) + colour design (kage shadow, iro-tore lines, lighting palettes). |
| `learn.py` | Self-improvement: lessons from errors, verification, render review, model insights, user feedback; `tune_style` changes defaults permanently. |
| `server.py` | 34 MCP tools. Every call: re-read scene, detect outside changes, snapshot, write, re-verify, journal. |

## Workflow (call order)
`scene_context` → `create_scene` → `add_note` (story) → `add_layer`/`add_peg`/`set_parent` (layout) → `draw` keys → `inbetween` → `set_exposure` (timing) → `set_pivot` → `set_keyframe`/`animate_action` → `camera_move` → `validate_scene` → `contact_sheet` → `render` → `learn_lesson` / `feedback` / `tune_style`.

## Run
Registered in `.mcp.json` (project scope). Start Claude Code in `D:\otmcp` and approve the `opentoonz` server.

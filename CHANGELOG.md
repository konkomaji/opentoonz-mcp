# Changelog

All notable changes to OpenToonz MCP are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every pull request that changes behaviour adds a line under **Unreleased**. A release moves those lines into a dated version section. CI checks both rules (see [RELEASING.md](RELEASING.md)).

## [Unreleased]

### Added

- **Ink layers: the assistant and I can now draw on the same drawing.** `add_layer(mode="ink")` makes a vector level whose `.pli` file is the only truth. `stroke` adds pen lines with pressure straight into that file, on top of whatever is there. `read_drawing` returns every stroke on a drawing in canvas pixels and says which ones the assistant drew. `erase_stroke` removes strokes. `ink_layer` turns an existing vector layer into an ink layer. Nothing is regenerated, so my own lines and paint stay, and editing the layer in OpenToonz no longer locks it.
- `otmcp/plidoc.py`, a `.pli` editor that rewrites the child offsets of group and image tags. Every level in my sandbox round-trips byte for byte, and OpenToonz 1.8.0 saves a level written by it back unchanged.

### Security

- After the eMudhra/emSign CA incident (October 2026), opentoonz-mcp requires `certifi>=2026.10.19`. Every workflow now installs hash-pinned pip 26.2.2 and the fixed certifi before anything else. The README asks for pip 26.2.2+ or uv 0.13.4+.

### Fixed

- When OpenToonz could not build a vector level, the error only said "could not build vector levels", because the script output was thrown away. Each script run now keeps its script, stdout, stderr and exit code in `WORK/logs/` (the 5 newest runs and the 5 newest failed runs; `OTMCP_KEEP_SCRIPT_LOGS` changes the count), and the compile and render errors quote OpenToonz's own message and the log folder. A script that hangs is stopped after 600 s with its output kept, and the compile reports the layers it did not build instead of a bare `TimeoutExpired`. One layer that fails no longer stops the layers after it (#9).
- `physics_bounce` with `start_on_ground=true` gave the first contact frame maximum stretch instead of the contact squash, because the squash pass skipped frame 0. It now squashes that frame like every other contact (#7).
- `preview_frame`, `contact_sheet` and `render` hung for two minutes on a scene with no drawing on the xsheet, because tcomposer waits forever on a scene without frames. They now fail at once with "nothing is drawn yet".
- Building vector levels failed with `WinError 17` when the work folder and the OpenToonz stuff folder were on different drives, because the finished `.pli` was moved with `os.replace`. I now copy it next to its target and swap it in there. matticusanvil reported it in #8.
- The MCP Registry rejected the 0.1.0 `server.json` because its description was over 100 characters. I shortened it, `release_check.py` now enforces the limit, and a manual `registry.yml` workflow re-publishes `server.json` without moving the tag.

## [0.1.0] - 2026-10-22

This is the first public release. I built it so an AI assistant can work inside OpenToonz like an animator: it writes real scenes and native vector levels, and it times them on the xsheet instead of producing flat video.

### Added
- **Scene engine.** A lossless reader and writer for OpenToonz's `.tnz` scene format. The scene file on disk is the source of truth, so changes you make in the OpenToonz GUI survive later edits from the assistant.
- **Native vector drawings.**
  - Shapes, SVG paths and pressure brush strokes become editable OpenToonz `.pli` vector levels. OpenToonz converts them itself, through a headless `.toonzscript`.
  - Lines get pen-pressure taper and hand-drawn wobble. The wobble is seeded per drawing, so held frames stay still.
  - Shapes can get anime-style cel shadows and coloured trace lines.
- **Studio styles.** `anime`, `feature`, `cartoon`, `sketch` and `clean` presets set line weight, wobble, taper, timing (ones/twos/threes) and squash limits.
- **Animation tools.** In-betweens with slow-in/slow-out spacing, arcs and squash-and-stretch that keeps the area constant. Xsheet exposure with line boil. Real OpenToonz keyframes. `animate_action` adds anticipation, overshoot and settle. Pivots. Pegbar rigs.
- **Physics.** Gravity-exact bounces with restitution-based material presets, recipe jumps, spring-based follow-through for hair, tails and cloth, smoothed camera follow, and trauma camera shake.
- **Camera work.** Pans with parallax and pan-speed limits, exponential trucks, impact shake. A warning appears when a move would reveal empty background.
- **Art import.**
  - An SVG file becomes editable shapes.
  - Images are placed on raster layers, or traced into vectors by OpenToonz's own outline or centerline vectorizer.
  - A global parts library (model sheets) has variants, mirroring and exact placement.
- **Character helpers.** Blink charts, and lip sync from text that uses mouth-shape part variants.
- **Exact keyframe math.** A port of OpenToonz's interpolation (linear, ease, ease %, speed in/out, exponential, constant, step, cycle). Tests check it against real OpenToonz renders to within 1.5 px.
- **Safety.**
  - Every edit runs under a per-scene file lock and takes a full snapshot first: scene, state, SVG sources and drawing files.
  - A failed call is rolled back automatically, and `undo` restores the earlier state exactly.
  - Every write is re-parsed and verified before it replaces the scene.
- **Context and memory.** `scene_context` reports the live scene, changes detected from the GUI, notes and recent history. A journal records every action.
- **Self-improvement.** Errors, verification problems, render reviews, the assistant's own insights and user feedback are stored as ranked lessons. `tune_style` makes default changes permanent.
- **Render checks.** Each render is checked for frame count, blank frames, single-frame pops, dead holds and photosensitive flashing.
- **Rendering.** Renders go through OpenToonz's own `tcomposer` to MP4, GIF or PNG sequences.
- `anatomy_reference` gives body proportions for any age and sex: heads tall, joint heights, segment lengths, widths and depths, mass split and centre of mass. The numbers come from Drillis-Contini, de Leva (1996) and CDC/NHANES stature.
- `build_character` builds a 16-part side-view cut-out rig with real proportions, parented like a skeleton with every pivot on its joint. It covers toddlers to old age, has a few hair styles, and can face left or right.
- `walk_cycle` and `run_cycle` animate those rigs from gait research. Timing by age and sex, planted feet that roll heel to ball to toe, two-bone leg IK, an inverted-pendulum walk and a spring-mass run with exact gravity in flight. Each call checks itself: it reports cadence, step length, vertical bounce, knee flexion and foot slip measured from the baked keys.
- `region` and `bmi` options on `anatomy_reference` and `build_character`, plus `list_regions`. The region sets the adult stature and the leg proportion (sitting-height ratio) from national health surveys: NHANES for the US, NFHS for India and West Bengal, HSE for the UK, Japan's national nutrition survey, DHS and NCD-RisC. A body type only changes when you pass a BMI, or `bmi="average"` for the surveyed mean. Heads and faces never change by region. Every result carries a note that these are population averages and that individuals vary far more.
- Hatched shading: `shadow={"style": "hatch"}` draws the shadow side as pen hatching instead of a flat tone. The lines are clipped exactly to the shadow, pulled in so they never cross the outline, packed densest on the side away from the light, and can cross-hatch. They are real inked .pli strokes with pressure taper. The idea comes from p5.brush's hatch(); I reimplemented it, I didn't copy it.
- `walk_cycle` takes a `mood`: natural, brisk, stroll, tired, proud, happy (double bounce), sneak (crouched), heavy or cartoon. The timings follow Richard Williams's beat chart, for example natural 12 frames a step, stroll 16, tired 20, sneak 24 and cartoon 8. Feet stay planted in every mood.
- Walks and runs scale by dynamic similarity: a taller body walks faster with a lower cadence (equal Froude number).
- `apply_look`, `list_looks` and `remove_look` give a scene a studio look built from real OpenToonz fx nodes, which you can open and tweak in the FX Schematic. Looks: ghibli, klaus (form shadow and rim light per layer), spider_verse (offset colour plates), comic, anime_satsuei, painterly, cartoon_saloon. Re-applying replaces the look, removing it restores the file byte for byte, and fx made by hand are kept. I checked that OpenToonz loads and re-saves the graph, and that fx lengths come out in exact pixels.
- **Packaging.** An `opentoonz-mcp` command, OpenToonz path auto-detection, an MCP Registry `server.json`, and CI and release workflows.

[Unreleased]: https://github.com/konkomaji/opentoonz-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/konkomaji/opentoonz-mcp/releases/tag/v0.1.0

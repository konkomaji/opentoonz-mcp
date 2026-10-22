# Changelog

All notable changes to OpenToonz MCP are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Every pull request that changes behaviour adds a line under **Unreleased**. A release moves those lines into a dated version section. CI checks both rules (see [RELEASING.md](RELEASING.md)).

## [Unreleased]

### Added

- `anatomy_reference` gives body proportions for any age and sex: heads tall, joint heights, segment lengths, widths and depths, mass split and centre of mass. The numbers come from Drillis-Contini, de Leva (1996) and CDC/NHANES stature.
- `build_character` builds a 16-part side-view cut-out rig with real proportions, parented like a skeleton with every pivot on its joint. It covers toddlers to old age, has a few hair styles, and can face left or right.
- `walk_cycle` and `run_cycle` animate those rigs from gait research. Timing by age and sex, planted feet that roll heel to ball to toe, two-bone leg IK, an inverted-pendulum walk and a spring-mass run with exact gravity in flight. Each call checks itself: it reports cadence, step length, vertical bounce, knee flexion and foot slip measured from the baked keys.

- `region` and `bmi` options on `anatomy_reference` and `build_character`, plus `list_regions`. The region sets the adult stature and the leg proportion (sitting-height ratio) from national health surveys: NHANES for the US, NFHS for India and West Bengal, HSE for the UK, Japan's national nutrition survey, DHS and NCD-RisC. A body type only changes when you pass a BMI, or `bmi="average"` for the surveyed mean. Heads and faces never change by region. Every result carries a note that these are population averages and that individuals vary far more.
- Walks and runs scale by dynamic similarity: a taller body walks faster with a lower cadence (equal Froude number).
- `apply_look`, `list_looks` and `remove_look` give a scene a studio look built from real OpenToonz fx nodes, which you can open and tweak in the FX Schematic. Looks: ghibli, klaus (form shadow and rim light per layer), spider_verse (offset colour plates), comic, anime_satsuei, painterly, cartoon_saloon. Re-applying replaces the look, removing it restores the file byte for byte, and fx made by hand are kept. I checked that OpenToonz loads and re-saves the graph, and that fx lengths come out in exact pixels.

### Changed

- The README badges now read the version and Python range from `pyproject.toml`, and the install steps use the GitHub URL until I publish to PyPI.

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
- **Packaging.** An `opentoonz-mcp` command, OpenToonz path auto-detection, an MCP Registry `server.json`, and CI and release workflows.

[Unreleased]: https://github.com/konkomaji/opentoonz-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/konkomaji/opentoonz-mcp/releases/tag/v0.1.0

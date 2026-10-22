# OpenToonz MCP: an editable, studio-grade 2D animation pipeline for AI assistants

**Author:** konkomaji ([konkomaji.com](https://konkomaji.com/)) · **Version:** 0.1.0 · **Date:** October 2026

## Abstract

AI systems can now produce moving images, but the output is usually a flat video. An animator can't retime it, redraw it, or pass it down a production pipeline.

In this paper I describe OpenToonz MCP, a Model Context Protocol server I built. It lets an AI assistant author animation *inside* OpenToonz, the open-source descendant of the Toonz software Studio Ghibli used, so the result is a normal production scene: native vector levels, xsheet timing, pegbar hierarchies, keyframes and camera moves.

Five ideas make it work:

1. **Lossless, in-place scene editing.** The human's scene file stays the source of truth.
2. **Native vector drawings with a hand-drawn character.** OpenToonz produces them itself, then the server adds pen pressure at the byte level.
3. **Exact reproduction of OpenToonz's motion math.** Verified against its own renderer.
4. **Transactional safety.** Snapshots, rollback, locking and verification.
5. **Studio practice and physics as parameters.** Not prose guidance.

The system has 52 tools and 165 automated tests, 41 of which run through the installed OpenToonz.

## 1. Problem

An animation tool for an assistant has to satisfy two audiences at once:

- **The assistant** needs a small, typed, forgiving API that works in pixels and frames and reports what happened.
- **The animator** needs the output to be indistinguishable from their own work: real levels and real timing, editable in the tool they already use.

The output must survive a round trip: assistant edits, then human edits, then assistant edits again, with no loss.

There is no live automation API in OpenToonz, but it does provide:

- documented-by-source file formats (`.tnz` scenes, `.pli` vector levels)
- a headless renderer, `tcomposer`
- a headless QtScript entry point (`OpenToonz.exe file.toonzscript`)

The design builds on exactly these.

## 2. Method: reading the source, verifying against the renderer

I derived every format fact the server depends on from the OpenToonz **v1.8.0** C++ source, then confirmed it by rendering with the installed `tcomposer`. Whenever reading and rendering disagreed, I trusted the render.

This caught problems I would have missed by reading alone:

- **Arrival ease is signed.** OpenToonz reads the incoming ease of an ease segment as `−speedIn.x` (`getEaseInOutValue`). A positive value is silently clamped to zero, which disables the slow-in. Rendering a keyed dot frame by frame exposed the difference immediately.
- **Columns need terminal fx.** A scene without `<fxnodes><terminal>` references to each column's fx loads fine but renders blank.
- **Pivots carry an offset.** OpenToonz places objects with `T(offset+pos)·R·Sh·S·T(−center)`. Moving a pivot without moving the drawing means moving the stored offset by the same amount.
- **SVG import paint and ink.** A fill-less closed loop drawn over a filled shape becomes its own transparent region and hides the paint. Ink and paint must be one element, the way OpenToonz's own ink-and-paint model works.

These facts are collected in [FORMAT_NOTES.md](FORMAT_NOTES.md).

## 3. Lossless scene editing

`.tnz` files use OpenToonz's TOStream syntax, which only resembles XML:

- no header
- bare or quoted tokens
- persistent objects defined once (`<tag id='N'>…`) and referenced later (`<tag id='N'/>`)

The server parses the file into a tree that keeps every token *raw*, edits only the nodes an operation needs, and writes the tree back. Parsing and re-writing an unchanged scene renders pixel-identical to the original.

Because the file on disk is the source of truth, the assistant always works from what the human last saved. The server detects outside changes per layer, using hashes of the scene, the cell signatures and the vector levels, and changes its behaviour:

- A layer retimed in the GUI switches to manual timing.
- A level redrawn in the GUI is protected from being regenerated.
- Keyframes the user tuned are carried through untouched, because key edits merge at the node level. Speed handles, expressions, steps and cycles all survive.

## 4. Native vector drawing with a hand-drawn character

The assistant describes drawings as shapes: ellipses, polygons, Catmull-Rom paths, SVG path data, text outlines, pressure brush strokes, imported SVG and reusable parts. The pipeline:

1. **Humanise.**
   - Contours are resampled at a quarter of the wobble wavelength and displaced along their normals by smooth two-octave noise (0.3–0.5 px at an 80 px wavelength for anime clean-up).
   - The noise is seeded by `crc32(scene/layer/drawing/variant)`, so a held drawing never boils unintentionally.
   - Free line ends sometimes overshoot by 1–3 px. Lines that bound paint stay closed.
   - The clean style returns geometry unchanged.
2. **Cel shading.** The shadow region is the shape minus a copy offset toward the light, inset by half the ink width so it never covers the line. Shadow colour turns the hue 8° toward blue (toward red for skin), adds 10% saturation and takes 18% off the value.
3. **SVG.** Elements are written at OpenToonz's vector scale (`53.333 / dpi`), with ink and paint as one element.
4. **OpenToonz conversion.** A generated `.toonzscript` loads the SVG sequence as a level and saves `.pli`. Builds go to a private temp folder and are swapped in atomically.
5. **Pen pressure.** OpenToonz's SVG importer gives each stroke a constant width. The server parses the `.pli` binary itself:
   - Tag stream, sign-magnitude dynamic integers, and per-control-point thickness bytes.
   - Thickness is rewritten **in place**, so no tag size or offset changes.
   - Open strokes get sinusoidal end tapers. Closed strokes get *periodic* pressure variation, so there is no seam where the loop closes.
   - Brush strokes carry explicit per-point pressure, mapped to the matching `.pli` stroke by arc length.
   - The parser was cross-checked against an independent implementation on 65 real `.pli` files (about 6,000 strokes, 8/16/32-bit coordinates).

Images can also go through OpenToonz's own outline or centerline vectorizer, which turns scanned drawings or paintings into editable vector levels.

## 5. Motion that matches OpenToonz exactly

The server ports OpenToonz's keyframe evaluator (`TDoubleParam::getValue`):

- linear, constant, exponential
- ease and ease-percentage (piecewise quadratic with the stored ease lengths)
- speed in/out (cubic Bézier with OpenToonz's speed truncation)
- step and cycle

Whenever a tool needs "the current value" (for example, where an action should start), it evaluates the real curve rather than approximating it. A test renders a dot animated by an ease → speed → linear chain through `tcomposer` and checks every frame against the evaluator. The worst error is under 1.5 px, which is the pixel quantisation of the measurement itself.

Physics-based motion is sampled per frame and baked as the fewest linear keys that reproduce every frame within 0.25 px (Ramer–Douglas–Peucker over (frame, value)):

- **Bounces** are exact gravity parabolas, with `g = 2h/N²` for an N-frame first drop. Bounce n rises `e^(2n)` as high and lasts `e^n` as long, for restitution `e`. Contact squash fades with energy, and `sx·sy = 1` throughout.
- **Follow-through** uses a second-order system (`y + k₁y' + k₂y'' = x + k₃x'`, parameterised by frequency, damping ratio and response). The overshoot matches the analytic `exp(−ζπ/√(1−ζ²))` within 3 percentage points in tests.
- **Camera follow** uses critically damped smoothing. **Trauma shake** uses `amplitude = trauma²` with deterministic gradient noise.

## 6. People: anatomy and gait

I wanted human motion that holds up to a physiotherapist's eye, not just an animator's. Two modules carry it.

- **Anatomy** (`anatomy.py`). Segment lengths and joint heights come from Drillis and Contini (as tabulated by Winter). Segment masses and centres of mass come from de Leva (1996), with separate male and female tables. Stature by age comes from the CDC growth charts and NHANES, head height per stature and sitting-height ratio from growth standards, and the child head mass from Jensen. For an adult the shoulder sits at 0.818 H, the hip at 0.530 H and the knee at 0.285 H, and the whole-body centre of mass lands at 0.56 H. A toddler is about 5 heads tall and an adult 7.6; legs grow fastest in the pubertal spurt, and old age adds height loss and a forward head. Child mass splits are an approximation (about ±15%).
- **Regions.** `region` scales adult stature to a population mean and shifts leg length by its sitting-height ratio. The data comes from national surveys (NHANES, NFHS-3/5, HSE 2019, Japan's 2018-19 national nutrition survey, DHS) and NCD-RisC. Build changes only on request, via BMI: soft-tissue widths scale with √(BMI/BMI_ref), bones stay fixed. Heads and faces are never varied by region. Every answer carries the caveat that differences between individuals far exceed differences between national averages, and that average height reflects childhood nutrition and health, not ethnicity.
- **Rig.** `build_character` turns those numbers into 16 vector layers parented like a skeleton, each pivot on its joint. I verified by render that OpenToonz composes the hierarchy as world = parent · T(offset)·R·S·T(−centre).
- **Gait** (`gait.py`). Timing comes from gait studies by age and sex (Sutherland for children, Bohannon and Andrews for adults by decade, jogging norms for running). The stance foot is planted in world space and rolls over Perry's rockers: heel, ankle, forefoot, then toe. When the hip moves out of reach, the heel rises just enough. Legs are solved by analytic two-bone IK.
  - **Walk.** The hip rides the highest arc the loaded legs allow, as an inverted pendulum: low at double support, high at mid-stance.
  - **Run.** A spring-mass model: an exact gravity parabola in flight, and a linear-spring arc in contact that matches position, velocity and acceleration (−g) at take-off.
- **Moods.** Richard Williams's beat chart and character walks become multipliers on the measured norms: stroll 16 frames a step, tired 20, sneak 24 (crouched, high lift), cartoon 8, a happy double bounce, and a heavy long contact. The constraints still hold, so feet stay planted in every mood.
- **Scaling.** Gait norms are rescaled to the actual body by dynamic similarity: speed ∝ √(stature) and cadence ∝ 1/√(stature) relative to the norm's stature.
- **Self-check.** Each call reports cadence, step length, vertical oscillation, knee flexion and foot slip. The slip is measured again by forward kinematics from the keys OpenToonz will actually evaluate.

| Check (adult man unless noted) | Model | Literature |
|---|---|---|
| Walk cadence, step length | 108 steps/min, 0.78 m | 105–115, 0.7–0.8 m |
| Walk pelvis vertical oscillation | 5.7 cm | about 4–6 cm |
| Peak knee flexion in swing: adult, older woman, child | 64°, 58°, 66° | 60–65°, 55–60°, 65–70° |
| Knee at toe-off | 40° | 35–40° |
| Run vertical oscillation, swing knee | 6.4 cm, 100° | 6–10 cm, 90–105° |
| Run flight acceleration | exactly g | g |
| Planted-foot slip after baking keys | < 0.5 px | 0 |

## 7. Studio looks as fx graphs

`apply_look` writes real OpenToonz fx nodes into the scene, so a look is something an artist can open in the FX Schematic and adjust, not a filter baked into pixels.

- **Format.** I read the serializer (`TFx::saveData`, `FxDag`, the param classes) and follow its rule that an object is written in full at its first occurrence and referenced afterwards. A post chain before the output therefore carries the xsheet node's definition inside its first port, exactly as OpenToonz saves it. One `normalize` pass restores that rule after any edit.
- **Units.** Length parameters are stored in 1/53.333 inch. I confirmed by render that an offset of d px comes out exactly d px wide.
- **Safety.** Every node is named `otmcp:<look>:<role>`. Re-applying replaces only those nodes, `remove_look` restores the file byte for byte, and fx made by hand stay connected. OpenToonz itself loads and re-saves the graph intact.
- **Looks.** Each is built from fx ids and parameter names verified in the source:
  - Klaus-style volumetric light: a body-highlight form shadow (Multiply) away from the light and a warm rim (Add) toward it, per layer, both following one light angle.
  - Spider-Verse misregistration: barrel-distortion chromatic aberration, posterize and print specks.
  - Bloom (ghibli, anime): `iwa_Bloom` light screened back over the picture.

## 8. Studio practice as parameters

Classic 2D practice is encoded as defaults the tools apply and validate against, not as text the assistant must remember:

- **Exposure.** Anime animates on threes, Western feature on twos, and fast action and camera moves on ones.
- **Anticipation and overshoot.** Anticipation is 10–25% of the move's size and 15–35% of its time. Overshoot is 10%, with each settle swing scaled by ×0.45.
- **Squash and stretch.** Area is preserved; the limits depend on style.
- **Spacing and arcs.** Slow-in/slow-out spacing is `p = tᵃ/(tᵃ+(1−t)ᵃ)`. Arcs bow 10–20% of the chord.
- **Camera.** Parallax ratios, pan-speed limits against strobing, and exponential trucks.
- **Safety.** No more than 3 large luminance flashes per second.

Each render is reviewed automatically: frame count, blank frames, single-frame pops, dead holds and photosensitive flashing.

## 9. Safety and trust

An assistant that edits a production file must never be the reason work is lost.

- **Locking.** Each scene-changing call takes an OS-level per-scene lock, which works across processes.
- **Snapshots.** Before any side effect, the server snapshots the scene, its MCP state, the SVG sources and the drawing files.
- **Rollback.** If the call fails, the snapshot is restored automatically. `undo` restores any earlier state exactly.
- **Verified writes.** Every write is serialised to a temp file, re-parsed and structurally verified, and only then atomically replaces the scene.
- **Journal.** Every action and every detected outside change is logged.

## 10. Context and learning

`scene_context` gives the assistant a complete, current picture of the scene:

- layers, timing, keys, pegs and camera
- outside edits
- notes and recent history
- the top-ranked lessons

Lessons accumulate across sessions from tool errors, verification problems, render reviews, the assistant's own recorded insights and the user's ratings. They are ranked by kind and recurrence. When feedback implies a default should change, `tune_style` makes it permanent for every later drawing.

## 11. Evaluation

| Check | Result |
|---|---|
| Unit and regression tests | 165 tests passing (41 run through OpenToonz 1.8.0) |
| Keyframe evaluator vs `tcomposer` | ≤ 1.5 px on every frame (ease → speed → linear chain) |
| Physics bake vs analytic solution | ≤ 0.25 px per frame after key thinning |
| Scene round trip (parse → write) | pixel-identical render |
| `.pli` reader vs independent reference parser | identical on 65 files / about 6,000 strokes |
| Independent code review | 52 findings, 50 fixed with regression tests |

## 12. Limitations and future work

- Developed and verified on Windows with OpenToonz 1.8. Other platforms are untested.
- Drawings come from shapes, paths, brushes and imported art. Generating detailed illustrations from scratch is out of scope; the parts library and import tools are the intended route.
- Future work:
  - Ben-Day dot and hatching screens restricted to shadow areas (needs a shadow matte per layer)
  - plastic skeleton deformation
  - audio-driven lip sync
  - front and three-quarter views for the character rigs (they are side view only today)

## Acknowledgements

My work stands on [OpenToonz](https://github.com/opentoonz/opentoonz) and the decades of production knowledge in it. Thanks to DWANGO, Digital Video, Studio Ghibli and the OpenToonz contributors. The animation principles referenced come from the classic literature of the craft, notably Thomas and Johnston's *The Illusion of Life* and Richard Williams' *The Animator's Survival Kit*.

"""How the README shot was made: every call below is one tool of the server, in the order I made them.

An assistant makes the same calls through MCP; this file replays them so the shot can be rebuilt and changed.
Nothing here draws by itself: each shape and each stroke is written out, and OpenToonz renders the result.

    python docs/media/making_of_nightwalk.py        (needs OpenToonz 1.8 and ffmpeg)
"""
from otmcp import server as S

SC = "nightwalk"
W, H, FRAMES = 960, 540, 96

# ------------------------------------------------------------------ 1. scene and layers, back to front
S.create_scene(SC, width=W, height=H, fps=24, frames=FRAMES, bg_color="#0d1230", style="clean", overwrite=True)
S.add_note(SC, "Night walk. A traveller crosses a ridge under a rising moon while a lighthouse sweeps the bay. "
               "Slow truck-in and pan right; seven depth planes; warm lamp against a cold sky.")
for name in ("sky", "stars", "moon", "far", "mid", "beam", "tower", "ridge"):
    S.add_layer(SC, name, style="clean")
S.add_layer(SC, "grass", mode="ink")      # drawn stroke by stroke
S.add_layer(SC, "fireflies", style="clean")

# ------------------------------------------------------------------ 2. sky: bands of colour, cold above, warm at the horizon
S.draw(SC, "sky", 1, [
    {"type": "rect", "x": -300, "y": y, "w": 1560, "h": h, "fill": c, "stroke": None, "human": False}
    for y, h, c in [(-80, 180, "#0b0f2a"), (90, 40, "#0e1432"), (124, 36, "#121a3c"), (154, 34, "#172046"),
                    (182, 32, "#1d2550"), (208, 30, "#242a58"), (232, 30, "#2d2e60"), (256, 28, "#383267"),
                    (278, 28, "#45366d"), (300, 26, "#553a72"), (320, 26, "#683f74"), (340, 24, "#7e4572"),
                    (358, 24, "#964c6a"), (376, 22, "#b0565c"), (392, 22, "#c8664c"), (408, 22, "#dc7c48"),
                    (424, 200, "#eb9858")]
])
S.draw(SC, "stars", 1, [
    {"type": "circle", "x": x, "y": y, "r": r, "fill": "#ffffff", "stroke": None, "opacity": o, "human": False}
    for x, y, r, o in [(60, 40, 2.2, .9), (130, 110, 1.4, .7), (190, 30, 1.6, .8), (250, 150, 1.2, .6), (310, 70, 2.0, .9),
                       (370, 20, 1.3, .7), (420, 120, 1.7, .8), (480, 50, 1.2, .6), (540, 170, 1.4, .6), (590, 25, 2.1, .9),
                       (860, 60, 1.5, .7), (900, 140, 1.2, .6), (930, 20, 1.9, .8), (40, 180, 1.2, .5), (160, 210, 1.1, .5),
                       (330, 200, 1.3, .5), (460, 220, 1.0, .4), (-60, 90, 1.6, .7), (-140, 40, 1.3, .6), (1010, 100, 1.5, .7)]
])
S.draw(SC, "moon", 1, [
    {"type": "circle", "x": 700, "y": 190, "r": 150, "fill": "#f6e7b0", "stroke": None, "opacity": 0.07, "human": False},
    {"type": "circle", "x": 700, "y": 190, "r": 118, "fill": "#f6e7b0", "stroke": None, "opacity": 0.12, "human": False},
    {"type": "circle", "x": 700, "y": 190, "r": 92, "fill": "#fdf3d0", "stroke": None, "human": False},
    {"type": "circle", "x": 668, "y": 165, "r": 17, "fill": "#eadfb4", "stroke": None, "human": False},
    {"type": "circle", "x": 728, "y": 214, "r": 24, "fill": "#eadfb4", "stroke": None, "human": False},
    {"type": "circle", "x": 716, "y": 150, "r": 9, "fill": "#eadfb4", "stroke": None, "human": False},
    {"type": "circle", "x": 664, "y": 226, "r": 11, "fill": "#eadfb4", "stroke": None, "human": False},
])

# ------------------------------------------------------------------ 3. mountains: two ranges, the far one paler
S.draw(SC, "far", 1, [
    {"type": "svgpath", "fill": "#4a3f78", "stroke": None, "human": False,
     "d": "M -320 420 L -200 330 L -120 372 L -20 290 L 70 352 L 150 300 L 250 366 L 340 312 L 430 372 L 520 322 "
          "L 620 380 L 730 326 L 830 376 L 930 318 L 1040 372 L 1160 330 L 1280 400 L 1280 600 L -320 600 Z"},
    {"type": "svgpath", "fill": "#6a5590", "stroke": None, "opacity": 0.55, "human": False,   # moonlit faces
     "d": "M -20 290 L 20 318 L -6 330 Z M 150 300 L 196 332 L 168 340 Z M 340 312 L 384 342 L 356 350 Z "
          "M 730 326 L 776 352 L 748 360 Z M 930 318 L 980 346 L 948 356 Z"},
])
S.draw(SC, "mid", 1, [
    {"type": "svgpath", "fill": "#2a2752", "stroke": None, "human": False,
     "d": "M -320 470 L -230 396 L -150 432 L -60 372 L 30 426 L 120 384 L 230 440 L 330 392 L 440 446 "
          "L 560 402 L 680 452 L 800 408 L 900 448 L 1020 398 L 1140 446 L 1280 410 L 1280 600 L -320 600 Z"},
    {"type": "rect", "x": -320, "y": 430, "w": 1600, "h": 34, "fill": "#b9a6d6", "stroke": None, "opacity": 0.16, "human": False},
    {"type": "rect", "x": -320, "y": 448, "w": 1600, "h": 26, "fill": "#e8c7c0", "stroke": None, "opacity": 0.12, "human": False},
])

# ------------------------------------------------------------------ 4. the lighthouse and its beam
S.draw(SC, "beam", 1, [
    {"type": "polygon", "points": [[842, 262], [-420, 150], [-420, 330]], "fill": "#fff1b8", "stroke": None,
     "opacity": 0.17, "human": False},
    {"type": "polygon", "points": [[842, 262], [-420, 196], [-420, 286]], "fill": "#fff7d6", "stroke": None,
     "opacity": 0.16, "human": False},
])
S.draw(SC, "tower", 1, [
    {"type": "svgpath", "d": "M 760 470 L 800 430 L 900 430 L 950 470 L 950 600 L 760 600 Z", "fill": "#1a1936",
     "stroke": None, "human": False},                                                    # the rock it stands on
    {"type": "polygon", "points": [[822, 432], [830, 286], [856, 286], [864, 432]], "fill": "#e9e4dc",
     "stroke": "#14142a", "stroke_width": 2.5},
    {"type": "polygon", "points": [[826, 372], [828, 338], [858, 338], [860, 372]], "fill": "#b8322c", "stroke": None,
     "human": False},
    {"type": "polygon", "points": [[829, 316], [830, 296], [856, 296], [857, 316]], "fill": "#b8322c", "stroke": None,
     "human": False},
    {"type": "rect", "x": 822, "y": 276, "w": 42, "h": 10, "fill": "#14142a", "stroke": None, "human": False},
    {"type": "rect", "x": 831, "y": 250, "w": 24, "h": 26, "fill": "#ffe9a0", "stroke": "#14142a", "stroke_width": 2.5},
    {"type": "polygon", "points": [[826, 250], [843, 232], [860, 250]], "fill": "#14142a", "stroke": None, "human": False},
    {"type": "circle", "x": 843, "y": 262, "r": 30, "fill": "#ffe9a0", "stroke": None, "opacity": 0.22, "human": False},
    {"type": "circle", "x": 843, "y": 262, "r": 54, "fill": "#ffe9a0", "stroke": None, "opacity": 0.10, "human": False},
    {"type": "rect", "x": 838, "y": 396, "w": 10, "h": 16, "radius": 5, "fill": "#14142a", "stroke": None, "human": False},
])

# ------------------------------------------------------------------ 5. the ridge the traveller walks on
S.draw(SC, "ridge", 1, [
    {"type": "svgpath", "fill": "#11112a", "stroke": None, "human": False,
     "d": "M -320 506 C -200 470 -120 474 0 472 L 760 472 C 860 474 980 480 1280 520 L 1280 640 L -320 640 Z"},
    {"type": "svgpath", "fill": "#2d2b55", "stroke": None, "opacity": 0.7, "human": False,   # moonlit rim of the path
     "d": "M 0 472 L 760 472 L 760 476 L 0 477 Z"},
])

# ------------------------------------------------------------------ 6. the traveller: a rig with real proportions, then a walk
S.build_character(SC, "traveller", person="young_woman", x=150, ground_y=473, px_per_m=74, facing="right",
                  hair="ponytail", style="clean",
                  colors={"skin": "#e9b48f", "hair": "#1d1630", "top": "#c8322b", "bottom": "#232447",
                          "shoes": "#14142a", "line": "#14142a"})
walk = S.walk_cycle(SC, "traveller", start=1, end=FRAMES, mood="stroll", travel=True)

# ------------------------------------------------------------------ 7. grass, by hand: one pen stroke per blade
blades = [(-150, 26, -9), (-118, 34, 6), (-70, 22, -5), (-30, 38, 9), (10, 28, -7), (52, 44, 5), (96, 24, -10),
          (140, 36, 8), (188, 30, -6), (236, 46, 7), (284, 26, -8), (330, 40, 10), (378, 30, -5), (424, 48, 6),
          (470, 26, -9), (516, 38, 8), (566, 30, -6), (612, 44, 9), (660, 24, -7), (708, 36, 6), (756, 28, -9),
          (800, 42, 8), (848, 26, -6), (896, 38, 10), (944, 30, -8), (992, 44, 6), (1040, 26, -7), (1090, 36, 8)]
S.stroke(SC, "grass", 1, [
    {"points": [[x, 522, 1.0], [x + lean * 0.5, 522 - h * 0.9, 0.7], [x + lean * 1.6, 522 - h * 1.7, 0.1]],
     "width": 6, "color": "#05051a"} for x, h, lean in blades])
S.stroke(SC, "grass", 1, [
    {"points": [[x + 13, 522, 1.0], [x + 13 - lean * 0.6, 522 - h * 0.7, 0.7], [x + 13 - lean * 1.8, 522 - h * 1.25, 0.1]],
     "width": 5, "color": "#0a0a24"} for x, h, lean in blades[::2]])
S.stroke(SC, "grass", 1, [   # the dark bank the blades grow from
    {"points": [[-320, 530, 1.0], [200, 524, 1.0], [700, 527, 1.0], [1280, 531, 1.0]], "width": 30, "color": "#05051a"}])

# ------------------------------------------------------------------ 8. fireflies
S.draw(SC, "fireflies", 1, [s for x, y in [(210, 430), (300, 396), (392, 444), (520, 410), (604, 440), (700, 420), (110, 410)]
                             for s in ({"type": "circle", "x": x, "y": y, "r": 5, "fill": "#ffd95a", "stroke": None,
                                        "opacity": 0.45, "human": False},
                                       {"type": "circle", "x": x, "y": y, "r": 2.4, "fill": "#fffbe0", "stroke": None,
                                        "human": False})])

# ------------------------------------------------------------------ 9. motion: beam sweep, moonrise, drifting fireflies
S.set_pivot(SC, "beam", x=842, y=262)
S.set_keyframe(SC, "beam", 1, rotation=7, interp="ease")
S.set_keyframe(SC, "beam", FRAMES, rotation=-9, interp="ease")
S.set_keyframe(SC, "moon", 1, y=26, interp="ease")
S.set_keyframe(SC, "moon", FRAMES, y=-10, interp="ease")
S.set_keyframe(SC, "fireflies", 1, x=0, y=0, interp="ease")
S.set_keyframe(SC, "fireflies", 32, x=10, y=-12, interp="ease")
S.set_keyframe(SC, "fireflies", 64, x=-6, y=-4, interp="ease")
S.set_keyframe(SC, "fireflies", FRAMES, x=8, y=-16, interp="ease")

# ------------------------------------------------------------------ 10. camera: push in and pan with the traveller, planes at different speeds
S.camera_move(SC, "truck", start=1, end=FRAMES, zoom=0.86)
S.camera_move(SC, "pan", start=1, end=FRAMES, dx=150, dy=-6,
              parallax={"sky": 0.03, "stars": 0.04, "moon": 0.07, "far": 0.22, "mid": 0.45, "beam": 0.7, "tower": 0.7,
                        "ridge": 1.0, "grass": 1.7, "fireflies": 1.25})

# ------------------------------------------------------------------ 11. the film look: real OpenToonz fx nodes (bloom and diffusion)
S.apply_look(SC, "anime_satsuei", strength=0.8)

# ------------------------------------------------------------------ 12. check, then render
print("walk self-check:", {k: walk[k] for k in walk if k in ("cadence", "step_length", "foot_slip", "check")})
print("validate:", S.validate_scene(SC, render_test=False).get("issues", "ok"))
out = S.render(SC, format="png")
print("render:", out["frames"], "frames in", out["seconds"], "s, verify:", out["verify"])

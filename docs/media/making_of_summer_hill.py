"""How the README shot was made: every call below is one tool of the server, in the order I made them.

"Summer hill" is an original scene in the hand-painted tradition of Japanese feature animation: a towering
summer cloud, layered green hills, a strip of sea, a cottage with a windmill, and a girl in a straw hat
walking through the grass. An assistant makes the same calls through MCP; this file replays them so the shot
can be rebuilt and changed. Each shape and each stroke is written out, and OpenToonz renders the result.

    python docs/media/making_of_summer_hill.py        (needs OpenToonz 1.8 and ffmpeg)
"""
from otmcp import server as S

SC = "summer_hill"
W, H, FRAMES = 960, 540, 96
GROUND = 452


def flat(kind, **kw):
    """A shape with exact edges and no outline: the way painted background pieces are laid in."""
    return {"type": kind, "stroke": None, "human": False, **kw}


# ------------------------------------------------------------------ 1. scene and layers, back to front
S.create_scene(SC, width=W, height=H, fps=24, frames=FRAMES, bg_color="#8fc7f2", style="feature", overwrite=True)
S.add_note(SC, "Summer hill. Noon light from the upper left. A girl walks a ridge path toward a cottage; a great "
               "cumulus stands over the sea. Slow pan with her, eight depth planes, soft painted finish.")
for name in ("sky", "cloud_far", "cloud_big", "sea", "hill_far", "hill_mid", "blades", "hill_near"):
    S.add_layer(SC, name, style="clean")
S.add_layer(SC, "tufts", mode="ink")      # short pen marks on the slope, under the girl

# ------------------------------------------------------------------ 2. sky: deep blue overhead, pale at the horizon
S.draw(SC, "sky", 1, [
    flat("rect", x=-300, y=y, w=1560, h=h, fill=c)
    for y, h, c in [(-80, 150, "#2f7ed6"), (60, 34, "#3987da"), (88, 32, "#4490de"), (114, 30, "#509ae2"),
                    (138, 28, "#5ea4e6"), (160, 28, "#6caee9"), (182, 26, "#7bb8ec"), (202, 26, "#8ac1ef"),
                    (222, 24, "#9acaf1"), (240, 24, "#aad3f3"), (258, 22, "#badcf5"), (274, 22, "#c9e4f7"),
                    (290, 200, "#d8ecf8")]
])

# ------------------------------------------------------------------ 3. clouds: shadow first, then body, then the sunlit tops
S.draw(SC, "cloud_big", 1,
       [flat("circle", x=x, y=y, r=r, fill="#93a9d6") for x, y, r in      # the shaded underside
        [(204, 288, 60), (286, 298, 70), (378, 302, 78), (470, 296, 68), (548, 288, 56), (148, 280, 40),
         (610, 292, 36)]]
       + [flat("ellipse", x=370, y=320, rx=270, ry=17, fill="#93a9d6")]   # flat base
       + [flat("circle", x=x, y=y, r=r, fill="#c7d6f0") for x, y, r in    # the body in half tone
          [(200, 256, 58), (272, 240, 70), (356, 224, 84), (446, 234, 76), (526, 254, 60), (318, 178, 64),
           (398, 158, 68), (468, 184, 54), (356, 118, 52), (414, 102, 42), (588, 270, 40)]]
       + [flat("circle", x=x, y=y, r=r, fill="#eef3fc") for x, y, r in    # light turning into shade
          [(186, 244, 50), (254, 222, 62), (334, 202, 72), (300, 160, 56), (378, 140, 60), (342, 104, 46),
           (400, 90, 36), (426, 200, 44), (498, 232, 42)]]
       + [flat("circle", x=x, y=y, r=r, fill="#ffffff") for x, y, r in    # the tops the sun reaches
          [(174, 236, 40), (238, 208, 50), (314, 186, 58), (286, 148, 46), (360, 128, 48), (330, 94, 38),
           (390, 80, 28), (408, 186, 30), (478, 222, 28)]])
S.draw(SC, "cloud_far", 1, [   # flat clouds low on the horizon, and a smaller tower to the right for balance
    flat("ellipse", x=x, y=y, rx=rx, ry=ry, fill=c, opacity=o)
    for x, y, rx, ry, c, o in [(700, 278, 120, 13, "#ffffff", .9), (760, 268, 60, 12, "#ffffff", .95),
                               (640, 284, 70, 8, "#e6eefb", .9), (930, 262, 90, 11, "#ffffff", .9),
                               (990, 252, 46, 11, "#ffffff", .95), (-40, 270, 110, 12, "#ffffff", .9),
                               (20, 260, 56, 12, "#ffffff", .95), (1120, 280, 100, 10, "#eef4fc", .9)]
] + [flat("circle", x=x, y=y, r=r, fill=c) for x, y, r, c in
     [(1010, 250, 44, "#93a9d6"), (1066, 258, 50, "#93a9d6"), (1124, 252, 40, "#93a9d6"),
      (1004, 228, 40, "#c7d6f0"), (1058, 206, 50, "#c7d6f0"), (1112, 224, 40, "#c7d6f0"), (1070, 168, 36, "#c7d6f0"),
      (994, 220, 32, "#ffffff"), (1044, 192, 40, "#ffffff"), (1062, 158, 30, "#ffffff"), (1098, 212, 28, "#eef3fc")]])

# ------------------------------------------------------------------ 4. sea and far hills
S.draw(SC, "sea", 1, [
    flat("rect", x=-300, y=304, w=1560, h=200, fill="#3f93cf"),
    flat("rect", x=-300, y=304, w=1560, h=7, fill="#bfe3f5", opacity=0.85),      # the bright line at the horizon
    flat("rect", x=120, y=322, w=260, h=3, fill="#d8f0fb", opacity=0.7),
    flat("rect", x=470, y=334, w=190, h=3, fill="#d8f0fb", opacity=0.6),
    flat("rect", x=-120, y=340, w=170, h=3, fill="#d8f0fb", opacity=0.6),
])
S.draw(SC, "hill_far", 1, [
    flat("svgpath", fill="#7fb7a2",
         d="M -320 380 C -220 330 -140 322 -40 344 C 40 360 110 372 190 366 C 260 362 300 372 340 384 L 340 600 L -320 600 Z"),
    flat("svgpath", fill="#8cc0a6",
         d="M 560 372 C 660 330 760 318 880 330 C 980 340 1100 336 1280 316 L 1280 600 L 560 600 Z"),
])

# ------------------------------------------------------------------ 5. the middle hill: trees, a cottage, a windmill
TREES = [(60, 372, 24), (92, 366, 30), (126, 374, 22), (300, 392, 22), (330, 386, 28), (880, 362, 26),
         (912, 356, 32), (948, 364, 24), (1040, 372, 26), (-90, 384, 28), (-56, 378, 22)]
S.draw(SC, "hill_mid", 1,
       [flat("svgpath", fill="#63b257",
             d="M -320 420 C -200 372 -80 366 40 388 C 150 408 240 404 340 392 C 460 378 560 352 690 344 "
               "C 820 336 960 360 1280 384 L 1280 600 L -320 600 Z"),
        flat("svgpath", fill="#84c96a", opacity=0.75,                              # sun on the crest
             d="M 460 380 C 540 358 610 350 690 346 C 780 342 860 348 940 358 C 860 356 780 356 700 360 "
               "C 620 364 540 374 460 388 Z")]
       + [flat("ellipse", x=220, y=412, rx=150, ry=16, fill="#2f7a45", opacity=0.22),            # a cloud's shadow
          flat("ellipse", x=1010, y=392, rx=130, ry=12, fill="#2f7a45", opacity=0.20)]
       + [flat("ellipse", x=x + r * 0.7, y=y + r * 0.95, rx=r * 1.1, ry=r * 0.22, fill="#2f6f3c", opacity=0.45)
          for x, y, r in TREES]                                                             # shade under each tree
       + [flat("circle", x=x + 5, y=y + 6, r=r, fill="#2f7a45") for x, y, r in TREES]      # tree shadow side
       + [flat("circle", x=x, y=y, r=r, fill="#3f9450") for x, y, r in TREES]
       + [flat("circle", x=x - r * 0.3, y=y - r * 0.32, r=r * 0.52, fill="#62b862") for x, y, r in TREES]
       + [   # the cottage
           flat("ellipse", x=640, y=354, rx=52, ry=6, fill="#2f6f3c", opacity=0.4),
           flat("ellipse", x=772, y=348, rx=30, ry=5, fill="#2f6f3c", opacity=0.4),
           flat("rect", x=592, y=318, w=62, h=34, fill="#f6efdc"),
           flat("rect", x=640, y=318, w=14, h=34, fill="#dcd2b8"),                 # the side away from the sun
           flat("polygon", points=[[584, 320], [616, 294], [662, 320]], fill="#d2553a"),
           flat("polygon", points=[[640, 303], [662, 320], [652, 320], [634, 306]], fill="#a83f2c"),
           flat("rect", x=636, y=292, w=8, h=14, fill="#b9aa8c"),
           flat("rect", x=602, y=330, w=10, h=22, fill="#7a5236"),
           flat("rect", x=622, y=328, w=11, h=10, fill="#6fa9cf"),
           # the windmill tower; its sails are a layer of their own so they can turn
           flat("polygon", points=[[742, 346], [750, 282], [766, 282], [774, 346]], fill="#f1e8d2"),
           flat("polygon", points=[[762, 282], [766, 282], [774, 346], [766, 346]], fill="#d6cbb0"),
           flat("polygon", points=[[746, 284], [758, 268], [770, 284]], fill="#8a4a36"),
           flat("rect", x=754, y=322, w=8, h=12, radius=4, fill="#6f4a33"),
       ])
S.draw(SC, "blades", 1, [
    flat("rect", x=755.5, y=232, w=5, h=96, fill="#7a5a44"),
    flat("rect", x=710, y=277.5, w=96, h=5, fill="#7a5a44"),
    flat("rect", x=760, y=234, w=15, h=38, fill="#fbf6e8", opacity=0.92),
    flat("rect", x=741, y=288, w=15, h=38, fill="#fbf6e8", opacity=0.92),
    flat("rect", x=712, y=265, w=38, h=15, fill="#fbf6e8", opacity=0.92),
    flat("rect", x=766, y=280, w=38, h=15, fill="#fbf6e8", opacity=0.92),
    flat("circle", x=758, y=280, r=5, fill="#5c4030"),
])

# ------------------------------------------------------------------ 6. the near hill and its path
S.draw(SC, "hill_near", 1, [
    flat("svgpath", fill="#57a84a",
         d="M -320 474 C -220 458 -120 453 0 452 L 1000 452 C 1100 454 1200 462 1280 476 L 1280 640 L -320 640 Z"),
    flat("svgpath", fill="#8fd06a", opacity=0.9,                                    # the sunlit top of the bank
         d="M -200 458 C -100 453 -40 452 0 452 L 1000 452 C 1080 453 1160 458 1240 468 C 1160 462 1080 460 1000 460 "
           "L 0 460 C -80 460 -140 462 -200 466 Z"),
    flat("svgpath", fill="#ecdfb0", opacity=0.95,                                   # the worn path
         d="M -120 456 L 1100 456 L 1100 461 L -120 462 Z"),
    flat("svgpath", fill="#3f8f3c", opacity=0.55,                                   # shade lower on the slope
         d="M -320 520 C -100 498 200 492 480 498 C 760 504 1000 500 1280 512 L 1280 640 L -320 640 Z"),
])

TUFTS = [(-90, 478), (-30, 490), (40, 474), (110, 486), (190, 476), (260, 492), (330, 478), (400, 488), (470, 474),
         (540, 490), (610, 478), (690, 486), (760, 474), (830, 490), (900, 478), (970, 488), (1040, 476), (1100, 490)]
S.stroke(SC, "tufts", 1, [
    {"points": [[x + dx, y, 1.0], [x + dx + lean, y - 10, 0.2]], "width": 3, "color": c, "smooth": False}
    for x, y in TUFTS for dx, lean, c in ((0, -3, "#3f8f3c"), (6, 1, "#9ad873"), (12, 4, "#3f8f3c"))])

# ------------------------------------------------------------------ 7. the girl: the feature design (face, dress, straw hat, one
#    shadow tone, thin warm line), staged at 40 % of the frame height so the face can be read, then a walk
S.build_character(SC, "girl", person="girl", age=10, x=130, ground_y=GROUND, px_per_m=150, facing="right",
                  hair="bob", outfit="dress", hat="straw", appeal=1.0, style="clean",
                  colors={"skin": "#f6d2b4", "hair": "#4a3024", "top": "#f7f1e3", "ribbon": "#c9483a",
                          "shoes": "#7b4a3a", "hat": "#ecd58f", "line": "#4a332a"})
walk = S.walk_cycle(SC, "girl", start=1, end=FRAMES, mood="happy", speed=0.82, travel=True)

# ------------------------------------------------------------------ 8. foreground, by hand: flowers, then one pen stroke per blade of grass
S.add_layer(SC, "flowers", style="clean")
FLOWERS = [(-140, 500, "#ffffff"), (-70, 512, "#ffd94a"), (-10, 496, "#ffffff"), (60, 514, "#ffffff"),
           (130, 500, "#ff8a7a"), (200, 512, "#ffffff"), (270, 498, "#ffd94a"), (350, 514, "#ffffff"),
           (430, 500, "#ffffff"), (500, 512, "#ffd94a"), (580, 498, "#ffffff"), (650, 514, "#ff8a7a"),
           (730, 500, "#ffffff"), (800, 512, "#ffffff"), (880, 498, "#ffd94a"), (960, 514, "#ffffff"),
           (1040, 500, "#ffffff"), (1110, 512, "#ffd94a")]
S.draw(SC, "flowers", 1,
       [flat("circle", x=x + dx, y=y + dy, r=4.2, fill=c) for x, y, c in FLOWERS
        for dx, dy in ((0, -5), (5, 0), (0, 5), (-5, 0))]
       + [flat("circle", x=x, y=y, r=3, fill="#e9a23a") for x, y, _ in FLOWERS])
S.add_layer(SC, "grass", mode="ink")
BLADES = [(-150, 30, -8), (-112, 42, 7), (-66, 26, -6), (-24, 46, 10), (18, 32, -7), (60, 50, 6), (104, 28, -10),
          (148, 42, 9), (196, 34, -6), (242, 52, 8), (290, 30, -9), (336, 46, 10), (384, 34, -6), (430, 54, 7),
          (476, 30, -9), (522, 44, 9), (572, 34, -7), (618, 50, 10), (666, 28, -8), (714, 42, 7), (762, 32, -9),
          (806, 48, 9), (854, 30, -7), (902, 44, 10), (950, 34, -8), (998, 50, 7), (1046, 30, -8), (1096, 42, 9)]
S.stroke(SC, "grass", 1, [
    {"points": [[-320, 536, 1.0], [200, 530, 1.0], [700, 533, 1.0], [1280, 537, 1.0]], "width": 30, "color": "#2f7f38"}])
S.stroke(SC, "grass", 1, [
    {"points": [[x, 528, 1.0], [x + lean * 0.5, 528 - h * 0.9, 0.7], [x + lean * 1.7, 528 - h * 1.6, 0.1]],
     "width": 6, "color": "#2f7f38"} for x, h, lean in BLADES])
S.stroke(SC, "grass", 1, [
    {"points": [[x + 14, 528, 1.0], [x + 14 - lean * 0.6, 528 - h * 0.7, 0.7], [x + 14 - lean * 1.9, 528 - h * 1.2, 0.1]],
     "width": 5, "color": "#4aa046"} for x, h, lean in BLADES[::2]])

# ------------------------------------------------------------------ 9. seeds on the wind
S.add_layer(SC, "seeds", style="clean")
S.draw(SC, "seeds", 1, [flat("ellipse", x=x, y=y, rx=3.4, ry=1.6, rotation=r, fill="#ffffff", opacity=0.9)
                        for x, y, r in [(90, 400, 20), (220, 350, -15), (330, 420, 30), (450, 372, 10),
                                        (560, 410, -25), (680, 360, 15), (790, 404, -10), (40, 330, 25)]])

# ------------------------------------------------------------------ 10. motion: the sails turn, the clouds and the seeds drift
S.set_pivot(SC, "blades", x=758, y=280)
S.set_keyframe(SC, "blades", 1, rotation=0, interp="linear")
S.set_keyframe(SC, "blades", FRAMES, rotation=-150, interp="linear")
S.set_keyframe(SC, "cloud_big", 1, x=0, interp="linear")
S.set_keyframe(SC, "cloud_big", FRAMES, x=16, interp="linear")
S.set_keyframe(SC, "cloud_far", 1, x=0, interp="linear")
S.set_keyframe(SC, "cloud_far", FRAMES, x=9, interp="linear")
S.set_keyframe(SC, "seeds", 1, x=-30, y=14, interp="ease")
S.set_keyframe(SC, "seeds", 48, x=40, y=-10, interp="ease")
S.set_keyframe(SC, "seeds", FRAMES, x=120, y=-34, interp="ease")

# ------------------------------------------------------------------ 11. camera: travel with her; every plane at its own speed
S.camera_move(SC, "truck", start=1, end=FRAMES, zoom=0.90)
S.camera_move(SC, "pan", start=1, end=FRAMES, dx=150, dy=-4,
              parallax={"sky": 0.02, "cloud_far": 0.05, "cloud_big": 0.08, "sea": 0.14, "hill_far": 0.2,
                        "hill_mid": 0.45, "blades": 0.45, "hill_near": 1.0, "tufts": 1.0, "flowers": 1.5, "grass": 1.7,
                        "seeds": 1.2})

# ------------------------------------------------------------------ 12. the painted finish: real OpenToonz fx nodes
S.apply_look(SC, "ghibli", strength=0.55)

# ------------------------------------------------------------------ 13. check, then render
print("validate:", S.validate_scene(SC, render_test=False).get("issues", "ok"))
out = S.render(SC, format="png")
print("render:", out["frames"], "frames in", out["seconds"], "s, verify:", out["verify"])

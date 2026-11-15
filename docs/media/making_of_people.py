"""The README line-up: nine different people from one character builder, one build_character call each.

    python docs/media/making_of_people.py        (needs OpenToonz 1.8)
"""
from pathlib import Path

from otmcp import server as S

sc = "people_lineup"
S.create_scene(sc, width=1720, height=560, frames=2, bg_color="#f3ecdd", style="clean", overwrite=True)
PEOPLE = [
    dict(name="a", person="toddler", sex="f", outfit="dress", skin_tone=3, hair="short", colors={"top": "#f2c94c"}),
    dict(name="b", person="boy", age=8, outfit="shorts", skin_tone=8, hair="afro",
         colors={"top": "#e9573f", "bottom": "#3d5a80"}),
    dict(name="c", person="girl", age=10, outfit="dress", hat="straw", skin_tone=2, hair="bob", appeal=1.0),
    dict(name="d", person="teen_girl", outfit="tunic", hat="headscarf", skin_tone=5,
         colors={"top": "#2a9d8f", "bottom": "#264653", "hat": "#e9c46a"}, face={"nose": 0.3, "eyes": 0.4}),
    dict(name="e", person="young_man", outfit="suit", skin_tone=9, hair="short",
         colors={"top": "#33415c", "bottom": "#33415c"}, face={"nose": 0.4, "lips": 0.7, "jaw": 0.3}),
    dict(name="f", person="woman", outfit="wrap", skin_tone=2, hair="bun",
         colors={"top": "#b5179e", "bottom": "#3a0ca3", "ribbon": "#f1c453"}, face={"eyes": -0.7, "nose": -0.5}),
    dict(name="g", person="man", age=45, outfit="robe", hat="turban", skin_tone=6,
         colors={"top": "#f4f1de", "bottom": "#f4f1de", "hat": "#e07a5f"}, face={"nose": 0.8, "brow": 0.5}),
    dict(name="h", person="old_woman", outfit="long_skirt", skin_tone=4, hair="bun",
         colors={"top": "#e5989b", "bottom": "#6d6875"}),
    dict(name="i", person="old_man", outfit="coat", hat="cap", skin_tone=1,
         colors={"top": "#7f5539", "bottom": "#582f0e", "hat": "#414833"}, face={"nose": 0.5, "jaw": -0.4}),
]
for k, p in enumerate(PEOPLE):
    S.build_character(sc, p.pop("name"), x=110 + k * 186, ground_y=530, px_per_m=250, style="clean", **p)
out = S.TMP / "lineup"
S._prepare(sc)
S._tcomposer(S._scene_file(sc), out / "p..png", (1, 1))
im = S._flat(next(out.glob("p.*.png")), (243, 236, 221, 255))
im.save(Path(__file__).with_name("people-lineup.png"))
print(im.size)

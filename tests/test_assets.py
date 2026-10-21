"""Parts library, SVG import, image import/trace (slow: renders through OpenToonz)."""
import pytest

from otmcp import assets, learn, pli
from otmcp import server as S

pytestmark = pytest.mark.slow
SVG = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 120"><g transform="translate(10,10)">
<rect x="0" y="0" width="80" height="50" rx="8" fill="#ffb703" stroke="#023047" stroke-width="3"/>
<path d="M 100 20 A 30 30 0 1 1 160 20 L 130 90 Z" fill="#8ecae6"/></g></svg>'''


@pytest.fixture(autouse=True)
def keep_lessons():
    bak = learn.LESSONS.read_text(encoding="utf-8") if learn.LESSONS.exists() else None
    yield
    if bak is None:
        learn.LESSONS.unlink(missing_ok=True)
    else:
        learn.LESSONS.write_text(bak, encoding="utf-8")


def test_part_placement_and_mirror():
    assets.save_part("qa_t_dot", [{"type": "circle", "x": 10, "y": 0, "r": 4, "fill": "#000"}], anchor=(0, 0))
    out = assets.expand_parts([{"type": "part", "name": "qa_t_dot", "x": 100, "y": 50, "flip": True, "scale": 2}])
    assert out[0]["type"] == "svgpath"  # mirror goes through exact matrix
    from otmcp import vec
    xs = [p[0] for p in vec.shape_contours(out[0])[0].pts]
    assert abs((min(xs) + max(xs)) / 2 - 80) < 0.5  # x = 100 - 2*10


def test_unknown_variant_is_reported():
    assets.save_part("qa_t_v", [{"type": "circle", "x": 0, "y": 0, "r": 4}], variants={"a": []})
    with pytest.raises(ValueError, match="no variant"):
        assets.expand_parts([{"type": "part", "name": "qa_t_v", "variant": "zzz"}])


def test_svg_import_fits_box(tmp_path):
    f = tmp_path / "a.svg"
    f.write_text(SVG, encoding="utf-8")
    shapes = assets.svg_file_to_shapes(str(f), (100, 100, 400, 240))
    from otmcp import vec
    pts = [p for sh in shapes for c in vec.shape_contours(sh) for p in c.pts]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    assert min(xs) >= 99 and max(xs) <= 501 and min(ys) >= 99 and max(ys) <= 341


def test_trace_and_raster_import_render(tmp_path):
    from PIL import Image, ImageDraw
    img = tmp_path / "src.png"
    im = Image.new("RGB", (400, 300), "white")
    ImageDraw.Draw(im).ellipse([100, 50, 300, 250], fill=(230, 60, 50), outline=(0, 0, 0), width=6)
    im.save(img)
    sc = "qa_t_assets"
    S.create_scene(sc, frames=2, overwrite=True)
    S.add_layer(sc, "ras", mode="raster")
    S.add_layer(sc, "tr", mode="traced")
    S.import_image(sc, "ras", str(img), box=[0, 0, 800, 600])
    S.import_image(sc, "tr", str(img), box=[1000, 0, 800, 600], trace="outline")
    res = S.compile_vectors(sc)
    assert [c["layer"] for c in res["compiled"]] == ["tr"]
    assert len(pli.read(str(S._pli_file(S._load_state(sc), "tr")))[1]) > 0
    assert S.render(sc, "png")["verify"] == "ok"
    assert "ras" not in S.scene_context(sc).get("pending_compile", [])

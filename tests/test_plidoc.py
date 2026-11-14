"""The .pli stroke editor: it must write exactly what OpenToonz reads (format facts in docs/FORMAT_NOTES.md)."""
import struct

import pytest

from otmcp import pli
from otmcp.plidoc import CHAIN, GROUP, IMAGE, INTERSECTION, PRECISION, PliDoc, Tag, _wr, sample, through_points

LINE = [(0.0, 0.0, 1.0), (5.0, 2.0, 1.5), (10.0, 0.0, 0.5)]


def doc_with(frame=1, n=2):
    d = PliDoc.new()
    d.append(frame, [{"points": [(x + i, y, t) for x, y, t in LINE], "style": 1} for i in range(n)])
    return d


def test_new_level_has_the_default_palette_and_no_drawing():
    d = PliDoc.new()
    assert d.frames() == [] and d.styles() == {1: (0, 0, 0, 255)}
    assert d.to_bytes()[:6] == b"PLIM" + bytes([71, 0])


def test_strokes_survive_a_save_and_agree_with_the_old_reader(tmp_path):
    d = doc_with(n=3)
    f = tmp_path / "a.pli"
    d.save(str(f))
    again = PliDoc.load(str(f))
    assert again.to_bytes() == f.read_bytes()  # a loaded file is written back byte for byte
    got = again.strokes(1)
    assert [s["index"] for s in got] == [0, 1, 2] and all(s["style"] == 1 and not s["loop"] for s in got)
    for (x, y, t), (gx, gy, gt) in zip(LINE, got[0]["points"]):
        assert abs(gx - x) < 1e-4 and abs(gy - y) < 1e-4 and abs(gt - t) < 0.01
    _, old = pli.read(str(f))
    assert [[(round(x, 6), round(y, 6)) for x, y, _ in s.points] for s in old] == \
           [[(round(x, 6), round(y, 6)) for x, y, _ in s["points"]] for s in got]


def test_append_keeps_existing_strokes_and_orders_drawings(tmp_path):
    d = doc_with(frame=5, n=1)
    first = d.strokes(5)[0]["points"]
    assert d.append(5, [{"points": LINE, "style": d.style_for((200, 0, 0, 255)), "loop": True}]) == [1]
    d.append(2, [{"points": LINE}])
    f = tmp_path / "b.pli"
    d.save(str(f))
    d = PliDoc.load(str(f))
    assert d.frames() == [2, 5]
    assert [t.payload[:2] for t in d.tags if t.type == IMAGE] == [struct.pack("<H", 2), struct.pack("<H", 5)]
    five = d.strokes(5)
    assert five[0]["points"] == first and five[1]["loop"] and d.styles()[five[1]["style"]] == (200, 0, 0, 255)
    assert struct.unpack_from("<H", f.read_bytes(), 12)[0] == 2  # frame count in the header


def test_same_colour_reuses_its_style():
    d = PliDoc.new()
    a = d.style_for((10, 20, 30, 255))
    assert d.style_for((10, 20, 30, 255)) == a == 2 and d.style_for((0, 0, 0, 255)) == 1
    assert d.style_for((10, 20, 30, 128)) == 3


def test_offsets_are_rewritten_when_the_file_grows(tmp_path):
    """Every GROUP and IMAGE tag names its children by file offset; a big drawing inserted first moves them all."""
    d = doc_with(frame=9, n=2)
    before = d.strokes(9)
    long_line = [(i * 0.5, (i % 7) * 0.3, 1.0) for i in range(4001)]
    d.append(1, [{"points": long_line} for _ in range(4)])  # pushes drawing 9 past offset 32767 (4-byte offsets)
    f = tmp_path / "c.pli"
    d.save(str(f))
    assert f.stat().st_size > 70000
    again = PliDoc.load(str(f))
    assert again.strokes(9) == before and len(again.strokes(1)) == 4
    assert again.to_bytes() == f.read_bytes()


def test_remove_renumbers_and_drops_an_emptied_group():
    d = doc_with(n=3)
    image = d._image(1)
    lone = [r for r in image.refs if r.type == CHAIN][1]
    group = Tag(GROUP, b"\x01", refs=[lone])  # OpenToonz wraps imported shapes in a group of one stroke
    image.refs[image.refs.index(lone)] = group
    d.tags.insert(d.tags.index(image), group)
    keep = [s["points"] for s in d.strokes(1)]
    d.remove(1, 1)
    assert [s["points"] for s in d.strokes(1)] == [keep[0], keep[2]]
    assert group not in d.tags and lone not in d.tags
    PliDoc_bytes = d.to_bytes()
    assert PliDoc_bytes.endswith(b"\x00")
    with pytest.raises(ValueError):
        d.remove(1, 5)


def test_remove_is_refused_under_a_painted_region():
    d = doc_with(n=3)
    image = d._image(1)
    # region data with one branch on stroke 1: count, then strokeIndex, currInter, nextBranch, style, w = 0
    region = Tag(INTERSECTION, struct.pack("<I", 1) + _wr(1, 1) + _wr(1, 1) + _wr(0, 1) + b"\x02\x00" + b"\x80", 1)
    d.tags.insert(d.tags.index(image), region)
    image.refs.append(region)
    assert d._region_strokes(image) == {1}
    for idx in (0, 1):
        with pytest.raises(ValueError, match="painted"):
            d.remove(1, idx)
    d.remove(1, 2)  # a stroke after every region stroke can go
    d.append(1, [{"points": LINE}])
    assert d._image(1).refs[-1] is region  # new strokes go before the region data, as OpenToonz writes it


def test_new_drawing_gets_the_precision_opentoonz_uses():
    d = doc_with()
    prec = [r for r in d._image(1).refs if r.type == PRECISION][0]
    assert prec.payload == b"\x00\x40"  # 16384 = 128 squared
    assert through_points([(0, 0, 1), (4, 0, 1)]) == [(0.0, 0.0, 1.0), (2.0, 0.0, 1.0), (4.0, 0.0, 1.0)]


def test_bad_input_is_rejected(tmp_path):
    d = PliDoc.new()
    with pytest.raises(ValueError):
        d.append(1, [{"points": LINE[:2]}])
    with pytest.raises(ValueError):
        d.append(0, [{"points": LINE}])
    with pytest.raises(ValueError):
        through_points([(1, 1, 1), (1, 1, 1)])
    f = tmp_path / "old.pli"
    f.write_bytes(b"PLIM" + bytes([5, 8]) + b"\x00" * 20)
    with pytest.raises(ValueError, match="not supported"):
        PliDoc.load(str(f))
    good = tmp_path / "g.pli"
    doc_with().save(str(good))
    data = bytearray(good.read_bytes())
    data[-2] ^= 1  # the last child offset of the IMAGE tag now points between tags
    good.write_bytes(bytes(data))
    with pytest.raises(ValueError, match="not a tag"):
        PliDoc.load(str(good))


@pytest.mark.parametrize("closed", [False, True])
def test_smooth_line_passes_through_every_point(closed):
    pts = [(0, 0, 1.0), (10, 8, 2.0), (20, 0, 1.0), (30, -8, 0.5), (40, 0, 1.0)]
    ctrl = through_points(pts, closed)
    assert len(ctrl) % 2 == 1 and len(ctrl) == 1 + 4 * (len(pts) if closed else len(pts) - 1)
    on_curve = ctrl[::4]
    for p in pts:
        assert any(abs(p[0] - q[0]) < 1e-9 and abs(p[1] - q[1]) < 1e-9 for q in on_curve)
    line = sample(ctrl, 8)
    assert max(abs(y) for _, y, _ in line) < 10.5 and all(t >= 0 for _, _, t in line)  # no wild overshoot
    if closed:
        assert ctrl[0][:2] == ctrl[-1][:2]

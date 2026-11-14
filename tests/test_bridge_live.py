"""The live link: strokes drawn inside a running OpenToonz through the otbridge plugin.

These tests open the OpenToonz window. They run only when the plugin is installed
(<OpenToonz>/imageformats/otbridge.dll, built by bridge/build.py). pytest -m slow
"""
import os
import subprocess
import time

import pytest

from otmcp import bridge, config, learn
from otmcp import server as S
from otmcp.plidoc import PliDoc

PLUGIN = config.opentoonz_home() / "imageformats" / "otbridge.dll"
pytestmark = [pytest.mark.slow,
              pytest.mark.skipif(not PLUGIN.exists(), reason="otbridge plugin is not installed in OpenToonz")]
RED = [208, 32, 32, 255]


@pytest.fixture(autouse=True)
def keep_lessons():
    bak = learn.LESSONS.read_text(encoding="utf-8") if learn.LESSONS.exists() else None
    yield
    if bak is None:
        learn.LESSONS.unlink(missing_ok=True)
    else:
        learn.LESSONS.write_text(bak, encoding="utf-8")


@pytest.fixture
def opentoonz():
    """Start OpenToonz on a scene and wait until the bridge reports that scene; stop it afterwards."""
    procs = []

    def start(scene):
        if bridge.open_scene() is not None:
            pytest.skip("an OpenToonz with the bridge is already running; close it to run the live tests")
        sf = S._scene_file(scene)
        p = subprocess.Popen([str(config.exe("OpenToonz")), str(sf)], cwd=str(config.opentoonz_home()),
                             env=dict(os.environ, OTBRIDGE="1"))
        procs.append(p)
        for _ in range(120):
            if p.poll() is not None:
                pytest.fail("OpenToonz exited while starting")
            if bridge.has_open(sf):
                return p
            time.sleep(1)
        pytest.fail("the bridge did not report the scene within two minutes")

    yield start
    for p in procs:
        if p.poll() is None:
            p.terminate()
            p.wait(30)


def scene_with_a_line(name):
    S.create_scene(name, width=640, height=360, frames=4, overwrite=True)
    S.add_layer(name, "hand", mode="ink")
    S.stroke(name, "hand", 1, [{"points": [[100, 300], [540, 300]], "width": 20, "color": "#2040d0", "smooth": False}])
    return name


def test_no_bridge_means_file_mode():
    assert bridge.open_scene() is None or True  # never raises when nothing listens
    os.environ["OTMCP_BRIDGE"] = "0"
    try:
        assert bridge.call("ping") is None and not bridge.has_open("x.tnz")
        assert S.opentoonz_live()["connected"] is False
    finally:
        del os.environ["OTMCP_BRIDGE"]


def test_strokes_go_into_the_open_drawing_and_reach_the_file_on_save(opentoonz):
    sc = scene_with_a_line("qa_live_draw")
    pf = S._pli_file(S._load_state(sc), "hand")
    opentoonz(sc)
    st = S.opentoonz_live()
    assert st["connected"] and st["scene"] == sc and st["unsaved"] is False and st["level"] == "hand"

    res = S.stroke(sc, "hand", 1, [{"points": [[150, 80], [320, 40], [490, 80]], "width": 8, "color": "#d02020"}])
    assert res["strokes"] == [1] and res["strokes_in_drawing"] == 2 and "live" in res
    assert len(PliDoc.load(str(pf)).strokes(1)) == 1  # the file has not changed: OpenToonz holds the stroke
    rd = S.read_drawing(sc, "hand", 1)
    assert rd["source"].startswith("OpenToonz") and [s["by"] for s in rd["strokes"]] == ["mcp", "mcp"]
    assert rd["strokes"][1]["color"] == "#d02020" and abs(rd["strokes"][1]["width"] - 8) < 0.2
    assert rd["strokes"][1]["points"][0][:2] == [150.0, 80.0]
    assert S.opentoonz_live()["unsaved"] is True

    S.opentoonz_live("undo")  # the same undo the user has in OpenToonz
    assert len(S.read_drawing(sc, "hand", 1)["strokes"]) == 1
    S.opentoonz_live("redo")
    assert len(S.read_drawing(sc, "hand", 1)["strokes"]) == 2

    S.erase_stroke(sc, "hand", 1, [0])
    left = S.read_drawing(sc, "hand", 1)["strokes"]
    assert [(s["index"], s["color"]) for s in left] == [(0, "#d02020")]

    assert S.opentoonz_live("save") == {"connected": True, "done": "save", "unsaved": False}
    on_disk = PliDoc.load(str(pf))
    assert len(on_disk.strokes(1)) == 1 and tuple(RED) in on_disk.styles().values()
    assert S._ink_sig(on_disk.strokes(1)[0]["points"]) in S._load_state(sc)["layers"]["hand"]["ink_sigs"]["1"]


def test_live_refusals_come_back_as_clear_errors(opentoonz):
    sc = scene_with_a_line("qa_live_refuse")
    opentoonz(sc)
    with pytest.raises(RuntimeError, match="not on the xsheet"):
        S.stroke(sc, "hand", 7, [{"points": [[10, 10], [90, 90]]}])  # drawing 7 is not exposed anywhere
    with pytest.raises(RuntimeError, match="strokes"):
        S.erase_stroke(sc, "hand", 1, [5])
    assert len(S.read_drawing(sc, "hand", 1)["strokes"]) == 1

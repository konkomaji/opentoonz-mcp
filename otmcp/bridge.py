"""Client for otbridge, the plugin that runs inside OpenToonz (bridge/otbridge.cpp).

One JSON object per line each way, over the local socket "otmcp-bridge": a named pipe on Windows, a Unix
socket elsewhere (that is what Qt's QLocalServer makes). When the plugin is not installed, or OpenToonz is
not running, there is simply nothing to connect to and every caller falls back to working on the files.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

NAME = "otmcp-bridge"


class BridgeError(RuntimeError):
    """OpenToonz answered, and the answer was a refusal (the message is OpenToonz's)."""


def _address() -> str:
    if sys.platform == "win32":
        return "\\\\.\\pipe\\" + NAME
    return str(Path(tempfile.gettempdir()) / NAME)  # QLocalServer puts a relative name in the temp folder


def _exchange(line: bytes) -> bytes:
    if sys.platform == "win32":
        with open(_address(), "r+b", buffering=0) as f:
            f.write(line)
            return f.readline()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(_address())
        s.sendall(line)
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        return buf


def call(op: str, timeout: float = 20.0, **args) -> dict | None:
    """Run one command inside OpenToonz. None: no bridge is listening. BridgeError: OpenToonz said no.
    TimeoutError: OpenToonz did not answer, which means its window is busy (a dialog is open, or it is saving)."""
    if os.environ.get("OTMCP_BRIDGE") == "0":
        return None
    box: dict = {}

    def work():
        try:
            box["raw"] = _exchange((json.dumps({"op": op, **args}) + "\n").encode())
        except (FileNotFoundError, ConnectionRefusedError):
            box["absent"] = True
        except OSError as e:
            # ERROR_PIPE_BUSY and friends: the server is there but did not take the connection
            box["absent" if getattr(e, "winerror", None) in (2, 3) else "error"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError("OpenToonz did not answer the bridge: its window is busy (close any open dialog)")
    if box.get("absent"):
        return None
    if "error" in box:
        raise BridgeError(f"could not talk to OpenToonz: {box['error']}")
    try:
        res = json.loads(box.get("raw") or b"")
    except ValueError as e:
        raise BridgeError("OpenToonz closed the bridge connection without an answer") from e
    if not res.get("ok"):
        raise BridgeError(res.get("error", "OpenToonz refused the command"))
    return res


def open_scene() -> dict | None:
    """What OpenToonz has open right now (scene, scene_path, unsaved, the cell under the cursor), or None."""
    try:
        return call("context", timeout=5.0)
    except (BridgeError, TimeoutError):
        return None


def has_open(scene_file: str | os.PathLike) -> bool:
    """True when the running OpenToonz has this very scene file open, so its memory, not the file, is the truth."""
    ctx = open_scene()
    if not ctx or not ctx.get("scene_path"):
        return False
    try:
        return os.path.samefile(ctx["scene_path"], scene_file)
    except OSError:
        return os.path.normcase(os.path.abspath(ctx["scene_path"])) == os.path.normcase(os.path.abspath(str(scene_file)))

"""Self-improvement memory shared across all scenes.

Lessons come from three places and are ranked by how often they recur:
  * automatic: tool errors, verification problems, studio-rule warnings, render review
  * the model: learn() after reviewing its own output
  * the user: feedback() ratings/comments (stored as preferences, ranked highest)
Style overrides (tune_style) persist and change the defaults every later job uses.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
from pathlib import Path

WORK = Path(os.environ.get("OTMCP_WORK", r"D:\otmcp\work"))
LESSONS = WORK / "knowledge" / "lessons.json"
STYLE_OVERRIDES = WORK / "knowledge" / "styles_custom.json"


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _key(text: str) -> str:
    """Normalise a message so recurrences of the same issue share one lesson."""
    t = text.lower()
    t = re.sub(r"'[^']*'", "'*'", t)
    t = re.sub(r"\d+(\.\d+)?", "#", t)
    return re.sub(r"\s+", " ", t).strip()[:160]


KIND_WEIGHT = {"preference": 5.0, "insight": 3.0, "quality": 2.0, "warning": 1.0, "error": 1.5}


def add(kind: str, text: str, scene: str | None = None, tags: list[str] | None = None,
        advice: str | None = None) -> dict:
    data = _load(LESSONS, {})
    k = f"{kind}:{_key(text)}"
    e = data.get(k) or {"kind": kind, "text": text, "count": 0, "first": _now(), "scenes": [], "tags": tags or []}
    e["count"] += 1
    e["last"] = _now()
    e["text"] = text
    if advice:
        e["advice"] = advice
    if scene and scene not in e["scenes"]:
        e["scenes"] = (e["scenes"] + [scene])[-10:]
    data[k] = e
    _save(LESSONS, data)
    return e


def top(n: int = 12, query: str | None = None) -> list[dict]:
    data = list(_load(LESSONS, {}).values())
    if query:
        q = query.lower()
        data = [e for e in data if q in e["text"].lower() or q in " ".join(e.get("tags", [])).lower()
                or q in e.get("advice", "").lower()]
    data.sort(key=lambda e: (KIND_WEIGHT.get(e["kind"], 1) * (1 + min(e["count"], 20) / 4), e.get("last", "")),
              reverse=True)
    return [{k: e[k] for k in ("kind", "text", "count", "advice", "last") if k in e} for e in data[:n]]


# ----------------------------------------------------------------- automatic advice for known issues
ADVICE = [
    (r"file missing", "Vector layers are compiled on preview/render; call compile_vectors if this persists."),
    (r"pan .*px/frame", "Keep pans <=10 px/frame on detailed BGs; lengthen the pan or use parallax layers."),
    (r"whip pan", "Whip pans need directional blur, or slow them under 32 px/frame."),
    (r"flat/blank|nothing visible", "Check layer visibility, exposure (empty cells) and that shapes are inside the canvas."),
    (r"photosensitivity", "Reduce rapid full-screen brightness changes to <=3 per second."),
    (r"pop at frame", "A single-frame jump: add an inbetween or ease the key before that frame."),
    (r"manual timing", "Layer was retimed in OpenToonz; use set_exposure to expose new drawings."),
    (r"edited in opentoonz", "Respect GUI edits: draw on a new layer, or ask the user before force=true."),
    (r"shapes pop instead of morphing", "Give key drawings the same number of shapes in the same order."),
    (r"on ones", "Anime style reads better on 2s/3s; keep 1s for fast action and camera moves."),
    (r"bg plate|exposes", "Make BG drawings larger than the camera by the move/shake amount."),
]


def advice_for(text: str) -> str | None:
    for pat, adv in ADVICE:
        if re.search(pat, text, re.I):
            return adv
    return None


def record_issues(kind: str, issues, scene: str | None) -> None:
    if not issues or issues == "ok" or issues == "none":
        return
    for it in issues if isinstance(issues, list) else [issues]:
        s = str(it)
        if "(vector levels are built" in s:
            continue
        add(kind, s, scene, advice=advice_for(s))


# ----------------------------------------------------------------- style overrides (auto-tuned defaults)
def style_overrides() -> dict:
    return _load(STYLE_OVERRIDES, {})


def tune_style(style: str, changes: dict, reason: str) -> dict:
    data = style_overrides()
    cur = data.get(style, {})
    cur.update(changes)
    data[style] = cur
    _save(STYLE_OVERRIDES, data)
    add("preference", f"style '{style}' tuned: {changes} because {reason}", tags=["style", style])
    return cur

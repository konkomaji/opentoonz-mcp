"""Studio style presets. Numbers from docs/studio_practices.md ("Rules to encode").

All pixel values assume a 1080p camera and scale with canvas height.
"""
from __future__ import annotations

import colorsys

STYLES: dict[str, dict] = {
    # Japanese TV/feature anime (Ghibli / KyoAni-like clean line)
    "anime": {"line": 2.0, "interior": 0.7, "wobble": 0.4, "wavelength": 80, "taper_start": 0.10,
              "taper_end": 0.15, "min_ratio": 0.35, "pressure_var": 0.15, "loop_var": 0.15,
              "exposure": 3, "squash": (0.6, 1.6), "shadow": True, "overshoot_ends": 0.25},
    # Western feature (Disney/Richard Williams): bolder silhouette, strong taper
    "feature": {"line": 3.5, "interior": 0.57, "wobble": 0.5, "wavelength": 80, "taper_start": 0.15,
                "taper_end": 0.15, "min_ratio": 0.2, "pressure_var": 0.25, "loop_var": 0.3,
                "exposure": 2, "squash": (0.8, 1.25), "shadow": False, "overshoot_ends": 0.25},
    # TV cartoon: thick even line, very squashy
    "cartoon": {"line": 5.0, "interior": 0.6, "wobble": 0.8, "wavelength": 60, "taper_start": 0.12,
                "taper_end": 0.12, "min_ratio": 0.35, "pressure_var": 0.2, "loop_var": 0.2,
                "exposure": 2, "squash": (0.5, 2.0), "shadow": False, "overshoot_ends": 0.3},
    # Rough pencil / storyboard feel
    "sketch": {"line": 2.5, "interior": 0.7, "wobble": 1.6, "wavelength": 45, "taper_start": 0.25,
               "taper_end": 0.3, "min_ratio": 0.1, "pressure_var": 0.45, "loop_var": 0.5,
               "exposure": 2, "squash": (0.7, 1.4), "shadow": False, "overshoot_ends": 0.8},
    # Mechanical / clean (no humanising)
    "clean": {"line": 3.0, "interior": 0.7, "wobble": 0.0, "wavelength": 80, "taper_start": 0.0,
              "taper_end": 0.0, "min_ratio": 1.0, "pressure_var": 0.0, "loop_var": 0.0,
              "exposure": 1, "squash": (0.8, 1.25), "shadow": False, "overshoot_ends": 0.0},
}


def get(name: str | None) -> dict:
    """Preset merged with persistent learned overrides (learn.tune_style)."""
    from . import learn
    key = name if name in STYLES else "anime"
    st = dict(STYLES[key])
    st.update(learn.style_overrides().get(key, {}))
    if isinstance(st.get("squash"), list):
        st["squash"] = tuple(st["squash"])
    return st


# ---------------------------------------------------------------- colour design (iro-shitei)
def _hsv(rgb):
    r, g, b = (c / 255 for c in rgb[:3])
    return colorsys.rgb_to_hsv(r, g, b)


def _rgb(h, s, v, a=255):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0, min(1, s)), max(0, min(1, v)))
    return (round(r * 255), round(g * 255), round(b * 255), a)


def _toward(h: float, target_deg: float, amount_deg: float) -> float:
    """Rotate hue h (0..1) toward target hue by at most amount_deg."""
    d = ((target_deg - h * 360 + 180) % 360) - 180
    step = max(-amount_deg, min(amount_deg, d))
    return (h * 360 + step) / 360.0


def shadow_color(rgba, warm: bool | None = None):
    """kage: hue 8deg toward blue (toward red for warm skin tones), sat +10%, value -18%."""
    h, s, v = _hsv(rgba)
    if warm is None:
        warm = 0.0 <= h * 360 <= 50 and s < 0.6
    return _rgb(_toward(h, 0 if warm else 240, 8), s * 1.10 + 0.02, v * 0.82, rgba[3])


def highlight_color(rgba):
    h, s, v = _hsv(rgba)
    return _rgb(h, s * 0.85, v * 1.15 + 0.03, rgba[3])


def trace_line_color(rgba):
    """iro-tore coloured line: adjacent fill, value -25%."""
    h, s, v = _hsv(rgba)
    return _rgb(h, s, v * 0.75, 255)


def lighting(rgba, condition: str):
    """Palette variant per lighting condition (not a comp tint)."""
    h, s, v = _hsv(rgba)
    if condition == "evening":
        return _rgb(_toward(h, 30, 10), s, v * 0.85, rgba[3])
    if condition == "night":
        return _rgb(_toward(h, 240, 30), s * 0.8, v * 0.6, rgba[3])
    return tuple(rgba)

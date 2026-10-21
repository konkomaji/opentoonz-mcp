"""Locate OpenToonz and the MCP work folder.

Order of precedence for every path: environment variable > OpenToonz's own registry entries
(Windows) > the standard install location.

  OPENTOONZ_HOME   folder containing OpenToonz.exe and tcomposer.exe
  OPENTOONZ_STUFF  OpenToonz "stuff" folder (TOONZROOT; holds the sandbox project)
  OTMCP_WORK       MCP data: state, journal, snapshots, renders, knowledge
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _reg(root, key: str, value: str) -> str | None:
    try:
        import winreg
        with winreg.OpenKey(root, key) as k:
            return str(winreg.QueryValueEx(k, value)[0])
    except (ImportError, OSError):
        return None


def _uninstall_location() -> str | None:
    try:
        import winreg
    except ImportError:
        return None
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for base in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                     r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"):
            try:
                with winreg.OpenKey(root, base) as k:
                    for i in range(winreg.QueryInfoKey(k)[0]):
                        sub = winreg.EnumKey(k, i)
                        name = _reg(root, f"{base}\\{sub}", "DisplayName") or ""
                        if name.lower().startswith("opentoonz"):
                            loc = _reg(root, f"{base}\\{sub}", "InstallLocation")
                            if loc and Path(loc, "tcomposer.exe").exists():
                                return loc
            except OSError:
                continue
    return None


def opentoonz_home() -> Path:
    env = os.environ.get("OPENTOONZ_HOME")
    if env:
        return Path(env)
    found = _uninstall_location()
    if found:
        return Path(found)
    for cand in (r"C:\Program Files\OpenToonz", r"D:\OpenToonz", "/Applications/OpenToonz.app/Contents/MacOS"):
        if Path(cand).exists():
            return Path(cand)
    return Path(r"C:\Program Files\OpenToonz")


def opentoonz_stuff() -> Path:
    env = os.environ.get("OPENTOONZ_STUFF")
    if env:
        return Path(env)
    if sys.platform == "win32":
        import winreg
        root = _reg(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\OpenToonz\OpenToonz", "TOONZROOT")
        if root:
            return Path(root)
    for cand in (r"C:\OpenToonz stuff", r"D:\OpenToonz stuff", str(Path.home() / ".config" / "OpenToonz" / "stuff")):
        if Path(cand).exists():
            return Path(cand)
    return Path(r"C:\OpenToonz stuff")


def work_dir() -> Path:
    env = os.environ.get("OTMCP_WORK")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "opentoonz-mcp"


def exe(name: str) -> Path:
    """OpenToonz executable path ('OpenToonz' or 'tcomposer'), with .exe on Windows."""
    return opentoonz_home() / (f"{name}.exe" if sys.platform == "win32" else name)

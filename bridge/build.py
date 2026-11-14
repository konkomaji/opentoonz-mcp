"""Build otbridge.dll, the Qt plugin that lets the MCP server draw inside a running OpenToonz 1.8.0.

It needs three things, all of which must match the OpenToonz you run:
  * the OpenToonz install (its DLLs give the import libraries),
  * the OpenToonz source of the SAME version (headers only; nothing in it is compiled),
  * the Qt SDK of the SAME version and compiler as the Qt DLLs OpenToonz ships (5.15.2, msvc2019_64).
and Visual Studio Build Tools with the C++ workload.

  python bridge/build.py --opentoonz D:\\OpenToonz --source D:\\src\\opentoonz --qt D:\\Qt\\5.15.2\\msvc2019_64
  python bridge/build.py ... --install      also copies the DLL to <OpenToonz>\\imageformats
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LIBS = ["tnzcore", "tnzbase", "toonzlib", "tnztools", "toonzqt"]
QT_MODULES = ["QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtOpenGL", "QtMultimedia", "QtXml"]


def vcvars() -> Path:
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft Visual Studio/Installer/vswhere.exe"
    if not vswhere.exists():
        sys.exit("Visual Studio Build Tools are not installed (vswhere.exe not found)")
    root = subprocess.run([str(vswhere), "-latest", "-products", "*", "-property", "installationPath"],
                          capture_output=True, text=True).stdout.strip()
    bat = Path(root) / "VC/Auxiliary/Build/vcvars64.bat"
    if not bat.exists():
        sys.exit(f"the C++ build tools are missing from {root}")
    return bat


def msvc_env() -> dict[str, str]:
    out = subprocess.run(f'cmd /s /c ""{vcvars()}" >nul && set"', capture_output=True, text=True, shell=True).stdout
    env = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    if "VCINSTALLDIR" not in env:
        sys.exit("vcvars64.bat did not set up the compiler")
    return env


def tool(name: str, env: dict[str, str]) -> str:
    """Windows starts a program from the PATH of this process, not of env, so look it up in env's PATH."""
    if Path(name).is_absolute():
        return name
    path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    found = shutil.which(name, path=path)
    if not found:
        sys.exit(f"{name} not found in the Visual Studio environment")
    return found


def run(cmd: list[str], env: dict[str, str], cwd: Path, log: Path) -> None:
    cmd = [tool(cmd[0], env)] + cmd[1:]
    r = subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True, errors="replace")
    log.write_text(" ".join(cmd) + "\n\n" + r.stdout + r.stderr, encoding="utf-8")
    if r.returncode:
        tail = "\n".join((r.stdout + r.stderr).splitlines()[-40:])
        sys.exit(f"FAILED ({log}):\n{tail}")


def import_lib(dll: Path, out: Path, env: dict[str, str]) -> Path:
    """An import library from the export table of a shipped DLL (OpenToonz does not ship .lib files)."""
    dump = subprocess.run([tool("dumpbin", env), "/nologo", "/exports", str(dll)], env=env, capture_output=True,
                          text=True).stdout
    names = re.findall(r"^\s+\d+\s+[0-9A-F]+\s+[0-9A-F]{8}\s+(\S+)", dump, re.M)
    if not names:
        sys.exit(f"no exports found in {dll}")
    d = out / f"{dll.stem}.def"
    d.write_text(f"LIBRARY {dll.name}\nEXPORTS\n" + "\n".join(f"    {n}" for n in names) + "\n", encoding="ascii")
    lib = out / f"{dll.stem}.lib"
    run(["lib", "/nologo", f"/def:{d}", "/machine:x64", f"/out:{lib}"], env, out, out / f"{dll.stem}.lib.log")
    return lib


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--opentoonz", required=True, help="OpenToonz install folder (has OpenToonz.exe)")
    ap.add_argument("--source", required=True, help="OpenToonz source checkout of the same version")
    ap.add_argument("--qt", required=True, help="Qt kit folder, e.g. D:\\Qt\\5.15.2\\msvc2019_64")
    ap.add_argument("--out", default=str(HERE / "build"), help="build folder")
    ap.add_argument("--install", action="store_true", help="copy otbridge.dll to <OpenToonz>\\imageformats")
    a = ap.parse_args()
    ot, src, qt, out = Path(a.opentoonz), Path(a.source) / "toonz/sources", Path(a.qt), Path(a.out)
    for need in (ot / "OpenToonz.exe", src / "include/tools/tool.h", qt / "bin/moc.exe", qt / "lib/Qt5Core.lib"):
        if not need.exists():
            sys.exit(f"not found: {need}")
    out.mkdir(parents=True, exist_ok=True)
    env = msvc_env()
    libs = [import_lib(ot / f"{n}.dll", out, env) for n in LIBS]
    # The plugin checks this digest when it starts and stays off inside any other build of OpenToonz.
    h = hashlib.sha256()
    for n in LIBS:
        h.update((ot / f"{n}.dll").read_bytes())
    r = subprocess.run([str(ot / "tcomposer.exe"), "-version"], capture_output=True, text=True, cwd=ot)
    ver = " ".join((r.stdout + r.stderr).split())[:60]
    label = (ver or "OpenToonz").replace('"', "'").replace("\\", "/") + ", " + h.hexdigest()[:12]
    (out / "otbridge_build.h").write_text(
        f'#define OTBRIDGE_HOST_DIGEST "{h.hexdigest()}"\n#define OTBRIDGE_HOST_LABEL "{label}"\n'
        "#define OTBRIDGE_HOST_DLLS " + ", ".join(f'"{n}.dll"' for n in LIBS) + "\n", encoding="utf-8")
    inc = [src / "include", qt / "include"] + [qt / "include" / m for m in QT_MODULES] + [out, HERE]
    run([str(qt / "bin/moc.exe"), str(HERE / "otbridge.cpp"), "-o", str(out / "otbridge.moc")]
        + [f"-I{p}" for p in inc], env, out, out / "moc.log")
    defs = ["NOMINMAX", "_USE_MATH_DEFINES", "VC_EXTRALEAN", "TNZ_LITTLE_ENDIAN=1", "QT_SHARED", "UNICODE", "_UNICODE",
            "WIN32", "_WIN32", "x64", "NDEBUG",
            "QT_NO_DEBUG", "QT_PLUGIN", "QT_CORE_LIB", "QT_GUI_LIB", "QT_WIDGETS_LIB", "QT_NETWORK_LIB"]
    cmd = (["cl", "/nologo", "/LD", "/MD", "/O2", "/EHsc", "/std:c++17", "/permissive-", "/Zc:__cplusplus", "/utf-8",
            "/wd4251", "/wd4275", "/wd4005"]
           + [f"/D{d}" for d in defs] + [f"/I{p}" for p in inc]
           + [str(HERE / "otbridge.cpp"), f"/Fe{out / 'otbridge.dll'}", f"/Fo{out / 'otbridge.obj'}", "/link"]
           + [str(p) for p in libs]
           + [str(qt / "lib" / f"Qt5{m}.lib") for m in ("Core", "Gui", "Widgets", "Network")])
    run(cmd, env, out, out / "cl.log")
    dll = out / "otbridge.dll"
    print(f"built {dll} ({dll.stat().st_size} bytes)")
    if a.install:
        dst = ot / "imageformats"
        dst.mkdir(exist_ok=True)
        shutil.copyfile(dll, dst / "otbridge.dll")
        print(f"installed {dst / 'otbridge.dll'}")
        print("restart OpenToonz; the bridge starts with it (set OTBRIDGE=0 to keep it off)")


if __name__ == "__main__":
    main()

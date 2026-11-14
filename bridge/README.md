# otbridge: draw live inside OpenToonz

OpenToonz has no way for another program to talk to it while it runs. It has no command socket, its script API only works on files, and it does not reload a level that changed on disk. I checked all three in the 1.8.0 binaries.

`otbridge` is a small plugin that adds that missing channel. With it, the assistant's `stroke`, `read_drawing` and `erase_stroke` work on the drawing that is open in my OpenToonz window:

- A stroke shows up in the viewer at once. No Revert Scene.
- `read_drawing` sees what is on screen, including strokes I have not saved.
- Every change is one normal undo step. Ctrl+Z in OpenToonz takes it back.
- Erasing goes through OpenToonz, so it rebuilds painted areas itself.

Without the plugin nothing breaks. The same tools work on the level files, as before.

## Status

| | |
|---|---|
| OpenToonz 1.8.0, official Windows build | built and tested (`tests/test_bridge_live.py` drives a real OpenToonz window) |
| Any other version, fork or platform | not built yet. See "Other builds" below. |

## Build and install (Windows)

The plugin calls C++ classes of OpenToonz directly. It therefore has to be compiled for the exact OpenToonz you run. You need:

1. Visual Studio Build Tools with the C++ workload.
2. The Qt SDK that matches the Qt DLLs in your OpenToonz folder. For 1.8.0 that is Qt 5.15.2, `msvc2019_64`. `pip install aqtinstall`, then `aqt install-qt windows desktop 5.15.2 win64_msvc2019_64 -O D:\Qt`.
3. The OpenToonz source of the same version, for its headers only. Nothing in it is compiled:
   `git clone --depth 1 --branch v1.8.0 --filter=blob:none --sparse https://github.com/opentoonz/opentoonz` and then `git sparse-checkout set toonz/sources/include`.

Then:

```
python bridge/build.py --opentoonz D:\OpenToonz --source D:\src\opentoonz --qt D:\Qt\5.15.2\msvc2019_64 --install
```

`--install` copies `otbridge.dll` into `<OpenToonz>\imageformats`. Restart OpenToonz. To check, ask the assistant for `opentoonz_live()`, or look at `%TEMP%\otbridge.log`.

To remove it, delete `<OpenToonz>\imageformats\otbridge.dll`. To keep it installed but off, start OpenToonz with the environment variable `OTBRIDGE=0`.

## Safety

- **It only runs inside the build it was compiled for.** `build.py` records a SHA-256 of the OpenToonz libraries it linked against. When the plugin starts it hashes the libraries of the running OpenToonz. If they differ (an update, another version, a fork) it writes one line to the log and stays off. OpenToonz runs normally and the tools use the files.
- **It listens only for the current user.** The socket is a local named pipe (`otmcp-bridge`) with user-only access. Nothing listens on the network.
- **It changes drawings only through OpenToonz's own calls**, on the GUI thread, with an undo step for each change.
- **It never saves by itself.** `opentoonz_live('save')` runs File > Save All, and only when asked.

## How it is loaded

It is an image-format plugin that registers for `png` and then tells Qt it cannot read anything, so Qt goes on to its real PNG reader. Qt loads it the first time OpenToonz reads a PNG, which happens during start-up. The bridge then starts once, on the GUI thread.

A "generic" Qt plugin would be the obvious kind. I tried it first: the Qt that OpenToonz 1.8.0 ships finds a generic plugin but does not create it from `QT_QPA_GENERICPLUGINS` or `-plugin`.

## Protocol

One JSON object per line each way. Coordinates are OpenToonz vector units (origin at the canvas centre, +y up); stroke points are the control points of the quadratic chain, `[x, y, half width]`.

| op | arguments | does |
|---|---|---|
| `ping` | | protocol version and the build the plugin was compiled for |
| `context` | | open scene, its path, unsaved flag, the cell under the cursor |
| `read` | target | every stroke of the drawing, with style and solid colour |
| `stroke` | target, `strokes: [{points, color: [r,g,b,a] or style, loop}]` | adds strokes on top; one undo step |
| `erase` | target, `indices` | removes strokes; one undo step |
| `fill` | target, `points: [[x, y], ...]`, `color` or `style` | paints the closed area around each point; one undo step; returns the points that missed |
| `view` | `path` | writes a PNG of the viewer as it is on screen |
| `save` | | File > Save All |
| `undo`, `redo` | | Edit > Undo, Edit > Redo |

A target is `level` + `drawing` (the first xsheet cell that shows that drawing), or `column` + `row` (0-based), or nothing (the cell under the cursor). A drawing that does not exist yet can be made on the empty cell under the cursor, the same way a drawing tool makes it.

An answer is `{"ok": true, ...}` or `{"ok": false, "error": "..."}`. An `id` in the request comes back in the answer.

## Limits

- Vector levels only.
- A new drawing can only be created on the cell the cursor is on. Creating one anywhere needs `TXshSimpleLevel`, whose header pulls in boost. I left that out to keep the build small.
- While a dialog is open in OpenToonz, the GUI thread is busy and commands wait. The client gives up after 20 seconds and says so.
- `preview_frame` and `render` read the files. After live strokes, call `opentoonz_live('save')` first.

## Other builds

The protocol and the Python client (`otmcp/bridge.py`) do not depend on the platform: Qt's local socket is a named pipe on Windows and a Unix socket elsewhere. What is specific is the build:

- `build.py` makes import libraries from the DLLs and calls MSVC. Linux and macOS need a CMake build that links the shared libraries of the install instead.
- Each OpenToonz version, and each fork (Tahoma2D, Morevna), needs its own compile against its own headers. `otbridge.cpp` uses a small set of long-standing classes (`TTool`, `TApplication`, `TXsheet`, `TVectorImage`, `TStroke`, `TUndoManager`, `CommandManager`), so I expect few source changes, but I have not tried it.

The lasting fix is upstream: a small, stable remote-control interface inside OpenToonz itself, so no plugin has to match a build. This plugin is a working prototype of what that interface needs.

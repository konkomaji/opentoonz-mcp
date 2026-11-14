# OpenToonz file-format notes (verified)

These are the OpenToonz facts my code depends on. I didn't trust any of them until I had checked each one twice: once in the OpenToonz **v1.8.0** C++ source, and once by rendering with OpenToonz's own `tcomposer`. Where the two disagreed, I believed the render.

Facts the MCP depends on. Every item was checked in two ways: against the OpenToonz **v1.8.0** C++ source (downloaded to `.cache/src/`) and by rendering with the installed `tcomposer.exe`. The source file is given for each item.

## Install facts (this machine)
- OpenToonz is **v1.8.0** (`tcomposer.exe -version`), installed at `D:\OpenToonz`.
- The stuff folder is `D:\OpenToonz stuff`. The registry key `HKLM\SOFTWARE\OpenToonz\OpenToonz\TOONZROOT` points to it.
- A scene outside any project uses the **sandbox** project (`sandbox_otprj.xml`). `+drawings/...` and `+outputs/...` resolve inside the sandbox.
- ffmpeg comes from WinGet (`%LOCALAPPDATA%\Microsoft\WinGet\Packages\*FFmpeg*`).

## .tnz (TOStream) syntax — `common/tstream/tstream.cpp`
- It is XML-like, but **there must be no `<?xml ?>` header**. A header makes the parser fail with "expected identifier".
- Text tokens are space separated. A token stays bare if it matches `[A-Za-z0-9_%]+`. Otherwise it is double-quoted, with `\` and `"` escaped.
- Persistent objects are written as `<tag id='N'>…</tag>` the first time and `<tag id='N'/>` for every later reference.
  - All objects share one id table.
  - The full definition must appear before any reference.
- Root element: `<tnz framecount="N" version="71.1">`. The children are `generator`, `properties`, `levelSet` and `xsheet`.

## Levels — `toonzlib/txshsimplelevel.cpp`, `levelset.cpp`
- Format: `<level id='K'> name <info dpix=".." dpiy=".."/> <path> "..." </path> </level>`.
- The level type comes from the file extension: pli → vector, png/tif/… → raster (OVL), tlv → toonz raster.
- `name..png` (two dots) means the image sequence `name.0001.png …`.
- **Raster levels need an explicit dpi.** With dpi 0, scale = Stage::inch/dpi = identity, which makes images 2.25× too big. Use `dpix=dpiy=camera dpi` (120 for a 16-inch-wide 1920 px camera).

## Xsheet — `toonzlib/txsheet.cpp`, `txshlevelcolumn.cpp`
- Cell format: `<cell> row count <level id='K'/> fid inc </cell>`. Row is 0-based. The run covers rows row..row+count−1 with frame fid + i·inc. Frame ids with a letter suffix require count 1.
- Column `<status>` bits are *hidden* flags: 0x1 = camstand, 0x2 = preview/render. **0 means visible.**
- `camerastand_opacity` only affects renders when `<columnColorFilterOnRender>1` is set in properties.

## FX DAG — `toonzlib/fxdag.cpp`, `tcolumnfx.cpp`
- `<fxnodes><terminal>` must reference every column's `Toonz_columnFx`. **Without it the render is blank.**
- Persist tags: `Toonz_columnFx`, `Toonz_xsheetFx`, `Toonz_outputFx`. The output's input port is named `source`.

## Stage objects — `toonzlib/tstageobject.cpp`, `tstageobjecttree.cpp`
- Ids: `Table`, `CameraN`, `PegN`, `ColN` (Col1 = column index 0). The active camera carries `activeboth="yes"`.
- Units: X/Y are inches with +y up (pixels = inches · dpi). Rotation is degrees CCW. Scale 1.0 = 100%.
- **Pivot:** placement = `T(offset+pos)·R·Sh·S·T(−center)`. The file stores `center cx cy −offx −offy`. Moving the center by d keeps the object still only if the offset also moves by d. A fresh object (center = offset = 0) therefore gets `cx cy −cx −cy`. An object whose center was moved in the GUI keeps its existing center−offset difference.
- Keyframes (`common/tparam/tdoublekeyframe.cpp`):
  - Format: `<keyframes><L> frame value </L> …`. Frames are 0-based.
  - Type codes: `L` linear, `C` constant, `Exp`, `S` speed (`frame value outX outY`), `E` ease in frames / `Ep` ease in percent (`frame value easeOut`), `Ex` expression.
  - **The arrival ease is stored NEGATIVE.** OpenToonz reads it as `e1 = max(−speedIn.x, 0)` (`getEaseInOutValue`), so a positive value silently disables the slow-in. Verified by rendering.
  - The interpolation of a segment comes from its **first** key.
  - `<prev>` holds the incoming handle and is optional on load.
- Camera: a camera scale below 1 zooms **in**.

## Cameras
- `properties/cameras/camera[i]` feeds stage object `Camera{i+1}`. Pixel/inch conversions must use the **active** camera, the one marked `activeboth`/`active`.

## Headless operation
- `tcomposer.exe scene.tnz -o out.png -range a b` renders frames a..b. The range is 1-based and inclusive. The output is a numbered sequence `out.0001.png` that keeps the scene's frame numbers.
- `OpenToonz.exe file.toonzscript` runs a QtScript and exits without showing a window. It takes about 1.5 s and always exits with code 1, so check the output files rather than the exit code.
- Vectorizers: `new OutlineVectorizer()` / `new CenterlineVectorizer()`, then `vectorize(level)` returns a vector level with a shared palette. Outline needs `preservePaintedAreas = true` to keep fills.
  - API: `new Level()`, `l.load(path)`, `l.save(path)`, `Image`, `Scene`, `OutlineVectorizer`, `CenterlineVectorizer`, `Rasterizer`.
  - The script API has no access to keyframes.

## SVG → vector (`image/svg/tiio_svg.cpp`, embedded nanosvg)
- 1 SVG unit = 1 vector unit. The SVG centre maps to the vector origin, with y flipped.
- 1 vector unit = dpi / 53.333 camera px (Stage::inch = 53.333). Scale the SVG by 53.333/dpi.
- Stroke thickness = stroke-width / 2, constant along the stroke.
- The palette is built by scanning every frame of the level, stroke colour first, then fill colour.
- **Draw ink and paint as one element.** A separate fill-less closed loop drawn on top of a filled shape becomes its own transparent region, which hides the paint underneath.

## .pli binary — `image/pli/pli_io.cpp`
- Header:
  - `PLIM`, then u8 major and u8 minor (71.0 here).
  - Creator string: u16 length + bytes.
  - u32 fileLength, u16 frames.
  - [u8 s if version ≥ 6.5], then u8 ii, u8 d.
- Tag header: one byte holding the type in the low 6 bits and a length id in the high 2 bits. Length id 0 = no length, 1 = u8, 2 = u16, 3 = u32. A first byte of `0xFF` means a u16 type follows.
- Tag ids:
  - 1/2/3 = `SET_DATA_8/16/32` (width of dynamic ints)
  - 10 = chain, 23 = loop (strokes)
  - 22 = `IMAGE_BEGIN` (frame)
  - 25 = precision scale (default 128)
- Stroke payload:
  - u8 maxThickness
  - P0 (2 dynamic ints) + u8 thickness
  - then per quadratic: d1 + thick1, d2 + thick2
- Dynamic ints are sign-magnitude. Real coordinate = int / precisionScale.
- More tag ids: 14 = group, 16 = image, 17 = colour, 20 = style, 21 = intersection data (the painted regions).
- **Children are named by absolute file offset.** A group (type byte 1 = strokes, 5 = the palette) and an image tag list the offsets of their child tags, in the current dynamic width. Inserting a byte anywhere moves every later offset, so a writer has to resolve them and write them again. `plidoc.py` does this.
- A drawing is: image-begin (u16 number, u8 letter), precision scale, then colour, stroke and group tags, then the intersection data, then the image tag (same number and letter, then the child offsets). The reader starts every drawing at dynamic width 2, so the writer puts a set-data tag before the first dynamic value of each drawing.
- A colour tag (`01 03` = solid, stroke colour, then the style id) applies to every stroke after it, groups included.
- Palette: style tags listed by one palette group. The first style tag (id 0) holds the page names. A solid colour is: u16 id, u16 page, string name (`06` + u16 length), `02 03 00 00 00` (the tag id of the solid style), then `01 r 01 g 01 b 01 a`.
- OpenToonz writes precision scale 16384 (128 squared) for every drawing.
- **A file written this way is one OpenToonz writes back unchanged.** Loading a `plidoc` level in OpenToonz 1.8.0 and saving it gives the same bytes (`tests/test_ink.py`).
- Intersection data names strokes by their position in the drawing. Strokes added after the existing ones leave it valid, and the paint survives (pixel test). Removing or reordering earlier strokes would not.
- **The GUI cuts loaded coordinates to 1/128 of a unit.** A point stored as 1601990/16384 is 1601920/16384 in the memory of the OpenToonz window (toward zero, to a multiple of 128), and that is what it saves. The script `Level.load` / `save` path keeps the full value. A stroke added in memory keeps what it was given. So anything that must survive a round trip exactly belongs on the 1/128 grid (0.018 px at 120 dpi).
- **No outside control.** The 1.8.0 Windows binaries have no command socket (Qt5Network is used for the host name only), watch directories but not level files, and the script bindings work on files, not on the open scene. The Qt it ships finds a generic plugin but does not create one from `QT_QPA_GENERICPLUGINS` or `-plugin`; an image-format plugin is loaded during start-up.
- **tcomposer does not fail on a scene with no exposed drawing; it waits forever.** The server checks for that before every render.
- **The thickness bytes can be patched in place.** Tag sizes don't change, so the offsets inside group and image tags stay valid. `pli.pressure_patch` uses this.

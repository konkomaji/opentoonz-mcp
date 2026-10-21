# OpenToonz file-format notes (verified)

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
- **The thickness bytes can be patched in place.** Tag sizes don't change, so the offsets inside group and image tags stay valid. `pli.pressure_patch` uses this.

# Drawing together

This is the guide to ink layers and live drawing: how the assistant and I work on one drawing without getting in each other's way.

## The idea

Until 1.0 a vector layer had one author. The assistant kept a list of shapes, built the level from them, and rebuilt it whenever a shape changed. If I touched the level in OpenToonz, the server noticed and locked the layer, because the next rebuild would have erased my work. It could not see my lines at all.

An **ink layer** has no list of shapes. The level file (`.pli`) is the only copy of the drawing. The assistant reads the strokes that are there, and adds pen strokes on top. It never rebuilds anything, so there is nothing to lock.

| | vector layer | ink layer |
|---|---|---|
| The truth | the assistant's shapes | the level file |
| Made with | `draw`, `draw_many`, `inbetween` | `stroke` |
| Sees my strokes | no | yes (`read_drawing`) |
| I edit it in OpenToonz | layer is protected until `force=true` | nothing special, keep going |
| Filled shapes and cel shadows | yes | lines, and `paint` for flat colour in live mode |
| Good for | layouts, shapes, parts, generated motion | sketching, clean-up, fixing, drawing on my art |

Both kinds can live in one scene. A common setup is shape layers for the background and props, and ink layers for the characters we draw together.

## Set up

**For drawing through the files** there is nothing to set up beyond the server itself (README, "Set up, step by step"). Use opentoonz-mcp 1.0.0 or later.

**For live drawing** build and install the plugin once:

1. Install Visual Studio Build Tools with the "Desktop development with C++" workload.
2. Get the Qt SDK that matches OpenToonz 1.8.0:
   `pip install aqtinstall` and then `aqt install-qt windows desktop 5.15.2 win64_msvc2019_64 -O D:\Qt`
3. Get the OpenToonz 1.8.0 headers (about 30 MB; nothing is compiled from it):
   `git clone --depth 1 --branch v1.8.0 --filter=blob:none --sparse https://github.com/opentoonz/opentoonz D:\src\opentoonz`
   then, inside that folder, `git sparse-checkout set toonz/sources/include`
4. Get this repository (the plugin source is in `bridge/`) and run, with OpenToonz closed:
   `python bridge/build.py --opentoonz "C:\Program Files\OpenToonz" --source D:\src\opentoonz --qt D:\Qt\5.15.2\msvc2019_64 --install`
   If OpenToonz is under `C:\Program Files`, run that from an administrator prompt, because it copies one file into the OpenToonz folder.
5. Start OpenToonz, open a scene, and ask the assistant: *"Run opentoonz_live."* `connected: true` means the link is up.

If it says `connected: false`, look at `%TEMP%\otbridge.log`. It has one line saying whether the bridge is listening or why it is not. The details, and how to remove the plugin, are in [bridge/README.md](../bridge/README.md).

## A first session

1. *"Create a scene called cat, 960 by 540, and add an ink layer called ink."*
2. *"Open it in OpenToonz."* Draw something on the `ink` column with the brush.
3. File mode: save. Live mode: nothing to do.
4. *"Look at my drawing on ink, frame 1, and tell me what you see."* The assistant calls `read_drawing`.
5. *"Add whiskers and a collar."* It calls `stroke`.
6. File mode: File > Revert Scene. Live mode: the strokes are already there.
7. Don't like a line? Live mode: Ctrl+Z. Either mode: *"Erase the last stroke you drew."*
8. *"Render it."* In live mode the assistant saves first with `opentoonz_live("save")`.

## The tools

| Tool | What it does |
|---|---|
| `add_layer(scene, layer, mode="ink")` | Makes an ink layer. Its level file appears with the first stroke. |
| `ink_layer(scene, layer)` | Turns an existing vector layer into an ink layer. The shapes are dropped; the drawings stay exactly as they are. |
| `read_drawing(scene, layer, frame)` | Every stroke on a drawing: index, colour, width, closed or open, bounding box, points with pressure, and `by` (`mcp` or `other`). Works on vector layers too. |
| `stroke(scene, layer, frame, strokes)` | Adds pen lines on top of the drawing. |
| `erase_stroke(scene, layer, frame, indices)` | Removes strokes by index. |
| `paint(scene, layer, frame, points, color)` | Fills the closed area around each point. Live mode only. |
| `live_view()` | A picture of my OpenToonz viewer as it is right now. Live mode only. |
| `opentoonz_live(action)` | The live link: `status`, `save`, `undo`, `redo`. |

All positions are canvas pixels, origin at the top left, the same as every other tool.

### A stroke

```json
{"points": [[436, 226, 1.0], [392, 216, 0.6], [350, 212, 0.1]], "width": 3, "color": "#1b1b22"}
```

- `points`: where the pen goes. The line passes through every point. The third number is pressure, 0 to 1.
- `width`: the line width in pixels at full pressure. Default: the line width of the layer's style.
- `color`: any colour. A new colour is added to the level palette; the same colour reuses its style.
- `closed`: join the end to the start (an eye, a button).
- `smooth`: `false` joins the points with straight segments (an ear, a corner).

If no point has a pressure value, the ends are thinned like a pen lifting off the paper. `taper=0` turns that off.

That example is one whisker of the cat in the README. The whole cat is 26 such strokes.

### Reading a drawing

```json
{"index": 4, "by": "other", "color": "#108030", "width": 4.5, "closed": false,
 "bbox": [240.0, 150.2, 400.0, 162.0],
 "points": [[240.0, 160.0, 0.4], [320.0, 150.2, 1.0], [400.0, 160.0, 0.4]]}
```

`by` is `mcp` for strokes the assistant drew and `other` for everything else: my strokes, and strokes that came from shapes. The server knows its own strokes by a checksum of their geometry, so the label survives my saving the level in OpenToonz.

`index` is the drawing order. A higher index is on top.

`read_drawing(..., only_new=true)` returns just the strokes that appeared since the assistant last looked at that drawing, and `gone` counts the ones that disappeared. After I add three lines to a drawing of two hundred, it reads three.

## Working through the files

This works with any OpenToonz, with nothing installed in it.

1. I draw in OpenToonz and **save**.
2. The assistant calls `read_drawing`, then `stroke`.
3. I use **File > Revert Scene** to load what it drew.

OpenToonz does not reload a level that changed on disk. If I skip step 1, the assistant does not see my new strokes. If I skip step 3 and save, my stale copy overwrites its strokes. `stroke` warns when OpenToonz is running.

## Working live

With the [otbridge plugin](../bridge/README.md) installed, steps 1 and 3 go away.

When the scene is open in OpenToonz, `stroke`, `read_drawing` and `erase_stroke` talk to OpenToonz itself instead of the files:

- A stroke shows up in my viewer at once.
- `read_drawing` returns what is on screen, including strokes I have not saved.
- Each call is one undo step. Ctrl+Z in OpenToonz takes it back, and so does `opentoonz_live("undo")`.
- Erasing is done by OpenToonz, so it works on strokes that border painted areas too.
- `paint` fills closed areas, and tells the assistant which points missed because the outline has a gap.
- `live_view` shows the assistant my viewer as I see it, zoom and onion skin included, without saving. It is how it checks its strokes between batches.

The tools decide by themselves. `opentoonz_live()` shows which mode is active:

```json
{"connected": true, "scene": "cat", "unsaved": true, "level": "ink", "drawing": 1, "column": 0, "row": 0, "strokes": 26}
```

Two things to know:

- **The files lag behind.** Live strokes are in OpenToonz's memory until the scene is saved. `preview_frame` and `render` read the files, so the assistant calls `opentoonz_live("save")` before them. Nothing is saved without that call or my own Ctrl+S.
- **A new drawing starts where my cursor is.** A drawing that is already on the xsheet can be drawn on from anywhere. To start a new one, I put the cursor on an empty cell of that column; the assistant's first stroke creates the drawing there, the way a brush would.

## Examples

**I sketch, it cleans up.**

> Make an ink layer `rough` and an ink layer `clean` above it. … I've sketched the head on `rough`. Read it and draw a clean outline on `clean`, following my lines.

The assistant reads my strokes from `rough` and draws on `clean`. My sketch is never touched.

**It blocks in, I fix, it continues.**

> Draw the cat's body in a few strokes on `ink`. … I moved the tail and redrew the ear. Look again and add the face.

Its second `read_drawing` shows my changes as `other`, and it draws around them.

**Carrying on with a layer made from shapes.**

> The ball layer is fine. Make it an ink layer and add speed lines behind it on frame 3.

`ink_layer` keeps the drawings, paint included. Strokes drawn across a painted area leave the paint as it is.

**Frame by frame.**

> On `ink`, draw the flag in three positions on frames 1, 3 and 5.

Each drawing is held until the next one, as on any layer with automatic timing. `set_exposure` changes that.

## Limits

- **Paint needs live mode.** The painted regions of a level are stored as a graph of stroke intersections. The server can keep that data valid when it adds strokes, but it cannot write it. In live mode OpenToonz works the regions out, so `paint` is there; through the files it is not. Use a vector layer for filled shapes if you have no bridge.
- **Erasing next to paint, through the files.** A stroke that a painted area depends on, or any stroke drawn before one, is refused with a clear message. In live mode OpenToonz does the erase and this limit is gone.
- **Live mode is one build.** The plugin is compiled for the official OpenToonz 1.8.0 Windows build and stays off anywhere else. File mode has no such limit.
- **Not two hands at the same moment.** In live mode the assistant's strokes arrive between mine, not during one. If a dialog is open in OpenToonz, its commands wait, and after 20 seconds it reports that OpenToonz is busy.
- **Coordinates are stored on a 1/128 unit grid** (0.018 px at the default camera). That is what the OpenToonz window itself keeps when it loads a level, so nothing shifts between a save and a reload.

## How it stays correct

- `plidoc.py` keeps every tag of the level as raw bytes and only rewrites the offsets that have to move. Every level in my test folder comes out byte for byte the same when it is loaded and written back.
- OpenToonz loads a level written by `plidoc` and saves it again with identical bytes.
- Rendering tests check the pixels: a stroke is where it was drawn, as wide as asked, in the right colour, and paint under a new stroke is unchanged.
- The live tests start a real OpenToonz, draw through the plugin, undo, redo, erase and save, and then check the saved file.
- The plugin hashes the OpenToonz libraries when it starts and refuses to run in a build it was not compiled for.

The format facts behind this are in [FORMAT_NOTES.md](FORMAT_NOTES.md), and the structure is in [ARCHITECTURE.md](ARCHITECTURE.md).

# Character design: what hand-drawn features do, and what the rig now does

The first rig in this project had correct proportions and a correct walk, and it still did not look like a drawn person. It looked like a jointed paper puppet. This document records what I found when I compared it with real frames, how studios actually make a scene, and what the `feature` design of `build_character` encodes as a result.

## What I studied

- **Frames.** Official stills that Studio Ghibli publishes for free use (ghibli.jp), from four films, next to enlarged crops of my own figure. I used them to study, and none are in this repository.
- **Process and other studios.** My notes on the Japanese production pipeline and on how Disney, Pixar, Sony Pictures Animation, SPA Studios, Cartoon Saloon, Fortiche, DreamWorks, ufotable, Trigger, Science SARU, Kyoto Animation and MAPPA build their look, plus Preston Blair and Loomis on proportions. Where a number below is my own estimate, it says so.

I did not study every release of every studio. Nobody could, and I will not claim it.

## How a scene is made

Every studio I looked at separates the same jobs. The names here are the Japanese ones, with the Western equivalent.

| Stage | What is produced | Who decides what |
|---|---|---|
| Storyboard (*ekonte*) | Numbered cuts with action, dialogue and a duration | The duration is fixed here. Later stages fit it. |
| Layout | One drawing per cut at final framing: where the camera is, how big the character is, where the horizon is, which parts are background and which are overlay | Framing is locked before anyone animates. |
| Key animation (*genga*) | The extreme poses, the timesheet, and spacing charts for the inbetweens | This is where the acting happens. |
| Animation direction (*sakkan*) | Corrections so every drawing matches the model sheet | Volume and likeness. |
| Inbetween and clean-up (*douga*) | Clean single-weight lines, closed for paint, with shadow boundaries drawn in coloured pencil | Line quality. |
| Colour design and paint (*iro-shitei*, *shiage*) | A palette per character **per lighting condition** (day, evening, night), then flat fills | Colour is designed, not tinted afterwards. |
| Background (*haikei*) | A painting, in poster colour or digital paint, with separate overlay pieces for depth | Texture and light live here. |
| Photography (*satsuei*) | The composite: camera moves, glow, diffusion, gradients, depth haze | The final mood. |

Western 2D features follow the same order with different names: story reel, layout, rough animation, clean-up, ink and paint, camera, compositing.

Two things in that table explain most of what was wrong with my shot.

1. **The character and the background are made by different hands with different tools.** The character is flat colour inside a clean line. The background is a painting with texture, soft edges and brushwork. The contrast is what makes the character readable. My first shot drew both the same way.
2. **Layout comes first.** Feature shots are mostly medium shots. My character was a tenth of the frame height, so even a good face would have been four pixels wide.

## What the frames have that my figure lacked

| | Real frames | The classic rig |
|---|---|---|
| Line | Thin. About 1 to 1.5 px at 1080p (my measurement). Warm brown, or a darker tone of the fill. | Thick and near black. |
| Height | Small child about 3.5 heads, ten-year-old about 5.5, teenager 6 to 6.5 (my count on the stills). Blair gives 5 heads for a young heroine. | Anatomical: about 6.5 heads at ten. |
| Face | Eye with iris and one highlight, a lash line, a brow, a nose mark, a small mouth, sometimes blush. | A dot and two lines. |
| Hair | One designed mass with a shadow shape and few interior lines. | A cap. |
| Costume | Shapes that define the silhouette: sleeve, collar, sash, flared skirt, hem. | A block and tubes. |
| Shading | One hard shadow tone per colour, in large simple shapes. | None. |
| Shadow colour | Darker, slightly more saturated, hue turned toward blue (toward red-orange on skin). | n/a |
| Body | One continuous silhouette. | Capsules with a line across every joint. |
| Hands and feet | A thumb, a suggestion of fingers, a shoe with a sole. | An ellipse. |
| Pose | A line of action; the head tilts, the weight shifts. | A vertical stack. |
| Framing | A third of the frame height or more. | A tenth. |

## What the `feature` design does about it

`build_character(design="feature")` is the default from 1.2.

| Gap | What the rig does now |
|---|---|
| Line | Width is 0.85 % of the character's height, in warm brown `#4a332a`. Cloth is outlined in a darker tone of itself (*iro-tore*, a colour trace line). |
| Height | `appeal` (0 to 1, default 0.8) enlarges a child's head about the neck, up to 1.6 times at age 6 and not at all from age 18. A ten-year-old is 6.5 heads tall at 0, 5.2 at the default and 5.0 at 1.0 (measured on the rig). The skeleton is unchanged, so the walk stays correct. |
| Face | White of the eye, iris, one highlight, a heavier lash line, a brow in the hair colour, blush, and a small smile in a skin trace colour. Children get larger eyes. |
| Hair | A cap with a fringe, a shadow shape at the back, and a sheen line. |
| Costume | `outfit="dress"` adds a bell skirt as its own layer over the legs, with a shaded back, two folds, a sash with a tail, short socks and soft sleeves. `outfit="shirt"` gives a collar, short sleeves for children, and a waistband. `hat="straw"` adds a brimmed hat with a band, a ribbon tail and the brim's shadow on the forehead. |
| Shading | Every limb, the torso, the skirt, the hat, the neck and the jaw get one shadow shape on the side away from the light. `anatomy.kage` derives the tone. |
| Body | A limb is a fill, a shadow, and an **open** outline that runs down one side, round the far end and up the other. The end at the upper joint has no line, so a bent knee or elbow has no seam. |
| Hands and feet | A mitten with a thumb and one finger line. A shoe with a shaded sole. |
| Framing | The docstring tells the assistant to stage a character at a third of the frame height or more. The README shot uses 40 %. |

## Every kind of person

![Nine people built by one tool: a toddler in a yellow dress, a boy with an afro in shorts, a girl in a sun dress and straw hat, a teenager in a tunic and headscarf, a young man in a suit, a woman in a wrap robe with a broad sash, a man in a long robe and turban, an old woman in a long skirt, an old man in a coat and cap](https://raw.githubusercontent.com/konkomaji/opentoonz-mcp/v1.2.0/docs/media/people-lineup.png)

Each of these is one `build_character` call ([`docs/media/making_of_people.py`](https://github.com/konkomaji/opentoonz-mcp/blob/main/docs/media/making_of_people.py)).

| What varies | How |
|---|---|
| **Age** | 1 to 85. Proportions, head size, posture, grey hair and a few lines of age follow the age. The figures above are 3, 8, 10, 15, 25, 35, 45, 75 and 80. |
| **Sex** | Proportions from anthropometric tables: shoulder and hip breadth, bust after puberty, stature. |
| **Skin** | `skin_tone` 1 to 10, light to deep, or any colour through `colors`. On deep tones the outline, the shadow tone and the blush change so the face still reads. The ten steps are ordered like the Monk Skin Tone scale; the colours themselves are my own cel-paint choices. |
| **Face** | `face` takes `nose`, `lips`, `jaw`, `brow` and `eyes`, each from -1 to 1. |
| **Hair** | short, long, bob, ponytail, bun, afro, none. |
| **Head covering** | straw hat, cap, headscarf, turban. A scarf or turban covers the hair. |
| **Clothes** | shirt, shorts, dress, skirt, long skirt, tunic (a kurta or long shirt), robe (a thobe, abaya or gown), wrap (a kimono or hanbok, with hanging sleeves and a broad sash), suit, coat. |

A garment is three choices: the sleeve, the legs, and what hangs from the waist (its shape, where the hem is, and which colour). An outfit is a named set of those, so adding one is a line in `anatomy.OUTFITS`.

### Why there are no region presets for faces

You can ask this tool for a tall man in a white robe and a turban with deep brown skin and a strong nose. You cannot ask it for "a man from" somewhere and get a face.

That is deliberate. Body measurements do differ between populations on average, and `region` in this project uses published survey averages for stature and leg length. Faces are another matter. Within any region, people differ from each other far more than the average of one region differs from the average of another. A preset that turns a place into a nose, an eye shape and a skin colour would be wrong for most people who live there, and it would be the tool's guess presented as a fact.

So the controls describe a person, not a place. Whoever is designing the character, you or the assistant working from your description or your reference, chooses the face, the skin, the hair and the clothes one by one. Clothes are named for what they are (a tunic, a robe, a wrap), not for who is supposed to wear them.

## What is still missing

I am listing these because the result is better, not finished.

- **Pose.** The rig walks correctly and stiffly. There is no head tilt, no torso twist, and the spine is straight. A drawn walk has a line of action.
- **Secondary motion.** The skirt hangs from the torso and does not swing. The hat ribbon and the hair do not trail.
- **One view.** Side view only. No three-quarter view, no turn.
- **Hands.** A mitten is a placeholder for a hand.
- **Faces in profile only.** The face controls shape a side view. Width of the nose, the jaw and the cheekbones, and the shape of the eye from the front, need a front or three-quarter view that does not exist yet.
- **More of everything worn.** No patterns or prints, no sari drape, no jewellery, no glasses, no beards, no braids or locs, no body shapes beyond the BMI control. Each is a small addition to the tables, and the list is long.
- **Lighting conditions.** One palette. A studio designs a second and third palette for evening and night.
- **Backgrounds.** They are still flat vector shapes. The contrast between a painted background and a flat character, which is half of the look, is not there yet.
- **Acting.** A face that cannot change expression cannot act.

The honest summary: the figure moved from "jointed puppet" to "clean cut-out character". A hand-drawn feature character is still a long way past that.

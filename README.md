# AI Studio for Blender

*Part of ID Toolkit.*

Product renders and the CAD hand-over up front, a Claude chat that works directly in your scene,
local models through LM Studio, all in the sidebar of the 3D viewport. Made so that people who
never used Blender get a clean product image from a CAD model.

> Unofficial. Not affiliated with Anthropic or the Blender Foundation. You need your own
> [Claude Code](https://claude.com/claude-code) access (Claude subscription or API key) or
> [LM Studio](https://lmstudio.ai) for local models.

## What it does

**Claude tab: chat that changes your scene**
- Claude (Sonnet, Opus, Haiku …) or a local model writes and runs Blender Python for you.
  Every step is one undo step. Versions let you step back and forth through the chat.
- It sees what matters: a short scene description, your selection, and a viewport
  screenshot when it needs one.
- **Skills** are reusable instructions in Markdown (tidy up, model a part from a datasheet,
  camera, light, materials, render loop …). Write your own with "+".
- Questions come back as buttons. Images, PDFs and 3D files can be attached.
- **Memory** per project and globally. **Lessons**: after fixing an error, the model writes
  down what worked and gets it again in later chats.
- Usage, context and auto summarizing of long chats.
- A **setup card** on first start: **Set Up Everything** allows online access, installs Claude Code
  (with a progress bar), opens the login, creates the base materials and connects the Claude apps;
  each step also has its own button. Once the chat has answered, the steps go away by themselves.
  It asks only one thing: whether Claude's code runs directly or asks first. Everything else has a
  good default (renders next to the file, backups every 5 minutes, automatic updates) and can be
  changed in the preferences or from the **?**, which also shows shortcuts and example prompts.
  With the Claude app connected, the card shows the last step there: set the permission switch
  under the message box to **Auto** (or *Customize › Connectors › ai-studio › Tool permissions ›
  Always allow*), so the Blender tools run without asking each time.
- **Set Up for Product Renders** (Render tab and setup card): one click sets Cycles on the graphics
  chip (Metal), 64 viewport / 512 final samples with adaptive sampling, noise removal, AgX colors,
  millimeters and light bounces for glass and metal, and lists what it set. The arrow next to it
  puts everything back, also after saving.
- **Name scheme** (Preferences › Studio, on by default): cameras `P_` (perspective) or `O_`
  (orthographic), lights `A_` (area), `P_` (point), `S_` (spot) or `D_` (sun), then what they show or
  do and a two-digit number: `P_side_01`, `O_front_01`, `A_key_01`. A camera whose name says nothing
  is named after where it looks from (front, side, back, top, bottom, iso). Rename right in the
  Render tab (the name field of the camera) or the Light tab: "Seite" becomes `P_side_01`. Older names
  ("AS_camera Ortho Oben.001") follow the scheme when a file is opened and after each Claude answer; a
  camera switched to orthographic goes from `P_` to `O_`. Off: only ".001" and old prefixes are
  fixed. Render files get the same numbers ("Stuhl_Front_01.png"); chats are named after the first
  words of their first message.
- **Fine camera fields**: distance, height, left/right, ortho scale, focus distance and clipping move
  in 1 mm steps (arrows and dragging), also in the camera settings (⚙).
- **Updates** (installed from the install page): once a day the add-on looks for a new version and
  installs it by itself when Blender is idle; the chat says so. Or only a notice with an Install
  button, or off (Preferences › General).
- **English** throughout, also in a German Blender; *Preferences › General › German Interface* shows
  the add-on in German.
- Replaced objects are never deleted unless you say so: they go into a hidden archive
  collection (`Archive`). Claude's helper objects go into `temp`, removed after the answer.

**Studio tab: render studio**
- **Render:** camera bar, orbit around an object or the 3D cursor (straight top views),
  lens and format presets, depth of field (its settings show while it is on, with a note when
  the viewport can not show it), framing guides. **Look:** glow, contrast and
  vignette presets for the Blender 5 compositor.
- **Light:** light setups and presets, single lights with target and height, HDRIs from a
  folder, generated HDRIs, rooms baked into HDRIs (objects marked `obj["as_env"]`).
  Applying a preset keeps the names of your lights.
- **Material:** preview, quick settings, a library of all materials grouped by part, base
  materials from your own asset file (Claude or a button can save improved ones back),
  presets, eyedroppers for material and color (objects picked together share changes).
  A click in the library switches every object with the current material (compare colors
  fast), color variants, favorites, search, usage counts and removing unused materials.
- **CAD:** *Import CAD* (GLB incl. Onshape's glTF, STEP through a Python with OCP/build123d, OBJ, STL,
  FBX …) checks size, floor, names and triangles. *New Version* gives a re-exported model the
  materials, collections, modifiers and settings of the old parts with the same id or name ("Fork",
  "Fork.001", "Fork <1>") and archives the old ones by version (`Archive V2`). A sidecar
  `<name>.cad.json` next to the export (written by the CAD side, see `src/studio/cadfiles.py`) adds part
  ids, CAD materials (mapped to base materials), mounting points (`MOUNT | …` empties that move with
  every version), sizes to check and variants. Claude: `studio_cad_import`, `studio_cad_check` (check
  images), `studio_cad_feedback` (`CAD-Feedback.md` next to the export for the CAD side), skill */cad*.
- **CAD skill** (skill row of the chat, on/off, stays as set after a restart): the link to the
  Claude chats that build the CAD model, all in the chat, no box of its own. New versions come in
  from the CAD chat in the Claude app (Live Connection), so there are no import buttons. Say what
  should change ("Wand 2.4 mm, an CAD 5"); without a chat Claude asks with buttons: the known CAD
  chats (last active first, the current model's chat first) or a new chat. A chat is known once it
  called **blender_jobs** with its name, a job went to it, or the export names it in its sidecar
  (`"chat": {"name", "url"}`). The job is a file in `~/.config/ai-studio/jobs/`: a new chat opens
  with the text filled in (`claude://claude.ai/new?q=…`), a known chat gets a notification and takes
  the job with **blender_jobs** (list / claim / done; a chat without it can use `blender_python` of
  the Mac's `blender` tools, the job text says how). The chat here says when the job is taken and
  when it is done. Remarks for later go into `CAD-Feedback.md` next to the export
  (`studio_cad_feedback`). Off: Claude in Blender has no CAD job or feedback tools.
- **Onshape sign-in:** `OS.session()` in the browser helpers checks the sign-in before anything else
  and says "sign in to Onshape in the browser pane of the Claude app" instead of failing with 401.
  Onshape sends the package straight into Blender with `tools/onshape/onshape-helpers.js`
  (`POST /cad` on the Live Connection, see below).
- **For Claude:** sections through a scan and the CAD part with measurements
  (`studio_section`), reference images of the project (a `reference` folder next to or above the
  .blend file, one subfolder per topic, e.g. `reference/CMF`).
- **Queue:** ⌘R (High) / ⇧⌘R (Low) adds the camera. Images render in a second Blender
  without a window, so you can keep working, on the Mac's GPU even when Cycles was never set up.
  The preview (as wide as the panel, sharp) shows the image that renders now: a quick draft within
  seconds, a sharper one for long renders, then the final, live also in the render window (⇧⌘E).
  The progress bar moves on smoothly with Cycles' own time estimate and is also in the chat.
  The image is exactly Blender's own render (color management, Cycles settings and file format
  are checked in the tests). Before every render it checks that the second Blender has your
  current scene (new cameras, undo).
  Includes material variants (series in different colors) and browsing through earlier
  renders. Renders go right next to the .blend file (`<name>_renders/low`, `high`, `animation`), or into
  an output folder you set; the folders are made only when needed.

**Files**
- Every new file gets its own project folder where you save it (Documents, Desktop, a project
  drive …): the file, backups (the newest N), assets, an archive for your own milestones, the
  project memory and the renders. Unsaved files render into `Documents/AI Studio`.
- Backups every few minutes and before risky steps. A crash notice offers the last backup,
  and a freeze recorder logs hangs (macOS).

## Requirements

- **macOS** (Apple Silicon or Intel). Windows and Linux are not supported.
- Blender **5.0 to 5.3** (tested with 5.0, 5.1, 5.2 and 5.3). Older versions (4.x) are not supported:
  the compositor works differently there.
- For Claude: [Claude Code](https://claude.com/claude-code). The setup card installs it in the
  background (about 300 MB, with a progress bar) and then opens the login in the browser.
- For local models (optional): [LM Studio](https://lmstudio.ai) with a model downloaded.

## Install

**With updates (recommended):** open **https://louisdotxyz.github.io/ai-studio-blender/** and drag the
button into the Blender window. Blender adds the repository, installs the add-on and later offers new
versions under *Preferences › Get Extensions*. This needs *Preferences › System › Network › Allow Online
Access* (a new Blender starts with it off). By hand: *Get Extensions › Repositories › + › Add Remote
Repository* with `https://louisdotxyz.github.io/ai-studio-blender/index.json`.

**Without online access:** download the zip from [Releases](../../releases) and drag it into Blender (or
*Preferences › Get Extensions › ⌄ › Install from Disk…*). Blender then does not look for updates.

Then open the sidebar in the 3D viewport (N): tabs **Claude** and **Studio**, and click **Set Up Everything**
in the setup card. It allows online access, installs Claude Code (official installer, in the background with
a progress bar), opens the login and connects Claude Code and the Claude app to the Live Connection. The
base materials are made by themselves the first time the Material tab shows its library. Claude Code needs online access even when the add-on was installed from a zip (not
possible when Blender was started with `--offline-mode`). The login terminal closes by itself when done.

## Settings

*Preferences › Add-ons › AI Studio* has five sections; the first four hold what people change, the
rest is under *Advanced*:

| Section | What you set |
|---|---|
| General | **Run Code Without Asking** (auto mode, on by default: Claude changes the scene directly, Ctrl+Z undoes every step), **Sees the Viewport** (a screenshot with every message, on by default), default model and effort for new chats, Claude app and Claude Code (Live Connection), **updates** (automatic, ask, off), German interface |
| Files | Backups (every 5 minutes, as copies or saving the file), a project folder per new file, the attach folder |
| Renders | Output folder (empty: next to the .blend file), High Quality samples for new files, Low Quality size and samples, render on adding, notification |
| Studio | HDRI folder, base materials file, name scheme, Onshape keys |
| Advanced | Claude CLI path, scene info and selection with every message, image size, auto summarizing, statistics, custom instructions, extra CLI arguments, Live Connection port; backup details and folder names; local models (LM Studio); lessons; render subfolders, GPU only, material presets folder; collections and shortcuts |

**Live Connection** (General section, on by default): other Claude sessions on this computer (Cowork,
blender-mcp) can work in the open Blender without a click. Blender listens on 127.0.0.1 and writes port and
token to `~/.config/ai-studio/live.json` (only your user can read it; the Blender opened last owns it). Send
`POST /live` with `Authorization: Bearer <token>` and `{"op": "ping" | "info" | "object" | "python" | "screenshot" | "image"}`.
`POST /cad?name=<file>[&folder=<folder>]` with the same token takes a CAD export as the body (also from
Onshape pages: CORS for `*.onshape.com`) and writes it to `CAD/` next to the open .blend file (or the folder,
inside your home folder).
"Run Code Without Asking" applies: when it is off, the code waits for your OK in the chat. The file is never saved.
While the chat is answering, code from another session waits until the answer is done; the chat shows
"Cowork connected / waits". The setup card connects **Claude Code** and the **Claude app** (Cowork) with
one click each: they get the `ai-studio` tools (blender_scene, blender_object, blender_python,
blender_screenshot, blender_image), an MCP server that comes with the add-on and runs with Blender's own Python. The Claude
app reads it at its next start. Set a fixed **Port** if a client needs one; the copy button puts
instructions for any other session into the clipboard.

The collection names (`Camera`, `Light`, `Archive`, `temp`; Claude's set pieces go into `Set`) are set
in Preferences › Advanced; collections of older versions (`AS_camera`, `AS_camera | Packshot`,
`AS_lights`, `AS_archiv V2`, `AS_set`, `RS_Cameras`, `RS_Lights`) are renamed when a file is opened.

`studio/make_base_materials.py` (run by itself the first time the Material tab needs it) creates a "Base Materials.blend" with 19 everyday materials
(glass, metals, plastics, rubber, paint, speaker cloth …) as assets.

## Privacy

The add-on runs Claude Code on your computer. Blender talks to it through a local server
(127.0.0.1, with a token). Sent are your messages, attachments and, depending on the
settings, a scene description, your selection and screenshots. With LM Studio, everything
stays on your computer.

## Development

- `./build.sh` builds `dist/AI_Studio-<version>.zip` (Blender in /Applications).
- `./build.sh --repo` also writes `docs/` (index.json, the zip, an install page) for GitHub Pages
  (*Settings › Pages › Deploy from branch › main › /docs*).
- `python3 tests/lint.py` runs a static check (needs `pip install pyflakes`).
- `Blender -b --python tests/ui_check.py` draws every panel headless.
- `Blender -b --python tests/functional.py` runs the functional checks headless.
- `python3 tests/cad_unit.py` checks the CAD hand-over without Blender; `Blender -b --factory-startup --python
  tests/cad_blender.py` checks import, take-over, archive and mounts (loads `src/` directly).
- `python3 tests/update_e2e.py` installs an old build from a local test repository into a fresh
  Blender profile and lets it update itself to the current version.

Up to 0.24 the extension id was `claude_assistant`. Version 1.0 (`AI_Studio`) takes over its settings,
chats and Studio data, turns it off and asks you to remove it in *Preferences › Get Extensions*.

## License

GPL-3.0-or-later, like Blender. See [LICENSE](LICENSE).

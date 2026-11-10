# AI Studio for Blender

A Claude chat that works directly in your scene, local models through LM Studio, and a
render studio for product shots, all in the sidebar of the 3D viewport.

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
- A **setup card** on first start checks Claude Code, the login, base materials, the render
  folder and the Live Connection, with one button each (install, log in, create …). The **?**
  at the top shows shortcuts, clicks worth knowing and example prompts.
- Replaced objects are never deleted unless you say so: they go into a hidden archive
  collection (`AS_archiv`). Claude's helper objects go into `temp`, removed after the answer.

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
  "Fork.001", "Fork <1>") and archives the old ones by version (`AS_archiv V2`). A sidecar
  `<name>.cad.json` next to the export (written by the CAD side, see `src/studio/cadfiles.py`) adds part
  ids, CAD materials (mapped to base materials), mounting points (`MOUNT | …` empties that move with
  every version), sizes to check and variants. Claude: `studio_cad_import`, `studio_cad_check` (check
  images), `studio_cad_feedback` (`CAD-Feedback.md` next to the export for the CAD side), skill */cad*.
  Onshape sends the package straight into Blender with `tools/onshape/onshape-helpers.js`
  (`POST /cad` on the Live Connection, see below).
- **For Claude:** sections through a scan and the CAD part with measurements
  (`studio_section`), reference images of the project (a `reference` folder next to or above the
  .blend file, one subfolder per topic, e.g. `reference/CMF`).
- **Queue:** ⌘R (High) / ⇧⌘R (Low) adds the camera. Images render in a second Blender
  without a window, so you can keep working. The preview shows the image that renders now
  (a quick draft within seconds, then the final), with progress and time left. Before every
  render it checks that the second Blender has your current scene (new cameras, undo).
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
- For Claude: [Claude Code](https://claude.com/claude-code). The setup card installs it and
  logs you in (a terminal opens; you type your login there).
- For local models (optional): [LM Studio](https://lmstudio.ai) with a model downloaded.

## Install

**With updates (recommended):** open **https://louisdotxyz.github.io/ai-studio-blender/** and drag the
button into the Blender window. Blender adds the repository, installs the add-on and later offers new
versions under *Preferences › Get Extensions*. This needs *Preferences › System › Network › Allow Online
Access* (a new Blender starts with it off). By hand: *Get Extensions › Repositories › + › Add Remote
Repository* with `https://louisdotxyz.github.io/ai-studio-blender/index.json`.

**Without online access:** download the zip from [Releases](../../releases) and drag it into Blender (or
*Preferences › Get Extensions › ⌄ › Install from Disk…*). Blender then does not look for updates.

Then open the sidebar in the 3D viewport (N): tabs **Claude** and **Studio**. The setup card checks online
access, Claude Code and the login and has a button for each. Claude Code needs online access even when the
add-on was installed from a zip; the card turns it on with one click (not possible when Blender was started
with `--offline-mode`). Terminal windows it opens (install, log in) close by themselves when done.

## Settings

*Preferences › Add-ons › AI Studio* has six sections:

| Section | What you set |
|---|---|
| Chat | Claude CLI path, what goes with every message, attach folder, auto summarizing |
| Files | Project folder per file, folder names (backups, assets, archive), main file suffix, backup interval and count |
| Renders | Output folder (empty: next to the .blend file) and its subfolders (low, high, animation, archive), High Quality samples for new files (512 instead of Blender's 4096), Low Quality size and samples, GPU only |
| Studio | HDRI folder, base materials file, material presets folder, shortcuts |
| Local | LM Studio model, context length, thinking |
| Learning | Where lessons are kept (e.g. a folder in your Obsidian vault) |

**Live Connection** (Chat section, on by default): other Claude sessions on this computer (Cowork,
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

The collection names (cameras `AS_camera`, lights `AS_lights`, archive `AS_archiv`, `temp`) are set in
Preferences › Studio; older `RS_Cameras` / `RS_Lights` collections are renamed.

`studio/make_base_materials.py` (also the *Create* button of the setup card) creates a "Base Materials.blend" with 19 everyday materials
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

Up to 0.24 the extension id was `claude_assistant`. Version 1.0 (`AI_Studio`) takes over its settings,
chats and Studio data, turns it off and asks you to remove it in *Preferences › Get Extensions*.

## License

GPL-3.0-or-later, like Blender. See [LICENSE](LICENSE).

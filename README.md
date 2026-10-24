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

**Studio tab: render studio**
- **Render:** camera bar, orbit around an object or the 3D cursor (straight top views),
  lens and format presets, depth of field, framing guides.
- **Light:** light setups and presets, single lights with target and height, HDRIs from a
  folder, generated HDRIs, rooms baked into HDRIs.
- **Material:** preview, quick settings, a library of all materials grouped by part, base
  materials from your own asset file, presets, eyedroppers for material and color.
- **Queue:** ⌘R (High) / ⇧⌘R (Low) adds the camera. Images render in a second Blender
  without a window, so you can keep working. It includes material variants (series in
  different colors), a preview with browsing, and progress with time left.

**Files**
- Every new file gets its own project folder: the file, backups (the newest N), assets, an
  archive for your own milestones, and the project memory.
- Backups every few minutes and before risky steps. A crash notice offers the last backup,
  and a freeze recorder logs hangs (macOS).

## Requirements

- Blender **5.2** or newer (tested on macOS with Apple Silicon; Linux and Windows are untested).
- For Claude: [Claude Code](https://claude.com/claude-code) installed and logged in once
  (`claude` in a terminal).
- For local models (optional): [LM Studio](https://lmstudio.ai) with a model downloaded.

## Install

1. Download `ai_studio-<version>.zip` from [Releases](../../releases).
2. In Blender: *Edit › Preferences › Get Extensions › ⌄ › Install from Disk…* and pick the zip.
3. Open the sidebar in the 3D viewport (N): tabs **Claude** and **Studio**.

## Settings

*Preferences › Add-ons › AI Studio* has six sections:

| Section | What you set |
|---|---|
| Chat | Claude CLI path, what goes with every message, attach folder, auto summarizing |
| Files | Project folder per file, folder names (backups, assets, archive), main file suffix, backup interval and count |
| Renders | Output folder and its subfolders (low, high, animation, archive), Low Quality size and samples, GPU only |
| Studio | HDRI folder, base materials file, material presets folder, shortcuts |
| Local | LM Studio model, context length, thinking |
| Learning | Where lessons are kept (e.g. a folder in your Obsidian vault) |

`tools/make_base_materials.py` creates a "Base Materials.blend" with 19 everyday materials
(glass, metals, plastics, rubber, paint, speaker cloth …) as assets.

## Privacy

The add-on runs Claude Code on your computer. Blender talks to it through a local server
(127.0.0.1, with a token). Sent are your messages, attachments and, depending on the
settings, a scene description, your selection and screenshots. With LM Studio, everything
stays on your computer.

## Development

- `./build.sh` builds `dist/claude_assistant-<version>.zip` (Blender in /Applications).
- `python3 tests/lint.py` runs a static check (needs `pip install pyflakes`).
- `Blender -b --python tests/ui_check.py` draws every panel headless.
- `Blender -b --python tests/functional.py` runs the functional checks headless.

The extension id is `claude_assistant` for historical reasons; your chats and settings live
under that name in Blender's extension folder.

## License

GPL-3.0-or-later, like Blender. See [LICENSE](LICENSE).

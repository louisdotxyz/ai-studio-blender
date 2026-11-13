---
name: setup
description: Sets up ID Toolkit on a Mac in a few minutes - Blender, the ID Toolkit add-on and the link between Blender and the Claude app. Use when the user says "set up ID Toolkit", "einrichten", "install", "Blender verbinden", when a Blender tool (blender_status, blender_python) is missing, or when Blender does not answer.
---

# Set up ID Toolkit

Goal: Blender with the ID Toolkit add-on, linked to the Claude app, in as few steps as possible. Ask nothing the
machine can answer. Never ask about preferences: every setting has a good default and can be changed later in
Blender (Edit › Preferences › Add-ons › AI Studio).

Answer in the user's language. Keep each message to what the user has to do now, one step at a time, short.

## 0. Already done?

Look for a `blender_status` tool (server `ai-studio`; with a linked computer it may carry a prefix). If it exists,
call it:
- "Blender … is open" → setup is complete. Say so in one sentence and offer the next step (a CAD part or a render).
- Blender is not open → ask the user to open Blender, call it again, done.
- Tool missing → continue below.

Requirements to state only if they are not met: a Mac (the add-on is macOS only) and the Claude desktop app
(the Blender link and the Onshape browser live there). On Windows or Linux say plainly that the Blender link does not
work there yet; the CAD skill still works.

## 1. Pick the path

Check whether a shell that runs **on the Mac itself** is available (Bash in Claude Code on the Mac, or a tool whose
`uname` prints `Darwin`). A Linux VM or a cloud container is not the Mac.

- macOS shell available → **A · automatic**.
- Otherwise → **B · guided** (three clicks for the user).

## A · Automatic (macOS shell)

Run each step, check its output, go on without asking.

1. Find Blender: `ls -d /Applications/Blender*.app ~/Applications/Blender*.app 2>/dev/null`, then
   `"<app>/Contents/MacOS/Blender" --version | head -1`. Take the newest version ≥ 5.0.
   None found or all older → tell the user to download Blender from https://www.blender.org/download/ (drag it into
   Applications) and say "weiter" / "done" when it is there. This is the only stop.
2. Install the add-on from its repository (gives automatic updates):
   ```sh
   B="<app>/Contents/MacOS/Blender"
   "$B" --command extension repo-list | grep -q id_toolkit || \
     "$B" --command extension repo-add --name "ID Toolkit" \
       --url https://louisdotxyz.github.io/ai-studio-blender/index.json id_toolkit
   "$B" --command extension install -s -e AI_Studio
   ```
   If `install` cannot find the package, run `"$B" --command extension list -s`, use the id it shows and try once more.
   If the repository cannot be reached, download the ZIP named in that index.json and install it with
   `"$B" --command extension install-file -r user_default -e <zip>` (no automatic updates then; say so in one line).
3. Link the Claude app (writes the `ai-studio` server into the Claude app's settings, keeps everything else):
   ```sh
   "$B" -b --python-expr "import bpy, importlib; m=[a.module for a in bpy.context.preferences.addons if a.module.endswith('AI_Studio')][0]; importlib.import_module(m+'.live_clients').connect_desktop(); print('LINKED')"
   ```
   It must print `LINKED`.
4. Open Blender: `open -a "<app>"`.
5. Go to **Finish**.

## B · Guided (no macOS shell)

Send the steps the user has not done yet as one short numbered list, then wait:

1. Blender 5 or newer from https://www.blender.org/download/ (skip if they have it).
2. Open https://louisdotxyz.github.io/ai-studio-blender/ and drag the big button into the open Blender window, then
   confirm the install. Blender then keeps the add-on up to date by itself.
3. In Blender press **N** in the 3D view, open the **Claude** tab and click **Connect** next to
   "Claude app (Cowork): not connected" on the setup card (or Edit › Preferences › Add-ons › AI Studio › General ›
   Live Connection › Connect). The other setup steps there (Claude Code, login) are not needed for this.

Then go to **Finish**.

## Finish

1. Tell the user: quit the Claude app completely once (**Cmd+Q**, closing the window is not enough) and open it again,
   with Blender open. The app loads the Blender link only at start. Then come back to this chat and say "weiter".
2. After that, call `blender_status`. If it answers, say in one line that everything is ready.
3. One optional tip, one line: in the Claude app, set the tool permission for **ai-studio** to "Always allow"
   (Customize › Connectors › ai-studio), so Blender steps do not ask every time.
4. Offer the next step: "Soll ich ein Teil konstruieren oder ein Produktbild rendern?" / "Shall I build a part or render a
   product image?"

## When something does not work

- `blender_status` says the Live Connection is off → Blender: Edit › Preferences › Add-ons › AI Studio › General ›
  Live Connection on.
- Tool still missing after Cmd+Q → check that `~/Library/Application Support/Claude/claude_desktop_config.json` has an
  `ai-studio` entry (path A: read it; path B: repeat step 3), then Cmd+Q again.
- Blender answers "not open" → the Live Connection belongs to the Blender opened last; open Blender and retry.
- Never edit other entries of the Claude app's settings, never log in for the user, never ask for passwords.

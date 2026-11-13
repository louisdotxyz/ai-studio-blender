---
name: cad-to-blender
description: Sends CAD models into Blender and keeps both sides in sync - Onshape export straight into the open Blender, STEP/GLB import, base materials, check images, CAD jobs (change requests from Blender back to the CAD chat). Use for "nach Blender", "in Blender importieren", "neue Version rüber", "CAD-Auftrag", "hol den Auftrag", "blender_jobs", or after a CAD part is ready to render.
---

# CAD → Blender

Needs Blender open with the ID Toolkit add-on (`blender_status` answers). If not: **setup** skill.
Answer briefly in the user's language.

## Send a version into Blender
**From Onshape (no files, no folders):**
1. Get port and token of the open Blender with `blender_python`:
   `import json, os; print(json.load(open(os.path.expanduser("~/.config/ai-studio/live.json"))))`.
   Use them only inside the browser call; never write the token into an answer or a file.
2. In the Onshape tab (helpers loaded, see the **cad** skill):
   `await OS.exportPackage({port, token, name: "<Product>", version: "V<n>", chat: {name: "<this chat's title>"}})`.
   It sends a GLB plus `<name>.cad.json` (part ids, materials, sizes, mounting points) into a `CAD` folder next to the
   open .blend (or `~/Downloads/CAD`). The chat name lets Blender send change requests back to this chat; ask the
   user once for the chat's title if you do not know it.
3. Import in Blender: `print(api.cad_summary(api.cad_import("<returned model path>")))`.

**From Rhino, Fusion or a file:** export GLB (preferred) or STEP to `~/Downloads/CAD/<Product>/`, then the same
`api.cad_import(path)`. Never import by hand with Blender's own importers.

Mounting points: Onshape mate connectors named `MOUNT | <where>` become empties in Blender.

## After the import
1. Read the summary: parts taken over, new parts, parts missing in this version (ask before anything happens to
   them: they are archived, not deleted), size differences, untagged parts.
2. New parts without a base material: offer 2–4 fitting base materials and remember the choice with
   `api.cad_material_map("<CAD material>", "<base material>")`. Never change other materials.
3. Check images: `api.cad_check_views()` starts them, `api.cad_check_result()` returns the paths when ready (poll with
   short calls); look at them with `blender_image`. Collisions and gaps: `mathutils.bvhtree` against the neighbours,
   distances in mm.
4. Geometry problems (collision, gap, wrong size, a radius that renders badly): do not fix the CAD mesh in Blender.
   Send them to the CAD side (below).

## CAD jobs (Blender → CAD chat)
Change requests from Blender wait as jobs; `blender_jobs` works even without Blender open.
- Blender side (a chat working in Blender): send one job per change, with numbers, through the studio helpers
  (see the **blender** skill): `S._cad_job({"text": "Wall 2.4 mm", "part": "<object>", "chat": "<CAD chat name>"})`;
  without `text` it lists the known CAD chats. Ask which chat if the user named none (or `"target": "new"`). If it
  says the CAD skill is off, ask the user to switch on the CAD icon in Blender's skill row.
- CAD chat: when the user says there is a job ("hol den Auftrag"): `blender_jobs` `list` with `chat` = this chat's
  name → `claim` (id, `by` = this chat's name) → change the model → export again as above → `done` with a short note.
- Remarks for later go into the feedback file: `api.cad_feedback(part, problem)`; `api.cad_feedback_open()` lists the
  open points. The CAD chat reads them before the next version.

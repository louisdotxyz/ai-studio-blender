---
name: blender
description: Works in the open Blender from the chat and renders product images with the ID Toolkit add-on - scene changes, materials, camera, light, preview and final renders, contact sheets. Use for "in Blender", "render", "Produktbild", "Rendering", "Material ändern", "Kamera", "Licht", "Szene aufräumen", or any request about the open Blender scene.
---

# Blender from the chat

Tools of the `ai-studio` server (with a linked computer they may carry a prefix): `blender_status`, `blender_scene`,
`blender_object`, `blender_python`, `blender_screenshot`, `blender_image`, `blender_jobs`.
Missing tools or "Blender is not open" → follow the **setup** skill of this plugin.

Answer briefly in the user's language. Ask when a choice changes the result (look, background, which product);
decide technical details yourself.

## Rules
- Start with `blender_scene`, then `blender_object` for the parts you change.
- `blender_python` runs bpy in Blender as **one undo step**; `print()` what you need. Ready to use: `bpy`, `bmesh`,
  `mathutils`, `math`, `np`, `api` (ID Toolkit helpers), `V` (variants and renders). Plain values and functions stay
  for the next call.
- Keep each call short (well under a minute). Long work (renders) runs in the add-on's queue, not in the call.
- **Never save the .blend file.** The user saves.
- Objects you replace go to the archive with `api.archive(objects)`, never delete them unless the user says so.
- Name what you create clearly (`Product | Housing`, `Light | Key`). Units are millimeters.
- After a visible change, check it with `blender_screenshot` before you report.
- Do not touch the user's own objects, materials or settings beyond the request.

## Studio helpers
The add-on's studio tools are reachable from `blender_python`:
```python
import importlib
S = importlib.import_module(api.__package__ + ".claude_tools")
print(S._context({"kind": "light"}))   # light format + subject size, camera, current lights ("hdri" likewise)
```
- `S._light_setup({"setup": {...}})` replaces the light setup (key/fill/rim as one dict, same name overwrites).
- `S._hdri_setup({"setup": {...}})` generates a studio HDRI as World.
- `S._camera({"action": "place", "horizontal": 30, "vertical": 15, "distance": 0.6, "focal_length": 85})` or
  `{"action": "from_view"}`; names itself (`P_front_01` …).
- `S._queue({"action": "add", "camera": "<name>", "quality": "LOW"})`, then `{"action": "start"}`,
  `{"action": "status"}`, `{"action": "cancel"}`.
Brighter/darker or rotated World: `bpy.context.scene.render_studio.world_strength` / `world_rotation`.

Environment in one call each (`api`):
- `api.scene_preset("Studio White" | "Dark Moody" | "Outdoor" | <saved>)`: lights, World, background, floor,
  haze and look together; `api.save_scene_preset(name)` keeps the current state.
- `api.floor("COVE" | "FLOOR" | "NONE", color=(r, g, b), roughness=0.6, shadow_catcher=False)`.
- `api.sun_from_hdri()`, `api.flag(light, "FLAG" | "BLINDS" | "WINDOW" | "DAPPLE")`, `api.only_product(light)`.
- Background the camera sees: `scene.render_studio.background_mode` = `NORMAL` / `TRANSPARENT` / `COLOR`.
- Several looks at once: shots in the Render tab (camera + scene preset + variant), `shots.render_shots(scene)`.

## Product render: the short path
1. **Setup once per file:** `bpy.ops.render_studio.product_setup()` (Cycles on the GPU, good samples, denoising,
   AgX, mm, bounces for glass and metal). The user can undo it in the Studio tab.
2. **Camera:** use the scene camera if it shows the product; otherwise `S._camera(...)`. Ask once which view
   matters if it is not clear (front, 3/4, top). Products: 50–100 mm, 10–15 % air around the product.
3. **Light:** product photo default = key 45° above on the camera side, soft fill opposite at about 1/3, rim from
   behind, area lights 1–2× the product size, 4500–6500 K. Keep the user's World unless asked.
4. **Preview render** through the queue (a second Blender renders, the user keeps working): add + start as above,
   then poll with short calls `print(S._queue({"action": "status"}))` a few seconds apart (never a long
   `time.sleep` inside Blender).
5. **Look at it:** `V.renders(last=1)` gives the newest file; show it with `blender_image`. Several images:
   `V.sheet([f for f, _ in V.renders(last=8)])` makes one contact sheet → `blender_image`.
   Check: exposure (nothing blown out or black), materials readable, product framed with air, no noise or artifacts.
6. Adjust and render again (at most 2–3 rounds on your own), then show the user. **Final**: same with
   `quality="HIGH"` only when the user likes the preview.
7. Report where the files are (`V.folder("HIGH")`) in one line.

## Variants (colors, materials)
For series of the same shot: `V.capture()` stores the current state as "00 Original"; variants live in the text
block "Varianten.json" (`V.data()`, `V.save()`): `"targets"` maps an alias to "Material/Node", `"variants"` maps a
name to `{alias: {input: value}}` (`"color": "#RRGGBB"`, or an input name with a number).
`V.queue([names], camera="…", quality="LOW")` renders one image per variant without changing the scene.

## CAD parts in Blender
Imports, CAD jobs and new versions from CAD: see the **cad-to-blender** skill.

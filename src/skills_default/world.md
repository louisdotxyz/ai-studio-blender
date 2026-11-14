---
title: WORLD
description: The world around the product - use a room or HDRI, a generated sky/studio HDRI, or build a room from a reference and bake it
icon: WORLD
studio_tab: LIGHT
---
World with Render Studio. Answer in 1-2 sentences. studio_context(kind="environment") -> subject, floor,
camera, HDRI folder, room objects (obj["as_env"]), worlds. Never delete or edit the user's worlds.
A) Existing room (objects marked obj["as_env"], older files "ENV | <Name>"): use the newest
   <HDRI folder>/Environment/<Name>_*.exr with api.use_hdri(path). Rotation 0 = as modelled.
B) Generated HDRI without a room (sky, sun, softboxes): studio_context(kind="hdri"), then
   studio_hdri_setup(setup={...}); changes: the same name again.
C) Build a new room from a reference or description and bake it:
  1. Read the reference: size, ceiling height, parts (walls, floor, ceiling, beams, columns, windows, niches,
     doors, radiators, pipes, lamps), materials, light (overcast or hard sun, direction).
  2. Build in the collection Set, objects "<Name> <Part>", each marked obj["as_env"] = True, boxes from real meters via
     mesh.from_pydata, the camera-facing side first. Bevel 5-20 mm on columns/beams/walls. Window glass
     visible_shadow = False. Outside: ground + neighbour facades 10-25 m away, not higher than the windows need.
  3. Materials: real PBR scans beat procedural. Poly Haven API (allowed): json from
     https://api.polyhaven.com/files/<id> -> Diffuse/Rough/nor_gl/AO at "2k" jpg into <project folder>/assets/textures/<id>/.
     Chain: TexCoord Object -> Mapping (1/size_m) -> Image (BOX, blend 0.25); Diffuse -> HueSat
     (desaturate/brighten) x AO x large Noise (scale 0.25) x AmbientOcclusion grime -> Base Color;
     Rough -> Roughness; nor_gl -> Normal Map. Good ids: hangar_concrete_floor, white_plaster_rough_02,
     painted_concrete, dirty_concrete, red_brick_plaster_patch_02, factory_brick. Never pure white/black.
  4. Bake World "<Name> Bake" (copy an existing "… Bake" world: overcast gradient, a Math node for the sky
     strength, 40-120 for a bright day). Never delete or edit the user's worlds.
  5. Backup first: bpy.ops.wm.save_as_mainfile(filepath=<file>_backup_<Name>.blend, copy=True).
     Then studio_environment_bake(name="<Name>", world="<Name> Bake", resolution=1024, samples=32)
     as a test (it takes the as_env objects, also when they are hidden in renders). Look at the EXR (T.exr_preview, Read).
     Fix, then final resolution=4096, samples=256 (several minutes; ignore an MCP timeout and check the file).
     Afterwards room objects hide_render = True (the bake does not need them visible); the
     shadow-catcher floor stays visible.
     Delete test EXRs only after asking.
Brighter / darker / rotated: only scene.render_studio.world_strength and world_rotation, never bake again for that.
Check: render_preview (max_size 600). An EXR itself: T.exr_preview(path) -> PNG in attachments, look at it
with Read, then T.cleanup().

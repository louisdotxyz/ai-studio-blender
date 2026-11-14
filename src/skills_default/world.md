---
title: WORLD
description: The world around the product - use a room or HDRI, or a generated sky/studio HDRI (a new room: ENV)
icon: WORLD
studio_tab: LIGHT
---
World with Render Studio. Answer in 1-2 sentences. studio_context(kind="environment") -> subject, floor,
camera, HDRI folder, room objects (obj["as_env"]), worlds. Never delete or edit the user's worlds.
A) Existing room (objects marked obj["as_env"], older files "ENV | <Name>"): use the newest
   <HDRI folder>/Environment/<Name>_*.exr with api.use_hdri(path). Rotation 0 = as modelled.
B) Generated HDRI without a room (sky, sun, softboxes): studio_context(kind="hdri"), then
   studio_hdri_setup(setup={...}); changes: the same name again.
C) Build a new room from a reference or description and bake it: with the ENV skill (turn it on if it is off).
Brighter / darker / rotated: only scene.render_studio.world_strength and world_rotation, never bake again for that.
Check: render_preview (max_size 600). An EXR itself: T.exr_preview(path) -> PNG in attachments, look at it
with Read, then T.cleanup().

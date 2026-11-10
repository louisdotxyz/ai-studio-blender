---
title: HDRI
description: World from a baked room or a generated HDRI; brightness and rotation
icon: WORLD
studio_tab: LIGHT
---
World with Render Studio. Answer in 1-2 sentences. (Building a new room: ENV skill.)
studio_context(kind="environment") -> subject, floor, camera, HDRI folder, room objects (obj["as_env"]), worlds.
A) Existing room (objects marked obj["as_env"], older files "ENV | <Name>"): use the newest <HDRI folder>/Environment/<Name>_*.exr with
   api.use_hdri(path) (api is ready in execute_python). Rotation 0 = as modelled.
B) Generated HDRI without a room (sky, sun, softboxes): studio_context(kind="hdri"), then
   studio_hdri_setup(setup={...}); changes: the same name again.
Brighter / darker / rotated: only scene.render_studio.world_strength and world_rotation. Never bake again for that.
Never delete or edit the user's worlds.
Check: render_preview (max_size 600). An EXR itself: T.exr_preview(path) -> PNG in attachments, look at it with
Read, then T.cleanup().

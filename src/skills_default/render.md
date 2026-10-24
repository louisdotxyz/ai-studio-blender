---
title: RENDER
description: Render with the Studio queue, check the images, adjust; variant series
icon: RENDER_STILL
studio_tab: QUEUE
chat: yes
---
Render loop with the Studio queue: render -> look -> adjust -> render again. Answer briefly.
The queue renders in a second Blender without a window; the user can keep working meanwhile.
1. Render only through the queue: one view with studio_render_queue(action="add", camera=…, quality="LOW") +
   action="start"; variant series with V.queue([...variant names...], camera="…", quality="LOW"|"HIGH")
   (V is ready in execute_python). LOW first (preview folder), HIGH only when the look is right.
   Samples, denoising and size of LOW/HIGH come from the queue presets (gear in the Queue tab): never change
   the scene's render settings for that. Camera and light only via studio_camera / studio_light_setup.
2. Wait: studio_render_queue(action="wait") answers when everything is rendered (no polling, never sleep
   in Blender's thread). Report failed entries.
3. Check: images are in V.folder() (the render folder from the preferences); V.renders() gives [(file, variant)].
   p = V.sheet([f for f, _ in V.renders(last=10)], name="sheet.png") and look at the PNG with Read
   (render_check/ is readable). Single image: V.sheet([path], cols=1, width=900).
   General criteria: exposure (nothing blown out or crushed), colors distinguishable and coherent, framing with
   air, no artifacts, brightness per numpy if needed. Project criteria are in the project memory.
4. Adjust and re-render only the affected entries: material values in Varianten.json (V.data() / V.save()),
   light via studio_light_setup (same name), camera via studio_camera, objects directly with bpy.
   At most 2-3 rounds on your own, then show the user the result.
Variants (text datablock "Varianten.json" in the .blend, editable by hand):
- "targets": alias -> "Material/Node", e.g. "BODY": "Wood/Stain", "TRIM": "Plastic/Principled BSDF".
- "variants": name -> {alias: {input: value}}; "ncs": "S4040-R40B" (NCS stain), "color": "#RRGGBB" (sRGB hex),
  otherwise an input name (or its start) with a number. "00 Original" = the starting state;
  V.capture() reads the current values, V.apply(name) sets them in the scene.
- A queue entry carries its variant; it is applied only to that entry's render copy, the scene stays as it is.
  varianten.txt in the output folder maps file -> variant.
- New palettes: read inspiration images (hues, brightness steps, accents); every variant independent.
  Wood stains e.g. as NCS (blackness 10-85, chromaticness 10-60), plastics as hex with roughness 0.3-0.45.

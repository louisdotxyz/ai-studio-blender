---
title: MATERIAL
description: Create or change the active material
icon: MATERIAL
studio_tab: MATERIAL
---
Material of the active object (the one in the Studio Material tab). Answer in 1-2 sentences.
- obj = bpy.context.active_object and its active material (create one if there is none).
  Never touch other materials.
- Shared materials: check material.users first. If other objects use it (e.g. one wood material on 20 parts), say so
  and ask with ask_user: change it for all / a copy only for this object.
- Colors of product variants (stain, print colors) belong into Varianten.json (RENDER skill), not into the
  material: change the material itself only for the base look.
- Generic materials (glass, stainless steel, aluminium, chrome, rubber, ABS, paint, speaker cloth) come from the
  library group "Grundlage" in the Material tab: reuse them instead of building new ones.
- Principled BSDF with real values: metallic 0 or 1, roughness 0.05-0.9, a fitting IOR, base color never
  pure black or white. Procedural detail (Noise/Voronoi/Bump) only if the wish needs it, scaled to the
  object. Keep node groups in use. Name materials "<Type> | <Material>" for
  bought parts, otherwise clearly.
- Lacquer on wood (RAL 9005 matte etc.): base color + subtle noise roughness (0.35-0.5), bump 0.02-0.05
  for the grain under the lacquer.
- Follow-ups change the same material. Check: render_preview (max_size 600).

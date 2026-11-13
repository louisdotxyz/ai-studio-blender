---
title: MATERIAL
description: Create or change the active material
icon: MATERIAL
studio_tab: MATERIAL
chat: yes
---
Material of the active object (the one in the Studio Material tab). Answer in 1-2 sentences.
- obj = bpy.context.active_object and its active material (create one if there is none).
  Never touch other materials.
- Shared materials: check material.users first. If other objects use it (e.g. one wood material on 20 parts), say so
  and ask with ask_user: change it for all / a copy only for this object.
- Colors of product variants (stain, print colors) belong into Varianten.json (RENDER skill), not into the
  material: change the material itself only for the base look.
- Generic materials (glass, stainless steel, aluminium, chrome, rubber, ABS, paint, speaker cloth) come from the
  base material group in the Material tab (base_group(), copies carry material["render_studio_base"]): reuse
  them instead of building new ones.
- Changing a base material for all projects (only when the user asks): improve the copy here, then
  studio_base_material(action="save"). A new base material: same with a clear name. "remove" only after
  ask_user.
- Principled BSDF with real values: metallic 0 or 1, roughness 0.05-0.9, a fitting IOR, base color never
  pure black or white. Procedural detail (Noise/Voronoi/Bump) only if the wish needs it, scaled to the
  object. Keep node groups in use. Name materials "<Type> | <Material>" for
  bought parts, otherwise clearly.
- Materials with a surface of their own are built in Blender from a structure (api is ready in execute_python):
  api.material_structure(kind, name=…, objects=[…], **values) with kind WORN_METAL (tumbled or used metal:
  scratches, patina), LAYERED_EARTH (rammed earth, cast stone: layers along Z, grain), SPECKLED (recycled
  plastic, powder coat: flecks), RESIN (amber, acetate: clear, color deepens with thickness; Cycles),
  HAMMERED (polished metal with dents), DARK_ANODIZED (dark metal, long scratches, wipe marks). It is
  procedural 3D (no UVs, no images): Texture Coordinate → Mapping → one node group "ID | …" → Bump →
  Principled. Change it through the group's inputs (color, roughness, scale, scratches, patina, color_a,
  color_b, layer_height, grain, fleck_size, amount, dent_size, depth, smudges, bands, band_size) as
  values of api.material_structure or on the node "ID Structure"; "Scale" fits the pattern to the size of
  the product (made for 10-30 cm: a 1 m part needs about 0.3). For another look start from the nearest
  structure and change it inside its group, instead of a new node tree from nothing.
- Used look on any material: api.wear(material, scratches=0.4, fingerprints=0.3, dust=0.2, scale=1)
  (api.wear(material, remove=True) takes it off). Subtle is right: 0.2-0.4.
- Lacquer on wood (RAL 9005 matte etc.): base color + subtle noise roughness (0.35-0.5), bump 0.02-0.05
  for the grain under the lacquer.
- Follow-ups change the same material. Check: render_preview (max_size 600).

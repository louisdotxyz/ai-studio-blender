---
title: BUILD
description: Model a bought part to its datasheet (exact dimensions, material from the photo)
icon: MODIFIER
---
Build a bought part (caster, handle, socket, driver …) from its datasheet/photo at the given position, for the
render. A part that is manufactured or sets dimensions of the product belongs in the CAD program, not here.
1. CAD first: search the manufacturer's site for a STEP/3D download (WebSearch/WebFetch). If there is one,
   tell the user the link and ask with ask_user: "import the STEP (studio_cad_import) / model it from the datasheet".
2. Dimensions: print every dimension from the datasheet as a list before building. Missing ones (sheet
   thickness, axle, hub, radii): look them up at the manufacturer; otherwise ask_user ("typical values /
   I give the dimensions"). Name assumed values at the end. Check how the drawing reads (plate A x B:
   A = direction of travel, B = axle direction).
3. Position: a mounting empty "MOUNT | <Name>" from the CAD import (collection "CAD Mounts") comes first: it
   moves with every new CAD version, so the part follows. Otherwise the user's placeholder (box or empty).
   Convention: empty with ARROWS, origin = middle of the mounting face, local +Z points into the part it is
   mounted on (the part hangs in -Z), local X = main/axle direction. For a box take the face lying on the target and project it onto the real outer face of the
   board (read the board's face normals). Unclear: ask_user.
4. Build in mm (MM = 0.001) in the empty's local space with bmesh: profile + extrude, solids of revolution
   with bmesh.ops.spin, holes with an Exact boolean applied via new_from_object. Sharp edges by angle,
   otherwise smooth. Modifiers as used in the project (e.g. Bevel 0.5-0.8 mm by angle +
   Weighted Normal); sheet parts with Solidify. Standard parts (screws, nuts) to DIN/ISO.
5. Material from the photo, Principled with real values, named "<Type> | <Material>", e.g.
   "Caster 125 | Galvanized steel" (Metallic 1, base ~0.78/0.81/0.87, roughness 0.22-0.38 + noise),
   "Caster 125 | Solid rubber black" (base ~0.01, roughness 0.68-0.82, IOR 1.45, fine bump).
   Reuse materials of the same name, never change other materials. Generic ones (glass, stainless, rubber, ABS)
   may come from the Material tab's library "Grundlage".
6. Structure: objects "<Type> | <Side> | <Part>" as children of the empty "<Type> | <Name>", repeated parts
   share one mesh (linked); parent the type's empty to the "MOUNT | …" empty when there is one. Own collection
   after the project scheme in the fitting parent collection. Store the datasheet URL on the empty: empty["datasheet"] = "<url>".
   Hide the placeholder (hide_set + hide_render), never delete it.
7. Check:
   - collisions and floor clearance with mathutils.bvhtree.BVHTree.FromObject (evaluated) against the
     neighbours, print the overlaps / distances in mm,
   - a detail render: temporary 85 mm camera aimed at the part, render_preview(camera="<name>"), compare with
     the datasheet photo, then delete the camera.

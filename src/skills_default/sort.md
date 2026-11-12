---
title: SORT
description: Tidy up the scene without changing its content
icon: SORTALPHA
---
Tidy up the scene without changing geometry, materials or settings. Answer briefly.
1. Analyse first and print a list: objects outside a fitting collection or named like "Cube.001",
   meshes/materials with ".001" duplicates, unapplied scale, orphan data (users == 0), empty collections.
2. Protect, never rename or move:
   - materials and nodes named in the text "Varianten.json" (targets "Material/Node"): renaming breaks the variants,
   - backup collections (name contains "backup"/"Backup") and their content, room objects (obj["as_env"], older "ENV | …" rooms), the archive and temp collections,
     the camera and light collections (Camera, Light; older AS_camera, AS_lights, RS_Cameras, RS_Lights), objects with
     "render_studio_temp_camera".
3. Rename after the project scheme "<Group> | <Part>" (e.g. "Cabinet | Lid | Top 01");
   move objects into fitting existing collections, never invent a new hierarchy.
4. Scale: apply only after ask_user, and never on objects whose mesh is shared (mesh.users > 1,
   e.g. feet, casters) or that are mirrored (a negative scale axis): list those instead.
5. Remove (orphans, empty collections) only after ask_user; unwanted objects go into the archive with
   api.archive(objects), never deleted unless the user says so. Never remove Worlds, cameras, lights or
   room objects; never delete files.
6. End with 2-3 lines: renamed, moved, removed, skipped (with the reason).

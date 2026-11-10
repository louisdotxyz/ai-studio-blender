---
title: CAD
description: Take a new CAD version over, check it against the CAD and report problems back
icon: FILE_REFRESH
---
A CAD model (Onshape, Rhino, Fusion) comes into the scene or a new version replaces the old one. Answer briefly.
1. Import with studio_cad_import(path) – never by hand. It reads the sidecar <name>.cad.json next to the export
   (part ids, CAD materials, mounting points, sizes, variants). Without a sidecar it pairs by part name.
2. Read the report: parts taken over, new parts without a partner, old parts not in this version (ask_user
   before anything happens to them: they are archived, not deleted), sizes off, untagged parts, open CAD feedback.
3. New parts without a base material ("no base material"): pick a fitting one from the base group with
   ask_user (2-4 options) and remember it for later imports with api.cad_material_map("<CAD material>", "<base>").
   Never change other materials.
4. Check: studio_cad_check (Iso + Front) and look at the images; collisions/clearances with T or
   mathutils.bvhtree against the neighbours, distances in mm.
5. Problems in the geometry (collision, gap, wrong size, a radius that renders badly): do not fix the CAD mesh
   in Blender. Note each one with studio_cad_feedback(part, problem) – one sentence with numbers.
6. End with: what changed, what was archived, open points for the CAD side.

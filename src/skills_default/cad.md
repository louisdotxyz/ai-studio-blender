---
title: CAD
description: Link to the CAD chats: send changes to the CAD side, take new versions over, report problems
icon: FILE_REFRESH
---
The CAD model is built in a Claude chat in the Claude app (Onshape). That chat sends each new version straight
into this Blender. Here you check it and send changes back. Answer briefly, in the user's language.

Changes for the CAD side (CAD jobs)
1. When the user wants something changed in the CAD model ("Wand 2.4 mm", "Loch weiter nach links"), send it with
   studio_cad_job(text, part, chat): one job per change, with numbers, part = the selected object if it fits.
2. The chat: the one the user names ("an CAD 5"). If none was named, call studio_cad_job without text first: it
   lists the known CAD chats (last active first) and the chat of the current model. Ask with ask_user: up to 3 of
   them (the model's chat first) and "Neuer Chat" (target "new"). Never guess.
3. Say in one sentence where it went. The chat shows by itself when the CAD chat takes the job and when it is done;
   the new version then comes in on its own.
4. Remarks for later (no change now) go into studio_cad_feedback(part, problem): CAD-Feedback.md next to the export,
   which the CAD chat reads. Without "problem" it lists the open points; tell the user when they ask.

A new version came in (the report of the import)
1. Read it: parts taken over, new parts without a partner, old parts not in this version (ask_user before anything
   happens to them: they are archived, not deleted), sizes off, untagged parts, open CAD feedback.
2. New parts without a base material ("no base material"): pick a fitting one from the base group with ask_user
   (2-4 options) and remember it with api.cad_material_map("<CAD material>", "<base>"). Never change other materials.
3. Check: studio_cad_check (Iso + Front) and look at the images; collisions/clearances with mathutils.bvhtree
   against the neighbours, distances in mm.
4. Problems in the geometry (collision, gap, wrong size, a radius that renders badly): do not fix the CAD mesh in
   Blender. Send them as a CAD job (above), or as feedback if the user wants it for later.
5. A file given by hand: studio_cad_import(path), never import by hand.

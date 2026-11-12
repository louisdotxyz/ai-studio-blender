---
title: PHOTO
description: Put the product into a reference photo 1:1 (framing, perspective, light)
icon: IMAGE_DATA
---
An attached photo is the background. Place the product so framing, perspective and background match the
photo 1:1. Use M (photo match helper, ready in execute_python).
1. Read the photo: horizon (eye height = vanishing point of floor lines) as a share from the bottom, focal
   length (wide interior 18-28 mm, normal 35-50 mm), camera height (doors ~2.0-2.1 m, window sill ~0.9 m),
   light direction and hardness.
2. M.setup(path, name="<short name>")  -> camera "P_match-<name>_01" in Camera, the photo as camera background,
   resolution in the photo's aspect (the old one is kept on the camera).
   cam.data.lens = …; M.set_horizon(cam, share_from_bottom, height=camera_height)   # straight verticals
   Move the camera in x/y so the product stands where it makes sense in the photo (ask_user if unclear:
   middle / left / right / closer). Never move the product, the camera moves.
3. Light to match the photo: environment with the HDRI skill (existing ENV or a room built from the photo,
   ENV skill), a sun only for hard shadows. The shadow-catcher floor stays visible (is_shadow_catcher).
4. Check: out, mean = M.check(cam)  -> the cut-out render laid over the photo in attachments/check.png;
   look at it with Read. Compare floor contact, vanishing lines, size, brightness and color; adjust lens /
   height / horizon / world_strength.
5. Final: M.final(cam) -> full resolution PNG in ~/Downloads (folder="…" for another one). Back to the normal format: M.restore(cam) and the
   previous scene camera.
Extend (wider view or another angle): build proxy geometry after the photo (floor, back wall, side walls as boxes
in Set, marked obj["as_env"] = True, measured from the match camera), then M.project(cam, [objects]) projects the photo onto them.
Fill outside the photo with materials as in the ENV skill.

---
title: CAM
description: Camera, framing, focal length, depth of field
icon: CAMERA_DATA
studio_tab: RENDER
chat: yes
---
Set up the active scene camera (bpy.context.scene.camera); a new camera only on request. Answer briefly.
Cameras live in the collection "Camera". Names: P_ (perspective) or O_ (orthographic), the view, a number:
"P_side_01", "O_front_01", "P_iso_01"; api.rename(cam, "front") does it. Never change the type of the
user's cameras without asking.
- Orthographic: straight from the front/side/back/top, no perspective. Frame with ortho_scale from
  the subject's bounding box projected onto the camera plane (width W, height H in m) plus 10-15 % margin:
  aspect a = res_x / res_y; ortho_scale = max(W, H * a) if a >= 1 else max(H, W / a); times 1.1-1.15.
  Center: put the camera's x/y (in its own plane) on the bounding box center.
- Perspective: product 50-100 mm, camera at product height or slightly above, keep the scene's format
  unless asked. Interiors/architecture 24-35 mm with straight verticals (rotation x = 90°, shift_y instead
  of tilting). Frame centered or by thirds with 10-15 % air; compute the aim with vectors, no Track To.
- Depth of field: dof.focus_object = the product, f/5.6-11 for products; ortho cameras without DOF.
- From a reference photo: read height, angle, image share and focal length and rebuild
  (studio_camera action="place" or directly). No photo and unclear goal: offer 2-3 classic views with ask_user.
- Final cameras go into the render queue on request (studio_render_queue action="add").
- Check: render_preview(camera="<name>"), small (max_size 600).

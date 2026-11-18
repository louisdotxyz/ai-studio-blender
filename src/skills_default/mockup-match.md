---
title: MOCKUP MATCH
description: Mockup on a model in the scene (a bike, a table, a car) - the camera measured from points of that model, so the product sits exactly where it is mounted
icon: MOD_WIREFRAME
studio_tab: MOCKUP
chat: yes
---
Use this before the MOCKUP steps 2-4 whenever the product is mounted on something that is also in the Blender
file as a model – the user's bike (VELO LUME: the lights on the bike), a table, a shelf, a car. Then the camera
is not guessed from a horizon: it is measured so that the model lies exactly on the same thing in the photo,
and the product, which sits on the model, is right by itself. Answer in 1-3 sentences, result first.

1. Reference: state = api.mockup_state(). No reference yet ("Match to: nothing" in the panel): find the model's
   collection (names with Velo, Bike, Fahrrad, Rad, Scan first: [c.name for c in bpy.data.collections]) and
   api.mockup_reference(collection="…"). Unsure which one: ask the user in one short question.
2. Points of the model (world, metres) – read them in execute_python, do not invent them:
   - a bike: the two axles (wheel centres), the top and the bottom of each tyre (axle ± radius in z), the
     bottom bracket, the top of the saddle, the ends of the handlebar (the only points off the bike's
     middle plane: they fix the lens). Wheels: bounding boxes of the wheel / tyre objects (centre = axle,
     half the height = radius). VELO LUME's scan: rear axle (0, -0.927, 0.339), front axle (0, 0.022, 0.344),
     tyre radius 0.335 – check them against the model before using them.
   - anything else: clear corners and ends you can also see in the photo (table corners, feet, edges).
3. The same points in the photo: look at the attached photo and give each as (u, v), 0-1 from the photo's TOP
   left. Mark only what you can really see; a point hidden behind a leg is left out, not guessed.
4. api.mockup_match([[[x, y, z], [u, v]], …], lens=…): lens in mm when known (EXIF, or a guess: phone 26,
   reportage 35); leave it out only with 6+ points and some off the middle plane (handlebar). It sets the camera
   and returns match_rms_px and the error per point: a point far off (> 3× the others) is misread – fix or
   drop it and match again. Good: under about 8 px at 2K.
5. Check: api.mockup_look() → the model in cyan over the photo, the product shaded. The cyan bike must lie on
   the photo's bike (wheels, frame, saddle). Off: correct the points (not the sliders) and match again, at most
   3 rounds. Then tell the user the result can be fine-tuned live: "Live View" in the panel (moving the view
   moves the camera, the model stays visible over the photo).
6. Then MOCKUP step 5 on (light from the photo, render, montage, Krea). The sliders for the product's place do
   nothing now (Matched Camera): never use them to "fix" the position – the model decides it.
Things of the photo in front of the product (the fork in front of a light pod, a leg in front of the rear
light): the model's own parts can mask it – the user selects them and clicks the mask button; a light that is
completely hidden in the photo is not shown, only its glow (say so).

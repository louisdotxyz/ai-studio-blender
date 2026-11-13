---
title: LIGHT
description: Studio light setup for the product
icon: LIGHT
studio_tab: LIGHT
chat: yes
---
Light setup with Render Studio's tools. Answer in 1-2 sentences.
1. studio_context(kind="light") -> setup format, subject size, camera, current lights.
2. studio_light_setup(setup={...}) replaces the previous setup and saves it as a preset; the same name
   overwrites. Do not create lights by hand for a normal setup.
   Product photo: key 45° above on the camera side, soft fill opposite at about 1/3, rim from behind;
   area lights ~1-2x the subject size; never pure white, 4500-6500 K. Keep the World unless asked.
3. Changes ("warmer", "softer", "more rim"): call studio_light_setup again with the changed setup.
Know:
- Render Studio names its lights itself (A_key_01, A_fill_01, A_rim_01 … in the collection Light; A/P/S/D =
  area/point/spot/sun): refer to them by those names, not by the names you asked for.
- Light per part (light linking): only when the wish needs it. One light per part, no drivers, constraints or
  helper empties. Link with object.light_linking.receiver_collection to a flat collection that holds the
  objects directly: nested collections inside it are not followed.
- Other add-ons: studio_context lists the ones that are on and how to reach them (atmosphere from Physical
  Starlight & Atmosphere, placing with Light Wrangler). Use them for what they add; do not rebuild a light setup
  with add-ons the Light tab covers (Quick Studio Light, QLE, Tri-lighting).
- Environment tab helpers (api is ready in execute_python):
  api.scene_preset("Studio Softboxes" | "Daylight" | "Golden Hour" | "Night" | "Studio White" | "Dark Moody" | saved)
  sets lights, World, background, floor, haze and look together.
  api.world("STUDIO", pins=[{"name": "Key", "horizontal": -45, "vertical": 35, "width": 50, "height": 40,
  "strength": 10, "softness": 0.35}, …], ambient=0.25): a studio World painted from softboxes (soft reflections on
  glossy products; 0° = from the camera, negative = left). api.world("SKY", time=17.2): sky by time of day
  (14 day, 17.2 golden hour, 23 night). api.world("IMAGE") goes back to the HDRI file.
  api.place_light(light, on_object, "REFLECTION" | "ILLUMINATION" | "RIM"): put a light so its highlight / light /
  rim falls on that part as the camera sees it.
  api.floor("COVE" | "FLOOR" | "NONE", color=(r, g, b), roughness=…, shadow_catcher=…); api.sun_from_hdri();
  api.flag(light, "FLAG" | "BLINDS" | "WINDOW" | "DAPPLE") (None removes it); api.only_product(light, True);
  haze: scene.render_studio.haze_density (0-0.2). Background: scene.render_studio.background_mode.
- Check: render_preview (max_size 600).

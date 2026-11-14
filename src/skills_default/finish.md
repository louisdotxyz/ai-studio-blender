---
title: FINISH
description: Refine a finished render with Krea - Claude writes the prompt from your sentence
icon: BRUSHES_ALL
studio_tab: FINISH
chat: no
---
A finished render goes to Krea's image editing (Finish tab). You write the prompt; the user wrote a short
wish. Answer in 1-2 sentences. The render (and an environment picture) are attached: look at them first.
1. Write the prompt in English, concrete and short: what to add or change, where, how it is lit (match the
   light, shadows and reflections of the render). Name the product by what it is ("the black rounded bike
   light pod on its metal mount"). Do not repeat "keep the product": the add-on adds that itself.
2. Model: PRECISE when something is added to the picture as it is (a hand, props, a person next to it).
   NANO when the environment changes, the user wants to zoom out or another perspective. A model named in
   the message wins.
3. Start it once: api.krea_refine(prompt, model=…, environment=path or "", zoom="KEEP"|"SOME"|"FAR",
   view="KEEP"|"EYE"|"ABOVE"|"LOW") in execute_python. It runs in the background (Finish tab shows it); do
   not wait or poll. It costs money per picture: never start more than one without being asked.
4. Wear (dust, fingerprints, scratches) does not belong here: an image AI paints it on. Say so and offer the
   Wear section of the material instead.
5. Tell the user in one line what you asked Krea for and with which model.

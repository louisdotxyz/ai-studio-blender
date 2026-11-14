---
title: FINISH
description: Refine a finished render with Krea - Claude writes the prompt and makes it with your Krea plan
icon: BRUSHES_ALL
studio_tab: FINISH
chat: no
---
A finished render goes to Krea (Finish tab), paid with the user's Krea plan through the Krea connector
(mcp__claude_ai_Krea__*). You write the prompt from the user's short wish. Answer in 1-2 sentences.
The render (and an environment picture) are attached: look at them first.
0. Load the tools in one ToolSearch call: "select:mcp__blender__execute_python,mcp__claude_ai_Krea__get_upload_url,
   mcp__claude_ai_Krea__generate_image,mcp__claude_ai_Krea__get_job". No Krea tools: tell the user to connect
   Krea in Claude (Settings › Connectors) or add an API token for ▶, and stop.
1. Prompt in English, concrete and short: what to add or change, where, how it is lit (match the light,
   shadows and reflections of the render). Name the product by what it is ("the black rounded bike light
   pod on its metal mount"). The add-on adds "keep the product" and the true scale itself (it reads the
   product's size from Blender: a hand is drawn at real size). Do not invent sizes.
2. Model: PRECISE when something is added to the picture as it is (a hand, props, a person next to it).
   NANO when the environment changes, the user wants to zoom out or another perspective. A model named in
   the message wins. protect (a mask keeps the product untouched, Precise only): as the message says, but
   protect=False when a hand or a person touches the product (fingers go over it); say so then.
3. get_upload_url, then in execute_python:
   p = api.krea_prepare(prompt, upload_url, model=…, environment=None, zoom=None, view=None, protect=…)
   (None = the Finish tab's choice; zoom CLOSER/KEEP/WIDER, view = camera height LOWER/KEEP/HIGHER, from
   the wish: "näher" CLOSER, "weiter weg" WIDER, "von oben" HIGHER). It returns {"model", "input", ...}:
   pass both on unchanged. The product's size is measured from what the user marked as the product
   (Finish tab); the message's "Product:" line shows it. If it looks wrong (much bigger than the product),
   ask the user to select the product and click "Product = Selection" first.
4. Price first: generate_image(model=p["model"], input=p["input"], preflight=true). Then
   api.krea_quote(credits, balance) so the Finish tab shows it, and say it in the chat. More than
   400 credits, or fewer credits left than it costs: ask the user before going on.
5. generate_image(model, input, sync=true, timeoutSeconds=280). Still running: get_job until it is done.
   Then api.krea_save(result_url, credits=…, balance=…) (balance after it: balance − credits).
   It failed: api.krea_failed(reason) and tell the user why. Never start more than one picture unasked.
6. Wear (dust, fingerprints, scratches) does not belong here: an image AI paints it on. Say so and offer
   the Wear section of the material instead.
7. Tell the user in one line what you asked Krea for, with which model and what it cost.

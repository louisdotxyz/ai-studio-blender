---
name: cad
description: Designs or changes parts and products in CAD - Onshape (in the Claude app's browser), Rhino or Fusion if connected, otherwise code CAD - parametric, checked and exported. Use for "konstruieren", "modellieren", "CAD", "Bauteil", "Gehäuse", "nachbauen", "Part Studio", "FeatureScript", "Onshape", "Rhino", "Fusion", "STEP", or a technical drawing to model.
---

# CAD

Build parametric, check, export. Answer briefly in the user's language. Work on your own on clear tasks; ask when a
missing size or choice would change the model; always ask before deleting anything the user built.

## 1 · Program (decide yourself, one sentence why)
| Situation | Program |
|---|---|
| The model already lives in a program | stay there |
| No preference, the Claude desktop app is open | **Onshape** in the app's built-in browser (free account, nothing to install) |
| A Rhino or Fusion MCP is connected and the user works there | Rhino / Fusion |
| No CAD reachable, a purely dimensional part | **build123d** (code CAD) → STEP the user can open anywhere |

The user can override any time. Check which programs are reachable silently (tool list, then the browser); do not ask.

## 2 · Connect
- **Onshape:** load the built-in browser skill, open `https://cad.onshape.com/documents`. The user signs in
  themselves (never type passwords). Then read `references/onshape-helpers.js` and run it once per tab with the
  browser's JavaScript tool: `window.OS` gives `OS.session()`, `OS.info()`, `OS.parts()`, `OS.addFeatures()`,
  `OS.updateFeatures()`, `OS.setVariables()`, `OS.eval()`, `OS.exportPackage()`. Run it again after each navigation.
  API details and pitfalls: `references/onshape.md`.
- **Rhino / Fusion:** only through an MCP that is already connected (`get_device_info`/tool list). Not connected →
  say in one line what the user would need (the program's MCP), and offer Onshape or build123d instead.
- A step fails twice → say in one sentence what is missing and continue with the next best option.

## 3 · Input → short brief
- **Technical drawing (PDF, image, DXF):** read views, scale, units, tolerances, threads, material. Make a table
  size · value · source. Ask for missing or contradicting sizes in one go, never guess.
- **Inspiration images:** describe briefly what you see, then ask what exactly the user likes (form, proportions,
  details, material, mechanism). Take over only that.
- **A product by name:** search for datasheets, dimension drawings, CAD downloads (manufacturer, TraceParts,
  GrabCAD), patents. Mark each size: datasheet · measured on a photo · estimated. Sources at the end.
- Send the brief (program, size table, assumptions, build plan). Clear task → build right away and send the brief
  along.

## 4 · Build
- Every size as a named parameter with a **descriptive name** (`wall_thickness`, `housing_length`, not `wt`).
  Onshape: variables or feature parameters; Rhino: a parameter block in the script; Fusion: user parameters.
- Numbered structure: `00` parameters/reference, `10 <part>`, `20 …`, `90 helpers`.
- Name every part clearly (`Housing | PETG`, `Lid | Aluminium`): the names become object and material names in
  Blender. Build each part around its own origin; repeated parts as instances; helper bodies as `REF …`.
- Think of manufacturing: 3D print (FDM clearance as its own variable, printable overhangs), sheet metal (bend radii,
  flat pattern), casting (draft angles), tube laser (wall thickness, cut-outs).
- Existing model: read the user's latest manual changes first and adapt everything else to them; never overwrite
  them. A feature of theirs that breaks: leave it and tell them.
- New version = new Part Studio / file with `V<n>`; never rebuild old versions. Move old ones into a folder "archive".

## 5 · Check (always)
1. Valid geometry: closed solids; Onshape: every feature OK and no unnamed parts ("Part 3" = a failed union).
2. Sizes against the table (bounding boxes); name every deviation.
3. Robustness: change 3–4 parameters strongly, everything stays valid, then reset.
4. Assemblies: collisions and clearances; movements; weight from material.
5. Compare with the input: a view in the same perspective as the drawing or image.

## 6 · Export and hand-over
- **STEP** as master for manufacturing, **STL/3MF** for printing, **DXF** for laser cutting.
- **To Blender** (render, check in context): **cad-to-blender** skill.
- Clean the view: hide origin, planes, sketches and helpers.

## 7 · Report
Short: an image (iso + the main view), program and file, the parameters the user can change, open assumptions,
one next step.

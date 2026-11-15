# Simulation (FEA) from the chat

Modal analysis (natural frequencies + mode shapes) of a CAD model, solved locally and rendered as
animations. Same physics as Fusion's Modal Frequencies study (Autodesk Nastran): the linear eigenproblem
(K − ω²M)·φ = 0 with quadratic tetrahedra. Accuracy depends on mesh, material and supports, not on the solver.

**Why not Fusion:** the Fusion API (and Autodesk's Fusion MCP) has no access to simulation studies; Fusion
studies can only be set up by clicking, and a cloud solve of a ~1 m enclosure takes about an hour. The pipeline
here is scriptable, repeatable and takes minutes.

## Pipeline
`CAD → STEP → gmsh (C3D10) → CalculiX *FREQUENCY → npz → Blender (frames) → HUD + MP4`

| Step | Tool | Script (`references/sim/`) |
|---|---|---|
| STEP from Onshape | `OS.exportSTEP(eid, filter)` + `OS.saveFile(...)` (see onshape.md) | – |
| List bodies, sizes | gmsh | `inspect_step.py a.step b.step` |
| Mesh + solve + results | gmsh, CalculiX `ccx` | `modal.py configs/<box>.json` |
| Frames (9:16, thermal look) | Blender headless (EEVEE) | `render_modes.py -- reel.json` |
| HUD, tone, MP4 | Pillow, ffmpeg | `hud.py reel.json` |
| Chain with restarts | bash | `reel.sh reel.json` |
| Run anything from the chat | headless Blender as launcher | `launch.py` |

## Setup (once, on the Mac)
- Conda env: `mamba create -y -n idtk-fea -c conda-forge python=3.11 gmsh python-gmsh calculix meshio numpy scipy pillow ffmpeg matplotlib`
- Work folder next to the project, e.g. `<project>/sim/{step,configs,runs,render,logs,scripts}`; copy the scripts
  into `scripts/` with `device_commit_files` (unique names, `force` when replacing).
- Without a terminal: start every job with `blender_render(script=<sim>/scripts/launch.py, args=[<log>, "<shell command>"])`
  and read the log with `device_bash`. The command keeps running after Blender is ended.
  Never `pkill -f` a pattern that also appears in the launching command line (it kills its own chain).

## Config (`configs/<box>.json`)
```json
{"name": "SUB", "step": "step/SUB.step", "exclude": "Feet|^Driver$",
 "material": {"E": 8000.0, "nu": 0.3, "rho": 5.8e-10}, "mesh_mm": 12, "modes": 20,
 "drivers": [{"label": "18DS115-8", "baffle": "8-Driver", "ring_parts": ["8-Driver", "Driver 2"],
              "flange_r": 230, "mass_kg": 12.55, "cg_mm": 120, "axial_mm": 40, "motor_side": "flip"}]}
```
- Units mm-N-t-s: E in MPa, ρ in t/mm³ (birch plywood 580 kg/m³ = 5.8e-10), mass in kg (converted).
- `exclude`: regex on part names (rubber feet, helper bodies, air volumes, driver dummies, grilles).
- Orthotropic material: `{"type": "orthotropic", "E1", "E2", "E3", "nu12", "nu13", "nu23", "G12", "G13", "G23", "rho"}`,
  local 3-axis = panel normal, 1-axis = longest panel direction; assign with `"part_material": {"regex": "NAME"}`.
- Drivers: the baffle's round hole is found automatically (largest circle edge smaller than `flange_r`); the
  flange ring becomes a rigid body with the driver's mass at its centre of gravity (`cg_mm` behind the hole).
  The motor side is guessed from the box centroid: **check it** (log line `axis`) against the real build and set
  `"motor_side": "flip"` if wrong. No round hole: `"select_box": [x0,x1,y0,y1,z0,z1], "cg": [x,y,z]`.
- Free support (no constraints) like Fusion's default: the first 6 modes are rigid-body modes (~0 Hz) and are
  skipped. More near-zero modes = parts not connected (log `connected components`).

## Validation (done, keep as reference)
Free square plate 500 × 500 × 15 mm, E 8 GPa, ν 0.3, ρ 580: FEA 142.7 / 209.6 / 259.5 / 367.8 / 649.2 Hz,
Kirchhoff plate theory (Leissa) 144.8 / 212.4 / 262.2 / 375.9 / 660.4 Hz → 1–2 % lower, as expected from
shear deformation. Mesh 12 → 6 mm changes < 0.1 %: one quadratic element through 15 mm is enough.
Re-run `configs/PLATE_h12.json` (`"box": [500, 500, 15]`) after changing the pipeline.

## Pitfalls (each cost a run)
- **Bonded joints:** `occ.fragment` makes touching parts share nodes (= glued). Map new volumes to part names
  through fragment's own output map; bounding-box ownership fails for slanted panels.
- **HXT 3D mesh failed / "self-intersecting facets":** fall back to Delaunay (Algorithm3D = 1); don't use
  `removeAllDuplicates` by default (it renumbers volumes and breaks the part mapping).
- **nonpositive jacobian in ccx:** curved mid-side nodes on tiny elements. Put every mid-side node exactly on
  its edge midpoint after meshing (`Mesh.SecondOrderLinear` alone is not enough).
- **Node order gmsh → ccx C3D10:** swap the last two mid-side nodes (gmsh 8, 9 → ccx 9, 8).
- **Periodic / curved print parts** (waveguides): "Impossible to mesh periodic surface" → heal the STEP or
  export the part as a closed STL and remesh; not solved yet.
- **Mode shapes are relative:** read frequencies, not amplitudes (linear modal analysis).
- Runtime M1 Max (spooles): 0.4 M DOF ≈ 1.5 min, 1 M DOF ≈ 6 min, 2.2 M DOF ≈ 20+ min.

## Rendering (Instagram / Reels)
- 9:16, 1080 × 1920, black background, emission shader with a thermal palette (deep violet → magenta → red →
  orange → yellow → white), colour = |φ| (98.5th percentile = max), animated sin oscillation, slow orbit.
- `reel.json`: `modes` (indices into the flexible modes), `mode_labels`, `views` (per-mode azimuth, e.g. 212°
  for a back wall), `cutaway` (hide a wall for an internal mode), `brand`, `title`, `subtitle`, `tech`, `tone`
  (each segment plays its real natural frequency).
- Blender may be ended after ~35 s: `render_modes.py` skips existing frames and writes via tmp + rename;
  `reel.sh` restarts it until `.done` exists.

## What to read from the results
- Panel modes inside the passband of the driver in that box are the ones to brace or damp.
- A brace is good if the first mode of that wall rises clearly; compare variants with one change per run.
- Free-free is conservative; feet, stacking and screwed-in parts (waveguides, grilles) raise frequencies.

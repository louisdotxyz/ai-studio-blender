"""S2N Modal-Reel: rendert Modenformen aus <box>_modes.npz als 9:16-Frames (Wärmebild-Look).

blender -b -P render_modes.py -- <reel.json>
reel.json: {"npz": "...", "out": ".../frames", "modes": [0,1,2], "sec_per_mode": 3, "fps": 30,
            "res": [1080,1920], "amp": 0.035, "cutaway": {"<mode idx>": ["part", ...]},
            "view": {"azim": 35, "elev": 18, "orbit_deg": 25, "dist": 2.6}, "samples": 16}
Schreibt PNGs  <out>/f_00001.png ...  plus frames.json (Mode / Frequenz je Frame) für das HUD.
"""
import bpy, sys, json, math, os
import numpy as np
from mathutils import Vector

cfg = json.load(open(sys.argv[sys.argv.index("--") + 1]))
os.makedirs(cfg["out"], exist_ok=True)
D = np.load(cfg["npz"], allow_pickle=True)
V = D["verts"].astype(np.float64) * 0.001          # mm -> m
T = D["tris"]
TP = D["tri_part"]
PN = list(D["part_names"])
F = D["freqs"]
M = D["modes"]
ctr = (V.max(0) + V.min(0)) / 2
V = V - ctr
size = float(np.linalg.norm(V.max(0) - V.min(0)))

bpy.ops.wm.read_factory_settings(use_empty=True)
sc = bpy.context.scene
sc.render.engine = "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in [e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items] else "BLENDER_EEVEE"
sc.render.resolution_x, sc.render.resolution_y = cfg.get("res", [1080, 1920])
sc.render.resolution_percentage = 100
sc.render.film_transparent = False
sc.render.image_settings.file_format = "PNG"
sc.view_settings.view_transform = "Standard"
try:
    sc.eevee.taa_render_samples = cfg.get("samples", 16)
except Exception:
    pass
w = bpy.data.worlds.new("black"); w.use_nodes = True
w.node_tree.nodes["Background"].inputs[0].default_value = (0, 0, 0, 1)
sc.world = w

# ---------------------------------------------------------------- mesh (Blender: Z up; CAD export is Z up too)
me = bpy.data.meshes.new("box")
me.from_pydata(V.tolist(), [], T.tolist())
me.update()
ob = bpy.data.objects.new("box", me)
sc.collection.objects.link(ob)
heat = me.attributes.new("heat", "FLOAT", "POINT")
partattr = me.attributes.new("part", "INT", "FACE")
partattr.data.foreach_set("value", TP.astype(np.int32))
base = V.copy()

# thermal palette (iron / inferno-like): deep violet -> magenta -> red -> orange -> yellow -> white
PAL = [(0.00, (0.010, 0.003, 0.030)), (0.18, (0.120, 0.012, 0.300)), (0.38, (0.620, 0.060, 0.500)),
       (0.55, (0.900, 0.180, 0.200)), (0.72, (1.000, 0.480, 0.040)), (0.88, (1.000, 0.820, 0.200)),
       (1.00, (1.000, 1.000, 0.950))]
mat = bpy.data.materials.new("thermal"); mat.use_nodes = True
nt = mat.node_tree; nt.nodes.clear()
out = nt.nodes.new("ShaderNodeOutputMaterial")
em = nt.nodes.new("ShaderNodeEmission")
attr = nt.nodes.new("ShaderNodeAttribute"); attr.attribute_name = "heat"
ramp = nt.nodes.new("ShaderNodeValToRGB")
cr = ramp.color_ramp
cr.elements[0].position, cr.elements[0].color = PAL[0][0], (*PAL[0][1], 1)
cr.elements[1].position, cr.elements[1].color = PAL[-1][0], (*PAL[-1][1], 1)
for p, c in PAL[1:-1]:
    e = cr.elements.new(p); e.color = (*c, 1)
# silhouettes slightly brighter (thermal cams show edges warmer through emissivity)
lw = nt.nodes.new("ShaderNodeLayerWeight"); lw.inputs[0].default_value = 0.25
mix = nt.nodes.new("ShaderNodeMath"); mix.operation = "MULTIPLY_ADD"
mix.inputs[1].default_value = cfg.get("edge_glow", 0.07)
nt.links.new(lw.outputs["Facing"], mix.inputs[0])
nt.links.new(attr.outputs["Fac"], mix.inputs[2])
nt.links.new(mix.outputs[0], ramp.inputs[0])
nt.links.new(ramp.outputs[0], em.inputs[0])
em.inputs[1].default_value = 1.0
nt.links.new(em.outputs[0], out.inputs[0])
me.materials.append(mat)
# hidden parts (cutaway): second material slot fully transparent
mat2 = bpy.data.materials.new("cut"); mat2.use_nodes = True
n2 = mat2.node_tree; n2.nodes.clear()
o2 = n2.nodes.new("ShaderNodeOutputMaterial"); tr = n2.nodes.new("ShaderNodeBsdfTransparent")
n2.links.new(tr.outputs[0], o2.inputs[0])
try:
    mat2.surface_render_method = "DITHERED"
except Exception:
    mat2.blend_method = "CLIP"
me.materials.append(mat2)

# ---------------------------------------------------------------- camera
cam_d = bpy.data.cameras.new("cam"); cam_d.lens = cfg.get("lens", 50)
cam_d.shift_y = cfg.get("view", {}).get("shift_y", -0.03)
cam = bpy.data.objects.new("cam", cam_d); sc.collection.objects.link(cam); sc.camera = cam
view = cfg.get("view", {})
def place_cam(azim, elev, dist_f=None):
    dist = (dist_f or view.get("dist", 2.6)) * size
    a, e = math.radians(azim), math.radians(elev)
    cam.location = Vector((dist * math.cos(e) * math.sin(a), -dist * math.cos(e) * math.cos(a), dist * math.sin(e)))
    cam.rotation_euler = (cam.location * -1).to_track_quat("-Z", "Y").to_euler()
    cam.location.z += view.get("lift", 0.0) * size

# ---------------------------------------------------------------- frames
fps = cfg.get("fps", 30); spm = cfg.get("sec_per_mode", 3.0)
modes = cfg.get("modes", list(range(min(4, len(F)))))
cycles = cfg.get("cycles_per_mode", 4)          # visual oscillations per segment (not real time)
amp = cfg.get("amp", 0.035) * size
cut = {int(k): v for k, v in cfg.get("cutaway", {}).items()}
# frame plan (deterministic, so killed runs can resume: existing frames are skipped)
plan, info = [], []
fr = 0
nseg = len(modes)
nfr = int(spm * fps)
for si, j in enumerate(modes):
    for k in range(nfr):
        fr += 1
        t = k / nfr
        env = min(1.0, t * 6, (1 - t) * 6)
        plan.append((fr, si, j, t, env))
        info.append({"frame": fr, "mode_idx": int(j), "mode_no": si + 1, "f_hz": round(float(F[j]), 1), "env": round(env, 3)})
json.dump({"fps": fps, "frames": info, "freqs": [float(f) for f in F]}, open(os.path.join(cfg["out"], "frames.json"), "w"))
cur_seg = None
for fr, si, j, t, env in plan:
    path = os.path.join(cfg["out"], f"f_{fr:05d}.png")
    if os.path.exists(path): continue
    if cur_seg != si:
        phi = M[j].astype(np.float64)
        mag = np.linalg.norm(phi, axis=1)
        ref = max(np.percentile(mag, cfg.get("norm_pct", 98.5)), 1e-12)
        s = amp / ref
        norm = np.clip(mag / ref, 0, 1)
        hidden = set(PN.index(p) for p in cut.get(j, []) if p in PN)
        me.polygons.foreach_set("material_index", np.array([1 if TP[k] in hidden else 0 for k in range(len(T))], np.int32))
        cur_seg = si
    ph = math.sin(2 * math.pi * cycles * t)
    me.vertices.foreach_set("co", (base + phi * s * ph * env).astype(np.float32).ravel())
    heat.data.foreach_set("value", ((norm ** cfg.get("heat_gamma", 0.8)) * (0.4 + 0.6 * abs(ph)) * env).astype(np.float32))
    me.update()
    vv = {**view, **cfg.get("views", {}).get(str(j), {})}
    place_cam(vv.get("azim", 35) + vv.get("orbit_deg", 25) * (t - 0.5), vv.get("elev", 18), vv.get("dist"))
    tmp = os.path.join(cfg["out"], f"tmp_{fr:05d}.png")
    sc.render.filepath = tmp
    bpy.ops.render.render(write_still=True)
    os.replace(tmp, path)
open(os.path.join(cfg["out"], ".done"), "w").write("ok")
print("RENDER DONE", fr)

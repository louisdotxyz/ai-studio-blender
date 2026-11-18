"""Dev tool (not a test): one mockup with real renders, to look at.

    Blender -b --python tests/mockup_look.py -- <photo> <out folder>

A small product (a rounded pod with a glowing end) goes into the photo; writes the quick look, the Cycles
render, the montage and a montage with other slider values."""
import os
import shutil
import sys

import bpy

args = sys.argv[sys.argv.index("--") + 1:]
photo, out = args[0], args[1]
os.makedirs(out, exist_ok=True)
PKG = next(n for n in sys.modules if n.endswith("AI_Studio") or n.endswith("claude_assistant"))
mock = sys.modules[PKG + ".studio.mockup"]
scene = bpy.context.scene
for obj in list(scene.objects):
    obj.hide_render = True
bpy.ops.mesh.primitive_cylinder_add(radius=0.03, depth=0.09, location=(0, 0, 0.045))
pod = bpy.context.active_object
bpy.ops.object.shade_smooth()
mat = bpy.data.materials.new("pod")
mat.use_nodes = True
bsdf = mat.node_tree.nodes["Principled BSDF"]
bsdf.inputs["Base Color"].default_value = (0.02, 0.02, 0.02, 1)
bsdf.inputs["Roughness"].default_value = 0.25
pod.data.materials.append(mat)
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.028, location=(0, 0, 0.095))
lamp = bpy.context.active_object
glow = bpy.data.materials.new("glow")
glow.use_nodes = True
g = glow.node_tree.nodes["Principled BSDF"]
g.inputs["Emission Color"].default_value = (1, 1, 1, 1)
g.inputs["Emission Strength"].default_value = 8.0
lamp.data.materials.append(glow)
scene.render.engine = 'CYCLES'
scene.cycles.samples = 48
values = dict(pair.split("=") for pair in args[2:])
values = {k: (v if k == "format" else (v == "True" if v in {"True", "False"} else float(v))) for k, v in values.items()}
m_scene = mock.create(bpy.context, photo)
state = mock.set_values(m_scene, **values)
print("STATE", state)
shutil.copy(mock.placement_preview(m_scene), os.path.join(out, "1_look.jpg"))
raw = os.path.join(out, "Pod_%s_01.png" % m_scene.camera.name)
r = m_scene.render
r.filepath, r.image_settings.file_format, r.image_settings.color_mode = raw, 'PNG', 'RGBA'
m_scene.cycles.samples = 48
bpy.ops.render.render(write_still=True, scene=m_scene.name)
print("MONTAGE", mock.use_render(m_scene, raw))
mock.set_values(m_scene, glow=0.5, grain=0.4, night=0.6, soften=0.4)
shutil.copy(mock.montage_path(raw), os.path.join(out, "3_night.png"))
plan = mock.krea_plan(m_scene, "at night", 'REGION')
print("PLAN", plan["box"], plan["prompt"])
print("LOOK_DONE")

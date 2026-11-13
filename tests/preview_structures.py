"""Renders one image with every material structure and the wear layer, to look at (not a pass / fail test).

    BLENDER_USER_RESOURCES=<test profile> Blender -b --python tests/preview_structures.py -- <output.png>

Front row, left to right: Worn Metal, Layered Earth, Speckled, Resin (a ring). Back row: Hammered (a ball),
Dark Anodized, glossy black plastic with wear.
"""
import math
import sys

import bpy
from mathutils import Vector

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "/tmp/structures.png"
PKG = next(n for n in sys.modules if n.startswith("bl_ext.") and n.endswith(".AI_Studio"))
mod = lambda name: sys.modules[PKG + "." + name]   # noqa: E731
ms, api = mod("studio.matstructures"), mod("studio.api")
scene = bpy.context.scene
for obj in list(scene.objects):
    bpy.data.objects.remove(obj)


def block(name, place):
    bpy.ops.mesh.primitive_cube_add(size=0.12, location=(place[0], place[1], 0.06))
    obj = bpy.context.active_object
    obj.name = name
    bevel = obj.modifiers.new("Bevel", 'BEVEL')
    bevel.width, bevel.segments = 0.018, 8
    bpy.ops.object.shade_smooth()
    return obj


def ball(name, place):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.065, segments=96, ring_count=48, location=(place[0], place[1], 0.065))
    obj = bpy.context.active_object
    obj.name = name
    bpy.ops.object.shade_smooth()
    return obj


def ring(name, place):
    bpy.ops.mesh.primitive_torus_add(major_radius=0.045, minor_radius=0.02, major_segments=96, minor_segments=32,
                                     location=(place[0], place[1], 0.05), rotation=(math.radians(70), 0, 0.5))
    obj = bpy.context.active_object
    obj.name = name
    bpy.ops.object.shade_smooth()
    return obj


front = [(-0.3 + 0.2 * i, 0.0) for i in range(4)]
back = [(-0.2 + 0.2 * i, 0.22) for i in range(3)]
plan = (('WORN_METAL', block, front[0]), ('LAYERED_EARTH', block, front[1]), ('SPECKLED', block, front[2]),
        ('RESIN', ring, front[3]), ('HAMMERED', ball, back[0]), ('DARK_ANODIZED', block, back[1]))
for kind, shape, place in plan:
    obj = shape(kind.title(), place)
    obj.data.materials.append(ms.create(kind))
worn = block("Wear", back[2])
plastic = bpy.data.materials.new("Plastic black glossy")
shader = mod("studio.matpresets")._principled(plastic)
shader.inputs["Base Color"].default_value = (0.01, 0.01, 0.012, 1.0)
shader.inputs["Roughness"].default_value = 0.12
worn.data.materials.append(plastic)
api.wear(plastic, scratches=0.5, fingerprints=0.6, dust=0.4)

bpy.ops.mesh.primitive_plane_add(size=6.0)
floor = bpy.context.active_object
ground = bpy.data.materials.new("Floor")
node = mod("studio.matpresets")._principled(ground)
node.inputs["Base Color"].default_value = (0.5, 0.5, 0.5, 1.0)
node.inputs["Roughness"].default_value = 0.6
floor.data.materials.append(ground)

camera = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
scene.collection.objects.link(camera)
camera.location = (-0.02, -1.0, 0.62)
camera.rotation_euler = (Vector((0.0, 0.1, 0.05)) - camera.location).to_track_quat('-Z', 'Y').to_euler()
camera.data.lens = 50
scene.camera = camera
api.world("STUDIO", ambient=0.35, pins=[
    {"name": "Key", "horizontal": -50, "vertical": 40, "width": 60, "height": 45, "strength": 9, "softness": 0.3},
    {"name": "Fill", "horizontal": 60, "vertical": 20, "width": 70, "height": 50, "strength": 3, "softness": 0.6},
    {"name": "Top", "horizontal": 150, "vertical": 55, "width": 40, "height": 30, "strength": 7, "softness": 0.3}])
scene.render.engine = 'CYCLES'
scene.cycles.samples = 72
scene.cycles.use_denoising = True
scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1600, 800, 100
scene.render.film_transparent = False
scene.render.filepath = OUT
scene.render.image_settings.file_format = 'PNG'
bpy.ops.render.render(write_still=True)
print("PREVIEW_OK", OUT)

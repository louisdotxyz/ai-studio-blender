# SPDX-License-Identifier: GPL-3.0-or-later
"""The size of the render, chosen in the Render tab: 1K, 2K or 4K on the long side. The Camera tab only
sets the aspect ratio (its pixels are the base, 1080 on the short side); the size here scales them
(resolution_percentage) and follows when the ratio changes."""

import bpy
from bpy.props import IntProperty
from bpy.types import Operator

SIZES = (("1K", 1024), ("2K", 2048), ("4K", 4096))
KEY = "id_render_long"


def long_side(scene):
    render = scene.render
    return round(max(render.resolution_x, render.resolution_y) * render.resolution_percentage / 100)


def pixels(scene):
    render = scene.render
    pct = render.resolution_percentage / 100
    return round(render.resolution_x * pct), round(render.resolution_y * pct)


def set_long_side(scene, length):
    """The render's long side becomes exactly `length` px, the ratio stays (and it stays when the ratio
    changes)."""
    render = scene.render
    ratio = render.resolution_x / max(render.resolution_y, 1)
    if ratio >= 1:
        size = (int(length), max(4, round(length / ratio)))
    else:
        size = (max(4, round(length * ratio)), int(length))
    if (render.resolution_x, render.resolution_y) != size:
        render.resolution_x, render.resolution_y = size
    render.resolution_percentage = 100
    scene[KEY] = int(length)


def follow(scene):
    """After a change of the aspect ratio or format: keep the chosen long side."""
    length = scene.get(KEY)
    if length:
        set_long_side(scene, length)


def chosen(scene):
    """The size button that matches now ("1K", "2K", "4K"), else ""."""
    current = long_side(scene)
    return next((name for name, length in SIZES if abs(current - length) <= max(4, length * 0.01)), "")


class RS_OT_render_size(Operator):
    bl_idname = "render_studio.render_size"
    bl_label = "Render Size"
    bl_options = {'UNDO', 'INTERNAL'}

    length: IntProperty(default=2048, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return "The long side of the render: %d px (the Camera tab sets the aspect ratio)" % props.length

    def execute(self, context):
        set_long_side(context.scene, self.length)
        return {'FINISHED'}


def draw(layout, scene):
    """Size [1K] [2K] [4K]   1536 × 2048 px"""
    row = layout.row(align=True)
    now = chosen(scene)
    for name, length in SIZES:
        row.operator(RS_OT_render_size.bl_idname, text=name, depress=name == now).length = length
    w, h = pixels(scene)
    sub = row.row(align=True)
    sub.alignment = 'RIGHT'
    sub.active = False
    sub.label(text="%d × %d px" % (w, h))


classes = (RS_OT_render_size,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

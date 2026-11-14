# SPDX-License-Identifier: GPL-3.0-or-later
"""Render controls right in the chat, above the skills (draw_inline, no popover): start renders without
Claude. [High|Low] [still|film] [1K|2K|4K] · the cameras to render (all or some) · [Render (n)];
the running render with cancel, or how many wait in the queue."""

import bpy
from bpy.props import StringProperty
from bpy.types import Operator, Panel

from . import cameras, render_size
from .runner import RUN
from .ui_common import _item_progress_text


def chosen(context):
    """The camera names checked in the popover (default: the active camera)."""
    scene = context.scene
    names = [n for n in context.window_manager.render_studio_chat_cams.split("\n") if n]
    names = [n for n in names if scene.objects.get(n) is not None]
    if not names and scene.camera is not None:
        names = [scene.camera.name]
    return names


class RS_OT_chat_camera(Operator):
    bl_idname = "render_studio.chat_camera"
    bl_label = "Camera to Render"
    bl_description = "Render this camera too (click again: not)"
    bl_options = {'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})     # "*" = all / none

    def execute(self, context):
        wm = context.window_manager
        names = chosen(context)
        every = [o.name for o in cameras.scene_cameras(context.scene)]
        if self.name == "*":
            names = [] if set(names) >= set(every) else every
        elif self.name in names:
            names.remove(self.name)
        else:
            names.append(self.name)
        wm.render_studio_chat_cams = "\n".join(names) or "\n"
        return {'FINISHED'}


class RS_OT_chat_render(Operator):
    bl_idname = "render_studio.chat_render"
    bl_label = "Render"
    bl_description = "Render the checked cameras with the quality, still/film and size chosen here (in the background)"

    def execute(self, context):
        names = chosen(context)
        if not names:
            self.report({'WARNING'}, "Check at least one camera")
            return {'CANCELLED'}
        for name in names:
            bpy.ops.render_studio.add(camera=name)
        if not RUN.busy and any(i.camera for i in context.scene.render_studio.items):
            bpy.ops.render_studio.render_queue()
        self.report({'INFO'}, "Rendering %d camera%s" % (len(names), "" if len(names) == 1 else "s"))
        return {'FINISHED'}


class RS_PT_chat_render(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Render"
    bl_ui_units_x = 13

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        rq = scene.render_studio
        if RUN.busy:
            row = layout.row(align=True)
            row.progress(factor=RUN.render_progress, type='BAR', text=_item_progress_text())
            row.operator("render_studio.cancel", text="", icon='X')
        row = layout.row(align=True)
        row.prop_enum(rq, "quality", 'HIGH', text="High")
        row.prop_enum(rq, "quality", 'LOW', text="Low")
        sub = row.row(align=True)
        sub.prop_enum(rq, "mode", 'STILL', text="", icon='IMAGE_DATA')
        sub.prop_enum(rq, "mode", 'ANIM', text="", icon='RENDER_ANIMATION')
        render_size.draw(layout, scene)
        cams = cameras.scene_cameras(scene)
        names = chosen(context)
        head = layout.row(align=True)
        head.label(text="Cameras", icon='CAMERA_DATA')
        head.operator(RS_OT_chat_camera.bl_idname, text="All",
                      depress=bool(cams) and set(names) >= {o.name for o in cams}).name = "*"
        col = layout.column(align=True)
        for obj in cams:
            col.operator(RS_OT_chat_camera.bl_idname, text=obj.name, depress=obj.name in names,
                         icon='CHECKBOX_HLT' if obj.name in names else 'CHECKBOX_DEHLT').name = obj.name
        if not cams:
            col.label(text="No camera in the scene", icon='INFO')
        row = layout.row()
        row.scale_y = 1.3
        row.enabled = bool(names)
        row.operator(RS_OT_chat_render.bl_idname, text="Render (%d)" % len(names), icon='RENDER_STILL')
        waiting = sum(1 for i in rq.items if i.camera)
        if waiting and not RUN.busy:
            layout.operator("render_studio.render_queue", text="Render Waiting Queue (%d)" % waiting,
                            icon='SEQ_STRIP_DUPLICATE')


def draw_inline(layout, context):
    """Compact, right in the chat: [High|Low][still|film][1K|2K|4K] / the cameras / [Render (n)]."""
    scene = context.scene
    rq = scene.render_studio
    col = layout.column(align=True)
    row = col.row(align=True)
    row.prop_enum(rq, "quality", 'HIGH', text="High")
    row.prop_enum(rq, "quality", 'LOW', text="Low")
    row.prop_enum(rq, "mode", 'STILL', text="", icon='IMAGE_DATA')
    row.prop_enum(rq, "mode", 'ANIM', text="", icon='RENDER_ANIMATION')
    now = render_size.chosen(scene)
    for name, length in render_size.SIZES:
        row.operator(render_size.RS_OT_render_size.bl_idname, text=name, depress=name == now).length = length
    cams = cameras.scene_cameras(scene)
    names = chosen(context)
    if cams:
        grid = col.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
        if len(cams) > 1:
            grid.operator(RS_OT_chat_camera.bl_idname, text="All",
                          depress=set(names) >= {o.name for o in cams}).name = "*"
        for obj in cams:
            grid.operator(RS_OT_chat_camera.bl_idname, text=obj.name, depress=obj.name in names).name = obj.name
    row = col.row(align=True)
    row.scale_y = 1.3
    if RUN.busy:
        row.progress(factor=RUN.render_progress, type='BAR', text=_item_progress_text())
        row.operator("render_studio.cancel", text="", icon='X')
    else:
        sub = row.row(align=True)
        sub.enabled = bool(names)
        sub.operator(RS_OT_chat_render.bl_idname, text="Render (%d)" % len(names), icon='RENDER_STILL')
        waiting = sum(1 for i in rq.items if i.camera)
        if waiting:
            row.operator("render_studio.render_queue", text=str(waiting), icon='SEQ_STRIP_DUPLICATE')


classes = (RS_OT_chat_camera, RS_OT_chat_render, RS_PT_chat_render)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.render_studio_chat_cams = StringProperty(options={'SKIP_SAVE'})


def unregister():
    del bpy.types.WindowManager.render_studio_chat_cams
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

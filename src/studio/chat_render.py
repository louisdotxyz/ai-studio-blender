# SPDX-License-Identifier: GPL-3.0-or-later
"""Render controls right in the chat, above the skills (draw_inline): start renders without Claude.
One row: [cameras ▾][All] [high|low] [still|film] [Render (n)]; size and format come from the Render tab,
progress and comparing are there. Several cameras at once go into one numbered folder (Batch_01 …)."""

import os

import bpy
from bpy.props import StringProperty
from bpy.types import Operator, Panel

from . import cameras
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
    bl_description = ("Render the checked cameras with the quality and still/film chosen here; size and format "
                      "from the Render tab. Several cameras go into one numbered folder (Batch_01 …)")

    def execute(self, context):
        names = chosen(context)
        if not names:
            self.report({'WARNING'}, "Check at least one camera")
            return {'CANCELLED'}
        batch = ""
        if len(names) > 1 and context.scene.render_studio.mode == 'STILL':
            from . import core, runner
            root = runner.settings_from_prefs()[0]
            name = context.scene.render_studio.output_name
            batch = core.next_batch(root, name)
            quality = context.scene.render_studio.quality
            project = core.project_name(name=name)
            try:   # made now, so a second click right after gets the next number
                os.makedirs(os.path.join(core.quality_dir(root, project, quality), batch), exist_ok=True)
            except OSError:
                pass
        for name in names:
            bpy.ops.render_studio.add(camera=name, batch=batch)
        if not RUN.busy and any(i.camera for i in context.scene.render_studio.items):
            bpy.ops.render_studio.render_queue()
        self.report({'INFO'}, ("Rendering %d cameras into %s" % (len(names), batch)) if batch else
                    "Rendering %s" % names[0] if len(names) == 1 else "Rendering %d cameras" % len(names))
        return {'FINISHED'}


class RS_PT_chat_render(Panel):
    """The cameras to render, picked in a small menu (stays open for several clicks)."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Cameras"
    bl_ui_units_x = 9

    def draw(self, context):
        layout = self.layout
        cams = cameras.scene_cameras(context.scene)
        names = chosen(context)
        col = layout.column(align=True)
        for obj in cams:
            col.operator(RS_OT_chat_camera.bl_idname, text=obj.name, depress=obj.name in names,
                         icon='CHECKBOX_HLT' if obj.name in names else 'CHECKBOX_DEHLT').name = obj.name
        if not cams:
            col.label(text="No camera in the scene", icon='INFO')


def _camera_text(names, cams):
    if cams and set(names) >= {o.name for o in cams} and len(cams) > 1:
        return "All"
    if len(names) == 1:
        return names[0]
    return "%d Cameras" % len(names) if names else "No Camera"


def draw_inline(layout, context):
    """One row in the chat: [cameras ▾][All] [high|low] [still|film] [Render (n)].
    Size and format come from the Render tab; progress and comparing are there too."""
    scene = context.scene
    rq = scene.render_studio
    cams = cameras.scene_cameras(scene)
    names = chosen(context)
    row = layout.row(align=False)
    row.scale_y = 1.2
    left = row.row(align=True)
    left.popover(RS_PT_chat_render.__name__, text=_camera_text(names, cams), icon='CAMERA_DATA')
    if len(cams) > 1:
        every = left.row(align=True)
        every.ui_units_x = 1.6
        every.operator(RS_OT_chat_camera.bl_idname, text="All",
                       depress=set(names) >= {o.name for o in cams}).name = "*"
    mid = row.row(align=True)
    mid.prop_enum(rq, "quality", 'HIGH', text="", icon='SHADING_RENDERED')
    mid.prop_enum(rq, "quality", 'LOW', text="", icon='SORTTIME')
    mid = row.row(align=True)
    mid.prop_enum(rq, "mode", 'STILL', text="", icon='IMAGE_DATA')
    mid.prop_enum(rq, "mode", 'ANIM', text="", icon='RENDER_ANIMATION')
    right = row.row(align=True)
    right.ui_units_x = 5.5
    if RUN.busy:
        right.progress(factor=RUN.render_progress, type='BAR', text=_item_progress_text())
        right.operator("render_studio.cancel", text="", icon='X')
    else:
        right.enabled = bool(names)
        right.operator(RS_OT_chat_render.bl_idname, text="Render (%d)" % len(names) if len(names) > 1 else "Render",
                       icon='RENDER_STILL' if rq.mode == 'STILL' else 'RENDER_ANIMATION')


classes = (RS_OT_chat_camera, RS_OT_chat_render, RS_PT_chat_render)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.render_studio_chat_cams = StringProperty(options={'SKIP_SAVE'})


def unregister():
    del bpy.types.WindowManager.render_studio_chat_cams
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

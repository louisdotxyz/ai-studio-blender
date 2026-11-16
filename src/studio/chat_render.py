# SPDX-License-Identifier: GPL-3.0-or-later
"""Render controls right in the chat, above the skills (draw_inline): start renders without Claude.
One row: [cameras ▾][All] [Render]. The quality chooses itself (autoquality.py: Low Quality first, High
Quality once nothing changed; Shift-click: the other one); size and format come from the Render tab,
progress and comparing are there. Several cameras at once go into one numbered folder (Batch_01 …).
Films render in the Video Editor (⌘E)."""

import os

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator, Panel

from . import cameras
from .runner import RUN


def chosen(context):
    """The camera names checked in the popover (default: the active camera)."""
    scene = context.scene
    names = [n for n in context.window_manager.studio_chat_cams.split("\n") if n]
    names = [n for n in names if scene.objects.get(n) is not None]
    if not names and scene.camera is not None:
        names = [scene.camera.name]
    return names


class STUDIO_OT_chat_camera(Operator):
    bl_idname = "studio.chat_camera"
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
        wm.studio_chat_cams = "\n".join(names) or "\n"
        return {'FINISHED'}


class STUDIO_OT_chat_render(Operator):
    bl_idname = "studio.chat_render"
    bl_label = "Render"

    other: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, _props):
        from . import autoquality
        names = chosen(context) if context else []
        if not names:
            return "Render the checked cameras"
        qualities = {autoquality.next_quality(context.scene, autoquality.still_key(n)) for n in names}
        quality = qualities.pop() if len(qualities) == 1 else ""
        return ("Render %s: %s. Shift-click: the other quality. Size and format from the Render tab; several "
                "cameras go into one numbered folder (Batch_01 …)" % (
                    names[0] if len(names) == 1 else "%d cameras" % len(names),
                    autoquality.describe(quality) if quality else "each in Low or High Quality, as it needs"))

    def invoke(self, context, event):
        self.other = event.shift
        return self.execute(context)

    def execute(self, context):
        from . import autoquality, core, runner
        names = chosen(context)
        if not names:
            self.report({'WARNING'}, "Check at least one camera")
            return {'CANCELLED'}
        scene = context.scene
        plan = []
        for name in names:
            quality = autoquality.next_quality(scene, autoquality.still_key(name))
            if self.other:
                quality = 'LOW' if quality == 'HIGH' else 'HIGH'
            plan.append((name, quality))
        batch = ""
        if len(names) > 1:
            root = runner.settings_from_prefs()[0]
            name = scene.studio.output_name
            batch = core.next_batch(root, name)
            project = core.project_name(name=name)
            for quality in {q for _n, q in plan}:
                try:   # made now, so a second click right after gets the next number
                    os.makedirs(os.path.join(core.quality_dir(root, project, quality), batch), exist_ok=True)
                except OSError:
                    pass
        for name, quality in plan:
            bpy.ops.studio.add(camera=name, batch=batch, quality=quality, mode='STILL')
        if not RUN.busy and any(i.camera for i in scene.studio.items):
            bpy.ops.studio.render_queue()
        high = sum(1 for _n, q in plan if q == 'HIGH')
        what = "HQ" if high == len(plan) else "LQ" if not high else "%d HQ, %d LQ" % (high, len(plan) - high)
        self.report({'INFO'}, ("Rendering %d cameras into %s (%s)" % (len(names), batch, what)) if batch else
                    "Rendering %s (%s)" % (names[0], what))
        return {'FINISHED'}


class STUDIO_PT_chat_render(Panel):
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
            col.operator(STUDIO_OT_chat_camera.bl_idname, text=obj.name, depress=obj.name in names,
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
    """One row in the chat: [cameras ▾][All] [Render]. Size and format come from the Render tab; progress
    and comparing are there too."""
    if context.area is not None and context.area.type == 'SEQUENCE_EDITOR':   # the cut chat: the film's row
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator("studio.anim_preview", text="Check", icon='PLAY')
        row.operator("studio.add", text="Render", icon='RENDER_ANIMATION').mode = 'ANIM'
        row.operator("studio.cut", text="Export", icon='EXPORT').action = 'EXPORT'
        return
    scene = context.scene
    cams = cameras.scene_cameras(scene)
    names = chosen(context)
    row = layout.row(align=False)
    row.scale_y = 1.2
    left = row.row(align=True)
    left.popover(STUDIO_PT_chat_render.__name__, text=_camera_text(names, cams), icon='CAMERA_DATA')
    if len(cams) > 1:
        every = left.row(align=True)
        every.ui_units_x = 1.6
        every.operator(STUDIO_OT_chat_camera.bl_idname, text="All",
                       depress=set(names) >= {o.name for o in cams}).name = "*"
    right = row.row(align=True)
    right.ui_units_x = 5.5
    right.enabled = bool(names)       # while one renders: more go into the queue (progress above the chat)
    right.operator(STUDIO_OT_chat_render.bl_idname, text="Render", icon='RENDER_STILL')


classes = (STUDIO_OT_chat_camera, STUDIO_OT_chat_render, STUDIO_PT_chat_render)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.studio_chat_cams = StringProperty(options={'SKIP_SAVE'})


def unregister():
    del bpy.types.WindowManager.studio_chat_cams
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

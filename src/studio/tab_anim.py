# SPDX-License-Identifier: GPL-3.0-or-later
"""The Animation tab: what needs the camera and the viewport, top to bottom in the order of the work:

1. Sections: the timeline's markers "NN Name" (+ here or in the timeline's header), each with its own
   camera, frames (click: move / lengthen, the following sections move along) and keys.
2. Motion: a preset with strength and direction over chosen frames, a chain of steps per section,
   the viewport's view kept as a key.
3. Check: a viewport preview of the section in seconds (OpenGL, half size, played right away).
4. Film: ⌘E or the button; it renders in its own process, one video per section.
5. Cut: the rendered sections in the Video Sequencer (cut.py; its own sidebar there).
"""

import os

import bpy
from bpy.types import Operator, Panel

from . import cut, film, motion
from .ui_common import _SidebarPanel, _unpin, section

ANIM_TAB_ORDER = 5


class STUDIO_OT_anim_preview(Operator):
    bl_idname = "studio.anim_preview"
    bl_label = "Check in the Viewport"
    bl_description = ("The current section (or the whole film) as a quick viewport film in seconds, played right "
                      "away: timing and camera moves (light and materials: LQ of a section)")

    def execute(self, context):
        scene = context.scene
        area = next((a for a in context.window.screen.areas if a.type == 'VIEW_3D'), None)
        if area is None or scene.camera is None:
            self.report({'WARNING'}, "A 3D view and a camera are needed")
            return {'CANCELLED'}
        start, end, name = motion.current_range(scene)
        render = scene.render
        images = render.image_settings
        keep = {"filepath": render.filepath, "pct": render.resolution_percentage,
                "start": scene.frame_start, "end": scene.frame_end, "format": images.file_format,
                "media": getattr(images, "media_type", None), "ext": render.use_file_extension}
        from .runner import jobs_dir
        out = os.path.join(jobs_dir(), "viewport_preview_%d_" % os.getpid())   # a second Blender: its own
        try:
            render.filepath = out
            render.resolution_percentage = 50
            scene.frame_start, scene.frame_end = start, end
            if hasattr(images, "media_type"):
                images.media_type = 'VIDEO'
            images.file_format = 'FFMPEG'
            render.ffmpeg.format, render.ffmpeg.codec = 'MPEG4', 'H264'
            render.use_file_extension = True
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with context.temp_override(area=area, region=region):
                bpy.ops.render.opengl(animation=True, view_context=False)
            bpy.ops.render.play_rendered_anim()
        except Exception as ex:
            self.report({'ERROR'}, "Preview failed: %s" % ex)
            return {'CANCELLED'}
        finally:
            render.filepath, render.resolution_percentage = keep["filepath"], keep["pct"]
            scene.frame_start, scene.frame_end = keep["start"], keep["end"]
            if keep["media"] is not None:
                images.media_type = keep["media"]
            images.file_format = keep["format"]
            render.use_file_extension = keep["ext"]
        self.report({'INFO'}, "Preview: %s, frames %d–%d" % (name or "whole film", start, end))
        return {'FINISHED'}


class STUDIO_PT_anim(_SidebarPanel, Panel):
    studio_tab = 'ANIM'
    bl_label = "Animation"
    bl_options = {'HIDE_HEADER'}
    bl_order = ANIM_TAB_ORDER

    def draw(self, context):
        _unpin(self)
        layout = self.layout
        scene = context.scene

        # 1 · the sections (each one its own camera and video), 2 · their movement, 3 · render and watch
        motion.draw_sections(layout, scene)

        box, _head, is_open = section(layout, "anim_motion", "Motion", 'ANIM')
        if is_open:
            motion.draw_presets(box, scene)

        box = layout.box()
        box.label(text="Render", icon='RENDER_ANIMATION')
        info = box.row()
        info.active = False
        found = motion.sections(scene)
        info.label(text="Whole film · %d sections · frames %d–%d · %.1f s" % (
            len(found), scene.frame_start, scene.frame_end,
            motion.seconds(scene, scene.frame_end - scene.frame_start + 1)), translate=False)
        row = box.row(align=True)
        row.scale_y = 1.2
        row.operator(STUDIO_OT_anim_preview.bl_idname, text="Check", icon='PLAY')
        op = row.operator("studio.add", text="Film LQ", icon='SORTTIME')
        op.mode, op.quality = 'ANIM', 'LOW'
        op = row.operator("studio.add", text="Film HQ", icon='SHADING_RENDERED')
        op.mode, op.quality = 'ANIM', 'HIGH'
        film.draw(box)
        row = box.row(align=True)
        row.operator("studio.tab", text="Watch Films", icon='FILE_MOVIE').tab = 'QUEUE'
        row.operator(cut.STUDIO_OT_cut.bl_idname, text="Cut", icon='SEQUENCE').action = 'OPEN'


classes = (STUDIO_OT_anim_preview, STUDIO_PT_anim)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

# SPDX-License-Identifier: GPL-3.0-or-later
"""The Animation tab: everything for a film in one place, top to bottom in the order of the work:

1. Sections: the timeline's markers (+ here or in the timeline's header), each with its camera, length
   and keys; go, quick film, film.
2. Motion: a finished movement over the current section (turntable, push in …), smooth or linear.
3. Check: a viewport preview of the section in seconds (OpenGL, half size, played right away).
4. Film: ⌘E or the button; it renders in its own process, one video per section.
"""

import os

import bpy
from bpy.types import Operator, Panel

from . import cameras, film, motion
from .ui_common import _SidebarPanel, _unpin, section

ANIM_TAB_ORDER = 5


class STUDIO_OT_anim_preview(Operator):
    bl_idname = "studio.anim_preview"
    bl_label = "Viewport Preview"
    bl_description = ("The current section (or the whole film) as a quick viewport film at half size, played "
                      "right away: for timing and camera moves (light and materials: the quick film of a section)")

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
        out = os.path.join(jobs_dir(), "viewport_preview_")
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
        start, end, name = motion.current_range(scene)

        # Head: where we are, quick check, film
        head = layout.row(align=True)
        head.scale_y = 1.2
        head.operator(motion.STUDIO_OT_section.bl_idname, text="Section", icon='MARKER_HLT').action = 'ADD'
        head.operator(STUDIO_OT_anim_preview.bl_idname, text="Preview", icon='PLAY')
        head.operator("studio.add", text="Film", icon='RENDER_ANIMATION').mode = 'ANIM'
        info = layout.row()
        info.active = False
        info.label(text="%s · frames %d–%d · %.1f s · %s" % (
            name or "Whole film", start, end, motion.seconds(scene, end - start + 1),
            scene.camera.name if scene.camera else "no camera"), translate=False)
        film.draw(layout)

        motion.draw_sections(layout, scene)

        box, _head, is_open = section(layout, "anim_motion", "Motion", 'ANIM')
        if is_open:
            motion.draw_presets(box, scene)

        box, head, is_open = section(layout, "anim_keys", "Keys at This Frame", 'DECORATE_KEYFRAME',
                                     default_open=False)
        if is_open:
            grid = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
            for what, label, icon in (('ORBIT', "Camera", 'CAMERA_DATA'), ('LIGHT', "Open Light", 'LIGHT'),
                                      ('LOOK', "Look", 'NODE_COMPOSITING'), ('WORLD', "World", 'WORLD')):
                grid.operator("studio.key", text=label, icon=icon).what = what
            cam = cameras.active_camera(scene)
            overview = motion.key_overview(scene, scene.frame_start, scene.frame_end, cam)
            sub = box.row()
            sub.active = False
            sub.label(text="Film: " + ("  ".join("◆ %s %d" % kv for kv in overview.items()) if overview
                                       else "no keys yet"), translate=False)


classes = (STUDIO_OT_anim_preview, STUDIO_PT_anim)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

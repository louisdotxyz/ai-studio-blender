# SPDX-License-Identifier: GPL-3.0-or-later
"""The Queue tab: adding cameras, the list, the preview of the renders, output settings."""

import os
import time

import bpy
from bpy.props import (EnumProperty, FloatProperty, IntProperty, StringProperty)
from bpy.types import Operator, Panel

from . import core, runner
from .ui_common import (QUEUE_TAB_ORDER, SCALE_PRESETS, _SidebarPanel, _item_progress_text, _look_through,
                        _queue_progress_text, _unpin, _user_resolution, active_camera, ensure_uids,
                        format_label, studio_tab)
from .runner import RUN


class RS_OT_render_queue(Operator):
    bl_idname = "render_studio.render_queue"
    bl_label = "Render Queue"
    bl_description = ("Renders the entries left in the list one after another in a second Blender "
                      "without a window. You can keep working meanwhile")

    @classmethod
    def poll(cls, context):
        return not RUN.busy and any(i.camera for i in context.scene.render_studio.items)

    def execute(self, context):
        rq = context.scene.render_studio
        ensure_uids(rq)
        try:
            RUN.start(context.scene, rq.quality, rq.mode == 'ANIM')   # defaults for entries from older files
        except Exception as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class RS_OT_cancel(Operator):
    bl_idname = "render_studio.cancel"
    bl_label = "Cancel"
    bl_description = "Cancel the queue (Shift+Esc). The current render stops right away and is not saved"

    @classmethod
    def poll(cls, context):
        return RUN.busy

    def execute(self, context):
        RUN.cancel()
        return {'FINISHED'}


class RS_OT_add(Operator):
    bl_idname = "render_studio.add"
    bl_label = "Add Camera"
    bl_description = ("Adds the active camera as it is now and renders it (⌘R High Quality, ⇧⌘R Low Quality, "
                      "⇧⌘E shows the image). The same camera can be added several times (move it in between)")
    bl_options = {'UNDO'}

    quality: EnumProperty(
        items=(('DEFAULT', "As Chosen", "The quality chosen in the Queue tab"),
               ('HIGH', "High Quality", ""),
               ('LOW', "Low Quality", "")),
        default='DEFAULT', options={'SKIP_SAVE'})

    def execute(self, context):
        rq = context.scene.render_studio
        # The camera bar's camera, not the viewport selection: cameras are usually
        # hidden, and hidden objects cannot be selected.
        cam = active_camera(context.scene)
        if cam is None or cam.type != 'CAMERA':
            self.report({'WARNING'}, "No active camera, pick one in the Render tab")
            return {'CANCELLED'}
        ensure_uids(rq)
        scene = context.scene
        runner.purge_deleted(scene)
        item = rq.items.add()
        item.camera = cam
        item.uid = rq.next_uid
        rq.next_uid += 1
        item.quality = rq.quality if self.quality == 'DEFAULT' else self.quality
        item.mode = rq.mode
        cam.render_studio_camera.queue_count += 1
        item.number = cam.render_studio_camera.queue_count
        item.has_snapshot = True
        item.matrix = [v for row in cam.matrix_world for v in row]
        item.lens = cam.data.lens
        item.resolution_x, item.resolution_y = _user_resolution(scene, cam)
        item.percentage = scene.render.resolution_percentage
        self.report({'INFO'}, "Render Studio: added %s" % runner.item_label(scene, item))
        p = runner.prefs(context)
        if (p is None or p.auto_render) and not RUN.busy:
            try:
                RUN.start(scene)
            except Exception as ex:
                self.report({'ERROR'}, str(ex))
                return {'CANCELLED'}
        return {'FINISHED'}


class RS_OT_clear(Operator):
    bl_idname = "render_studio.clear"
    bl_label = "Clear"
    bl_description = "Remove all cameras from the list. A running render is discarded"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return len(context.scene.render_studio.items) > 0

    def execute(self, context):
        context.scene.render_studio.items.clear()
        return {'FINISHED'}


class RS_OT_remove(Operator):
    bl_idname = "render_studio.remove"
    bl_label = "Remove"
    bl_description = "Remove from the list. If this entry is rendering right now, its image is not saved"
    bl_options = {'UNDO', 'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        items = context.scene.render_studio.items
        if 0 <= self.index < len(items):
            items.remove(self.index)
        return {'FINISHED'}


class RS_OT_move(Operator):
    bl_idname = "render_studio.move"
    bl_label = "Move"
    bl_description = "Move up or down in the order"
    bl_options = {'UNDO', 'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})
    direction: IntProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=-1)

    def execute(self, context):
        items = context.scene.render_studio.items
        target = self.index + self.direction
        if 0 <= self.index < len(items) and 0 <= target < len(items):
            items.move(self.index, target)
        return {'FINISHED'}


class RS_OT_view_camera(Operator):
    bl_idname = "render_studio.view_camera"
    bl_label = "Look Through Camera"
    bl_description = "Makes the camera the active scene camera and looks through it"
    bl_options = {'UNDO', 'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        items = context.scene.render_studio.items
        if not (0 <= self.index < len(items)) or items[self.index].camera is None:
            return {'CANCELLED'}
        _look_through(context, items[self.index].camera)
        return {'FINISHED'}


_preview_pending = set()


_view = {"path": ""}   # the image you browsed to; "" = the entry that renders now, else the newest


def _live():
    """The entry that renders now (a still of the list, not a preview for Claude)."""
    job = RUN.current
    return job if job and not job.get("preview") and not RUN.animation else None


def _selected_image(images):
    path = _view["path"]
    if path and path in images:
        return path
    _view["path"] = ""
    live = _live()
    if live is not None:
        return live.get("draft", "")   # its quick draft, "" until there is one
    return images[0] if images else ""


class RS_OT_preview_step(Operator):
    bl_idname = "render_studio.preview_step"
    bl_label = "Browse Renders"
    bl_description = "Older (◀) or newer (▶) image of the queue; the newest follows new renders"
    bl_options = {'INTERNAL'}

    direction: IntProperty(default=1, options={'SKIP_SAVE'})

    def execute(self, context):
        images = runner.queue_images(context.scene)
        entries = ([""] if _live() else []) + images   # "" = the entry that renders now
        if not entries:
            return {'CANCELLED'}
        current = _view["path"] if _view["path"] in images else entries[0]
        index = max(0, min(len(entries) - 1, entries.index(current) + self.direction))
        _view["path"] = entries[index] if index else ""
        return {'FINISHED'}


def _schedule_preview(path):
    """Load `path` into the preview image right after this redraw (once)."""
    if path in _preview_pending:
        return
    _preview_pending.add(path)

    def load():
        _preview_pending.discard(path)
        try:
            runner.show_in_preview(path)
        except (RuntimeError, OSError) as ex:
            print("Render Studio: preview failed:", ex)
        runner.tag_redraw()
        return None
    bpy.app.timers.register(load, first_interval=0.0)


_prewarmed = [0.0]


def _schedule_prewarm():
    """The Queue tab is open: start the render worker now, so the first entry does not wait for
    Blender to start (at most every 10 minutes; it closes again after a while without work)."""
    now = time.monotonic()
    if RUN.busy or runner.WORKER.alive() or now - _prewarmed[0] < 600:
        return
    _prewarmed[0] = now
    bpy.app.timers.register(lambda: runner.prewarm() or None, first_interval=0.5)


def _show_image_in_render_window(image):
    """Put `image` into the image editor of the render window (it opens with Render Result)."""
    for window in bpy.context.window_manager.windows:
        if not window.screen.is_temporary:
            continue
        for area in window.screen.areas:
            if area.type == 'IMAGE_EDITOR':
                area.spaces.active.image = image
                area.tag_redraw()


class RS_OT_show_render(Operator):
    bl_idname = "render_studio.show_render"
    bl_label = "View Render"
    bl_description = "Shows the image of the preview big in the render window, or hides it (⇧⌘E)"

    @classmethod
    def poll(cls, context):
        return context.window is not None

    def execute(self, context):
        path = RUN.last_path
        if path and os.path.isdir(path) and not _view["path"]:   # an animation: its folder
            bpy.ops.wm.path_open(filepath=path)
            return {'FINISHED'}
        images = runner.queue_images(context.scene)
        path = _selected_image(images) or (images[0] if images else "")   # no draft yet: the newest
        image = runner.show_in_preview(path) if path and os.path.isfile(path) else None
        runner.with_render_window(lambda: bpy.ops.render.view_show('INVOKE_DEFAULT'))
        if image is not None:
            # The render window opens with Render Result (the window appears a moment later).
            _show_image_in_render_window(image)
            name = image.name

            def later():
                img = bpy.data.images.get(name)
                if img is not None:
                    _show_image_in_render_window(img)
                return None
            bpy.app.timers.register(later, first_interval=0.1)
        return {'FINISHED'}


def draw_statusbar(self, context):
    """The queue in Blender's status bar (at the bottom), like Blender's own renders."""
    if not RUN.busy:
        return
    row = self.layout.row(align=True)
    bar = row.row(align=True)
    bar.ui_units_x = 14
    bar.progress(factor=RUN.list_progress, type='BAR', text="Render Studio · " + _queue_progress_text())
    row.operator(RS_OT_cancel.bl_idname, text="", icon='X', emboss=False)


LOW_SCALES = (0.25, 0.5, 0.75, 1.0)


class RS_OT_low_scale(Operator):
    bl_idname = "render_studio.low_scale"
    bl_label = "Low Quality"
    bl_description = "Low Quality relative to High: resolution or samples"
    bl_options = {'INTERNAL'}

    factor: FloatProperty()
    target: StringProperty(default="resolution")   # resolution / samples

    def execute(self, context):
        p = runner.prefs(context)
        if p is not None:
            key = "low_samples_percent" if self.target == "samples" else "low_resolution"
            setattr(p, key, round(self.factor * 100))
            context.preferences.is_dirty = True
        return {'FINISHED'}


def _factor_row(layout, label, factors, op, depress_value, **props):
    row = layout.row(align=True)
    split = row.split(factor=0.3, align=True)
    split.label(text=label)
    buttons = split.row(align=True)
    for factor in factors:
        o = buttons.operator(op, text="%gx" % factor, depress=depress_value == round(factor * 100))
        o.factor = factor
        for key, value in props.items():
            setattr(o, key, value)


class RS_PT_output(Panel):
    """Gear menu in the Queue tab, top to bottom: High Quality, Low Quality, Still, Animation."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Output"
    bl_ui_units_x = 15

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        rq = scene.render_studio
        render = scene.render
        p = runner.prefs(context)
        cycles = scene.render.engine == 'CYCLES'

        # High Quality: the scene's own settings
        box = layout.box()
        box.label(text="High Quality", icon='SHADING_RENDERED')
        _factor_row(box, "Size", SCALE_PRESETS, 'render_studio.set_scale', render.resolution_percentage)
        pct = render.resolution_percentage / 100
        sub = box.row()
        sub.alignment = 'CENTER'
        sub.active = False
        sub.label(text="%d × %d px" % (round(render.resolution_x * pct), round(render.resolution_y * pct)))
        if cycles:
            cy = scene.cycles
            col = box.column(align=True)
            col.prop(cy, "samples", text="Samples")
            row = col.row(align=True)
            if cy.use_adaptive_sampling:
                row.prop(cy, "use_adaptive_sampling", text="")
                row.prop(cy, "adaptive_threshold", text="Noise Threshold")
            else:
                row.prop(cy, "use_adaptive_sampling", text="Noise Threshold", toggle=True)
            col.prop(cy, "time_limit", text="Time Limit")
            box.prop(cy, "use_denoising", text="Denoise", toggle=True, icon='SHADERFX')
        elif hasattr(scene, "eevee"):
            box.prop(scene.eevee, "taa_render_samples", text="Samples")

        # Low Quality: shares of High
        box = layout.box()
        box.label(text="Low Quality", icon='SORTTIME')
        if p is not None:
            _factor_row(box, "Resolution", LOW_SCALES, RS_OT_low_scale.bl_idname, p.low_resolution,
                        target="resolution")
            _factor_row(box, "Samples", LOW_SCALES, RS_OT_low_scale.bl_idname, p.low_samples_percent,
                        target="samples")
        if cycles:
            box.prop(rq, "low_denoise", text="Denoise", toggle=True, icon='SHADERFX')

        # Still
        box = layout.box()
        box.label(text="Still", icon='IMAGE_DATA')
        box.row(align=True).prop(rq, "still_format", expand=True)
        _fmt, _ext, depths, alpha = core.STILL_FORMATS[rq.still_format]
        row = box.row(align=True)
        for depth in depths:
            row.prop_enum(rq, "still_depth", depth)
        if rq.still_format == 'JPEG':
            box.prop(rq, "jpeg_quality", slider=True)
        if not alpha and rq.background_mode == 'TRANSPARENT':
            box.label(text="No transparency in this format", icon='ERROR')

        # Animation
        box = layout.box()
        box.label(text="Animation", icon='RENDER_ANIMATION')
        row = box.row(align=True)
        row.prop(rq, "anim_format", text="")
        row.prop(render, "fps", text="FPS")
        if rq.anim_format == 'MP4':
            box.row(align=True).prop(rq, "video_quality", expand=True)
        if rq.anim_format in {'MP4', 'PRORES'} and rq.background_mode == 'TRANSPARENT':
            box.label(text="Video has no transparency", icon='ERROR')

        # File names
        box = layout.box()
        box.label(text="File Names", icon='SORTALPHA')
        box.prop(rq, "name_pattern", text="")

        # Behaviour
        if p is not None:
            box = layout.box()
            box.label(text="Behaviour", icon='SETTINGS')
            box.prop(p, "auto_render")
            box.prop(p, "notify")
            box.prop(p, "worker_gpu_only")


class RS_PT_render_queue(_SidebarPanel, Panel):
    """The queue, in its own tab. New name in 0.14: a pinned panel shows up in every tab,
    and Blender keeps the pin per panel name in the file (RS_PT_panel, RS_PT_queue)."""
    studio_tab = 'QUEUE'
    bl_label = "Queue"
    bl_options = {'HIDE_HEADER'}
    bl_order = QUEUE_TAB_ORDER

    def draw(self, context):
        _unpin(self)
        _schedule_prewarm()
        layout = self.layout
        scene = context.scene
        rq = scene.render_studio

        box = layout.box()
        col = box.column()
        row = col.row(align=True)
        row.label(text="", icon='SORTALPHA')
        row.prop(rq, "output_name", text="", placeholder=core.project_name())
        row.popover(RS_PT_output.__name__, text="", icon='PREFERENCES')
        p = runner.prefs(context)
        if p is not None:
            row = col.row(align=True)
            row.label(text="", icon='FILE_FOLDER')
            row.prop(p, "output_root", text="", placeholder="Next to the .blend file")
        # The entry that renders now (its quick draft, then the image), or the newest render,
        # ◀ ▶ through the queue's images, the magnifier opens it big in the render window.
        images = runner.queue_images(scene)
        path = _selected_image(images)
        live = _live() if not _view["path"] else None
        image = bpy.data.images.get(runner.PREVIEW_IMAGE)
        if path and (image is None or
                     os.path.normpath(bpy.path.abspath(image.filepath)) != os.path.normpath(path)):
            _schedule_preview(path)   # data can't change inside draw
            image = None              # still the older image until it is loaded
        entries = ([""] if _live() else []) + images
        if entries:
            index = 0 if live is not None or path not in entries else entries.index(path)
            pic = col.box()
            head = pic.row(align=True)
            older = head.row(align=True)
            older.enabled = index < len(entries) - 1
            older.operator(RS_OT_preview_step.bl_idname, text="", icon='TRIA_LEFT').direction = 1
            count = head.row(align=True)
            count.alignment = 'CENTER'
            count.label(text="Rendering" if live is not None else
                        "%d / %d" % (index + 1, len(entries)) if index else "Newest")
            newer = head.row(align=True)
            newer.enabled = index > 0
            newer.operator(RS_OT_preview_step.bl_idname, text="", icon='TRIA_RIGHT').direction = -1
            head.operator(RS_OT_show_render.bl_idname, text="", icon='ZOOM_IN')
            if image is not None and path:
                pic.template_icon(icon_value=layout.icon(image), scale=8.0)
            else:   # the same height until the draft is there
                wait = pic.row()
                wait.alignment = 'CENTER'
                wait.scale_y = 8.0
                wait.active = False
                wait.label(text="", icon='TIME' if live is not None else 'NONE')
            if live is not None:
                pic.progress(factor=RUN.render_progress, type='BAR', text=_item_progress_text())
                pic.label(text=live["camera"], icon='RENDER_STILL', translate=False)
            else:
                variant = runner.variant_of(path)
                pic.label(text=variant or os.path.basename(path), icon='RENDER_RESULT', translate=False)
                if variant:
                    sub = pic.row()
                    sub.active = False
                    sub.label(text=os.path.basename(path), translate=False)
        # [+ Add] [LQ|HQ] [still|animation] [clear]
        row = col.row(align=True)
        row.scale_y = 1.2
        row.operator(RS_OT_add.bl_idname, text="Add", icon='ADD')
        sub = row.row(align=True)
        sub.prop_enum(rq, "quality", 'LOW', text="LQ")
        sub.prop_enum(rq, "quality", 'HIGH', text="HQ")
        sub = row.row(align=True)
        sub.prop_enum(rq, "mode", 'STILL', text="", icon='IMAGE_DATA')
        sub.prop_enum(rq, "mode", 'ANIM', text="", icon='RENDER_ANIMATION')
        row.operator(RS_OT_clear.bl_idname, text="", icon='TRASH')
        if rq.mode == 'ANIM':
            row = col.row(align=True)
            row.prop(scene, "frame_start", text="Start")
            row.prop(scene, "frame_end", text="End")
        cam = active_camera(scene)
        if cam is not None and cam.type == 'CAMERA':
            col.label(text="Adds %s %d" % (cam.name, cam.render_studio_camera.queue_count + 1), icon='CAMERA_DATA')

        box = layout.box()
        col = box.column(align=True)
        col.label(text="Queue List:", icon='ALIGN_JUSTIFY')
        if not len(rq.items):
            col.label(text="(empty)", icon='INFO')
        current = RUN.current["uid"] if RUN.current else None
        last = len(rq.items) - 1
        for i, item in enumerate(rq.items):
            row = col.row(align=True)
            if item.camera is not None:
                rendering = item.uid == current
                row.operator(RS_OT_view_camera.bl_idname, text=runner.item_label(scene, item),
                             icon='RENDER_STILL' if rendering else 'CAMERA_DATA',
                             emboss=False).index = i
                parts = [item.variant, {"LOW": "Low", "HIGH": "High"}.get(item.quality, "")]
                if item.mode == 'ANIM':
                    parts.append("Anim")
                if item.has_snapshot:
                    parts.append(format_label(item.resolution_x, item.resolution_y))
                sub = row.row()
                if rendering:   # a bar instead of the settings while it renders
                    sub.progress(factor=RUN.render_progress, type='BAR', text=_item_progress_text())
                else:
                    sub.alignment = 'RIGHT'
                    sub.label(text=" · ".join(p for p in parts if p))
            else:
                row.label(text="Camera deleted", icon='ERROR')
            sub = row.row(align=True)
            up = sub.row(align=True)
            up.enabled = i > 0
            op = up.operator(RS_OT_move.bl_idname, text="", icon='TRIA_UP', emboss=False)
            op.index, op.direction = i, -1
            down = sub.row(align=True)
            down.enabled = i < last
            op = down.operator(RS_OT_move.bl_idname, text="", icon='TRIA_DOWN', emboss=False)
            op.index, op.direction = i, 1
            sub.operator(RS_OT_remove.bl_idname, text="", icon='X', emboss=False).index = i

        if not RUN.busy:   # while rendering, progress and cancel are in the tab bar (RS_PT_tabs)
            count = sum(1 for i in rq.items if i.camera)
            if count:
                # Left over after a cancel, or with "Render Right Away" off.
                layout.operator(RS_OT_render_queue.bl_idname, text="Render (%d)" % count, icon='RENDER_STILL')
            if RUN.message:
                layout.label(text=RUN.message, icon='INFO')

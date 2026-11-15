# SPDX-License-Identifier: GPL-3.0-or-later
"""The Render tab: output settings, the progress of what renders (started in the chat), the preview,
all renders (batches grouped) and comparing two of them (A / B)."""

import os
import time

import bpy
from bpy.props import (EnumProperty, FloatProperty, IntProperty, StringProperty)
from bpy.types import Menu, Operator, Panel

from . import core, runner, shots
from .ui_common import (QUEUE_TAB_ORDER, SCALE_PRESETS, _SidebarPanel, _item_progress_text, _look_through,
                        _queue_progress_text, _unpin, _user_resolution, active_camera, ensure_uids,
                        studio_tab)
from .runner import RUN


class STUDIO_OT_render_queue(Operator):
    bl_idname = "studio.render_queue"
    bl_label = "Render Queue"
    bl_description = ("Renders the entries left in the list one after another in a second Blender "
                      "without a window. You can keep working meanwhile")

    @classmethod
    def poll(cls, context):
        return not RUN.busy and any(i.camera for i in context.scene.studio.items)

    def execute(self, context):
        rq = context.scene.studio
        ensure_uids(rq)
        try:
            RUN.start(context.scene, rq.quality, rq.mode == 'ANIM')   # defaults for entries from older files
        except Exception as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class STUDIO_OT_cancel(Operator):
    bl_idname = "studio.cancel"
    bl_label = "Cancel"
    bl_description = "Cancel the queue (Shift+Esc). The current render stops right away and is not saved"

    @classmethod
    def poll(cls, context):
        return RUN.busy

    def execute(self, context):
        RUN.cancel()
        return {'FINISHED'}


class STUDIO_OT_add(Operator):
    bl_idname = "studio.add"
    bl_label = "Add Camera"
    bl_description = ("Renders the active camera as it is now: ⌘R an image, ⌘E a film (the quality chosen in the "
                      "chat), ⇧⌘R an image in Low Quality, ⇧⌘E shows the image")
    bl_options = {'UNDO'}

    quality: EnumProperty(
        items=(('DEFAULT', "As Chosen", "The quality chosen in the Queue tab"),
               ('HIGH', "High Quality", ""),
               ('LOW', "Low Quality", "")),
        default='DEFAULT', options={'SKIP_SAVE'})
    mode: EnumProperty(
        items=(('DEFAULT', "As Chosen", "Still or film as chosen in the chat"),
               ('STILL', "Image", "One image (⌘R)"),
               ('ANIM', "Film", "The frame range as a film (⌘E)")),
        default='DEFAULT', options={'SKIP_SAVE'})
    camera: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})    # this camera instead of the active one
    batch: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})     # "Batch_03": into that folder, numbered

    def execute(self, context):
        rq = context.scene.studio
        # The camera bar's camera, not the viewport selection: cameras are usually
        # hidden, and hidden objects cannot be selected.
        cam = context.scene.objects.get(self.camera) if self.camera else active_camera(context.scene)
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
        item.mode = rq.mode if self.mode == 'DEFAULT' else self.mode
        item.batch = self.batch
        cam.studio_camera.queue_count += 1
        item.number = cam.studio_camera.queue_count
        item.has_snapshot = True
        item.matrix = [v for row in cam.matrix_world for v in row]
        item.lens = cam.data.lens
        item.resolution_x, item.resolution_y = _user_resolution(scene, cam)
        item.percentage = scene.render.resolution_percentage
        item.frame_start, item.frame_end = scene.frame_start, scene.frame_end
        item.parts = rq.film_parts
        self.report({'INFO'}, "AI Studio: added %s" % runner.item_label(scene, item))
        p = runner.prefs(context)
        if (p is None or p.auto_render) and not RUN.busy:
            try:
                RUN.start(scene)
            except Exception as ex:
                self.report({'ERROR'}, str(ex))
                return {'CANCELLED'}
        return {'FINISHED'}


class STUDIO_OT_clear(Operator):
    bl_idname = "studio.clear"
    bl_label = "Clear"
    bl_description = "Remove all cameras from the list. A running render is discarded"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return len(context.scene.studio.items) > 0

    def execute(self, context):
        context.scene.studio.items.clear()
        return {'FINISHED'}


class STUDIO_OT_remove(Operator):
    bl_idname = "studio.remove"
    bl_label = "Remove"
    bl_description = "Remove from the list. If this entry is rendering right now, its image is not saved"
    bl_options = {'UNDO', 'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        items = context.scene.studio.items
        if 0 <= self.index < len(items):
            items.remove(self.index)
        return {'FINISHED'}


class STUDIO_OT_move(Operator):
    bl_idname = "studio.move"
    bl_label = "Move"
    bl_description = "Move up or down in the order"
    bl_options = {'UNDO', 'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})
    direction: IntProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=-1)

    def execute(self, context):
        items = context.scene.studio.items
        target = self.index + self.direction
        if 0 <= self.index < len(items) and 0 <= target < len(items):
            items.move(self.index, target)
        return {'FINISHED'}


class STUDIO_OT_view_camera(Operator):
    bl_idname = "studio.view_camera"
    bl_label = "Look Through Camera"
    bl_description = "Makes the camera the active scene camera and looks through it"
    bl_options = {'UNDO', 'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        items = context.scene.studio.items
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


class STUDIO_OT_preview_step(Operator):
    bl_idname = "studio.preview_step"
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
            print("AI Studio: preview failed:", ex)
        runner.tag_redraw()
        return None
    bpy.app.timers.register(load, first_interval=0.0)


def on_live_image(path):
    """A new draft or the finished image of the entry that renders: into the preview right away
    (also into an open render window, which shows the same image: the live preview), unless you
    are browsing older images."""
    if _view["path"]:
        return
    try:
        runner.show_in_preview(path)
    except (RuntimeError, OSError) as ex:
        print("AI Studio: preview failed:", ex)
    runner.tag_redraw()


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


class STUDIO_OT_show_render(Operator):
    bl_idname = "studio.show_render"
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


_ab = {"A": "", "B": ""}       # the two renders to compare (paths); "" = the shown one and the one before it
_overview = {"all": False}
OVERVIEW_FIRST = 12


def _batch_of(path):
    folder = os.path.basename(os.path.dirname(path))
    return folder if folder.startswith(core.BATCH + "_") else ""


def _title(path):
    return runner.variant_of(path) or os.path.splitext(os.path.basename(path))[0]


class STUDIO_OT_render_pick(Operator):
    bl_idname = "studio.render_pick"
    bl_label = "Render"
    bl_options = {'INTERNAL'}

    path: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    slot: StringProperty(default="VIEW", options={'HIDDEN', 'SKIP_SAVE'})   # VIEW / A / B

    @classmethod
    def description(cls, _context, props):
        if props.slot == "VIEW":
            return "Show this render big above (also in the Refine tab)"
        return "Compare: this render as %s (click again: not)" % props.slot

    def execute(self, context):
        if self.slot == "VIEW":
            _view["path"] = self.path
        else:
            _ab[self.slot] = "" if _ab[self.slot] == self.path else self.path
        return {'FINISHED'}


class STUDIO_OT_render_ab(Operator):
    bl_idname = "studio.render_ab"
    bl_label = "A / B"
    bl_description = "Swap A and B, or clear both"
    bl_options = {'INTERNAL'}

    action: StringProperty(default="SWAP", options={'HIDDEN', 'SKIP_SAVE'})   # SWAP / CLEAR

    def execute(self, context):
        if self.action == "CLEAR":
            _ab.update(A="", B="")
        else:
            _ab["A"], _ab["B"] = _ab["B"], _ab["A"]
        return {'FINISHED'}


class STUDIO_OT_render_rate(Operator):
    bl_idname = "studio.render_rate"
    bl_label = "Stars"
    bl_description = "Rate this render (the same star again: none); sort and filter by it below"
    bl_options = {'INTERNAL'}

    path: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    stars: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        from . import render_review
        try:
            render_review.set_rating(self.path, self.stars)
        except OSError as exc:
            self.report({'ERROR'}, "Could not save the stars: %s" % exc)
            return {'CANCELLED'}
        return {'FINISHED'}


class STUDIO_OT_render_arrange(Operator):
    bl_idname = "studio.render_arrange"
    bl_label = "Sort and Filter"
    bl_options = {'INTERNAL'}

    key: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})     # sort / filter
    value: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return "Sort the renders" if props.key == "sort" else "Show only these renders"

    def execute(self, context):
        from . import render_review
        render_review.state[self.key] = self.value
        return {'FINISHED'}


class STUDIO_MT_render_arrange(Menu):
    bl_idname = "STUDIO_MT_render_arrange"
    bl_label = "Sort and Filter"

    def draw(self, context):
        from . import render_review
        layout = self.layout
        layout.label(text="Sort")
        for key, label, icon in render_review.SORTS:
            op = layout.operator(STUDIO_OT_render_arrange.bl_idname, text=label, icon=icon,
                                 depress=render_review.state["sort"] == key)
            op.key, op.value = "sort", key
        layout.separator()
        layout.label(text="Show")
        for key, label, icon in render_review.FILTERS:
            op = layout.operator(STUDIO_OT_render_arrange.bl_idname, text=label, icon=icon,
                                 depress=render_review.state["filter"] == key)
            op.key, op.value = "filter", key


class STUDIO_OT_render_refine(Operator):
    bl_idname = "studio.render_refine"
    bl_label = "Refine"
    bl_description = "Open this render in the Refine tab: Krea, look and adjustments (the result comes back here)"
    bl_options = {'INTERNAL'}

    path: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        if self.path:
            _view["path"] = self.path
        context.window_manager.studio_tab = 'FINISH'
        return {'FINISHED'}


class STUDIO_OT_render_reveal(Operator):
    bl_idname = "studio.render_reveal"
    bl_label = "Show in Finder"
    bl_description = "Show this render (or batch folder) in the Finder"
    bl_options = {'INTERNAL'}

    path: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        if not os.path.exists(self.path):
            self.report({'WARNING'}, "Not there any more")
            return {'CANCELLED'}
        import subprocess
        try:
            subprocess.Popen(["open", "-R", self.path] if os.path.isfile(self.path) else ["open", self.path])
        except OSError:
            bpy.ops.wm.path_open(filepath=self.path if os.path.isdir(self.path) else os.path.dirname(self.path))
        return {'FINISHED'}


class STUDIO_OT_render_overview(Operator):
    bl_idname = "studio.render_overview"
    bl_label = "All Renders"
    bl_description = "Show all renders or only the newest"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        _overview["all"] = not _overview["all"]
        return {'FINISHED'}


def _ab_button(row, path, slot):
    sub = row.row(align=True)
    sub.ui_units_x = 1.1
    op = sub.operator(STUDIO_OT_render_pick.bl_idname, text=slot, depress=_ab[slot] == path)
    op.path, op.slot = path, slot


def _stars(row, path):
    """☆☆☆☆☆ as five small buttons: click = that many stars."""
    from . import render_review
    have = render_review.rating(path)
    sub = row.row(align=True)
    sub.emboss = 'NONE'
    for n in range(1, 6):
        op = sub.operator(STUDIO_OT_render_rate.bl_idname, text="", icon='SOLO_ON' if n <= have else 'SOLO_OFF')
        op.path, op.stars = path, n


def _draw_compare(layout, context):
    """A and B side by side as small pictures, with stars; [⇆] [✕] and the big side by side window."""
    from . import render_review
    a, b = (_ab[k] if _ab[k] and os.path.isfile(_ab[k]) else "" for k in ("A", "B"))
    box = layout.box()
    if not (a or b):
        hint = box.row()
        hint.active = False
        hint.label(text="Compare: pick A and B below (or just compare with the one before)", icon='INFO')
        shots.draw_results(box)
        return
    width = context.region.width / (20 * (context.preferences.system.ui_scale or 1.0))
    scale = max(4.0, min(11.0, width / 2 - 1.5))
    split = box.split(factor=0.5, align=True)
    for slot, path in (("A", a), ("B", b)):
        col = split.column(align=True)
        head = col.row(align=True)
        head.label(text=slot)
        if path:
            icon = render_review.thumb(path)
            if icon:
                col.template_icon(icon_value=icon, scale=scale)
            name = col.row(align=True)
            op = name.operator(STUDIO_OT_render_pick.bl_idname, text=_title(path), emboss=False, translate=False)
            op.path, op.slot = path, "VIEW"
            _stars(col.row(align=True), path)
        else:
            empty = col.row()
            empty.active = False
            empty.scale_y = scale * 0.6
            empty.label(text="pick %s below" % slot)
    row = box.row(align=True)
    row.scale_y = 1.2
    row.operator(STUDIO_OT_render_ab.bl_idname, text="", icon='ARROW_LEFTRIGHT').action = "SWAP"
    shots.draw_results(row)
    row.operator(STUDIO_OT_render_ab.bl_idname, text="", icon='X').action = "CLEAR"


def _draw_overview(layout, context, images, shown):
    """All renders as small pictures: sorted and filtered (menu), a batch as one group, refined
    versions marked. Click the name = big above, [A] [B] = compare."""
    from . import render_review
    every = len(images)
    images = render_review.arrange(images)
    box = layout.box()
    head = box.row(align=True)
    sort = next(label for key, label, _i in render_review.SORTS if key == render_review.state["sort"])
    flt = next(label for key, label, _i in render_review.FILTERS if key == render_review.state["filter"])
    head.label(text="Renders (%d)" % len(images) if len(images) == every else "Renders (%d of %d)" % (len(images), every),
               icon='RENDERLAYERS')
    sub = head.row(align=True)
    sub.alignment = 'RIGHT'
    sub.active = False
    sub.label(text=sort if flt == "All" else "%s · %s" % (sort, flt))
    if not images:
        box.label(text="None of them", icon='INFO')
        return
    width = context.region.width / (20 * (context.preferences.system.ui_scale or 1.0))
    columns = max(2, min(5, int(width // 6)))
    grouped = render_review.state["sort"] == 'NEW'
    shown_list = images if _overview["all"] else images[:OVERVIEW_FIRST]
    grid = None
    group_now = None
    for path in shown_list:
        group = _batch_of(path) if grouped else render_review.camera_key(path) \
            if render_review.state["sort"] == 'CAMERA' else ""
        if grid is None or group != group_now:
            if group:
                row = box.row(align=True)
                row.label(text=group, icon='FILE_FOLDER' if grouped else 'CAMERA_DATA', translate=False)
                if grouped:
                    row.operator(STUDIO_OT_render_reveal.bl_idname, text="", icon='VIEWZOOM',
                                 emboss=False).path = os.path.dirname(path)
            grid = box.grid_flow(row_major=True, columns=columns, even_columns=True, align=False)
            group_now = group
        cell = grid.column(align=True)
        pic = cell.box() if path == shown else cell.column()
        icon = render_review.thumb(path)
        if icon:
            pic.template_icon(icon_value=icon, scale=max(3.0, width / columns - 1.2))
        kind = render_review.refined_kind(path)
        name = cell.row(align=True)
        name.scale_y = 0.8
        op = name.operator(STUDIO_OT_render_pick.bl_idname, text=_title(path), emboss=path == shown,
                           icon='SHADERFX' if kind == "krea" else 'MODIFIER' if kind == "edit" else 'NONE',
                           translate=False)
        op.path, op.slot = path, "VIEW"
        info = cell.row(align=True)
        info.scale_y = 0.8
        _ab_button(info, path, "A")
        _ab_button(info, path, "B")
        tag = info.row(align=True)
        tag.active = False
        tag.alignment = 'RIGHT'
        stars = render_review.rating(path)
        tag.label(text=render_review.stars_text(stars) if stars else
                  ("Low" if render_review.quality_of(path) == 'LOW' else ""))
    if len(images) > OVERVIEW_FIRST:
        box.operator(STUDIO_OT_render_overview.bl_idname, emboss=False,
                     text="Less" if _overview["all"] else "All %d Renders" % len(images))


def draw_statusbar(self, context):
    """The queue in Blender's status bar (at the bottom), like Blender's own renders."""
    if not RUN.busy:
        return
    row = self.layout.row(align=True)
    bar = row.row(align=True)
    bar.ui_units_x = 14
    bar.progress(factor=RUN.list_progress, type='BAR', text="AI Studio · " + _queue_progress_text())
    row.operator(STUDIO_OT_cancel.bl_idname, text="", icon='X', emboss=False)


LOW_SCALES = (0.25, 0.5, 0.75, 1.0)


class STUDIO_OT_low_scale(Operator):
    bl_idname = "studio.low_scale"
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


class STUDIO_PT_output(Panel):
    """Gear menu in the Queue tab, top to bottom: High Quality, Low Quality, Still, Animation."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Output"
    bl_ui_units_x = 15

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        rq = scene.studio
        render = scene.render
        p = runner.prefs(context)
        cycles = scene.render.engine == 'CYCLES'

        # Where the renders go and how big (the chat renders with these)
        from . import render_size
        box = layout.box()
        col = box.column()
        row = col.row(align=True)
        row.label(text="", icon='SORTALPHA')
        row.prop(rq, "output_name", text="", placeholder=core.project_name())
        if p is not None:
            row = col.row(align=True)
            row.label(text="", icon='FILE_FOLDER')
            row.prop(p, "output_root", text="", placeholder="Next to the .blend file")
        render_size.draw(col, scene)

        # High Quality: the scene's own settings
        box = layout.box()
        box.label(text="High Quality", icon='SHADING_RENDERED')
        _factor_row(box, "Size", SCALE_PRESETS, 'studio.set_scale', render.resolution_percentage)
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
            _factor_row(box, "Resolution", LOW_SCALES, STUDIO_OT_low_scale.bl_idname, p.low_resolution,
                        target="resolution")
            _factor_row(box, "Samples", LOW_SCALES, STUDIO_OT_low_scale.bl_idname, p.low_samples_percent,
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


class STUDIO_MT_render_presets(Menu):
    """The preset menu of the Render tab: quality and what is rendered."""
    bl_idname = "STUDIO_MT_render_presets"
    bl_label = "Render Presets"

    def draw(self, context):
        layout = self.layout
        rq = context.scene.studio
        layout.prop_enum(rq, "quality", 'HIGH', text="High Quality")
        layout.prop_enum(rq, "quality", 'LOW', text="Low Quality")
        layout.separator()
        layout.prop_enum(rq, "mode", 'STILL', text="Still Image", icon='IMAGE_DATA')
        layout.prop_enum(rq, "mode", 'ANIM', text="Animation", icon='RENDER_ANIMATION')


class STUDIO_PT_render_queue(_SidebarPanel, Panel):
    """The queue, in its own tab. New name in 0.14: a pinned panel shows up in every tab,
    and Blender keeps the pin per panel name in the file (STUDIO_PT_panel, STUDIO_PT_queue)."""
    studio_tab = 'QUEUE'
    bl_label = "Queue"
    bl_options = {'HIDE_HEADER'}
    bl_order = QUEUE_TAB_ORDER

    def draw(self, context):
        _unpin(self)
        _schedule_prewarm()
        layout = self.layout
        scene = context.scene
        rq = scene.studio
        if not RUN.busy:
            shots.draw_check(layout, scene)

        # The head: what is shown below [sort · filter ▾] and the output settings (name, folder, size,
        # quality details) in the gear; renders are started in the chat
        head = layout.row(align=True)
        head.scale_y = 1.2
        from . import render_review
        sort = next(label for key, label, _i in render_review.SORTS if key == render_review.state["sort"])
        head.menu(STUDIO_MT_render_arrange.bl_idname, text=sort, icon='FILTER')
        head.popover(STUDIO_PT_output.__name__, text="", icon='PREFERENCES')

        # What renders now and what waits: progress only (renders are started in the chat).
        current = RUN.current["uid"] if RUN.current else None
        waiting = [(i, item) for i, item in enumerate(rq.items) if item.camera is not None]
        from . import film
        films = [f for f in film.films() if f[2] != 'DONE']
        if RUN.busy or waiting or films:
            box = layout.box()
            col_run = box.column(align=True)
            film.draw(col_run)
            for i, item in waiting:
                row = col_run.row(align=True)
                rendering = item.uid == current
                label = runner.item_label(scene, item)
                if item.batch:
                    label = "%s · %s" % (item.batch, label)
                row.label(text=label, icon='RENDER_STILL' if rendering else 'TIME', translate=False)
                if rendering:
                    row.progress(factor=RUN.render_progress, type='BAR', text=_item_progress_text())
                    row.operator(STUDIO_OT_cancel.bl_idname, text="", icon='X', emboss=False)
                else:
                    row.operator(STUDIO_OT_remove.bl_idname, text="", icon='X', emboss=False).index = i
            if not RUN.busy and waiting:   # left over after a cancel
                col_run.operator(STUDIO_OT_render_queue.bl_idname, text="Render the Rest (%d)" % len(waiting),
                                 icon='PLAY')
            if RUN.message and not RUN.busy:
                col_run.label(text=RUN.message, icon='INFO')

        # Sections of the film (markers in the timeline): camera, length, keys, preview, render
        if rq.mode == 'ANIM' or len(scene.timeline_markers):
            from . import motion
            motion.draw_sections(layout, scene)

        # The preview: the entry that renders now (its quick draft, then the image) or the render you
        # picked; ◀ ▶ through all renders, the magnifier opens it big, [A] [B] and Compare next to it.
        images = runner.queue_images(scene)
        path = _selected_image(images)
        live = _live() if not _view["path"] else None
        image = bpy.data.images.get(runner.PREVIEW_IMAGE)
        if path and (image is None or
                     os.path.normpath(bpy.path.abspath(image.filepath)) != os.path.normpath(path)):
            _schedule_preview(path)   # data can't change inside draw
            image = None              # still the older image until it is loaded
        entries = ([""] if _live() else []) + images
        if not entries:
            sub = layout.row()
            sub.active = False
            sub.label(text="No renders yet: start them in the chat", icon='INFO')
            return
        index = 0 if live is not None or path not in entries else entries.index(path)
        pic = layout.box()
        head = pic.row(align=True)
        older = head.row(align=True)
        older.enabled = index < len(entries) - 1
        older.operator(STUDIO_OT_preview_step.bl_idname, text="", icon='TRIA_LEFT').direction = 1
        count = head.row(align=True)
        count.alignment = 'CENTER'
        count.label(text="Rendering" if live is not None else
                    "%d / %d" % (index + 1, len(entries)) if index else "Newest")
        newer = head.row(align=True)
        newer.enabled = index > 0
        newer.operator(STUDIO_OT_preview_step.bl_idname, text="", icon='TRIA_RIGHT').direction = -1
        head.operator(STUDIO_OT_show_render.bl_idname, text="", icon='ZOOM_IN')
        scale = max(6.0, min(24.0, context.region.width / (20 * (context.preferences.system.ui_scale or 1.0)) - 2.5))
        if image is not None and path:
            pic.template_icon(icon_value=layout.icon(image), scale=scale)
        else:   # the same height until the draft is there
            wait = pic.row()
            wait.alignment = 'CENTER'
            wait.scale_y = scale
            wait.active = False
            wait.label(text="", icon='TIME' if live is not None else 'NONE')
        row = pic.row(align=True)
        if live is not None:
            row.label(text=live["camera"], icon='RENDER_STILL', translate=False)
        elif path:
            _ab_button(row, path, "A")
            _ab_button(row, path, "B")
            row.label(text=_title(path), translate=False)
            row.operator(STUDIO_OT_render_reveal.bl_idname, text="", icon='FILE_FOLDER', emboss=False).path = path
            row = pic.row(align=True)
            _stars(row, path)
            refine = row.row(align=True)
            refine.alignment = 'RIGHT'
            refine.operator(STUDIO_OT_render_refine.bl_idname, text="Refine", icon='NODE_COMPOSITING').path = path
        _draw_compare(layout, context)
        _draw_overview(layout, context, images, path)

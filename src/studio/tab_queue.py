# SPDX-License-Identifier: GPL-3.0-or-later
"""The Render tab: output settings, the progress of what renders (started in the chat), the preview,
all renders (batches grouped) and comparing two of them (A / B)."""

import os
import time

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty)
from bpy.types import Menu, Operator, Panel

from . import core, runner, shots

ORIGIN = [""]            # set around an add that is not the user's own (HQ after the preview, after a restart)


def _chat_busy():
    try:
        from .. import core as chat
        return chat.is_busy()
    except Exception:
        return False
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
            RUN.start(context.scene, rq.quality, False)   # defaults for entries from older files
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
    bl_label = "Render"
    bl_options = {'UNDO'}

    quality: EnumProperty(
        items=(('DEFAULT', "Automatic", "Low Quality first, High Quality once nothing changed since"),
               ('HIGH', "High Quality", ""),
               ('LOW', "Low Quality", "")),
        default='DEFAULT', options={'SKIP_SAVE'})
    mode: EnumProperty(
        items=(('DEFAULT', "Image", "One image"),
               ('STILL', "Image", "One image (⌘R)"),
               ('ANIM', "Film", "The film's sections as frames into the Video Editor (⌘E)")),
        default='DEFAULT', options={'SKIP_SAVE'})
    camera: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})    # this camera instead of the active one
    batch: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})     # "Batch_03": into that folder, numbered
    section: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})   # a film: only this section ("" = all)
    frames: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})    # a film: "a-b" only these frames again
    other: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})       # shift-click: the other quality
    force: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})       # a film: render again though nothing changed
    folder: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})    # a film: a version of its own (Frames/<s> <this>)
    keep_running: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})   # a film: what renders of it goes on

    @classmethod
    def description(cls, context, props):
        from . import autoquality
        if props.mode == 'ANIM' and props.force:
            return ("Render every frame of this section again (nothing changed since its render; each older "
                    "frame stays until its new one comes, then goes to archiv)")
        if props.mode == 'ANIM':
            what = ("Render this section's frames" if props.section else
                    "Render every section that needs it (one after the other; sections in High Quality with "
                    "nothing changed are skipped)")
            text = what + "; they come into the Video Editor by themselves"
            if props.quality == 'DEFAULT' and not props.frames:
                text += ". Low Quality first, High Quality once nothing changed. Shift-click: the other quality"
            return text
        if props.quality == 'DEFAULT':
            scene = context.scene if context else None
            cam = (scene.objects.get(props.camera) if props.camera else active_camera(scene)) if scene else None
            if cam is not None:
                quality = autoquality.next_quality(scene, autoquality.still_key(cam.name))
                return "Render the camera now: %s. Shift-click: the other quality" % autoquality.describe(quality)
        return "Render the camera as it is now (⌘R an image, ⌘E the film, ⇧⌘R an image in Low Quality)"

    where: EnumProperty(
        items=(('AUTO', "Automatic", "Stills and short previews on this Mac, longer films on the render farm "
                                     "(when on)"),
               ('LOCAL', "This Mac", "On this Mac, also with the render farm on"),
               ('FARM', "Render Farm", "On the render farm (also a still or a short preview)")),
        default='AUTO', options={'SKIP_SAVE', 'HIDDEN'})
    no_preview: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})   # High Quality at once (only when asked for)

    def invoke(self, context, event):
        if self.force:                                # render again: the frames there go to archiv
            return context.window_manager.invoke_confirm(self, event)
        self.other = event.shift and self.quality == 'DEFAULT'
        return self.execute(context)

    def _quality(self, scene, key, done=True, up_to_date=False):
        """The quality for `key`; None: up to date (nothing to render)."""
        from . import autoquality
        if self.quality != 'DEFAULT':
            return self.quality
        quality = autoquality.next_quality(scene, key, done)
        if self.other:
            return 'LOW' if quality == 'HIGH' else 'HIGH'
        if up_to_date and quality == 'HIGH':
            return None
        return quality

    def execute(self, context):
        from . import cut
        scene = cut.film_scene(context.scene)        # from the Video Editor's scene: the film's 3D scene
        if self.mode == 'ANIM':
            import time as _time
            started = _time.time()
            result = self._film(context, scene)
            runner.log("render a film: %.2f s in Blender's window (%s)" % (_time.time() - started,
                                                                          self.section or "all sections"))
            return result
        # The camera bar's camera, not the viewport selection: cameras are usually
        # hidden, and hidden objects cannot be selected.
        cam = scene.objects.get(self.camera) if self.camera else active_camera(scene)
        if cam is None or cam.type != 'CAMERA':
            self.report({'WARNING'}, "No active camera, pick one in the Render tab")
            return {'CANCELLED'}
        from . import autoquality
        key = autoquality.still_key(cam.name)
        quality = self._quality(scene, key)
        item = self._item(scene, cam, quality, 'STILL')
        autoquality.record(scene, key)
        self.report({'INFO'}, "AI Studio: added %s (%s)" % (runner.item_label(scene, item),
                                                           "HQ" if quality == 'HIGH' else "LQ"))
        return self._start(context, scene)

    def _item(self, scene, cam, quality, mode):
        rq = scene.studio
        ensure_uids(rq)
        runner.purge_deleted(scene)
        item = rq.items.add()
        item.camera = cam
        item.uid = rq.next_uid
        rq.next_uid += 1
        item.quality = quality
        item.mode = mode
        item.batch = self.batch
        cam.studio_camera.queue_count += 1
        item.number = cam.studio_camera.queue_count
        item.has_snapshot = True
        item.matrix = [v for row in cam.matrix_world for v in row]
        item.lens = cam.data.lens
        item.resolution_x, item.resolution_y = _user_resolution(scene, cam)
        item.percentage = scene.render.resolution_percentage
        item.frame_start, item.frame_end = scene.frame_start, scene.frame_end
        item.parts = 1
        item.where = self.where
        item.origin = ORIGIN[0] or ("the chat" if _chat_busy() else "you")
        return item

    def _start(self, context, scene=None):
        p = runner.prefs(context)
        if (p is None or p.auto_render) and not RUN.busy:
            try:
                RUN.start(scene or context.scene)
            except Exception as ex:
                self.report({'ERROR'}, str(ex))
                return {'CANCELLED'}
        return {'FINISHED'}

    def _film(self, context, scene):
        """One entry per section (each renders its frames in its own process, one after the other)."""
        from . import autoquality, cut
        found = cut.sections_of(scene)
        if self.section:
            found = [f for f in found if f[0] == self.section]
            if not found:
                self.report({'WARNING'}, "No section %s" % self.section)
                return {'CANCELLED'}
        waiting = {i.section for i in scene.studio.items if i.mode == 'ANIM' and i.camera is not None}
        added, skipped = [], []
        added_sections = []
        no_camera = []
        for name, start, end, marker in found:
            if marker is not None:          # a section renders from its own camera only (never another one)
                cam = marker.camera
            else:
                cam = (scene.objects.get(self.camera) if self.camera else None) or active_camera(scene)
            if cam is None or cam.type != 'CAMERA':
                no_camera.append(name)
                continue
            if not self.folder and not self.keep_running and (name in waiting or cut._rendering(name, fresh=True)):
                if not self.section:
                    skipped.append(name)
                    continue
                from . import film_tools, renderfarm   # this section sent again: its older render stops,
                if self.force or not any(              # unless it renders on the farm from the same scene
                        j.get("section") == name and j["phase"] == "sent" and renderfarm.unchanged(j)
                        for j in renderfarm._jobs):    # (then only what it does not render goes)
                    film_tools.supersede(scene, name)
            a, b, keep = start, end, False
            if self.frames:
                try:
                    a, b = (int(v) for v in self.frames.split("-"))
                except ValueError:
                    a, b = start, end
                a, b, keep = max(start, a), min(end, b), True
            st = cut.frame_status(name, start, end, fresh=True)
            key = autoquality.section_key(name)
            if self.force:                      # again in the quality it has (its frames go to archiv)
                quality = self.quality if self.quality != 'DEFAULT' else (st["quality"] or 'HIGH')
            elif keep and self.quality == 'DEFAULT':
                quality = st["quality"] or 'LOW'
            else:
                quality = self._quality(scene, key, done=st["done"] == st["total"],
                                        up_to_date=not self.section and st["quality"] == 'HIGH')
            if quality is None:
                skipped.append(name)
                continue
            if quality == 'HIGH' and not self.no_preview and not autoquality.clean(scene, key) \
                    and not (keep and st["quality"] == 'HIGH'):
                quality = 'LOW'                     # a video preview first, High Quality follows by itself
                autoquality.follow_with_hq(scene, name, self.where)   # once nothing changed since
            item = self._item(scene, cam, quality, 'ANIM')
            item.section, item.keep_frames, item.redo = name, keep, bool(self.force)
            item.job_folder = self.folder
            item.percentage = 100       # a film: High Quality at full size (Low: its share of it), whatever
            #                             size the scene is set to for the viewport
            item.frame_start, item.frame_end = a, b
            if not keep and not self.folder:
                autoquality.record(scene, key)
                added_sections.append(name)
            added.append("%s %s" % (name, "HQ" if quality == 'HIGH' else "LQ"))
        fresh = [n for n in added_sections if n]
        if fresh:                                  # until frames come: one preview image over each section
            from . import film_tools
            try:
                film_tools.queue_posters(scene, fresh)
            except (ValueError, OSError, RuntimeError) as ex:
                print("AI Studio: preview images:", ex)
        missing = (" · no camera: %s (set one in the Video Editor, Sections)" % ", ".join(no_camera)
                   if no_camera else "")
        if not added:
            self.report({'WARNING'} if no_camera else {'INFO'}, "Nothing to render%s" % (
                missing or ": everything is in High Quality and nothing changed"))
            return {'CANCELLED'}
        self.report({'WARNING'} if no_camera else {'INFO'}, "Rendering %s%s" % (", ".join(added), missing))
        return self._start(context, scene)


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


def open_workspace(context, name, space_type):
    """Switch to a workspace (Rendering, Compositing …) and open the Studio sidebar of its `space_type`
    editor. Without that workspace: the biggest editor that is not a 3D view becomes one. Returns its spaces."""
    window = context.window
    space = bpy.data.workspaces.get(name)
    if space is not None:
        window.workspace = space
        screens = list(space.screens)
    else:
        area = max((a for a in window.screen.areas if a.type != 'VIEW_3D'), key=lambda a: a.width * a.height,
                   default=None)
        if area is not None:
            if space_type == 'NODE_EDITOR':
                area.ui_type = 'CompositorNodeTree'
            else:
                area.type = space_type
        screens = [window.screen]
    found = []
    for screen in screens:
        for area in screen.areas:
            if area.type == space_type:
                for sp in area.spaces:
                    if sp.type == space_type:
                        sp.show_region_ui = True
                        found.append(sp)
    return found


def show_in_image_editor(context, path):
    """The render in the Image Editors of the window (the Compare & Sort workspace)."""
    if not path or not os.path.isfile(path):
        return
    try:
        image = bpy.data.images.load(path, check_existing=True)
    except RuntimeError:
        return
    for area in context.window.screen.areas:
        if area.type == 'IMAGE_EDITOR':
            for sp in area.spaces:
                if sp.type == 'IMAGE_EDITOR':
                    sp.image = image


PHOTO_WORKSPACES = ("Photo", "Compositing", "Rendering")   # the Photo workspace (renamed Compositing)


def photo_workspace():
    return next((n for n in PHOTO_WORKSPACES if bpy.data.workspaces.get(n) is not None), "Photo")


class STUDIO_OT_open_review(Operator):
    bl_idname = "studio.open_review"
    bl_label = "Compare"
    bl_description = ("All renders big in the Photo workspace: compare A / B, stars, sort, batches (the queue is "
                      "there too)")
    bl_options = {'INTERNAL'}

    def execute(self, context):
        open_workspace(context, photo_workspace(), 'IMAGE_EDITOR')
        images = runner.queue_images(context.scene)
        show_in_image_editor(context, _selected_image(images) if images else "")
        return {'FINISHED'}


class STUDIO_OT_render_pick(Operator):
    bl_idname = "studio.render_pick"
    bl_label = "Render"
    bl_options = {'INTERNAL'}

    path: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    slot: StringProperty(default="VIEW", options={'HIDDEN', 'SKIP_SAVE'})   # VIEW / A / B

    @classmethod
    def description(cls, _context, props):
        if props.slot == "VIEW":
            return "Show this render big (also in Refine)"
        return "Compare: this render as %s (click again: not)" % props.slot

    def execute(self, context):
        if self.slot == "VIEW":
            _view["path"] = self.path
            if context.area is not None and context.area.type == 'IMAGE_EDITOR':
                show_in_image_editor(context, self.path)
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
    bl_description = ("Work on this render in the Photo workspace: big, with compare, look, adjustments, Krea, "
                      "mockup and export in one sidebar")
    bl_options = {'INTERNAL'}

    path: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        if self.path:
            _view["path"] = self.path
        open_workspace(context, photo_workspace(), 'IMAGE_EDITOR')
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


class STUDIO_PT_photo_export(Panel):
    """Export, in the Photo workspace's top bar (not under the renders)."""
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'HEADER'
    bl_label = "Export"
    bl_ui_units_x = 14

    def draw(self, context):
        from . import photo_view
        scene = context.scene
        path = _selected_image(runner.queue_images(scene))
        photo_view.draw_export(self.layout, context, path)


def draw_photo_header(self, context):
    """[⤴ Export] at the right of the Image Editor's top bar, in the Photo workspace."""
    workspace = getattr(context, "workspace", None)
    if workspace is None or workspace.name != photo_workspace():
        return
    self.layout.popover(STUDIO_PT_photo_export.__name__, text="Export", icon='EXPORT')


class STUDIO_OT_photo_step(Operator):
    bl_idname = "studio.photo_step"
    bl_label = "Next Render"
    bl_description = "Arrow keys in the Photo workspace: the next (→ ↑) or the one before (← ↓), big"
    bl_options = {'INTERNAL'}

    direction: IntProperty(default=1, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        workspace = getattr(context, "workspace", None)
        return workspace is not None and workspace.name == photo_workspace() and \
            context.area is not None and context.area.type == 'IMAGE_EDITOR'

    def execute(self, context):
        from . import render_review
        images = render_review.arrange(runner.queue_images(context.scene))   # in the order of the list
        if not images:
            return {'CANCELLED'}
        current = _view["path"] if _view["path"] in images else images[0]
        index = max(0, min(len(images) - 1, images.index(current) + self.direction))
        _view["path"] = images[index]
        show_in_image_editor(context, images[index])
        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


def draw_sort_head(layout, context):
    """The top of Sort: [Low | High] [Sort ▾] [▦ | ☰] [size]."""
    from . import render_review
    scene, wm = context.scene, context.window_manager
    row = layout.row(align=True)
    row.scale_y = 1.2
    row.prop_enum(scene.studio, "quality", 'LOW', text="Low", icon='SORTTIME')
    row.prop_enum(scene.studio, "quality", 'HIGH', text="High", icon='SHADING_RENDERED')
    row.separator()
    sort = next(label for key, label, _i in render_review.SORTS if key == render_review.state["sort"])
    row.menu(STUDIO_MT_render_arrange.bl_idname, text=sort, icon='FILTER')
    row.separator()
    row.prop(wm, "studio_render_layout", expand=True, icon_only=True)
    if wm.studio_render_layout == 'GRID':
        sub = row.row(align=True)
        sub.ui_units_x = 3
        sub.prop(wm, "studio_render_columns", text="")


def _mark_buttons(row, path):
    """[👍] [👎]: good or bad (click again: no mark)."""
    from . import render_review
    stars = render_review.rating(path)
    op = row.operator(STUDIO_OT_render_rate.bl_idname, text="", icon='CHECKMARK',
                      depress=stars >= render_review.GOOD, emboss=stars >= render_review.GOOD)
    op.path, op.stars = path, render_review.GOOD
    op = row.operator(STUDIO_OT_render_rate.bl_idname, text="", icon='X',
                      depress=0 < stars <= render_review.BAD, emboss=0 < stars <= render_review.BAD)
    op.path, op.stars = path, render_review.BAD


class STUDIO_OT_render_rename(Operator):
    bl_idname = "studio.render_rename"
    bl_label = "Rename Render"
    bl_description = "Give this render a name of its own (the file is renamed in its folder)"
    bl_options = {'INTERNAL'}

    path: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    name: StringProperty(name="Name", options={'SKIP_SAVE'})

    def invoke(self, context, _event):
        self.name = os.path.splitext(os.path.basename(self.path))[0]
        return context.window_manager.invoke_props_dialog(self, confirm_text="Rename")

    def execute(self, context):
        from . import render_review
        try:
            new = render_review.rename_render(self.path, self.name)
        except (OSError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        if _view["path"] == self.path:
            _view["path"] = new
        for key in ("A", "B"):
            if _ab[key] == self.path:
                _ab[key] = new
        return {'FINISHED'}


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
    wm = context.window_manager
    as_list = getattr(wm, "studio_render_layout", 'GRID') == 'LIST'
    columns = 1 if as_list else max(1, min(6, getattr(wm, "studio_render_columns", 2)))
    grouped = render_review.state["sort"] == 'NEW'
    shown_list = images                                  # all of them: the sidebar scrolls
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
        if as_list:                                # one line each: small picture, name, marks
            line = grid.row(align=True)
            op = line.operator(STUDIO_OT_render_pick.bl_idname, text=_title(path), translate=False,
                               icon_value=render_review.thumb(path) or 0, depress=path == shown)
            op.path, op.slot = path, "VIEW"
            _mark_buttons(line, path)
            line.operator(STUDIO_OT_render_rename.bl_idname, text="", icon='GREASEPENCIL', emboss=False).path = path
            continue
        cell = grid.column(align=True)
        pic = cell.box() if path == shown else cell.column()
        icon = render_review.thumb(path)
        if icon:                                   # (only template_icon draws a preview big; the name is the button)
            pic.template_icon(icon_value=icon, scale=max(3.0, width / columns - 0.8))
        kind = render_review.refined_kind(path)
        name = cell.row(align=True)
        name.scale_y = 0.8
        op = name.operator(STUDIO_OT_render_pick.bl_idname, text=_title(path), emboss=path == shown,
                           icon='SHADERFX' if kind == "krea" else 'MODIFIER' if kind == "edit" else 'NONE',
                           translate=False)
        op.path, op.slot = path, "VIEW"
        info = cell.row(align=True)
        info.scale_y = 0.8
        _mark_buttons(info, path)
        info.operator(STUDIO_OT_render_rename.bl_idname, text="", icon='GREASEPENCIL', emboss=False).path = path
        tag = info.row(align=True)
        tag.active = False
        tag.alignment = 'RIGHT'
        tag.label(text="Low" if render_review.quality_of(path) == 'LOW' else "")



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


class STUDIO_OT_goto_workspace(Operator):
    bl_idname = "studio.goto_workspace"
    bl_label = "Open"
    bl_options = {'INTERNAL'}

    which: StringProperty(default='PHOTO', options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return ("The Photo workspace: all renders big, sort, adjust, Krea" if props.which == 'PHOTO'
                else "The Film workspace: the film's sections, cut and export")

    def execute(self, context):
        if self.which == 'PHOTO':
            return bpy.ops.studio.open_review()
        name = next((n for n in ("Film", "Video Editing") if bpy.data.workspaces.get(n)), "Film")
        open_workspace(context, name, 'SEQUENCE_EDITOR')
        return {'FINISHED'}


def draw_render_head(layout, context):
    """The top of the Render tab, compact: [Mac|Farm] [LQ|HQ] [▣|▶] [⚙], the size, then [Render] [Photo] [Video]."""
    scene = context.scene
    rq = scene.studio
    render = scene.render
    row = layout.row(align=True)
    sub = row.row(align=True)
    sub.prop_enum(rq, "farm_mode", 'OFF', text="Mac", icon='DESKTOP')
    sub.prop_enum(rq, "farm_mode", 'ON', text="Farm", icon='NETWORK_DRIVE')
    row.separator(factor=0.5)
    sub = row.row(align=True)
    sub.prop_enum(rq, "quality", 'LOW', text="LQ")
    sub.prop_enum(rq, "quality", 'HIGH', text="HQ")
    row.separator(factor=0.5)
    sub = row.row(align=True)
    sub.prop_enum(rq, "quality_view", 'STILL', text="", icon='IMAGE_DATA')
    sub.prop_enum(rq, "quality_view", 'ANIM', text="", icon='RENDER_ANIMATION')
    row.popover(STUDIO_PT_output.__name__, text="", icon='PREFERENCES')
    pct = render.resolution_percentage / 100
    row = layout.row(align=True)                  # size: the pixels and 0.5× … 4×
    split = row.split(factor=0.42, align=True)
    left = split.row()
    left.label(text="%d × %d px" % (round(render.resolution_x * pct), round(render.resolution_y * pct)),
               icon='FULLSCREEN_ENTER', translate=False)
    buttons = split.row(align=True)
    for factor in SCALE_PRESETS:
        op = buttons.operator('studio.set_scale', text="%gx" % factor,
                              depress=render.resolution_percentage == round(factor * 100))
        op.factor = factor
    row = layout.row(align=True)                  # go: render, or open the pictures / the film
    row.scale_y = 1.4
    op = row.operator(STUDIO_OT_add.bl_idname, text="Render", icon='RENDER_STILL'
                      if rq.quality_view == 'STILL' else 'RENDER_ANIMATION')
    op.mode = 'ANIM' if rq.quality_view == 'ANIM' else 'STILL'
    op.quality = rq.quality
    row.operator(STUDIO_OT_goto_workspace.bl_idname, text="Photo", icon='IMAGE').which = 'PHOTO'
    row.operator(STUDIO_OT_goto_workspace.bl_idname, text="Video", icon='SEQUENCE').which = 'FILM'


def draw_quality(layout, context):
    """Render tab: [Stills | Animation], then High and Low Quality of that kind, each from a preset
    (Quick, Medium, High, Max) with its values (quality.py)."""
    from . import quality
    from .ui_common import section
    scene = context.scene
    rq = scene.studio
    render = scene.render
    if not rq.quality_ready:
        quality.schedule_ensure()               # files from before: filled once (not while drawing)
    cycles = render.engine == 'CYCLES'
    animation = rq.quality_view == 'ANIM'
    pct = render.resolution_percentage / 100
    width, height = round(render.resolution_x * pct), round(render.resolution_y * pct)
    for which, title, icon in (('HIGH', "High Quality", 'SHADING_RENDERED'), ('LOW', "Low Quality", 'SORTTIME')):
        prof = quality.profile(scene, animation, which)
        key = "render_%s_%s" % ("anim" if animation else "still", which.lower())
        box, head, is_open = section(layout, key, title, icon, default_open=which == 'HIGH')
        name = dict((k, n) for k, n, _d in quality.PRESET_ITEMS).get(prof.preset, "")
        head.label(text="%s · %d · %d × %d" % (name, prof.samples if cycles else prof.eevee_samples,
                                              round(width * prof.resolution / 100),
                                              round(height * prof.resolution / 100)), translate=False)
        if not is_open:
            continue
        presets = box.row(align=True)
        for preset in ('QUICK', 'MEDIUM', 'HIGH', 'MAX'):
            presets.prop_enum(prof, "preset", preset)
        if prof.preset == 'CUSTOM':
            sub = box.row()
            sub.active = False
            sub.label(text="Custom: values changed by hand", icon='MODIFIER')
        col = box.column(align=True)
        col.prop(prof, "resolution", slider=True)
        if cycles:
            col = box.column(align=True)
            col.prop(prof, "samples")
            row = col.row(align=True)
            if prof.adaptive:
                row.prop(prof, "adaptive", text="")
                row.prop(prof, "threshold")
            else:
                row.prop(prof, "adaptive", toggle=True)
            col.prop(prof, "time_limit")
            col = box.column(align=True)
            col.prop(prof, "bounces")
            row = col.row(align=True)
            row.prop(prof, "diffuse")
            row.prop(prof, "glossy")
            row.prop(prof, "transmission")
            col.prop(prof, "clamp")
            row = box.row(align=True)
            row.prop(prof, "denoise", toggle=True, icon='SHADERFX')
            row.prop(prof, "simplify", toggle=True, icon='MOD_DECIM')
            row.prop(prof, "motion_blur", toggle=True, icon='ONIONSKIN_ON')
        else:
            box.prop(prof, "eevee_samples")
            box.prop(prof, "motion_blur", toggle=True, icon='ONIONSKIN_ON')
    box = layout.box()                           # the same for every render
    row = box.row(align=True)
    row.prop(render, "film_transparent", text="Transparent", toggle=True, icon='IMAGE_RGB_ALPHA')
    row.prop(render, "use_persistent_data", text="Keep Data", toggle=True, icon='MEMORY')


class STUDIO_PT_output(Panel):
    """Gear menu of the Render tab: name, folder and size; still and animation format, file names,
    behaviour (High and Low Quality and the farm are in the tab itself)."""
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
        if rq.name_pattern == 'TEMPLATE':
            box.prop(rq, "name_template", text="", icon='SORTALPHA')

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


def _draw_renders(layout, context, review):
    """The newest render or the one picked (◀ ▶, A / B, stars, Refine); in the Image Editor (review) also
    comparing and all renders."""
    scene = context.scene
    # The preview: the entry that renders now (its quick draft, then the image) or the render you
    # picked; ◀ ▶ through all renders, the magnifier opens it big, [A] [B] and Compare next to it.
    images = runner.queue_images(scene)
    path = _selected_image(images)
    live = _live() if not _view["path"] else None
    frame = None                     # a film renders: its newest frame, if newer than the newest image
    if not review and not _view["path"] and live is None:
        from . import renderfarm
        newest = renderfarm.newest_frame
        try:
            if newest["path"] and os.path.isfile(newest["path"]) and (
                    not images or os.path.getmtime(newest["path"]) > os.path.getmtime(images[0])):
                frame, path = newest["title"], newest["path"]
        except OSError:
            frame = None
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
    count.label(text="Rendering" if live is not None else "Newest frame" if frame else
                "%d / %d" % (index + 1, len(entries)) if index else "Newest")
    newer = head.row(align=True)
    newer.enabled = index > 0
    newer.operator(STUDIO_OT_preview_step.bl_idname, text="", icon='TRIA_RIGHT').direction = -1
    head.operator(STUDIO_OT_show_render.bl_idname, text="", icon='ZOOM_IN')
    full = context.region.width / (20 * (context.preferences.system.ui_scale or 1.0)) - 2.5
    scale = max(6.0, min(24.0, full)) if review else max(4.0, min(10.0, full * 0.55))   # small here
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
    elif frame:
        row.label(text=frame, icon='RENDER_ANIMATION', translate=False)
        row.operator(STUDIO_OT_render_reveal.bl_idname, text="", icon='FILE_FOLDER', emboss=False).path = path
    elif path:
        _mark_buttons(row, path)
        row.label(text=_title(path), translate=False)
        row.operator(STUDIO_OT_render_rename.bl_idname, text="", icon='GREASEPENCIL', emboss=False).path = path
        row.operator(STUDIO_OT_render_reveal.bl_idname, text="", icon='FILE_FOLDER', emboss=False).path = path
    if not review:              # the queue, comparing and sorting: in the Photo workspace (the tab's head)
        return
    _draw_compare(layout, context)
    _draw_overview(layout, context, images, path)


def _queue_head(col, text, icon):
    row = col.row()
    row.active = False
    row.scale_y = 0.8
    row.label(text=text, icon=icon)


def _queue_row(col, scene, i, item, rendering, buttons, waits="waits"):
    row = col.row(align=True)
    label = runner.item_label(scene, item)
    if item.batch:
        label = "%s · %s" % (item.batch, label)
    if rendering:
        row.progress(factor=RUN.render_progress, type='BAR', text="%s · %s" % (label, _item_progress_text()))
        if buttons:
            row.label(text="", icon='BLANK1')
            row.operator(STUDIO_OT_cancel.bl_idname, text="", icon='X', emboss=False)
    else:
        row.progress(factor=0.0, type='BAR', text="%s · %s" % (label, waits))
        if buttons:
            up = row.operator(STUDIO_OT_move.bl_idname, text="", icon='TRIA_UP', emboss=False)
            up.index, up.direction = i, -1
            row.operator(STUDIO_OT_remove.bl_idname, text="", icon='X', emboss=False).index = i


def draw_queue(layout, context, buttons=True):
    """The one queue: what renders now and what waits, on this Mac (images, films) and on the render farm.
    A bar per entry with its progress and time left; ▲ renders it earlier, ✕ takes it out. Returns
    whether there was anything to show."""
    scene = context.scene
    rq = scene.studio
    from . import film, renderfarm
    current = RUN.current["uid"] if RUN.current else None
    waiting = [(i, item) for i, item in enumerate(rq.items) if item.camera is not None]
    films = [f for f in film.films_here() if f[2] != 'DONE']
    mine, other = renderfarm.rows()
    if mine or other or rq.farm_on:
        renderfarm.watch()
    preview = RUN.busy and RUN.current and RUN.current.get("preview")
    if not (RUN.busy or waiting or films or mine or other):
        if RUN.message:
            note = layout.row()
            note.active = False
            note.label(text=RUN.message, icon='INFO')
        return False
    box = layout.box()
    col = box.column(align=True)
    head = col.row()
    head.label(text="Queue", icon='SORTTIME')
    secs = ((RUN.total_left() or 0.0) if RUN.busy else 0.0) + (renderfarm.total_left() or 0.0)
    if secs > 0:
        right = head.row()
        right.alignment = 'RIGHT'
        right.active = False
        right.label(text="~%s left" % runner.format_time(secs), translate=False)
    farm_items = [(i, item) for i, item in waiting if renderfarm.takes(scene, item)]
    to_farm = {item.uid for _i, item in farm_items}
    local_items = [(i, item) for i, item in waiting if item.uid not in to_farm]
    if films or preview or local_items:              # two queues: this Mac and the farm (neither waits for
        _queue_head(col, "This Mac", 'DESKTOP')       # the other)
    film.draw(col, buttons)
    if preview:
        col.progress(factor=RUN.render_progress, type='BAR', text=_queue_progress_text())
    for i, item in local_items:
        _queue_row(col, scene, i, item, item.uid == current, buttons)
    if mine or other or farm_items:
        _queue_head(col, "Render Farm", 'NETWORK_DRIVE')
        if buttons and (mine or other):
            col.row(align=True).prop(rq, "farm_sort", expand=True)
    for i, item in farm_items:
        _queue_row(col, scene, i, item, False, buttons, "goes to the farm")
    renderfarm.draw_rows(col, buttons)
    if buttons and not RUN.busy and waiting:   # left over after a cancel
        col.operator(STUDIO_OT_render_queue.bl_idname, text="Render the Rest (%d)" % len(waiting), icon='PLAY')
    if RUN.message and not RUN.busy:
        note = col.row()
        note.active = False
        note.label(text=RUN.message, icon='INFO')
    return True


def _draw_pick(layout, context, images, path):
    """The picked picture in one small box (it is big next to the sidebar): ◀ 3 / 20 ▶, A B, name, stars."""
    entries = ([""] if _live() else []) + images
    if not entries:
        sub = layout.row()
        sub.active = False
        sub.label(text="No renders yet: start them in the chat", icon='INFO')
        return
    live = _live() if not _view["path"] else None
    index = 0 if live is not None or path not in entries else entries.index(path)
    box = layout.box()
    head = box.row(align=True)
    older = head.row(align=True)
    older.enabled = index < len(entries) - 1
    older.operator(STUDIO_OT_preview_step.bl_idname, text="", icon='TRIA_LEFT').direction = 1
    count = head.row(align=True)
    count.alignment = 'CENTER'
    count.label(text="Rendering" if live is not None else "%d / %d" % (index + 1, len(entries)) if index else "Newest")
    newer = head.row(align=True)
    newer.enabled = index > 0
    newer.operator(STUDIO_OT_preview_step.bl_idname, text="", icon='TRIA_RIGHT').direction = -1
    if live is not None or not path:
        return
    row = box.row(align=True)
    _mark_buttons(row, path)
    row.label(text=_title(path), translate=False)
    row.operator(STUDIO_OT_render_rename.bl_idname, text="", icon='GREASEPENCIL', emboss=False).path = path
    row.operator(STUDIO_OT_render_reveal.bl_idname, text="", icon='FILE_FOLDER', emboss=False).path = path


class STUDIO_PT_review(Panel):
    """The Photo workspace's one sidebar (Image Editor › Studio); the picked picture is big next to it.
    Four tabs, the steps of the work: Sort (rate, sort, compare, export) · Adjust (draw on the picture,
    a note, send it to Claude) · Look (all settings of the look and of this picture) · Mockup (Krea and
    the product in a photo). The picked picture stays on top in all of them."""
    bl_space_type = 'IMAGE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Studio"
    bl_label = "Renders"
    bl_options = {'HIDE_HEADER'}

    def draw(self, context):
        layout = self.layout
        from . import image_edit, mockup, photo_view, render_review, tab_finish
        from .ui_common import section
        photo_view.check_layout(context)
        scene = context.scene
        wm = context.window_manager
        tab = wm.studio_photo_tab
        head = layout.row(align=True)                    # the four steps (their names in full) ...
        head.scale_y = 1.3
        head.prop(wm, "studio_photo_tab", expand=True)
        tools = layout.row(align=True)                   # ... and the view's buttons under them, right
        tools.alignment = 'RIGHT'
        tools.operator(photo_view.STUDIO_OT_photo_fit.bl_idname, text="", icon='ZOOM_ALL')
        op = tools.operator(photo_view.STUDIO_OT_photo_layout.bl_idname, text="", icon='NODE_COMPOSITING',
                            depress=photo_view.compositor_open(context.screen))
        op.action = 'LOOK'
        tools.popover(STUDIO_PT_output.__name__, text="", icon='PREFERENCES')
        # no queue here: its bars came and went while rendering and moved everything below (Render tab)
        images = runner.queue_images(scene)
        path = _selected_image(images)
        a, b = (_ab[k] if _ab[k] and os.path.isfile(_ab[k]) else "" for k in ("A", "B"))
        photo_view.want(context, image_edit.preview_file(scene, path) if path else "", a, b)
        if tab != 'SORT':
            _draw_pick(layout, context, images, path)
        if tab == 'SORT':                                # only the renders, one after another (← → step)
            draw_sort_head(layout, context)
            _draw_overview(layout, context, images, path)
        elif tab == 'ADJUST':
            photo_view.draw_adjust_bar(layout, context, path)     # the chat follows below (ui_panel)
        elif tab == 'LOOK':
            # Only this picture's light and colour: the look of the next renders (compositor) is in the
            # Camera tab of the 3D view – here it changed the viewport, not the picture
            tab_finish._draw_adjust(layout, context, path)
        else:
            tab_finish._draw_krea(layout, context, path)


class STUDIO_PT_render_queue(_SidebarPanel, Panel):
    """The Render tab, top to bottom: where renders go (this Mac / the render farm) and the gear; the queue
    (this Mac and the farm in one list); the newest render as a preview (Compare opens the Photo
    workspace); High and Low Quality. The panel keeps its name of 0.14: Blender keeps a pin per panel
    name in the file."""
    studio_tab = 'QUEUE'
    bl_label = "Render"
    bl_options = {'HIDE_HEADER'}
    bl_order = QUEUE_TAB_ORDER

    def draw(self, context):
        _unpin(self)
        _schedule_prewarm()
        layout = self.layout
        scene = context.scene
        if not RUN.busy:
            shots.draw_check(layout, scene)
        from . import renderfarm
        draw_render_head(layout, context)              # where, LQ/HQ, still/film, size, [Render] [Photo] [Video]
        draw_queue(layout, context)                    # what is coming
        renderfarm.draw_state(layout, context)
        _draw_renders(layout, context, review=False)   # what came out (small; big in the Photo workspace)
        draw_quality(layout, context)                  # High and Low Quality in detail

"""The Photo workspace's everyday tools: the versions of a picture as a strip (click = big, ⇄ = wipe against
the one shown), the tools in the picture's top bar, keys (1 good, 0 bad, Space: compare with the one before),
one status line at the bottom of the window (Claude, Krea, renders – nothing in the panels moves any more),
a look for all the good pictures at once, and after Krea: good → the set, or the same again."""
import glob
import os
import time

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

GOOD_SET = "Set"          # the folder of the good pictures (Export/Set)


def _workspace_ok(context):
    from .tab_queue import photo_workspace
    workspace = getattr(context, "workspace", None)
    return workspace is not None and workspace.name == photo_workspace()


def versions(path):
    """The render and everything made from it (…_krea_01, …_edit_01), oldest first."""
    if not path:
        return []
    from . import render_review
    root = render_review.source_of(path) or path
    stem = os.path.splitext(root)[0]
    found = [root] if os.path.isfile(root) else []
    for kind in ("krea", "edit"):
        found += glob.glob(glob.escape(stem) + "_%s_*.png" % kind) + glob.glob(glob.escape(stem) + "_%s_*.jpg" % kind)
    found = [p for p in dict.fromkeys(found) if os.path.isfile(p)]
    return sorted(found, key=os.path.getmtime)


def _shown():
    from . import runner, tab_queue
    return tab_queue._selected_image(runner.queue_images(bpy.context.scene))


def _show(context, path):
    from . import tab_queue
    tab_queue._view["path"] = path
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


class STUDIO_OT_photo_version(Operator):
    bl_idname = "studio.photo_version"
    bl_label = "Version"
    bl_options = {'INTERNAL'}

    path: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    action: EnumProperty(items=[('SHOW', "Show", ""), ('WIPE', "Compare", "")], options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return ("Wipe this version against the one shown (the line in the picture)" if props.action == 'WIPE'
                else "Show this version big")

    def execute(self, context):
        from . import runner, tab_queue
        if self.action == 'WIPE':
            tab_queue._ab["A"], tab_queue._ab["B"] = _shown(), self.path
            if context.window_manager.studio_compare == 'OFF':
                context.window_manager.studio_compare = 'WIPE'
        else:
            tab_queue._ab["A"] = tab_queue._ab["B"] = ""
            runner._images_cache["time"] = 0.0
            _show(context, self.path)
        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


def draw_filmstrip(layout, context, path):
    """The versions of the picture shown: small pictures in a row; the shown one framed."""
    found = versions(path)
    if len(found) < 2 and not path:
        return
    from . import render_review
    box = layout.box()
    head = box.row(align=True)
    head.label(text="Versions (%d)" % len(found), icon='RENDERLAYERS')
    columns = min(4, max(1, len(found)))
    grid = box.grid_flow(row_major=True, columns=columns, even_columns=True, align=True)
    width = context.region.width / (20 * (context.preferences.system.ui_scale or 1.0))
    scale = max(2.5, width / columns - 1.2)
    for version in found:
        col = grid.column(align=True)
        cell = col.box() if version == path else col.column()
        icon = render_review.thumb(version)
        if icon:
            cell.template_icon(icon_value=icon, scale=scale)
        row = cell.row(align=True)
        kind = render_review.refined_kind(version)
        name = (kind.capitalize() + " " + version.rsplit("_", 1)[-1].split(".")[0]) if kind else "Render"
        op = row.operator(STUDIO_OT_photo_version.bl_idname, text=name, depress=version == path, translate=False)
        op.path, op.action = version, 'SHOW'
        sub = row.row(align=True)
        sub.enabled = version != path
        op = sub.operator(STUDIO_OT_photo_version.bl_idname, text="", icon='ARROW_LEFTRIGHT')
        op.path, op.action = version, 'WIPE'
    if path and render_review.refined_kind(path) == 'krea':
        draw_after_krea(box, context, path)


# ------------------------------------------------------------------ after Krea: good → the set, or again

_last = {"wish": ""}       # the last wish sent to Krea (for "Again")


def remember_wish(text):
    if text and text.strip():
        _last["wish"] = text.strip()


class STUDIO_OT_photo_good(Operator):
    bl_idname = "studio.photo_good"
    bl_label = "Good → Set"
    bl_description = "Mark this picture good and copy it into the presentation set (Export/Set, 2560 px JPEG)"
    bl_options = {'INTERNAL'}

    path: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        from . import photo_view, render_review
        try:
            render_review.set_rating(self.path, render_review.GOOD)
            folder = os.path.join(os.path.dirname(self.path), "Export", GOOD_SET)
            number = len([f for f in os.listdir(folder) if f[:2].isdigit()]) + 1 if os.path.isdir(folder) else 1
            os.makedirs(folder, exist_ok=True)
            written = photo_view.export([self.path], 'JPEG', '2560')
            if written:
                target = os.path.join(folder, "%02d_%s" % (number, os.path.basename(written[0])))
                os.replace(written[0], target)
                self.report({'INFO'}, "Good, and in the set: %s" % os.path.basename(target))
        except (OSError, RuntimeError) as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class STUDIO_OT_krea_again(Operator):
    bl_idname = "studio.krea_again"
    bl_label = "Again"
    bl_description = "The same once more with Krea (same settings and wish, a new take) – the price is asked first"
    bl_options = {'INTERNAL'}

    path: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        from . import tab_finish
        wm = context.window_manager
        if not wm.studio_krea_prompt.strip():
            wm.studio_krea_prompt = _last["wish"]
        from . import render_review
        source = render_review.source_of(self.path) or self.path
        _show(context, source)                       # Krea starts from the render, not from its last take
        return bpy.ops.studio.krea_claude('INVOKE_DEFAULT') if not tab_finish.krea.token() else \
            bpy.ops.studio.krea_refine('INVOKE_DEFAULT')

    def execute(self, context):
        return {'CANCELLED'}


def draw_after_krea(layout, context, path):
    row = layout.row(align=True)
    row.scale_y = 1.2
    row.operator(STUDIO_OT_photo_good.bl_idname, text="Good → Set", icon='CHECKMARK').path = path
    row.operator(STUDIO_OT_krea_again.bl_idname, text="Again", icon='FILE_REFRESH').path = path


# ------------------------------------------------------------------ keys: 1 good, 0 bad, Space compare

class STUDIO_OT_photo_key(Operator):
    bl_idname = "studio.photo_key"
    bl_label = "Photo Key"
    bl_description = "1: good · 0: bad · Space: compare with the one before (again: off)"
    bl_options = {'INTERNAL'}

    action: EnumProperty(items=[('GOOD', "Good", ""), ('BAD', "Bad", ""), ('COMPARE', "Compare", "")],
                         options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return _workspace_ok(context) and context.area is not None and context.area.type == 'IMAGE_EDITOR'

    def execute(self, context):
        from . import render_review, runner, tab_queue
        path = _shown()
        if not path:
            return {'CANCELLED'}
        if self.action == 'COMPARE':
            if tab_queue._ab["A"] or tab_queue._ab["B"]:
                tab_queue._ab["A"] = tab_queue._ab["B"] = ""
            else:
                images = render_review.arrange(runner.queue_images(context.scene))
                index = images.index(path) if path in images else -1
                before = images[index + 1] if 0 <= index < len(images) - 1 else ""
                if not before:
                    return {'CANCELLED'}
                tab_queue._ab["A"], tab_queue._ab["B"] = before, path
                if context.window_manager.studio_compare == 'OFF':
                    context.window_manager.studio_compare = 'WIPE'
        else:
            stars = render_review.GOOD if self.action == 'GOOD' else render_review.BAD
            render_review.set_rating(path, 0 if render_review.rating(path) == stars else stars)
        for area in context.screen.areas:
            area.tag_redraw()
        return {'FINISHED'}


_keymaps = []


def register_keymaps():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    km = kc.keymaps.new(name="Image", space_type='IMAGE_EDITOR')
    for key, action in (('ONE', 'GOOD'), ('NUMPAD_1', 'GOOD'), ('ZERO', 'BAD'), ('NUMPAD_0', 'BAD'),
                        ('SPACE', 'COMPARE')):
        kmi = km.keymap_items.new(STUDIO_OT_photo_key.bl_idname, key, 'PRESS')
        kmi.properties.action = action
        _keymaps.append((km, kmi))


def unregister_keymaps():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()


# ------------------------------------------------------------------ the picture's top bar

def draw_header_tools(layout, context):
    """[✎] [⇄] [⤢] in the Image Editor's top bar (Photo workspace), before Export."""
    from . import photo_view, tab_queue
    row = layout.row(align=True)
    row.operator(photo_view.STUDIO_OT_photo_draw.bl_idname, text="", icon='GREASEPENCIL',
                 depress=photo_view.drawing(context)).action = 'PEN'
    on = bool(tab_queue._ab["A"] and tab_queue._ab["B"])
    row.operator(STUDIO_OT_photo_key.bl_idname, text="", icon='ARROW_LEFTRIGHT', depress=on).action = 'COMPARE'
    row.operator(photo_view.STUDIO_OT_photo_fit.bl_idname, text="", icon='ZOOM_ALL')


# ------------------------------------------------------------------ one status line at the bottom

def status_text():
    """(text, icon, progress 0-1 or None) of what is going on, the most important first; None when idle."""
    from . import krea
    from .. import core
    if krea.busy():
        words = {"upload": "Krea: uploading", "working": "Krea is working", "download": "Krea: downloading"}
        return "%s · %d s" % (words.get(krea.job["state"], "Krea"), time.monotonic() - krea.job["started"]), \
            'BRUSHES_ALL', None
    for tab in ('MOCKUP', 'FINISH', 'QUEUE'):
        try:
            status, reply = core.studio_reply(tab)
        except Exception:
            status, reply = "", ""
        if status == "working":
            return "Claude: %s" % (reply[:70] or "working…"), 'TIME', None
    if krea.job.get("state") == "error":
        return "Krea: %s" % krea.job["text"][:70], 'ERROR', None
    return None


def draw_status(self, context):
    """Blender's status bar: Claude and Krea at one fixed place (renders show there by themselves)."""
    found = status_text()
    if not found:
        return
    text, icon, _progress = found
    row = self.layout.row(align=True)
    row.label(text=text, icon=icon, translate=False)


def _tick():
    """While something runs, the status bar redraws once a second (its seconds count)."""
    if status_text():
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'STATUSBAR':
                    area.tag_redraw()
    return 1.0


# ------------------------------------------------------------------ one look for all good pictures

class STUDIO_OT_edit_apply_good(Operator):
    bl_idname = "studio.edit_apply_good"
    bl_label = "To All Good"
    bl_description = ("These light and colour settings on every picture marked good (✓): each saved as …_edit_01.png "
                      "next to it, the renders stay as they are")
    bl_options = {'INTERNAL'}

    def execute(self, context):
        from . import image_edit, render_review, runner
        good = [p for p in runner.queue_images(context.scene, limit=400)
                if render_review.rating(p) >= render_review.GOOD and not render_review.refined_kind(p)]
        if not good:
            self.report({'WARNING'}, "Mark the good pictures with ✓ first (Sort, or the key 1)")
            return {'CANCELLED'}
        done = 0
        for path in good:
            try:
                image_edit.save(context.scene, path)
                done += 1
            except (ValueError, OSError, RuntimeError) as ex:
                print("AI Studio: look on %s: %s" % (path, ex))
        runner._images_cache["time"] = 0.0
        self.report({'INFO'}, "The look on %d good pictures (…_edit_01.png)" % done)
        return {'FINISHED'}


classes = (STUDIO_OT_photo_version, STUDIO_OT_photo_good, STUDIO_OT_krea_again, STUDIO_OT_photo_key,
           STUDIO_OT_edit_apply_good)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.STATUSBAR_HT_header.append(draw_status)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=1.0, persistent=True)
    try:
        register_keymaps()
    except (AttributeError, RuntimeError) as ex:
        print("AI Studio: photo keys:", ex)


def unregister():
    unregister_keymaps()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    try:
        bpy.types.STATUSBAR_HT_header.remove(draw_status)
    except (ValueError, RuntimeError):
        pass
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

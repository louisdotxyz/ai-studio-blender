# SPDX-License-Identifier: GPL-3.0-or-later
"""The Photo workspace: one big picture and one sidebar.

- The Image Editor always shows what is picked in its Studio sidebar, big: the render (with the Adjust
  sliders: its adjusted copy), or – while A and B are picked – both in ONE picture: a wipe (a line you move
  with a slider, A left of it, B right) or side by side.
- tidy(): the workspace named "Photo" is set up by itself, once: only the Image Editor with its sidebar
  stays (Blender's Properties and the timeline are not needed to work on pictures). The compositor comes
  and goes with the Look button (toggle_compositor).
- export(): finished pictures as JPEG or PNG at a chosen size into an "Export" folder.
Blender's data is only changed in timers and operators, never while a panel draws."""

import os

import bpy
from bpy.props import EnumProperty, FloatProperty
from bpy.types import Operator

COMPARE_IMAGE = "Studio Compare"
LAYOUT_KEY = "studio_photo_layout"      # on the workspace: set up once
LONGEST = 2400                          # the compare picture's longest side
_want = {"key": None}                   # what the big picture should show (set while the panel draws)
_shown = {"key": None}
_cache = {"pair": None, "a": None, "b": None}


# ------------------------------------------------------------------ the big picture

def _editors(window):
    for area in window.screen.areas:
        if area.type == 'IMAGE_EDITOR':
            yield area, area.spaces.active


def want(context, path, a="", b=""):
    """(while drawing) The big picture: A and B compared when both are picked, else `path`."""
    wm = context.window_manager
    if a and b and wm.studio_compare != 'OFF':
        key = ("compare", a, b, wm.studio_compare, round(wm.studio_compare_split, 3))
    else:
        key = ("file", path)
    _want["key"] = key
    if key != _shown["key"] and not bpy.app.timers.is_registered(_apply):
        bpy.app.timers.register(_apply, first_interval=0.0)


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _compare_image(a, b, mode, split):
    import numpy as np
    from . import image_edit, mockup_image
    pair = (a, _mtime(a), b, _mtime(b))
    if _cache["pair"] != pair:
        pa, (wa, ha), _f = image_edit._read(a, LONGEST)
        pb, (wb, hb), _f = image_edit._read(b, LONGEST)
        first, second = pa.reshape(ha, wa, 4), pb.reshape(hb, wb, 4)
        if (wb, hb) != (wa, ha):
            second = mockup_image.resize(second, wa, ha)
        _cache.update(pair=pair, a=first, b=second)
    first, second = _cache["a"], _cache["b"]
    h, w = first.shape[:2]
    if mode == 'SIDE':
        gap = max(2, w // 200)
        out = np.zeros((h, 2 * w + gap, 4), dtype=np.float32)
        out[:, :w], out[:, w + gap:] = first, second
        out[..., 3] = 1.0
    else:
        x = int(round(max(0.0, min(1.0, split)) * w))
        out = first.copy()
        out[:, x:] = second[:, x:]
        line = max(1, w // 900)
        out[:, max(0, x - line):min(w, x + line)] = (1.0, 1.0, 1.0, 1.0)
    image = bpy.data.images.get(COMPARE_IMAGE)
    if image is None or tuple(image.size) != (out.shape[1], out.shape[0]):
        if image is not None:
            bpy.data.images.remove(image)
        image = bpy.data.images.new(COMPARE_IMAGE, out.shape[1], out.shape[0], alpha=False)
    image.pixels.foreach_set(out.reshape(-1))
    image.update()
    return image


def _apply():
    key = _want["key"]
    if key is None or key == _shown["key"]:
        return None
    image = None
    try:
        if key[0] == "compare":
            image = _compare_image(key[1], key[2], key[3], key[4])
        elif key[1] and os.path.isfile(key[1]):
            image = bpy.data.images.load(key[1], check_existing=True)
            if _shown.get("mtime") != (key[1], _mtime(key[1])):
                image.reload()                       # the same file written again (a montage, a new render)
                _shown["mtime"] = (key[1], _mtime(key[1]))
    except (RuntimeError, OSError, ValueError) as ex:
        print("AI Studio: Photo view:", ex)
    _shown["key"] = key
    if image is None:
        return None
    wm = bpy.context.window_manager
    for window in (wm.windows if wm else ()):
        if window.screen.is_temporary:
            continue
        for area, space in _editors(window):
            if space.image != image:
                space.image = image
            area.tag_redraw()
    old = bpy.data.images.get(_shown.get("temp") or "")
    _shown["temp"] = image.name if os.sep + "finish" + os.sep in image.filepath else ""
    if old is not None and old != image and old.name != COMPARE_IMAGE:   # the adjusted copy before this one
        old.user_clear()
        bpy.data.images.remove(old)
    return None


def _split_changed(_self, _context):
    wm = bpy.context.window_manager
    for window in (wm.windows if wm else ()):
        for area in window.screen.areas:
            area.tag_redraw()


# ------------------------------------------------------------------ the workspace

def _override(window, area):
    region = next((r for r in area.regions if r.type == 'WINDOW'), area.regions[0])
    return bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region)


def tidy(window):
    """Only the Image Editor (with its sidebar) stays in the window's screen. Returns how many editors went."""
    screen = window.screen
    closed = 0
    for _round in range(16):
        images = [a for a in screen.areas if a.type == 'IMAGE_EDITOR']
        if not images:
            biggest = max(screen.areas, key=lambda a: a.width * a.height)
            biggest.type = 'IMAGE_EDITOR'
            continue
        keep = max(images, key=lambda a: a.width * a.height)
        others = [a for a in screen.areas if a != keep]
        if not others:
            break
        done = False
        for area in others:
            try:
                with _override(window, area):
                    if bpy.ops.screen.area_close.poll():
                        bpy.ops.screen.area_close()
                        done = True
            except RuntimeError:
                done = False
            if done:
                closed += 1
                break
        if not done:
            break
    for _area, space in _editors(window):
        space.show_region_ui = True
        space.ui_mode = 'VIEW'
    return closed


def _auto_tidy():
    wm = bpy.context.window_manager
    for window in (wm.windows if wm else ()):
        space = window.workspace
        if space is not None and space.name == "Photo" and not space.get(LAYOUT_KEY):
            space[LAYOUT_KEY] = 1                       # once: what you change afterwards stays
            try:
                tidy(window)
            except Exception as ex:
                print("AI Studio: Photo workspace:", ex)
    return None


def check_layout(context):
    """(while drawing) The Photo workspace is set up once, right after this redraw."""
    space = context.workspace
    if space is not None and space.name == "Photo" and not space.get(LAYOUT_KEY) \
            and not bpy.app.timers.is_registered(_auto_tidy):
        bpy.app.timers.register(_auto_tidy, first_interval=0.1)


def compositor_open(screen):
    return any(a.type == 'NODE_EDITOR' for a in screen.areas)


def toggle_compositor(window):
    """The compositor under the picture (the look's nodes), or away again."""
    screen = window.screen
    nodes = [a for a in screen.areas if a.type == 'NODE_EDITOR']
    if nodes:
        for area in nodes:
            with _override(window, area):
                if bpy.ops.screen.area_close.poll():
                    bpy.ops.screen.area_close()
        return False
    images = [a for a in screen.areas if a.type == 'IMAGE_EDITOR']
    if not images:
        return False
    keep = max(images, key=lambda a: a.width * a.height)
    before = {a.as_pointer() for a in screen.areas}
    with _override(window, keep):
        bpy.ops.screen.area_split(direction='HORIZONTAL', factor=0.38)
    new = next((a for a in screen.areas if a.as_pointer() not in before), None)
    if new is None:
        return False
    low = min((new, keep), key=lambda a: a.y)
    high = keep if low == new else new
    high.type = 'IMAGE_EDITOR'
    low.type = 'NODE_EDITOR'
    low.ui_type = 'CompositorNodeTree'
    for area, space in ((high, high.spaces.active), (low, low.spaces.active)):
        space.show_region_ui = area == high             # the one sidebar stays the picture's
    return True


class STUDIO_OT_photo_layout(Operator):
    bl_idname = "studio.photo_layout"
    bl_label = "Picture and Sidebar Only"
    bl_options = {'INTERNAL'}

    action: EnumProperty(items=[('TIDY', "Tidy", ""), ('LOOK', "Look", "")], default='TIDY',
                         options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        if props.action == 'LOOK':
            return "Show or hide the compositor under the picture (the look's nodes, every effect and its values)"
        return ("Set this workspace up for working on pictures: only the big picture and this sidebar "
                "(Blender's Properties, the timeline and other editors close)")

    def execute(self, context):
        if self.action == 'LOOK':
            toggle_compositor(context.window)
        else:
            tidy(context.window)
            if context.workspace is not None:
                context.workspace[LAYOUT_KEY] = 1
        return {'FINISHED'}


# ------------------------------------------------------------------ compare

class STUDIO_OT_photo_compare(Operator):
    bl_idname = "studio.photo_compare"
    bl_label = "Compare with the One Before"
    bl_description = "This picture as B and the one before it as A: both in the big picture, with a line to move"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        from . import runner, tab_queue
        images = runner.queue_images(context.scene)
        path = tab_queue._selected_image(images)
        if not path or path not in images or images.index(path) + 1 >= len(images):
            self.report({'WARNING'}, "There is no picture before this one")
            return {'CANCELLED'}
        tab_queue._ab.update(A=images[images.index(path) + 1], B=path)
        if context.window_manager.studio_compare == 'OFF':
            context.window_manager.studio_compare = 'WIPE'
        return {'FINISHED'}


def draw_compare(layout, context, a, b):
    """A / B in the big picture: Wipe | Side by Side | Off, the line's place, swap and clear."""
    from . import tab_queue
    wm = context.window_manager
    if not (a and b):
        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator(STUDIO_OT_photo_compare.bl_idname, icon='IMAGE_REFERENCE')
        hint = layout.row()
        hint.active = False
        hint.label(text="or pick A and B on any two pictures", icon='INFO')
        return
    row = layout.row(align=True)
    row.prop(wm, "studio_compare", expand=True)
    row.operator(tab_queue.STUDIO_OT_render_ab.bl_idname, text="", icon='ARROW_LEFTRIGHT').action = "SWAP"
    row.operator(tab_queue.STUDIO_OT_render_ab.bl_idname, text="", icon='X').action = "CLEAR"
    if wm.studio_compare == 'WIPE':
        layout.prop(wm, "studio_compare_split", slider=True)
    names = layout.row()
    names.active = False
    names.label(text="A  %s" % tab_queue._title(a), translate=False)
    names.label(text="B  %s" % tab_queue._title(b), translate=False)


# ------------------------------------------------------------------ export

SIZES = [('ORIGINAL', "Original", "As it is"), ('3840', "4K", "Longest side 3840 px"),
         ('2560', "2.5K", "Longest side 2560 px"), ('1920', "HD", "Longest side 1920 px")]


def export(paths, fmt='JPEG', size='ORIGINAL', quality=92):
    """Copies of the pictures as JPEG or PNG, the longest side at most `size`, into "Export" next to them.
    Returns the files written."""
    written = []
    for path in paths:
        if not path or not os.path.isfile(path):
            continue
        folder = os.path.join(os.path.dirname(path), "Export")
        os.makedirs(folder, exist_ok=True)
        stem = os.path.splitext(os.path.basename(path))[0]
        target = os.path.join(folder, stem + (".jpg" if fmt == 'JPEG' else ".png"))
        image = bpy.data.images.load(path, check_existing=False)
        try:
            w, h = image.size
            if size != 'ORIGINAL' and max(w, h) > int(size):
                f = int(size) / max(w, h)
                image.scale(max(1, round(w * f)), max(1, round(h * f)))
            image.file_format = fmt
            image.save(filepath=target, quality=quality)
            written.append(target)
        finally:
            bpy.data.images.remove(image)
    return written


class STUDIO_OT_photo_export(Operator):
    bl_idname = "studio.photo_export"
    bl_label = "Export"
    bl_description = ("Save finished pictures for presentations and the portfolio: JPEG or PNG at the chosen "
                      "size, into the folder \"Export\" next to them (the renders stay as they are)")
    bl_options = {'INTERNAL'}

    which: EnumProperty(items=[('THIS', "This Picture", ""), ('STARS', "Rated", "")], default='THIS',
                        options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        from . import render_review, runner, tab_queue
        wm = context.window_manager
        images = runner.queue_images(context.scene, limit=400)
        if self.which == 'THIS':
            paths = [tab_queue._selected_image(images)]
        else:
            paths = [p for p in images if render_review.rating(p) >= int(wm.studio_export_stars)]
        try:
            written = export(paths, wm.studio_export_format, wm.studio_export_size)
        except (OSError, RuntimeError) as ex:
            self.report({'ERROR'}, "Export: %s" % ex)
            return {'CANCELLED'}
        if not written:
            self.report({'WARNING'}, "Nothing to export")
            return {'CANCELLED'}
        self.report({'INFO'}, "%d pictures in %s" % (len(written), os.path.dirname(written[0])))
        bpy.ops.studio.render_reveal(path=written[0])
        return {'FINISHED'}


def draw_export(layout, context, path):
    wm = context.window_manager
    row = layout.row(align=True)
    row.prop(wm, "studio_export_format", expand=True)
    layout.row(align=True).prop(wm, "studio_export_size", expand=True)
    row = layout.row(align=True)
    row.scale_y = 1.2
    sub = row.row(align=True)
    sub.enabled = bool(path)
    sub.operator(STUDIO_OT_photo_export.bl_idname, text="This Picture", icon='EXPORT').which = 'THIS'
    row.operator(STUDIO_OT_photo_export.bl_idname, text="All Rated", icon='SOLO_ON').which = 'STARS'
    row.prop(wm, "studio_export_stars", text="")


classes = (STUDIO_OT_photo_layout, STUDIO_OT_photo_compare, STUDIO_OT_photo_export)


def register():
    wm = bpy.types.WindowManager
    wm.studio_compare = EnumProperty(
        name="Compare", options={'SKIP_SAVE'}, default='WIPE', update=_split_changed,
        items=[('WIPE', "Wipe", "A and B in one picture: A left of the line, B right of it"),
               ('SIDE', "Side by Side", "A and B next to each other"),
               ('OFF', "Off", "Show the picked picture, keep A and B")])
    wm.studio_compare_split = FloatProperty(
        name="Line", description="Where the line between A and B is", default=0.5, min=0.0, max=1.0,
        subtype='FACTOR', options={'SKIP_SAVE'}, update=_split_changed)
    wm.studio_export_format = EnumProperty(
        name="Format", options={'SKIP_SAVE'}, default='JPEG',
        items=[('JPEG', "JPG", "Small files, for slides, the web and e-mail"), ('PNG', "PNG", "Lossless, with alpha")])
    wm.studio_export_size = EnumProperty(name="Size", options={'SKIP_SAVE'}, default='ORIGINAL', items=SIZES)
    wm.studio_export_stars = EnumProperty(
        name="Stars", options={'SKIP_SAVE'}, default='3', description="Export the pictures with at least so many stars",
        items=[(str(n), "%d+" % n, "At least %d stars" % n) for n in range(1, 6)])
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for timer in (_apply, _auto_tidy):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    wm = bpy.types.WindowManager
    for name in ("studio_compare", "studio_compare_split", "studio_export_format", "studio_export_size",
                 "studio_export_stars"):
        delattr(wm, name)

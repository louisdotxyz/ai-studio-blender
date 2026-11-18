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
        elif key[1] and os.path.isfile(key[1]) and os.sep + "finish" + os.sep in key[1]:
            image = _adjusted_image(key[1])        # one image for the adjusted copy, named after the render
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
            fit(window, area)                        # the whole picture, as big as the editor allows
            area.tag_redraw()
    old = bpy.data.images.get(_shown.get("temp") or "")
    _shown["temp"] = image.name if os.sep + "finish" + os.sep in image.filepath else ""
    if old is not None and old != image and old.name != COMPARE_IMAGE and old.users == 0 \
            and not old.get(ADJUSTED_KEY):
        bpy.data.images.remove(old)       # the adjusted copy before this one, only once nothing shows it
        # (user_clear() removed it under editors and renders that still used it: a crash while adjusting)
    return None


ADJUSTED_KEY = "studio_adjusted"


def _adjusted_image(path):
    """The adjusted copy of a render: always the same image, its file swapped (no preview_123_45.png images
    piling up in the editor's list), named after the render it shows."""
    from . import image_edit
    image = next((i for i in bpy.data.images if i.get(ADJUSTED_KEY)), None)
    if image is None:
        image = bpy.data.images.load(path, check_existing=False)
        image[ADJUSTED_KEY] = True
    else:
        image.filepath = path
        image.reload()
    source = image_edit._cache.get("source") or path
    name = "%s (adjusted)" % os.path.splitext(os.path.basename(source))[0]
    if image.name != name:
        image.name = name
    return image


def fit(window, area):
    """The picture fills the editor (Blender would show it at 100 %: a 4K render far too big)."""
    try:
        with _override(window, area):
            if bpy.ops.image.view_all.poll():
                bpy.ops.image.view_all(fit_view=True)
            _fit_free(area)
    except (RuntimeError, TypeError) as ex:
        print("AI Studio: Photo view fit:", ex)


def _fit_free(area):
    """view_all fills the whole editor, under the sidebar too: the picture goes into the part the sidebar
    leaves free, centered there, so it is never covered."""
    space = area.spaces.active
    image = getattr(space, "image", None)
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    ui = next((r for r in area.regions if r.type == 'UI' and r.width > 1), None)
    if image is None or region is None or ui is None or not image.size[0] or not image.size[1]:
        return
    margin = 0.94
    free_w = max(50, region.width - ui.width)
    w, h = image.size
    zoom = margin * min(free_w / w, region.height / h)
    bpy.ops.image.view_zoom_ratio(ratio=zoom)
    v2d = region.view2d

    def center_x():
        return (v2d.view_to_region(0.0, 0.5, clip=False)[0] + v2d.view_to_region(1.0, 0.5, clip=False)[0]) / 2.0

    sidebar_right = ui.x > region.x + region.width / 2
    edge = 0.03 * min(free_w, region.height)               # left-aligned, a small margin
    target = (0 if sidebar_right else ui.width) + edge + zoom * w / 2.0
    before = center_x()
    bpy.ops.image.view_pan(offset=(10.0, 0.0))         # one known step: how many pixels is it?
    step = center_x() - before
    if abs(step) > 1e-6:
        bpy.ops.image.view_pan(offset=(10.0 * (target - center_x()) / step, 0.0))


class STUDIO_OT_photo_fit(Operator):
    bl_idname = "studio.photo_fit"
    bl_label = "Fit"
    bl_description = "Show the whole picture, as big as it fits"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        for area, _space in _editors(context.window):
            fit(context.window, area)
        return {'FINISHED'}


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
            with _override(window, biggest):
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
    for area, space in list(_editors(window)):
        with _override(window, area):               # from a timer there is no window: Blender needs one here
            if not space.show_region_ui:
                space.show_region_ui = True
            if space.ui_mode != 'VIEW':
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
    with _override(window, high):
        high.type = 'IMAGE_EDITOR'
    with _override(window, low):
        low.type = 'NODE_EDITOR'
        low.ui_type = 'CompositorNodeTree'
    for area in (high, low):
        with _override(window, area):
            area.spaces.active.show_region_ui = area == high      # the one sidebar stays the picture's
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


# ------------------------------------------------------------------ notes: draw, write, send to Claude

ANNOTATE = "builtin.annotate"
_send = {"step": 0, "path": "", "shot": "", "note": "", "area": 0, "keep": None}


def _picture_area(window):
    areas = [a for a in window.screen.areas if a.type == 'IMAGE_EDITOR']
    return max(areas, key=lambda a: a.width * a.height) if areas else None


def drawing(context):
    """Is the annotate pen the active tool of the picture?"""
    try:
        tool = context.workspace.tools.from_space_image_mode('VIEW', create=False)
        return tool is not None and tool.idname == ANNOTATE
    except (AttributeError, TypeError):
        return False


def marks(window):
    """The annotation of the picture (or None) and how many strokes it has."""
    area = _picture_area(window)
    data = getattr(area.spaces.active, "annotation", None) if area else None
    count = 0
    if data is not None:
        for layer in data.layers:
            for frame in layer.frames:
                count += len(frame.strokes)
    return data, count


def marks_box(window, pad=0.04):
    """The box around the marks on the picture: (x, y from the TOP, width, height), 0-1 of the picture
    (Krea's regions); None without marks. Marks are stored in the picture's own coordinates (0-1)."""
    data, count = marks(window)
    if not count:
        return None
    xs, ys = [], []
    for layer in data.layers:
        for frame in layer.frames:
            for stroke in frame.strokes:
                for point in stroke.points:
                    xs.append(point.co[0])
                    ys.append(point.co[1])
    if not xs:
        return None
    x0, x1 = max(0.0, min(xs) - pad), min(1.0, max(xs) + pad)
    y0, y1 = max(0.0, min(ys) - pad), min(1.0, max(ys) + pad)
    if x1 - x0 < 0.01 or y1 - y0 < 0.01:
        return None
    return tuple(round(v, 4) for v in (x0, 1.0 - y1, x1 - x0, y1 - y0))


def clear_marks(window):
    data, _count = marks(window)
    if data is not None:
        bpy.data.annotations.remove(data)


class STUDIO_OT_photo_draw(Operator):
    bl_idname = "studio.photo_draw"
    bl_label = "Draw"
    bl_description = "Draw on the picture (marks for Claude); again to stop drawing"
    bl_options = {'INTERNAL'}

    action: EnumProperty(items=[('PEN', "Pen", ""), ('UNDO', "Undo", ""), ('CLEAR', "Clear", "")])

    def execute(self, context):
        window = context.window
        area = _picture_area(window)
        if area is None:
            return {'CANCELLED'}
        if self.action == 'CLEAR':
            clear_marks(window)
        elif self.action == 'UNDO':
            data, _count = marks(window)
            for layer in reversed(list(data.layers) if data else []):
                frame = layer.active_frame or (layer.frames[-1] if len(layer.frames) else None)
                if frame is not None and len(frame.strokes):
                    frame.strokes.remove(frame.strokes[-1])
                    break
        else:
            space = area.spaces.active
            if space.ui_mode != 'VIEW':
                space.ui_mode = 'VIEW'
            ts = context.scene.tool_settings
            if hasattr(ts, "annotation_stroke_placement_image_editor"):
                ts.annotation_stroke_placement_image_editor = 'IMAGE'    # marks stay on the picture
            with _override(window, area):
                # off: back to the Image Editor's own tool of the view mode (there is no Select Box there,
                # so "Stop Drawing" did nothing before)
                bpy.ops.wm.tool_set_by_id(name="builtin.sample" if drawing(context) else ANNOTATE,
                                          space_type='IMAGE_EDITOR')
        area.tag_redraw()
        return {'FINISHED'}


def _restore(window, area):
    keep = _send.get("keep")
    if keep and area is not None:
        space = area.spaces.active
        space.show_region_ui = keep[0]
        if hasattr(space, "show_gizmo"):
            space.show_gizmo = keep[1]
        fit(window, area)
    _send["keep"] = None


def _find_area(pointer):
    wm = bpy.context.window_manager
    for window in (wm.windows if wm else ()):
        for area in window.screen.areas:
            if area.as_pointer() == pointer:
                return window, area
    return None, None


def send_text(path, note, marked):
    """The message for Claude: which picture, the note, and that the marks are in the attached picture."""
    from . import tab_queue
    name = tab_queue._title(path) if path else "the picture"
    parts = ["Feedback on the render %s (%s)." % (name, path) if path else "Feedback on the picture."]
    if marked:
        parts.append("The attached picture shows it with my marks drawn on it.")
    if note.strip():
        parts.append("My note: " + note.strip())
    parts.append("Change the scene accordingly (only what I marked or wrote), then render it again with the "
                 "same camera and show me the result.")
    return " ".join(parts)


def _send_step():
    """1: fit the picture without the sidebar · 2: the screenshot (picture with the marks) · 3: to the chat."""
    window, area = _find_area(_send["area"]) if _send["area"] else (bpy.context.window, None)
    if area is None and _send["step"] != 2:
        _send["step"] = 0
        return None
    if _send["step"] == 1:
        fit(window, area)
        area.tag_redraw()
        _send["step"] = 2
        return 0.3
    shot = ""
    if area is not None:                         # with marks: the picture as it is shown, marks on it
        try:
            os.makedirs(os.path.dirname(_send["shot"]), exist_ok=True)
            with _override(window, area):
                bpy.ops.screen.screenshot_area(filepath=_send["shot"])
            shot = _send["shot"] if os.path.isfile(_send["shot"]) else ""
        except (RuntimeError, TypeError) as ex:
            print("AI Studio: screenshot of the picture:", ex)
        _restore(window, area)
    _data, count = marks(window) if window is not None else (None, 0)
    try:
        from .. import core, ui_menus
        attach = shot or (_send["path"] if os.path.isfile(_send["path"]) else "")
        if attach:
            core.add_attachment(attach)
        wm = bpy.context.window_manager
        wm.claude_prompt = send_text(_send["path"], _send["note"], bool(shot and count))
        _send["sending"] = True                  # our own message: not caught by chat_send again
        try:
            ui_menus._send_prompt(wm)
        finally:
            _send["sending"] = False
        wm.studio_photo_note = ""
        if window is not None:
            clear_marks(window)
    except Exception as ex:
        print("AI Studio: send to Claude:", ex)
    _send["step"] = 0
    return None


def chat_send(context, text):
    """A message typed in the chat of the Photo workspace's Adjust tab: the picture goes with it by itself
    (with the marks drawn on it when there are some). True when it was taken over."""
    wm = context.window_manager
    workspace = getattr(context, "workspace", None)
    if _send.get("sending") or _send["step"] or getattr(wm, "studio_photo_tab", "") != 'ADJUST':
        return False
    from . import runner, tab_queue
    if workspace is None or workspace.name != tab_queue.photo_workspace():
        return False
    path = tab_queue._selected_image(runner.queue_images(context.scene))
    if not path and not text.strip():
        return False
    window = context.window
    area = _picture_area(window)
    import time
    folder = os.path.join(os.path.dirname(path), "Notes") if path else bpy.app.tempdir
    stem = os.path.splitext(os.path.basename(path))[0] if path else "picture"
    _send.update(step=1, path=path or "", note=text, area=area.as_pointer() if area else 0,
                 shot=os.path.join(folder, "%s_note_%s.png" % (stem, time.strftime("%H%M%S"))))
    _data, count = marks(window)
    if area is None or not count:
        _send["step"] = 2                        # no marks: the render itself goes with the message
        if area is not None:
            _send["area"] = 0
    else:
        space = area.spaces.active
        _send["keep"] = (space.show_region_ui, getattr(space, "show_gizmo", True))
        space.show_region_ui = False             # only the picture in the screenshot
        if hasattr(space, "show_gizmo"):
            space.show_gizmo = False
        if drawing(context):
            with _override(window, area):
                bpy.ops.wm.tool_set_by_id(name="builtin.sample", space_type='IMAGE_EDITOR')
    bpy.app.timers.register(_send_step, first_interval=0.25)
    return True


class STUDIO_OT_photo_send(Operator):
    bl_idname = "studio.photo_send"
    bl_label = "Send to Claude"
    bl_description = ("The picture with your marks and your note go to the chat: Claude changes the scene and "
                      "renders it again")
    bl_options = {'INTERNAL'}

    path: bpy.props.StringProperty()

    def execute(self, context):
        window = context.window
        area = _picture_area(window)
        _data, count = marks(window)
        note = context.window_manager.studio_photo_note
        if not count and not note.strip():
            self.report({'WARNING'}, "Draw on the picture or write a note first")
            return {'CANCELLED'}
        if _send["step"]:
            return {'CANCELLED'}
        import time
        folder = os.path.join(os.path.dirname(self.path), "Notes") if self.path else bpy.app.tempdir
        stem = os.path.splitext(os.path.basename(self.path))[0] if self.path else "picture"
        _send.update(step=1, path=self.path, note=note, area=area.as_pointer() if area else 0,
                     shot=os.path.join(folder, "%s_note_%s.png" % (stem, time.strftime("%H%M%S"))))
        if area is None:
            _send["step"] = 2
        else:
            space = area.spaces.active
            _send["keep"] = (space.show_region_ui, getattr(space, "show_gizmo", True))
            space.show_region_ui = False                 # only the picture in the screenshot
            if hasattr(space, "show_gizmo"):
                space.show_gizmo = False
            if drawing(context):
                with _override(window, area):
                    bpy.ops.wm.tool_set_by_id(name="builtin.sample", space_type='IMAGE_EDITOR')
        bpy.app.timers.register(_send_step, first_interval=0.25)
        self.report({'INFO'}, "Sent to Claude (see the Claude tab)")
        return {'FINISHED'}


def draw_adjust_bar(layout, context, path, send=True):
    """Adjust: one bar of icons – [✎ draw] [undo] [clear] [color] [send the marks] – the chat below."""
    _data, count = marks(context.window)
    row = layout.row(align=True)
    row.scale_y = 1.3
    on = drawing(context)
    row.operator(STUDIO_OT_photo_draw.bl_idname, text="", icon='GREASEPENCIL', depress=on).action = 'PEN'
    sub = row.row(align=True)
    sub.enabled = count > 0
    sub.operator(STUDIO_OT_photo_draw.bl_idname, text="", icon='LOOP_BACK').action = 'UNDO'
    sub.operator(STUDIO_OT_photo_draw.bl_idname, text="", icon='TRASH').action = 'CLEAR'
    data, _c = marks(context.window)
    layer = data.layers.active if data is not None and len(data.layers) else None
    if layer is not None:
        color = row.row(align=True)
        color.ui_units_x = 2
        color.prop(layer, "color", text="")
    info = row.row(align=True)
    info.active = False
    info.label(text="%d marks" % count if count else ("Draw on the picture" if on else ""))
    if send:
        button = row.row(align=True)
        button.enabled = count > 0 and not _send["step"]
        button.operator(STUDIO_OT_photo_send.bl_idname, text="", icon='EXPORT').path = path or ""


def draw_notes(layout, context, path):
    """Adjust: draw on the picture, write a note, send both to Claude."""
    wm = context.window_manager
    _data, count = marks(context.window)
    box = layout.box()
    col = box.column(align=True)
    row = col.row(align=True)
    row.scale_y = 1.4
    on = drawing(context)
    row.operator(STUDIO_OT_photo_draw.bl_idname, text="Stop Drawing" if on else "Draw on the Picture",
                 icon='GREASEPENCIL', depress=on).action = 'PEN'
    sub = row.row(align=True)
    sub.enabled = count > 0
    sub.operator(STUDIO_OT_photo_draw.bl_idname, text="", icon='LOOP_BACK').action = 'UNDO'
    sub.operator(STUDIO_OT_photo_draw.bl_idname, text="", icon='TRASH').action = 'CLEAR'
    data, _c = marks(context.window)
    layer = data.layers.active if data is not None and len(data.layers) else None
    if layer is not None:
        row = col.row(align=True)
        row.prop(layer, "color", text="")
        row.prop(layer, "thickness", text="Line")
    hint = box.row()
    hint.active = False
    hint.label(text="%d marks · drag on the picture" % count if count else "Circle what should change",
               icon='INFO')
    try:
        box.prop(wm, "studio_photo_note", text="", icon='TEXT', placeholder="What should change?")
    except TypeError:
        box.prop(wm, "studio_photo_note", text="", icon='TEXT')
    row = box.row(align=True)
    row.scale_y = 1.5
    row.enabled = bool(count or wm.studio_photo_note.strip()) and not _send["step"]
    row.operator(STUDIO_OT_photo_send.bl_idname, icon='EXPORT').path = path or ""


# ------------------------------------------------------------------ camera: in Blender, then a new preview

CAMERA_MOVES = (('CLOSER', "Closer", "The camera nearer to the product (15 %)", 'ZOOM_IN'),
                ('WIDER', "Wider", "The camera further from the product (15 %)", 'ZOOM_OUT'),
                ('HIGHER', "Higher", "The camera higher around the product, looking down a little more (8°)", 'TRIA_UP'),
                ('LOWER', "Lower", "The camera lower around the product, looking up a little more (8°)", 'TRIA_DOWN'),
                ('LEFT', "Left", "The camera around the product to the left (10°)", 'TRIA_LEFT'),
                ('RIGHT', "Right", "The camera around the product to the right (10°)", 'TRIA_RIGHT'))


def product_center(scene):
    from . import krea_scene
    from mathutils import Vector
    bounds = krea_scene.product_bounds(scene)
    return (bounds[0] + bounds[1]) / 2 if bounds else Vector((0.0, 0.0, 0.0))


def move_camera(camera, center, move):
    """Moves the real camera (Blender, not Krea): nearer / further along its view to the product, or around
    the product up, down, left, right, still looking at the same point of it."""
    from math import radians
    from mathutils import Matrix, Vector
    from . import orbit
    world = camera.matrix_world.copy()
    pos = world.translation.copy()
    forward = -(world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    # the point of the product the camera looks at (nearest point on its view line to the product's middle)
    aim = pos + forward * max(0.05, (center - pos).dot(forward))
    offset = pos - aim
    if move in {'CLOSER', 'WIDER'}:
        moved = offset * (0.85 if move == 'CLOSER' else 1.0 / 0.85)
        matrix = Matrix.Translation(moved - offset) @ world
    else:
        if move in {'HIGHER', 'LOWER'}:
            axis = (world.to_3x3() @ Vector((1.0, 0.0, 0.0))).normalized()
            angle = radians(8.0)
            if ((Matrix.Rotation(angle, 3, axis) @ offset).z > offset.z) != (move == 'HIGHER'):
                angle = -angle
        else:
            axis = Vector((0.0, 0.0, 1.0))
            angle = radians(10.0) * (-1 if move == 'LEFT' else 1)
        moved = Matrix.Rotation(angle, 3, axis) @ offset
        if abs(moved.normalized().z) > 0.985:            # never straight over or under the product
            return False
        # turned around the point it looks at: the framing and the camera's own roll stay
        matrix = Matrix.Translation(aim) @ Matrix.Rotation(angle, 4, axis) @ Matrix.Translation(-aim) @ world
    orbit.put(camera, matrix, moved.length)
    return True


class STUDIO_OT_photo_camera(Operator):
    bl_idname = "studio.photo_camera"
    bl_label = "Camera"
    bl_description = "Moves this render's camera in Blender (the product stays exactly yours) and renders a preview"
    bl_options = {'REGISTER', 'UNDO'}

    move: EnumProperty(items=[(k, n, d, i, n_) for n_, (k, n, d, i) in enumerate(CAMERA_MOVES)])
    path: bpy.props.StringProperty()

    @classmethod
    def description(cls, _context, props):
        return next((d for k, _n, d, _i in CAMERA_MOVES if k == props.move), cls.bl_description) + \
            ", then a new preview of it"

    def execute(self, context):
        from . import krea_scene
        scene = context.scene
        camera = krea_scene.render_camera(scene, self.path) if self.path else scene.camera
        if camera is None:
            self.report({'ERROR'}, "No camera for this picture")
            return {'CANCELLED'}
        move_camera(camera, product_center(scene), self.move)
        try:
            bpy.ops.studio.add(camera=camera.name, quality='LOW')
        except RuntimeError as ex:
            self.report({'WARNING'}, "Camera moved; render: %s" % ex)
        return {'FINISHED'}


def draw_adjust(layout, context, path):
    """Adjust: [In Blender | With Krea], marks on the picture, the camera (Blender), and for Krea its few
    settings. In Blender the chat below takes what you write; with Krea the marks are where it may change."""
    wm = context.window_manager
    row = layout.row(align=True)
    row.scale_y = 1.2
    row.prop(wm, "studio_adjust_target", expand=True)
    krea_on = wm.studio_adjust_target == 'KREA'
    draw_adjust_bar(layout, context, path, send=not krea_on)
    _data, count = marks(context.window)
    hint = layout.row()
    hint.active = False
    if krea_on:
        hint.label(text="Krea changes only inside your marks" if count else
                   "No marks: Krea makes the whole picture, your product stays", icon='INFO')
    else:
        hint.label(text="Mark it, write below what should change (free)" if not count else
                   "%d marks: write below, or send them ⤴" % count, icon='INFO')
    box = layout.box()
    head = box.row()
    head.label(text="Camera (in Blender, then a preview)", icon='CAMERA_DATA')
    grid = box.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
    for key, label, _d, icon in CAMERA_MOVES:
        op = grid.operator(STUDIO_OT_photo_camera.bl_idname, text=label, icon=icon)
        op.move, op.path = key, path or ""
    if krea_on:
        from . import tab_finish
        tab_finish._draw_krea(layout, context, path)


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


def export_set(paths, folder, size=2560):
    """The good pictures as one set for a presentation: 01_name.jpg, 02_… (longest side `size`) into
    `folder`, and an overview of all of them (00_overview.jpg). Returns the files written."""
    import numpy as np
    from . import image_edit, mockup_image
    os.makedirs(folder, exist_ok=True)
    written, thumbs = [], []
    for number, path in enumerate([p for p in paths if p and os.path.isfile(p)], 1):
        stem = os.path.splitext(os.path.basename(path))[0]
        target = os.path.join(folder, "%02d_%s.jpg" % (number, stem))
        image = bpy.data.images.load(path, check_existing=False)
        try:
            w, h = image.size
            if max(w, h) > size:
                f = size / max(w, h)
                image.scale(max(1, round(w * f)), max(1, round(h * f)))
            image.file_format = 'JPEG'
            image.save(filepath=target, quality=92)
            written.append(target)
        finally:
            bpy.data.images.remove(image)
        pixels, (w, h), _f = image_edit._read(path, 640)
        thumbs.append(pixels.reshape(h, w, 4))
    if thumbs:                                          # the overview: three in a row on dark grey
        cols = min(3, len(thumbs))
        cell_w, cell_h, gap = 640, 400, 24
        rows = (len(thumbs) + cols - 1) // cols
        W, H = cols * cell_w + (cols + 1) * gap, rows * cell_h + (rows + 1) * gap
        sheet = np.full((H, W, 4), 0.03, dtype=np.float32)
        sheet[..., 3] = 1.0
        for i, thumb in enumerate(thumbs):
            h, w = thumb.shape[:2]
            f = min(cell_w / w, cell_h / h)
            small = mockup_image.resize(thumb, max(1, int(w * f)), max(1, int(h * f)))
            r, c = rows - 1 - i // cols, i % cols                 # row 0 at the bottom
            y = gap + r * (cell_h + gap) + (cell_h - small.shape[0]) // 2
            x = gap + c * (cell_w + gap) + (cell_w - small.shape[1]) // 2
            sheet[y:y + small.shape[0], x:x + small.shape[1], :3] = small[..., :3]
        overview = os.path.join(folder, "00_overview.png")
        image_edit._write(sheet.reshape(-1), (W, H), overview)
        written.insert(0, overview)
    return written


class STUDIO_OT_photo_export(Operator):
    bl_idname = "studio.photo_export"
    bl_label = "Export"
    bl_description = ("Save finished pictures for presentations and the portfolio: JPEG or PNG at the chosen "
                      "size, into the folder \"Export\" next to them (the renders stay as they are)")
    bl_options = {'INTERNAL'}

    which: EnumProperty(items=[('THIS', "This Picture", ""), ('STARS', "Rated", ""), ('SET', "Set", "")],
                        default='THIS', options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        if props.which == 'SET':
            return ("The good pictures (✓) as a set for a presentation: numbered JPEGs (2560 px) and an overview, "
                    "in a folder of their own (Export/Set <date>)")
        return cls.bl_description

    def execute(self, context):
        from . import render_review, runner, tab_queue
        wm = context.window_manager
        images = runner.queue_images(context.scene, limit=400)
        if self.which == 'SET':
            import time
            good = [p for p in images if render_review.rating(p) >= render_review.GOOD]
            if not good:
                self.report({'WARNING'}, "Mark the good pictures with ✓ first (Sort)")
                return {'CANCELLED'}
            folder = os.path.join(os.path.dirname(good[0]), "Export", "Set %s" % time.strftime("%Y-%m-%d %H%M"))
            try:
                written = export_set(list(reversed(good)), folder)    # the oldest first: the order of the work
            except (OSError, RuntimeError, ValueError) as ex:
                self.report({'ERROR'}, "Export: %s" % ex)
                return {'CANCELLED'}
            self.report({'INFO'}, "Set of %d pictures in %s" % (len(written) - 1, folder))
            bpy.ops.studio.render_reveal(path=written[0])
            return {'FINISHED'}
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
    row = layout.row(align=True)
    row.scale_y = 1.3
    row.operator(STUDIO_OT_photo_export.bl_idname, text="Presentation Set (✓)", icon='PRESENTATION').which = 'SET'


classes = (STUDIO_OT_photo_layout, STUDIO_OT_photo_compare, STUDIO_OT_photo_export, STUDIO_OT_photo_fit,
           STUDIO_OT_photo_draw, STUDIO_OT_photo_send, STUDIO_OT_photo_camera)


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
    wm.studio_photo_tab = EnumProperty(
        name="Tab", options={'SKIP_SAVE'}, default='SORT',
        items=[('SORT', "Sort", "Rate, sort and compare the renders", 'SORTSIZE', 0),
               ('ADJUST', "Adjust", "Mark on the picture what should change: in Blender (free) or with Krea; "
                "the camera moves in Blender", 'GREASEPENCIL', 1),
               ('LOOK', "Look", "Light and colour of this picture", 'COLOR', 2),
               ('MOCKUP', "Mockup", "Your product into a photo of your own: placed, rendered, blended in",
                'IMAGE_REFERENCE', 3)])
    wm.studio_adjust_target = EnumProperty(
        name="Change", options={'SKIP_SAVE'}, default='BLENDER',
        items=[('BLENDER', "In Blender", "Claude changes the scene and renders again: free, the product stays yours",
                'BLENDER', 0),
               ('KREA', "With Krea", "Krea paints the change (credits): only inside your marks, or the whole picture "
                "with your product put back", 'BRUSHES_ALL', 1)])
    wm.studio_photo_note = bpy.props.StringProperty(
        name="Note", description="What should change (Claude gets it with the picture and your marks)",
        options={'SKIP_SAVE'})
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
    for timer in (_apply, _auto_tidy, _send_step):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    wm = bpy.types.WindowManager
    for name in ("studio_compare", "studio_compare_split", "studio_photo_tab", "studio_photo_note", "studio_adjust_target",
                 "studio_export_format", "studio_export_size",
                 "studio_export_stars"):
        delattr(wm, name)

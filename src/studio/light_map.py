# SPDX-License-Identifier: GPL-3.0-or-later
"""The light map: every light as a point you can drag, drawn at the bottom of the 3D viewport while the
Environment tab is open (as the canvas of HDRI light editors).

The map is the surroundings of the product unrolled, seen from the camera's side: the middle is "from the
camera" (front light), left and right are the camera's left and right, the two edges are "from behind".
Up is higher. Drag a light to move it; Ctrl + drag changes its size, Shift + drag its brightness;
Ctrl + click on a free spot makes a new light there.

Drawing is a viewport draw handler (pixel space); the dragging is one invisible gizmo that covers the map
(so nothing runs all the time and the clicks never reach the scene behind the map).
"""

import math

import bpy
from bpy.types import Gizmo, GizmoGroup, Operator

from . import hdri, light_items, world_modes
from ..i18n import tr
from .ui_common import studio_tab

LOW, HIGH = math.radians(-30.0), math.radians(90.0)      # heights the map shows
BASE_WIDTH = 480.0                                       # px at interface scale 1, map size 1
ACCENT = (1.0, 0.62, 0.16)
_state = {"handle": None, "hover": None, "textures": {}, "drag": None}


# ------------------------------------------------------------------ geometry (no drawing: testable headless)

def rect_for(region_width, left, right, bottom, ui_scale, size=1.0):
    """(x, y, width, height) of the map in a viewport region of this width; `left`, `right`, `bottom` are
    covered by the toolbar, the sidebar and the asset shelf."""
    margin = 14.0 * ui_scale
    free = max(region_width - left - right - 2 * margin, 120.0)
    width = min(free, max(free * 0.45 * size, 240.0 * ui_scale), BASE_WIDTH * ui_scale * size)
    height = width * (HIGH - LOW) / (2 * math.pi)
    return (left + margin + (free - width) / 2.0, bottom + margin + 16.0 * ui_scale, width, height)


def to_map(rect, around, height):
    x, y, w, h = rect
    return (x + (around + math.pi) / (2 * math.pi) * w, y + (height - LOW) / (HIGH - LOW) * h)


def from_map(rect, px, py):
    x, y, w, h = rect
    around = (px - x) / w * 2 * math.pi - math.pi
    height = LOW + (py - y) / h * (HIGH - LOW)
    return max(-math.pi, min(math.pi, around)), max(LOW, min(math.radians(89.0), height))


def inside(rect, px, py):
    x, y, w, h = rect
    return x <= px <= x + w and y <= py <= y + h


def light_box(rect, around, height, width, tall, minimum=9.0):
    """(center x, center y, half width, half height) in px of a light on the map."""
    cx, cy = to_map(rect, around, height)
    half_w = max(width / (2 * math.pi) * rect[2] / 2.0, minimum)
    half_h = max(tall / (HIGH - LOW) * rect[3] / 2.0, minimum)
    return cx, cy, min(half_w, rect[2] / 2.0), min(half_h, rect[3])


def pick(rect, boxes, px, py):
    """Index of the light under the mouse: the smallest box that contains the point (so a small light in
    front of a big one can be picked), also across the left / right edge."""
    best, best_area = None, None
    for index, (cx, cy, hw, hh) in enumerate(boxes):
        for shift in (0.0, rect[2], -rect[2]):
            if abs(px - cx - shift) <= hw and abs(py - cy) <= hh:
                area = hw * hh
                if best is None or area < best_area:
                    best, best_area = index, area
    return best


def backdrop_u(front, rotation):
    """Image coordinate u (0..1) at the left edge of the map; u falls by 1 from left to right (the map is
    seen from outside, mirrored against the panorama)."""
    return (0.5 - ((front - math.pi) + rotation) / (2 * math.pi)) % 1.0


# ------------------------------------------------------------------ what is shown

def map_on(context):
    wm = context.window_manager
    return bool(getattr(wm, "render_studio_light_map", False)) and studio_tab(context) == 'LIGHT'


def shown(context):
    area = context.area
    return area is not None and area.type == 'VIEW_3D' and map_on(context) and context.mode == 'OBJECT'


def map_rect(context):
    area, region = context.area, context.region
    if region is None or region.type != 'WINDOW':
        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    left = right = bottom = 0.0
    if context.preferences.system.use_region_overlap:
        for r in area.regions:
            if r.width <= 1 or r.height <= 1:
                continue
            if r.type == 'TOOLS':
                left = max(left, r.width)
            elif r.type == 'UI':
                right = max(right, r.width)
            elif r.type in {'ASSET_SHELF', 'ASSET_SHELF_HEADER'}:
                bottom += r.height
    wm = context.window_manager
    return rect_for(region.width, left, right, bottom, context.preferences.system.ui_scale,
                    getattr(wm, "render_studio_light_map_size", 1.0))


def boxes_for(scene, rect, items):
    result = []
    for item in items:
        around, height = light_items.angles(scene, item)
        width, tall = light_items.angular_size(scene, item)
        result.append(light_box(rect, around, height, width, tall))
    return result


# ------------------------------------------------------------------ drawing

def clip(points, rect):
    """A convex polygon cut to the map (Sutherland-Hodgman). Returns the points that are left."""
    x, y, w, h = rect
    for axis, limit, keep_below in ((0, x, False), (0, x + w, True), (1, y, False), (1, y + h, True)):
        if not points:
            break
        result = []
        previous = points[-1]
        for point in points:
            inside_now = point[axis] <= limit if keep_below else point[axis] >= limit
            inside_before = previous[axis] <= limit if keep_below else previous[axis] >= limit
            if inside_now != inside_before:
                t = (limit - previous[axis]) / (point[axis] - previous[axis])
                result.append((previous[0] + t * (point[0] - previous[0]), previous[1] + t * (point[1] - previous[1])))
            if inside_now:
                result.append(point)
            previous = point
        points = result
    return points


def fan(points):
    """Triangles of a convex polygon."""
    return [p for i in range(1, len(points) - 1) for p in (points[0], points[i], points[i + 1])]


def box_points(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def ellipse_points(cx, cy, rx, ry, steps=28):
    return [(cx + rx * math.cos(2 * math.pi * i / steps), cy + ry * math.sin(2 * math.pi * i / steps))
            for i in range(steps)]


def shape_tris(cx, cy, hw, hh, round_shape, rect, edge=0.0):
    """Triangles of a light on the map, cut to the map: its area (edge=0) or its outline of this width."""
    if not edge:
        outline = ellipse_points(cx, cy, hw, hh) if round_shape else box_points(cx - hw, cy - hh, cx + hw, cy + hh)
        return fan(clip(outline, rect))
    if round_shape:
        outer = ellipse_points(cx, cy, hw, hh)
        inner = ellipse_points(cx, cy, max(hw - edge, 0.0), max(hh - edge, 0.0))
    else:
        outer = box_points(cx - hw, cy - hh, cx + hw, cy + hh)
        inner = box_points(cx - hw + edge, cy - hh + edge, cx + hw - edge, cy + hh - edge)
    tris = []
    for i in range(len(outer)):
        j = (i + 1) % len(outer)
        tris += fan(clip([outer[i], outer[j], inner[j], inner[i]], rect))
    return tris


def _tris(shader, verts, color):
    from gpu_extras.batch import batch_for_shader
    if not verts:
        return
    shader.uniform_float("color", color)
    batch_for_shader(shader, 'TRIS', {"pos": verts}).draw(shader)


def _rect(shader, x0, y0, x1, y1, color):
    _tris(shader, fan(box_points(x0, y0, x1, y1)), color)


def _text(text, x, y, size, color, center=False):
    import blf
    blf.size(0, size)
    blf.color(0, *color)
    if center:
        x -= blf.dimensions(0, text)[0] / 2.0
    blf.position(0, round(x), round(y), 0)
    blf.draw(0, text)


def _backdrop_texture(scene):
    """A small picture of the HDRI image (from its thumbnail); None for the studio World and the sky."""
    if scene.render_studio.world_mode != 'IMAGE':
        return None
    path = hdri.current_path(scene)
    if not path:
        return None
    if path in _state["textures"]:
        return _state["textures"][path]
    texture = None
    try:
        import gpu
        if hdri.preview_icon(path):
            preview = hdri._collection()[path]
            width, height = preview.image_size
            if width > 1 and height > 1:
                buffer = gpu.types.Buffer('FLOAT', width * height * 4, list(preview.image_pixels_float))
                texture = gpu.types.GPUTexture((width, height), format='RGBA16F', data=buffer)
    except Exception as exc:           # a missing backdrop is no reason to lose the map
        print("ID Toolkit: light map backdrop:", exc)
    if len(_state["textures"]) > 8:
        _state["textures"].clear()
    _state["textures"][path] = texture
    return texture


def _draw_backdrop(scene, rect, front):
    import gpu
    from gpu_extras.batch import batch_for_shader
    texture = _backdrop_texture(scene)
    if texture is None:
        return False
    x, y, w, h = rect
    u_left = backdrop_u(front, hdri.get_rotation(scene.render_studio))
    v0, v1 = (LOW + math.pi / 2) / math.pi, (HIGH + math.pi / 2) / math.pi
    split = x + u_left * w          # here the image wraps
    shader = gpu.shader.from_builtin('IMAGE')
    shader.bind()
    shader.uniform_sampler("image", texture)
    for xa, xb, ua, ub in ((x, split, u_left, 0.0), (split, x + w, 1.0, u_left)):
        if xb - xa < 0.5:
            continue
        batch_for_shader(shader, 'TRIS', {
            "pos": [(xa, y), (xb, y), (xb, y + h), (xa, y), (xb, y + h), (xa, y + h)],
            "texCoord": [(ua, v0), (ub, v0), (ub, v1), (ua, v0), (ub, v1), (ua, v1)]}).draw(shader)
    return True


def _draw():
    context = bpy.context
    try:
        if not shown(context) or context.region is None or context.region.type != 'WINDOW':
            return
        _draw_map(context)
    except Exception as exc:           # a draw handler must never raise
        print("ID Toolkit: light map:", exc)


def _draw_map(context):
    import gpu
    from . import lights
    scene = context.scene
    rs = scene.render_studio
    ui = context.preferences.system.ui_scale
    rect = map_rect(context)
    x, y, w, h = rect
    front = lights.front_angle(scene, light_items.center_of(scene))
    gpu.state.blend_set('ALPHA')
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.bind()
    pad = 5.0 * ui
    _rect(shader, x - pad, y - pad - 15 * ui, x + w + pad, y + h + pad + 15 * ui, (0.09, 0.09, 0.1, 0.9))
    has_image = _draw_backdrop(scene, rect, front)
    shader.bind()
    if not has_image:
        if rs.world_mode == 'STUDIO':
            base = [c * min(1.0, 0.15 + rs.studio_ambient * 0.6) for c in rs.studio_color]
        elif rs.world_mode == 'SKY':
            day = max(0.0, min(1.0, math.degrees(world_modes.sun_position(rs.sky_time)[0]) / 25.0 + 0.3))
            base = [0.1 + 0.25 * day, 0.14 + 0.33 * day, 0.25 + 0.45 * day]
        else:
            base = [0.16, 0.16, 0.17]
        _rect(shader, x, y, x + w, y + h, (*base, 1.0))
        horizon = to_map(rect, 0.0, 0.0)[1]
        _rect(shader, x, y, x + w, horizon, (base[0] * 0.55, base[1] * 0.55, base[2] * 0.55, 1.0))
    line = max(1.0, round(ui))
    grid = (1.0, 1.0, 1.0, 0.16)
    horizon = to_map(rect, 0.0, 0.0)[1]
    _rect(shader, x, horizon, x + w, horizon + line, grid)
    for around in (-math.pi / 2, 0.0, math.pi / 2):
        gx = to_map(rect, around, 0.0)[0]
        _rect(shader, gx, y, gx + line, y + h, grid)
    items = light_items.all_items(scene)
    active = light_items.active_item(scene)
    boxes = boxes_for(scene, rect, items)
    labels = []
    dot = 3.0 * ui
    for index, (item, (cx, cy, hw, hh)) in enumerate(zip(items, boxes)):
        on = light_items.is_on(scene, item)
        is_active = active is not None and active[0] == item[0] and active[1] == item[1]
        hover = _state["hover"] == index
        color = light_items.color_of(scene, item)
        edge = (*ACCENT, 1.0) if is_active else ((1.0, 1.0, 1.0, 0.95) if hover else (1.0, 1.0, 1.0, 0.5 if on else 0.25))
        thick = (2.0 if is_active or hover else 1.0) * line
        round_shape = light_items.is_round(scene, item)
        area, outline = [], []
        for shift in (0.0, w, -w):          # a light at the edge shows on both sides
            sx = cx + shift
            if sx + hw < x or sx - hw > x + w:
                continue
            area += shape_tris(sx, cy, hw, hh, round_shape, rect)
            outline += shape_tris(sx, cy, hw, hh, round_shape, rect, edge=thick)
            # scene lights: a dot in the middle, studio World lights: a square
            outline += shape_tris(sx, cy, dot, dot, item[0] == 'LIGHT', rect)
            if is_active or hover:
                labels.append((light_items.name_of(scene, item), sx, cy + hh + 4 * ui))
        _tris(shader, area, (*color, 0.55 if on else 0.08))
        _tris(shader, outline, edge)
    small = 10.0 * ui
    faint = (1.0, 1.0, 1.0, 0.55)
    for around, word in ((-math.pi / 2, "Left"), (0.0, "Front"), (math.pi / 2, "Right")):
        _text(tr(word), to_map(rect, around, 0.0)[0], y + h + 4 * ui, small,
              faint, center=True)
    back = tr("Back")
    _text(back, x + 2 * ui, y + h + 4 * ui, small, faint)
    import blf
    blf.size(0, small)
    _text(back, x + w - blf.dimensions(0, back)[0] - 2 * ui, y + h + 4 * ui, small, faint)
    for text, lx, ly in labels:
        _text(text, lx, min(ly, y + h - 12 * ui), 11.0 * ui, (1.0, 1.0, 1.0, 1.0), center=True)
    space = context.space_data
    hint = "Drag: move   ·   Ctrl + drag: size   ·   Shift + drag: brightness   ·   Ctrl + click: new light"
    if space is not None and not space.show_gizmo:
        hint = "Switch gizmos on (viewport header) to drag the lights"
    _text(tr(hint), x, y - 13 * ui, small, faint)
    gpu.state.blend_set('NONE')


# ------------------------------------------------------------------ dragging

class RS_GT_light_map(Gizmo):
    """Invisible, as big as the map: picks and drags the lights."""
    bl_idname = "RS_GT_light_map"

    def draw(self, context):
        pass

    def test_select(self, context, location):
        if not shown(context):
            return -1
        rect = map_rect(context)
        if not inside(rect, location[0], location[1]):
            if _state["hover"] is not None:
                _state["hover"] = None
                context.area.tag_redraw()
            return -1
        scene = context.scene
        hover = pick(rect, boxes_for(scene, rect, light_items.all_items(scene)), location[0], location[1])
        if hover != _state["hover"]:
            _state["hover"] = hover
            context.area.tag_redraw()
        return 0

    def invoke(self, context, event):
        scene = context.scene
        rect = map_rect(context)
        mouse = (event.mouse_region_x, event.mouse_region_y)
        items = light_items.all_items(scene)
        index = pick(rect, boxes_for(scene, rect, items), *mouse)
        created = None
        if index is None:
            if not event.ctrl:
                return {'RUNNING_MODAL'}        # a free spot: nothing moves, the click stays on the map
            try:
                item = created = light_items.add_like(scene, light_items.active_item(scene))
            except ValueError:
                return {'RUNNING_MODAL'}
            light_items.set_angles(scene, item, *from_map(rect, *mouse))
            how = 'MOVE'
        else:
            item = items[index]
            light_items.select(scene, item)
            how = 'SIZE' if event.ctrl else ('POWER' if event.shift else 'MOVE')
        around, height = light_items.angles(scene, item)
        from . import light_paint
        _state["drag"] = {"item": item, "how": how, "mouse": mouse, "last": mouse, "angles": (around, height),
                          "saved": light_paint._item_state(scene, item), "created": created, "moved": bool(created)}
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def modal(self, context, event, tweak):
        drag = _state["drag"]
        if drag is None:
            return {'RUNNING_MODAL'}
        scene = context.scene
        rect = map_rect(context)
        mouse = (event.mouse_region_x, event.mouse_region_y)
        dx, dy = mouse[0] - drag["last"][0], mouse[1] - drag["last"][1]
        if not dx and not dy:
            return {'RUNNING_MODAL'}
        drag["last"] = mouse
        drag["moved"] = True
        item = drag["item"]
        if drag["how"] == 'MOVE':
            around = drag["angles"][0] + (mouse[0] - drag["mouse"][0]) / rect[2] * 2 * math.pi
            height = drag["angles"][1] + (mouse[1] - drag["mouse"][1]) / rect[3] * (HIGH - LOW)
            around = (around + math.pi) % (2 * math.pi) - math.pi       # across the back to the other side
            light_items.set_angles(scene, item, around, max(LOW, min(math.radians(89.0), height)))
        elif drag["how"] == 'SIZE':
            if item[0] == 'PIN':            # width and height on their own
                pin = light_items.pin_of(scene, item)
                pin.width = max(0.02, min(math.pi, pin.width * math.exp(dx / 120.0)))
                pin.height = max(0.02, min(math.pi, pin.height * math.exp(dy / 120.0)))
            else:
                light_items.scale_size(scene, item, math.exp((dx + dy) / 120.0))
        else:
            light_items.scale_power(scene, item, math.exp(dy / 90.0))
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}

    def exit(self, context, cancel):
        drag, _state["drag"] = _state["drag"], None
        if drag is None:
            return
        scene = context.scene
        if cancel:
            from . import light_paint
            if drag["created"] is not None:
                light_items.remove(scene, drag["created"])
            else:
                light_paint._restore_item(scene, drag["item"], drag["saved"])
        elif drag["moved"]:
            try:
                bpy.ops.ed.undo_push(message="Light Map")
            except RuntimeError:
                pass
        context.area.tag_redraw()


class RS_GGT_light_map(GizmoGroup):
    bl_idname = "RS_GGT_light_map"
    bl_label = "Light Map"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'PERSISTENT', 'SHOW_MODAL_ALL'}

    @classmethod
    def poll(cls, context):
        return shown(context)

    @classmethod
    def setup_keymap(cls, keyconfig):
        # press, not drag: a plain click on a light opens it (and never reaches the scene behind the map)
        keymap = keyconfig.keymaps.new(name="ID Toolkit Light Map", space_type='EMPTY', region_type='WINDOW')
        if not any(item.idname == "gizmogroup.gizmo_tweak" for item in keymap.keymap_items):
            keymap.keymap_items.new("gizmogroup.gizmo_tweak", 'LEFTMOUSE', 'PRESS', any=True)
        return keymap

    def setup(self, context):
        gizmo = self.gizmos.new(RS_GT_light_map.bl_idname)
        gizmo.use_draw_modal = True


class RS_OT_light_map(Operator):
    bl_idname = "render_studio.light_map"
    bl_label = "Light Map"
    bl_description = ("Show the light map at the bottom of the 3D viewport: every light as a point to drag. "
                      "The middle is the camera's side, the edges are behind the product")
    bl_options = {'INTERNAL'}

    def execute(self, context):
        wm = context.window_manager
        wm.render_studio_light_map = not wm.render_studio_light_map
        space = context.space_data
        if wm.render_studio_light_map and space is not None and space.type == 'VIEW_3D' and not space.show_gizmo:
            space.show_gizmo = True         # the lights are dragged with a gizmo
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
        return {'FINISHED'}


classes = (RS_GT_light_map, RS_GGT_light_map, RS_OT_light_map)


def register():
    from bpy.props import BoolProperty, FloatProperty
    bpy.types.WindowManager.render_studio_light_map = BoolProperty(
        name="Light Map", description="Show the light map in the 3D viewport while the Environment tab is open",
        default=True, options={'SKIP_SAVE'})
    bpy.types.WindowManager.render_studio_light_map_size = FloatProperty(
        name="Map Size", description="Size of the light map in the 3D viewport", default=1.0, min=0.5, max=2.5,
        subtype='FACTOR', options={'SKIP_SAVE'})
    for cls in classes:
        bpy.utils.register_class(cls)
    if not bpy.app.background and _state["handle"] is None:
        _state["handle"] = bpy.types.SpaceView3D.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    if _state["handle"] is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_state["handle"], 'WINDOW')
        _state["handle"] = None
    _state["textures"].clear()
    _state["hover"] = _state["drag"] = None
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.render_studio_light_map
    del bpy.types.WindowManager.render_studio_light_map_size

# SPDX-License-Identifier: GPL-3.0-or-later
"""Place a light by clicking on the product (as HDRI light editors do).

While a light is open in the Environment tab, the "Place Light" tool is active in the 3D viewport: click
or drag on the product and the open light goes where it does what the chosen way says:
- REFLECTION: the light's reflection (highlight) appears where you click.
- ILLUMINATION: the light shines straight onto the spot you click.
- RIM: the light comes from behind the spot, for an edge light.
- SHADE: the spot you click falls into shadow (the light goes to the other side).
- SHADOW: the product's shadow falls onto the spot you click (click the floor).
Ctrl + click makes a new light there. Clicks next to the product work as always.

Works for the lights of the scene and for the lights of the studio World (world_modes.py pins: only
their direction counts, they are infinitely far away).
"""

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator, WorkSpaceTool
from bpy_extras import view3d_utils
from mathutils import Vector

from . import light_items, orbit, world_modes
from .props import PLACE_MODES

MODES = PLACE_MODES
TOOL_ID = "render_studio.light_place"
_previous = {"tool": ""}


def light_direction(view, normal, mode, hit=None, center=None):
    """Unit vector from the clicked spot towards the light. `view`: direction the eye looks along.
    SHADOW needs the clicked spot and the product's center."""
    view = view.normalized()
    n = normal.normalized()
    if n.dot(view) > 0:      # back face: use the side that faces the eye
        n = -n
    if mode == 'ILLUMINATION':
        return n
    if mode == 'RIM':
        return (view * 0.8 + n * 0.6).normalized()
    if mode == 'SHADE':
        d = -n
        if d.xy.length < 0.2:           # a face that looks up: the light goes behind the product instead
            d = Vector((view.x, view.y, 0.0))
        d.z = max(d.z, 0.08)            # never from under the floor
        return d.normalized()
    if mode == 'SHADOW' and hit is not None and center is not None:
        d = center - hit
        if d.length < 1e-6:
            return n
        d.normalize()
        d.z = max(d.z, 0.08)
        return d.normalized()
    return (view - 2.0 * view.dot(n) * n).normalized()


def place_light(light, hit, direction, distance):
    """Puts a scene light `distance` away from the spot along `direction`, looking at the spot."""
    position = hit + direction * distance
    scale = light.matrix_world.to_scale()
    light.matrix_world = orbit.look_at_matrix(position, hit, scale)


def place_item(scene, item, hit, direction, mode, distance=None):
    """Places a light (scene light or studio World light) for a click at `hit`."""
    if item[0] == 'PIN':
        world_modes.set_pin_direction(scene, light_items.pin_of(scene, item), direction)
        return
    obj = item[1]
    if obj.data.type == 'SUN':
        obj.rotation_euler = direction.to_track_quat('Z', 'Y').to_euler()
        return
    center = light_items.center_of(scene, obj)
    if distance is None:
        distance = max((obj.matrix_world.translation - center).length, 0.05)
    place_light(obj, center if mode == 'SHADOW' else hit, direction, distance)


def _ray(context, region, rv3d, coord):
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    ok, location, normal, _index, obj, _matrix = context.scene.ray_cast(context.evaluated_depsgraph_get(),
                                                                         origin, direction)
    if not ok or (obj is not None and obj.get("as_studio") == "flag"):
        return None
    return location, normal, direction


def _item_state(scene, item):
    if item[0] == 'PIN':
        pin = light_items.pin_of(scene, item)
        return (pin.horizontal, pin.vertical, pin.width, pin.height, pin.strength)
    obj = item[1]
    return (obj.matrix_world.copy(), obj.data.energy, getattr(obj.data, "size", 0.0), getattr(obj.data, "size_y", 0.0))


def _restore_item(scene, item, state):
    if item[0] == 'PIN':
        pin = light_items.pin_of(scene, item)
        if pin is not None:
            pin.horizontal, pin.vertical, pin.width, pin.height, pin.strength = state
        return
    obj = item[1]
    obj.matrix_world, obj.data.energy = state[0], state[1]
    if obj.data.type == 'AREA':
        obj.data.size, obj.data.size_y = state[2], state[3]


class RS_OT_light_paint(Operator):
    bl_idname = "render_studio.light_paint"
    bl_label = "Place Light on the Product"
    bl_description = ("Click or drag on the product to place the open light. Ctrl + click: a new light. "
                      "While dragging: wheel = distance, Ctrl + wheel = size, Shift + wheel = brightness")
    bl_options = {'UNDO', 'BLOCKING', 'INTERNAL'}

    mode: EnumProperty(name="Place", items=(('AUTO', "As Chosen", "The way picked in the Environment tab"),) + MODES,
                       default='AUTO', options={'SKIP_SAVE'})
    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})      # a scene light (empty: the open light)
    pin: IntProperty(default=-1, options={'HIDDEN', 'SKIP_SAVE'})   # or a light of the studio World
    new: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})   # make a new light at the spot

    def _item(self, context):
        scene = context.scene
        if self.pin >= 0:
            return ('PIN', self.pin) if self.pin < len(scene.render_studio.hdri_pins) else None
        if self.name:
            obj = bpy.data.objects.get(self.name)
            return ('LIGHT', obj) if obj is not None and obj.type == 'LIGHT' else None
        return light_items.active_item(scene)

    def invoke(self, context, event):
        area = context.area
        if area is None or area.type != 'VIEW_3D':
            return {'PASS_THROUGH'}
        self.region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        self.rv3d = area.spaces.active.region_3d
        scene = context.scene
        self.way = scene.render_studio.place_mode if self.mode == 'AUTO' else self.mode
        item = self._item(context)
        if self.region is None or (item is None and not self.new):
            return {'PASS_THROUGH'}
        coord = (event.mouse_x - self.region.x, event.mouse_y - self.region.y)
        if _ray(context, self.region, self.rv3d, coord) is None:
            return {'PASS_THROUGH'}         # next to the product: select, deselect … as always
        if self.new:
            try:
                item = light_items.add_like(scene, item)
            except ValueError as exc:
                self.report({'WARNING'}, str(exc))
                return {'CANCELLED'}
            self.created = item
        else:
            self.created = None
        self.item = item
        self.saved = _item_state(scene, item)
        self.distance = None
        if item[0] == 'LIGHT':
            center = light_items.center_of(scene, item[1])
            self.distance = max((item[1].matrix_world.translation - center).length, 0.05)
        self.hit = None
        self._apply(context, event)
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Place light (%s): drag on the product   ·   Wheel: distance   ·   Ctrl + Wheel: size   ·   "
            "Shift + Wheel: brightness   ·   Esc: back" % dict((k, n) for k, n, _d in MODES)[self.way])
        return {'RUNNING_MODAL'}

    def _apply(self, context, event=None):
        scene = context.scene
        if event is not None:
            coord = (event.mouse_x - self.region.x, event.mouse_y - self.region.y)
            found = _ray(context, self.region, self.rv3d, coord)
            if found is None:
                return
            location, normal, view = found
            center = light_items.center_of(scene, self.item[1] if self.item[0] == 'LIGHT' else None)
            self.hit = (location, light_direction(view, normal, self.way, location, center))
        if self.hit is None:
            return
        location, direction = self.hit
        place_item(scene, self.item, location, direction, self.way, self.distance)

    def _wheel(self, context, event, step):
        scene = context.scene
        factor = 1.1 if step > 0 else 1 / 1.1
        if event.ctrl:
            light_items.scale_size(scene, self.item, factor)
        elif event.shift:
            light_items.scale_power(scene, self.item, factor)
        elif self.item[0] == 'LIGHT' and self.item[1].data.type != 'SUN':
            old = self.distance
            self.distance = max(0.02, self.distance * factor)
            self.item[1].data.energy *= (self.distance / old) ** 2   # same brightness on the product
            self._apply(context)

    def _finish(self, context):
        context.workspace.status_text_set(None)
        if context.area is not None:
            context.area.tag_redraw()

    def modal(self, context, event):
        if event.type == 'MOUSEMOVE':
            self._apply(context, event)
            return {'RUNNING_MODAL'}
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            self._wheel(context, event, 1 if event.type == 'WHEELUPMOUSE' else -1)
            return {'RUNNING_MODAL'}
        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            self._finish(context)
            return {'FINISHED'}
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            scene = context.scene
            if self.created is not None:
                light_items.remove(scene, self.created)
            else:
                _restore_item(scene, self.item, self.saved)
            self._finish(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


class RS_OT_light_nudge(Operator):
    bl_idname = "render_studio.light_nudge"
    bl_label = "Change the Open Light"
    bl_description = "Size, brightness or distance of the open light, one step"
    bl_options = {'UNDO', 'INTERNAL'}

    what: EnumProperty(items=(('SIZE', "Size", ""), ('POWER', "Brightness", ""), ('DISTANCE', "Distance", "")),
                       default='SIZE', options={'SKIP_SAVE'})
    factor: FloatProperty(default=1.1, options={'SKIP_SAVE'})

    def execute(self, context):
        scene = context.scene
        item = light_items.active_item(scene)
        if item is None:
            return {'PASS_THROUGH'}
        if self.what == 'SIZE':
            light_items.scale_size(scene, item, self.factor)
        elif self.what == 'POWER':
            light_items.scale_power(scene, item, self.factor)
        else:
            light_items.scale_distance(scene, item, self.factor)
        return {'FINISHED'}


def _wheel_keys():
    keys = []
    for what, mods in (('SIZE', {"ctrl": True}), ('POWER', {"shift": True}), ('DISTANCE', {"alt": True})):
        for kind, factor in (('WHEELUPMOUSE', 1.1), ('WHEELDOWNMOUSE', 1 / 1.1)):
            keys.append((RS_OT_light_nudge.bl_idname, dict(type=kind, value='PRESS', **mods),
                         {"properties": [("what", what), ("factor", factor)]}))
    return tuple(keys)


class RS_WT_light_place(WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'
    bl_idname = TOOL_ID
    bl_label = "Place Light"
    bl_description = ("Click or drag on the product: the open light of the Environment tab goes there "
                      "(reflection, light, rim, shade or shadow).\nCtrl + click: a new light. "
                      "Ctrl / Shift / Alt + wheel: size, brightness, distance")
    bl_icon = "ops.generic.cursor"
    bl_cursor = 'CROSSHAIR'
    bl_widget = None
    bl_keymap = (
        (RS_OT_light_paint.bl_idname, {"type": 'LEFTMOUSE', "value": 'PRESS'}, None),
        (RS_OT_light_paint.bl_idname, {"type": 'LEFTMOUSE', "value": 'PRESS', "ctrl": True},
         {"properties": [("new", True)]}),
    ) + _wheel_keys()

    def draw_settings(context, layout, _tool):
        scene = context.scene
        item = light_items.active_item(scene)
        layout.label(text=light_items.name_of(scene, item) if item else "Open a light in the Environment tab",
                     icon='LIGHT')
        layout.prop(scene.render_studio, "place_mode", text="")


def _current_tool(context):
    try:
        return context.workspace.tools.from_space_view3d_mode('OBJECT', create=False)
    except (AttributeError, RuntimeError, TypeError):
        return None


def tool_active(context):
    tool = _current_tool(context)
    return tool is not None and tool.idname == TOOL_ID


def activate_tool(context):
    """Makes "Place Light" the active tool of the 3D viewport (remembers the one before)."""
    if bpy.app.background or context.mode != 'OBJECT' or context.area is None or context.area.type != 'VIEW_3D':
        return False
    tool = _current_tool(context)
    if tool is not None and tool.idname == TOOL_ID:
        return True
    if tool is not None:
        _previous["tool"] = tool.idname
    try:
        from bl_ui.space_toolsystem_common import activate_by_id
        return bool(activate_by_id(context, 'VIEW_3D', TOOL_ID))
    except Exception as exc:       # the tool system must never break a click in the panel
        print("ID Toolkit: place tool:", exc)
        return False


def restore_tool(context):
    """Back to the tool that was active before "Place Light"."""
    if bpy.app.background or not tool_active(context) or context.area is None or context.area.type != 'VIEW_3D':
        return
    try:
        from bl_ui.space_toolsystem_common import activate_by_id
        if not activate_by_id(context, 'VIEW_3D', _previous["tool"] or "builtin.select_box"):
            activate_by_id(context, 'VIEW_3D', "builtin.select_box")
    except Exception as exc:
        print("ID Toolkit: place tool:", exc)


class RS_OT_place_mode(Operator):
    bl_idname = "render_studio.place_mode"
    bl_label = "Place on the Product"
    bl_description = "How a click on the product places the open light"
    bl_options = {'INTERNAL'}

    mode: EnumProperty(items=MODES, default='REFLECTION', options={'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return dict((k, d) for k, _n, d in MODES).get(props.mode, "") + \
            ". Then click or drag on the product; Ctrl + click makes a new light. Click again to switch placing off"

    def execute(self, context):
        rs = context.scene.render_studio
        wm = context.window_manager
        if tool_active(context) and rs.place_mode == self.mode:
            restore_tool(context)
            wm.render_studio_place_off = True       # stays off until a way is picked again
        else:
            rs.place_mode = self.mode
            wm.render_studio_place_off = False
            activate_tool(context)
        return {'FINISHED'}


class RS_OT_sky_time(Operator):
    bl_idname = "render_studio.sky_time"
    bl_label = "Time of Day"
    bl_options = {'UNDO', 'INTERNAL'}

    hours: FloatProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        context.scene.render_studio.sky_time = self.hours
        return {'FINISHED'}


SKY_TIMES = (("Sunrise", 6.4), ("Morning", 9.0), ("Noon", 12.0), ("Golden", 17.2), ("Dusk", 18.3), ("Night", 23.0))


def draw_place_row(layout, context):
    """Click on the product: [Reflection] [Light] [Rim] / [Shade] [Shadow]. The pressed one is active."""
    rs = context.scene.render_studio
    on = tool_active(context)
    # [🖱] [Reflection] [Light] [Rim] / [Shade] [Shadow]: the mouse says "click on the product"
    split = layout.split(factor=0.12, align=True)
    icon = split.column()
    icon.alignment = 'CENTER'
    icon.scale_y = 2.0
    icon.label(text="", icon='MOUSE_LMB')
    grid = split.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
    for mode, label, _d in MODES:
        grid.operator(RS_OT_place_mode.bl_idname, text=label, depress=on and rs.place_mode == mode).mode = mode


def draw_studio(box, scene):
    """Studio World: its ambient light. Its lights are in the Lights list."""
    rs = scene.render_studio
    row = box.row(align=True)
    row.prop(rs, "studio_ambient", slider=True)
    sub = row.row(align=True)
    sub.ui_units_x = 2
    sub.prop(rs, "studio_color", text="")


def draw_sky(box, scene):
    rs = scene.render_studio
    box.prop(rs, "sky_time", slider=True)
    grid = box.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
    for label, hours in SKY_TIMES:
        grid.operator(RS_OT_sky_time.bl_idname, text=label, depress=abs(rs.sky_time - hours) < 0.05).hours = hours
    box.prop(rs, "sky_north")
    if world_modes.atmosphere_available() and scene.world is not None \
            and getattr(scene.world, "psa_general_settings", None) and scene.world.psa_general_settings.enabled:
        settings = scene.world.psa_atmosphere_settings
        col = box.column(align=True)
        for prop, label in (("atmosphere_density", "Haze"), ("clouds_amount", "Clouds"),
                            ("stars_intensity", "Stars"), ("sun_intensity", "Sun Strength")):
            if hasattr(settings, prop):
                col.prop(settings, prop, text=label)
    elif not world_modes.atmosphere_available():
        sub = box.row()
        sub.active = False
        sub.label(text="Clouds and stars: with Physical Starlight & Atmosphere", icon='INFO')


classes = (RS_OT_light_paint, RS_OT_light_nudge, RS_OT_place_mode, RS_OT_sky_time)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    try:
        bpy.utils.register_tool(RS_WT_light_place, after={"builtin.cursor"}, separator=True)
    except Exception as exc:
        print("ID Toolkit: place tool:", exc)


def unregister():
    world_modes.unregister()
    try:
        bpy.utils.unregister_tool(RS_WT_light_place)
    except Exception as exc:
        print("ID Toolkit: place tool:", exc)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

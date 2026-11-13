# SPDX-License-Identifier: GPL-3.0-or-later
"""Place a light by clicking on the product (as HDRI light editors do).

Three ways, picked on the button:
- REFLECTION: the light's reflection (highlight) appears where you click.
- ILLUMINATION: the light shines straight onto the spot you click.
- RIM: the light comes from behind the spot, for an edge light.

Works for the lights of the scene and for the lights of the studio World (world_modes.py pins: only
their direction counts, they are infinitely far away).
"""

import bpy
from bpy.props import EnumProperty, FloatProperty, IntProperty, StringProperty
from bpy.types import Operator
from bpy_extras import view3d_utils
from mathutils import Vector

from . import lights, orbit, world_modes

MODES = (
    ('REFLECTION', "Reflection", "The light's reflection appears where you click"),
    ('ILLUMINATION', "Light", "The light shines straight onto the spot you click"),
    ('RIM', "Rim", "The light comes from behind the spot you click (edge light)"),
)


def light_direction(view, normal, mode):
    """Unit vector from the clicked spot towards the light. `view`: direction the eye looks along."""
    view = view.normalized()
    n = normal.normalized()
    if n.dot(view) > 0:      # back face: use the side that faces the eye
        n = -n
    if mode == 'ILLUMINATION':
        return n
    if mode == 'RIM':
        return (view * 0.8 + n * 0.6).normalized()
    return (view - 2.0 * view.dot(n) * n).normalized()


def place_light(light, hit, direction, distance):
    """Puts a scene light `distance` away from the spot along `direction`, looking at the spot."""
    position = hit + direction * distance
    scale = light.matrix_world.to_scale()
    light.matrix_world = orbit.look_at_matrix(position, hit, scale)


def _ray(context, region, rv3d, coord):
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
    ok, location, normal, _index, obj, _matrix = context.scene.ray_cast(context.evaluated_depsgraph_get(),
                                                                         origin, direction)
    if not ok or (obj is not None and obj.get("as_studio") == "flag"):
        return None
    return location, normal, direction


class RS_OT_light_paint(Operator):
    bl_idname = "render_studio.light_paint"
    bl_label = "Place by Click"
    bl_description = ("Click or drag on the product to place the light. Wheel: distance, Ctrl + wheel: size, "
                      "Shift + wheel: brightness. Enter or right-click: done, Esc: back")
    bl_options = {'UNDO', 'BLOCKING', 'INTERNAL'}

    mode: EnumProperty(name="Place", items=MODES, default='REFLECTION')
    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})      # a scene light
    pin: IntProperty(default=-1, options={'HIDDEN', 'SKIP_SAVE'})   # or a light of the studio World

    @classmethod
    def description(cls, _context, props):
        text = dict((k, d) for k, _n, d in MODES).get(props.mode, "")
        return text + ". Click or drag on the product. Wheel: distance, Ctrl + wheel: size, Shift + wheel: brightness"

    def _target(self, context):
        rs = context.scene.render_studio
        if self.pin >= 0:
            return rs.hdri_pins[self.pin] if self.pin < len(rs.hdri_pins) else None
        obj = bpy.data.objects.get(self.name)
        return obj if obj is not None and obj.type == 'LIGHT' else None

    def invoke(self, context, event):
        area = context.area
        if area is None or area.type != 'VIEW_3D':
            self.report({'WARNING'}, "Use it in the 3D viewport")
            return {'CANCELLED'}
        self.region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        self.rv3d = area.spaces.active.region_3d
        target = self._target(context)
        if target is None or self.region is None:
            return {'CANCELLED'}
        rs = context.scene.render_studio
        if self.pin >= 0:
            self.saved = (target.horizontal, target.vertical, target.width, target.height, target.strength)
            self.distance = 1.0
        else:
            self.saved = (target.matrix_world.copy(), target.data.energy,
                          getattr(target.data, "size", 0.0), getattr(target.data, "size_y", 0.0))
            center, _radius = lights.subject(context.scene, lights.light_target(target, rs))
            self.distance = max((target.matrix_world.translation - center).length, 0.05)
        self.hit = None
        self.dragging = False
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set(
            "Place light (%s): click or drag on the product   ·   Wheel: distance   ·   Ctrl + Wheel: size   ·   "
            "Shift + Wheel: brightness   ·   Enter / Right-click: done   ·   Esc: back" % self.mode.title())
        return {'RUNNING_MODAL'}

    def _apply(self, context, event=None):
        target = self._target(context)
        if target is None:
            return
        if event is not None:
            coord = (event.mouse_x - self.region.x, event.mouse_y - self.region.y)
            found = _ray(context, self.region, self.rv3d, coord)
            if found is None:
                return
            location, normal, view = found
            self.hit = (location, light_direction(view, normal, self.mode))
        if self.hit is None:
            return
        location, direction = self.hit
        if self.pin >= 0:
            world_modes.set_pin_direction(context.scene, target, direction)
        elif target.data.type == 'SUN':
            target.rotation_euler = direction.to_track_quat('Z', 'Y').to_euler()
        else:
            place_light(target, location, direction, self.distance)

    def _wheel(self, context, event, step):
        target = self._target(context)
        if target is None:
            return
        factor = 1.1 if step > 0 else 1 / 1.1
        if event.ctrl:
            if self.pin >= 0:
                target.width, target.height = target.width * factor, target.height * factor
            elif target.data.type == 'AREA':
                target.data.size *= factor
                target.data.size_y *= factor
        elif event.shift:
            if self.pin >= 0:
                target.strength *= factor
            else:
                target.data.energy *= factor
        elif self.pin < 0:
            old = self.distance
            self.distance = max(0.02, self.distance * factor)
            if target.data.type != 'SUN':   # same brightness on the product at the new distance
                target.data.energy *= (self.distance / old) ** 2
            self._apply(context)

    def _finish(self, context):
        context.workspace.status_text_set(None)
        context.area.tag_redraw()

    def modal(self, context, event):
        if event.type in {'MIDDLEMOUSE', 'TRACKPADPAN', 'TRACKPADZOOM', 'NDOF_MOTION'} or (
                event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'} and event.alt):
            return {'PASS_THROUGH'}      # still move around the product
        if event.type == 'LEFTMOUSE':
            self.dragging = event.value == 'PRESS'
            if self.dragging:
                self._apply(context, event)
            return {'RUNNING_MODAL'}
        if event.type == 'MOUSEMOVE' and self.dragging:
            self._apply(context, event)
            return {'RUNNING_MODAL'}
        if event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            self._wheel(context, event, 1 if event.type == 'WHEELUPMOUSE' else -1)
            return {'RUNNING_MODAL'}
        if event.type in {'RET', 'NUMPAD_ENTER', 'SPACE', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._finish(context)
            return {'FINISHED'}
        if event.type == 'ESC':
            target = self._target(context)
            if target is not None:
                if self.pin >= 0:
                    (target.horizontal, target.vertical, target.width, target.height,
                     target.strength) = self.saved
                else:
                    target.matrix_world, target.data.energy = self.saved[0], self.saved[1]
                    if target.data.type == 'AREA':
                        target.data.size, target.data.size_y = self.saved[2], self.saved[3]
            self._finish(context)
            return {'CANCELLED'}
        return {'RUNNING_MODAL'}


# ------------------------------------------------------------------ studio World: its lights

class RS_OT_pin_add(Operator):
    bl_idname = "render_studio.pin_add"
    bl_label = "Add Light"
    bl_description = "A new light in the studio World"
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        rs = context.scene.render_studio
        if len(rs.hdri_pins) >= 16:
            self.report({'WARNING'}, "At most 16 lights")
            return {'CANCELLED'}
        pin = rs.hdri_pins.add()
        pin.name = "Light %d" % len(rs.hdri_pins)
        rs.hdri_pin_index = len(rs.hdri_pins) - 1
        world_modes.on_change(context.scene)
        return {'FINISHED'}


class RS_OT_pin_remove(Operator):
    bl_idname = "render_studio.pin_remove"
    bl_label = "Remove Light"
    bl_options = {'UNDO', 'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        rs = context.scene.render_studio
        if 0 <= self.index < len(rs.hdri_pins):
            rs.hdri_pins.remove(self.index)
            rs.hdri_pin_index = min(rs.hdri_pin_index, len(rs.hdri_pins) - 1)
            world_modes.on_change(context.scene)
        return {'FINISHED'}


class RS_OT_pin_select(Operator):
    bl_idname = "render_studio.pin_select"
    bl_label = "Light Settings"
    bl_description = "Show the settings of this light (click again to close)"
    bl_options = {'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        rs = context.scene.render_studio
        rs.hdri_pin_index = -1 if rs.hdri_pin_index == self.index else self.index
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


def draw_place_row(layout, name="", pin=-1):
    """[Reflection] [Light] [Rim]: place by clicking on the product."""
    row = layout.row(align=True)
    row.label(text="", icon='MOUSE_LMB_DRAG')
    for mode, label, _d in MODES:
        op = row.operator(RS_OT_light_paint.bl_idname, text=label)
        op.mode, op.name, op.pin = mode, name, pin


def draw_studio(box, scene):
    rs = scene.render_studio
    row = box.row(align=True)
    row.prop(rs, "studio_ambient", slider=True)
    sub = row.row(align=True)
    sub.ui_units_x = 2
    sub.prop(rs, "studio_color", text="")
    col = box.column(align=True)
    for i, pin in enumerate(rs.hdri_pins):
        row = col.row(align=True)
        row.prop(pin, "on", text="", icon='HIDE_OFF' if pin.on else 'HIDE_ON', emboss=False)
        sub = row.row(align=True)
        sub.active = pin.on
        sub.operator(RS_OT_pin_select.bl_idname, text="", depress=(i == rs.hdri_pin_index),
                     icon='LIGHT_AREA' if pin.shape == 'RECT' else 'LIGHT_POINT').index = i
        sub.prop(pin, "name", text="")
        power = sub.row(align=True)
        power.ui_units_x = 4.5
        power.prop(pin, "strength", text="")
        if i == rs.hdri_pin_index:
            details = col.box()
            draw_place_row(details, pin=i)
            sub = details.column(align=True)
            line = sub.row(align=True)
            line.prop(pin, "horizontal")
            line.prop(pin, "vertical")
            line = sub.row(align=True)
            line.prop(pin, "width")
            line.prop(pin, "height")
            sub.prop(pin, "softness", slider=True)
            line = details.row(align=True)
            line.prop(pin, "shape", expand=True)
            color = line.row(align=True)
            color.ui_units_x = 2
            color.prop(pin, "color", text="")
            line.operator(RS_OT_pin_remove.bl_idname, text="", icon='TRASH').index = i
            col.separator()
    box.operator(RS_OT_pin_add.bl_idname, icon='ADD')


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


classes = (RS_OT_light_paint, RS_OT_pin_add, RS_OT_pin_remove, RS_OT_pin_select, RS_OT_sky_time)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    world_modes.unregister()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

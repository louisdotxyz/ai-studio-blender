# SPDX-License-Identifier: GPL-3.0-or-later
"""The Lights list of the Environment tab: one list for the lights of the scene and of the studio World,
the looks row above it, and their operators (logic in light_items.py and looks.py)."""

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Menu, Operator

from . import env_ui, environment, light_items, light_paint, lights, looks
from .ui_common import _report_value_error, _select_only, subsection

LIGHT_ICONS = {'POINT': 'LIGHT_POINT', 'SUN': 'LIGHT_SUN', 'SPOT': 'LIGHT_SPOT', 'AREA': 'LIGHT_AREA'}
PIN_ICON = 'WORLD'


class _ItemOperator:
    """An operator on one light of the list: `name` (a scene light) or `pin` (a studio World light)."""
    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    pin: IntProperty(default=-1, options={'HIDDEN', 'SKIP_SAVE'})

    def item(self, context):
        scene = context.scene
        if self.pin >= 0:
            return ('PIN', self.pin) if self.pin < len(scene.render_studio.hdri_pins) else None
        if self.name:
            obj = bpy.data.objects.get(self.name)
            return ('LIGHT', obj) if obj is not None and obj.type == 'LIGHT' else None
        return light_items.active_item(scene)


def _set_item(op, item):
    if item[0] == 'PIN':
        op.pin = item[1]
    else:
        op.name = item[1].name


def _placing_wanted(context):
    return not getattr(context.window_manager, "render_studio_place_off", False)


def _opened(context, item):
    """A light was opened: select it in the viewport and make clicks on the product place it."""
    if item[0] == 'LIGHT':
        _select_only(context, item[1])
    if _placing_wanted(context):
        light_paint.activate_tool(context)


class RS_OT_light_open(_ItemOperator, Operator):
    bl_idname = "render_studio.light_open"
    bl_label = "Open Light"
    bl_description = ("Show the settings of this light (click again to close). While it is open, a click on "
                      "the product places it")
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        scene = context.scene
        item = self.item(context)
        if item is None:
            return {'CANCELLED'}
        active = light_items.active_item(scene)
        if active is not None and active[0] == item[0] and active[1] == item[1]:
            light_items.select(scene, None)         # close
            light_paint.restore_tool(context)
            return {'FINISHED'}
        light_items.select(scene, item)
        _opened(context, item)
        return {'FINISHED'}


NEW_KINDS = (
    ('AREA', "Area Light", "A soft light with a size, front left, aimed at the product"),
    ('SPOT', "Spot Light", "A cone of light, for accents and hard shadows"),
    ('SUN', "Sun Light", "Parallel light with sharp shadows"),
    ('POINT', "Point Light", "A bulb that shines to all sides"),
    ('PIN', "Light in the Studio World", "A softbox painted into the environment: it lights and shows in "
     "reflections, but has no place in the room. Switches the World to Studio"),
)


class RS_OT_light_new(Operator):
    bl_idname = "render_studio.light_new"
    bl_label = "New Light"
    bl_description = "Add a light"
    bl_options = {'UNDO', 'INTERNAL'}

    kind: EnumProperty(items=NEW_KINDS, default='AREA', options={'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return dict((k, d) for k, _n, d in NEW_KINDS).get(props.kind, cls.bl_description)

    def execute(self, context):
        scene = context.scene
        rs = scene.render_studio
        if self.kind == 'PIN':
            index = _report_value_error(self, lambda: light_items.add_pin(scene))
            if index is None:
                return {'CANCELLED'}
            item = ('PIN', index)
        else:
            if self.kind == 'AREA':
                obj = lights.add_light(scene, rs.light_orbit_target)
            else:
                spec = {"name": self.kind.lower(), "type": self.kind, "horizontal": -45, "vertical": 40,
                        "distance": 3.0, "size": 0.2, "power": 1.0}
                obj = lights.apply_setup(scene, rs.light_orbit_target, {"lights": [spec]}, replace=False, tag=False)[0]
            item = ('LIGHT', obj)
            light_items.select(scene, item)
        _opened(context, item)
        return {'FINISHED'}


class RS_MT_light_new(Menu):
    bl_idname = "RS_MT_light_new"
    bl_label = "New Light"

    def draw(self, _context):
        layout = self.layout
        names = dict((k, n) for k, n, _d in NEW_KINDS)
        for kind, icon in (('AREA', 'LIGHT_AREA'), ('SPOT', 'LIGHT_SPOT'), ('SUN', 'LIGHT_SUN'),
                           ('POINT', 'LIGHT_POINT')):
            layout.operator(RS_OT_light_new.bl_idname, icon=icon, text=names[kind]).kind = kind
        layout.separator()
        layout.operator(RS_OT_light_new.bl_idname, icon=PIN_ICON, text=names['PIN']).kind = 'PIN'


class RS_OT_light_remove(_ItemOperator, Operator):
    bl_idname = "render_studio.light_remove"
    bl_label = "Delete Light"
    bl_description = "Delete this light"
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        item = self.item(context)
        if item is None:
            return {'CANCELLED'}
        light_items.remove(context.scene, item)
        return {'FINISHED'}


class RS_OT_light_copy(_ItemOperator, Operator):
    bl_idname = "render_studio.light_copy"
    bl_label = "Duplicate"
    bl_description = "A copy of this light; the copy is opened"
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        item = self.item(context)
        if item is None:
            return {'CANCELLED'}
        new = _report_value_error(self, lambda: light_items.duplicate(context.scene, item))
        if new is None:
            return {'CANCELLED'}
        _opened(context, new)
        return {'FINISHED'}


class RS_OT_light_mirror(_ItemOperator, Operator):
    bl_idname = "render_studio.light_mirror"
    bl_label = "Mirror"
    bl_description = "Move this light to the other side of the product (left and right as the camera sees it)"
    bl_options = {'UNDO', 'INTERNAL'}

    copy: BoolProperty(default=False, options={'SKIP_SAVE'})
    everything: BoolProperty(default=False, options={'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        if props.everything:
            return "Flip the whole lighting: every light goes to the other side of the product"
        if props.copy:
            return "Leave this light and put a copy on the other side of the product"
        return cls.bl_description

    def execute(self, context):
        scene = context.scene
        if self.everything:
            looks.mirror_all(scene)
            return {'FINISHED'}
        item = self.item(context)
        if item is None:
            return {'CANCELLED'}
        new = _report_value_error(self, lambda: light_items.mirror(scene, item, copy=self.copy))
        if new is None:
            return {'CANCELLED'}
        if self.copy:
            _opened(context, new)
        return {'FINISHED'}


# ------------------------------------------------------------------ looks

class RS_OT_look_add(Operator):
    bl_idname = "render_studio.look_add"
    bl_label = "New Look"
    bl_description = ("Keep the lighting as it is and go on in a copy: lights, World, background, floor and "
                      "haze. Switch between the looks with their buttons to compare")
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        scene = context.scene
        first = not len(scene.render_studio.light_looks)
        name = looks.add(scene)
        if first:
            self.report({'INFO'}, "The lighting so far is look A. You are now in look %s: change it and "
                        "compare with the buttons" % name)
        return {'FINISHED'}


class RS_OT_look_switch(Operator):
    bl_idname = "render_studio.look_switch"
    bl_label = "Look"
    bl_description = "Switch to this look (the open look keeps what you changed)"
    bl_options = {'UNDO', 'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        notes = _report_value_error(self, lambda: looks.switch(context.scene, self.index))
        if notes is None:
            return {'CANCELLED'}
        for note in notes:
            self.report({'WARNING'}, note)
        return {'FINISHED'}


class RS_OT_look_remove(Operator):
    bl_idname = "render_studio.look_remove"
    bl_label = "Remove Look"
    bl_description = "Remove the open look and go back to the one next to it"
    bl_options = {'UNDO', 'INTERNAL'}

    def invoke(self, context, event):
        scene = context.scene
        index = looks.active(scene)
        if index < 0:
            return {'CANCELLED'}
        return context.window_manager.invoke_confirm(
            self, event, title="Remove look %s?" % scene.render_studio.light_looks[index].name, confirm_text="Remove")

    def execute(self, context):
        looks.remove(context.scene)
        return {'FINISHED'}


class RS_MT_environment(Menu):
    """The preset menu of the Environment tab: scenes (everything), light setups, looks of this file."""
    bl_idname = "RS_MT_environment"
    bl_label = "Presets"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        current = scene.render_studio.scene_preset
        layout.label(text="Scene (lights, World, backdrop, look)")
        for name in list(environment.BUILTIN_SCENES) + sorted(environment.saved_scenes()):
            layout.operator(env_ui.RS_OT_scene_preset.bl_idname, text=name, translate=False,
                            icon='CHECKMARK' if name == current else 'BLANK1').name = name
        if current and current in environment.saved_scenes():
            layout.operator(env_ui.RS_OT_scene_preset_delete.bl_idname, text="Delete %s" % current,
                            icon='TRASH').name = current
        layout.separator()
        layout.menu("RS_MT_light_presets", text="Only the Lights", icon='LIGHT')
        layout.separator()
        layout.operator(RS_OT_look_add.bl_idname, icon='DUPLICATE',
                        text="New Look" if len(scene.render_studio.light_looks) else "Compare Looks (A / B)")
        if looks.active(scene) >= 0:
            layout.operator(RS_OT_look_remove.bl_idname, icon='REMOVE')


def draw_head(layout, scene, settings):
    """[presets ▾] [A] [B] … [+ save scene] [settings]: the looks of this file sit between the two."""
    rs = scene.render_studio
    row = layout.row(align=True)
    row.scale_y = 1.2
    row.menu(RS_MT_environment.bl_idname, text=rs.scene_preset or "Preset", icon='PRESET')
    current = looks.active(scene)
    for index, look in enumerate(rs.light_looks):
        sub = row.row(align=True)
        sub.ui_units_x = 1.4
        sub.operator(RS_OT_look_switch.bl_idname, text=look.name, depress=index == current).index = index
    row.operator(env_ui.RS_OT_scene_preset_save.bl_idname, text="", icon='ADD')
    row.popover(settings, text="", icon='PREFERENCES')


# ------------------------------------------------------------------ the open light and the list

def _same(a, b):
    return a is not None and b is not None and a[0] == b[0] and a[1] == b[1]


def draw_open_light(layout, context):
    """The open light: [kind] [name] [brightness] [close], how a click on the product places it, its few
    values, the rest under "More". Without an open light: what to do."""
    scene = context.scene
    rs = scene.render_studio
    item = light_items.active_item(scene)
    box = layout.box()
    if item is None:
        sub = box.row()
        sub.active = False
        sub.label(text="Open a light below, then click on the product", icon='MOUSE_LMB_DRAG')
        return
    row = box.row(align=True)
    if item[0] == 'PIN':
        pin = rs.hdri_pins[item[1]]
        row.prop(pin, "on", text="", icon='HIDE_OFF' if pin.on else 'HIDE_ON')
        row.prop(pin, "name", text="", icon=PIN_ICON)
        power = row.row(align=True)
        power.ui_units_x = 4.5
        power.prop(pin, "strength", text="")
    else:
        obj = item[1]
        row.operator("render_studio.toggle_light", text="",
                     icon='HIDE_ON' if obj.hide_render else 'HIDE_OFF').name = obj.name
        row.prop(obj.render_studio_light, "short_name", text="", icon=LIGHT_ICONS.get(obj.data.type, 'LIGHT'))
        power = row.row(align=True)
        power.ui_units_x = 4.5
        power.prop(obj.data, "energy", text="")
    _set_item(row.operator(RS_OT_light_open.bl_idname, text="", icon='X'), item)
    if item[0] == 'PIN':
        _draw_pin(box, context, item)
    else:
        _draw_light(box, context, item)


def _draw_row(col, scene, item, active):
    rs = scene.render_studio
    is_open = _same(item, active)
    row = col.row(align=True)
    if item[0] == 'PIN':
        pin = rs.hdri_pins[item[1]]
        row.prop(pin, "on", text="", icon='HIDE_OFF' if pin.on else 'HIDE_ON', emboss=False)
        sub = row.row(align=True)
        sub.active = pin.on
        op = sub.operator(RS_OT_light_open.bl_idname, text=pin.name, depress=is_open, icon=PIN_ICON, translate=False)
        op.pin = item[1]
        power = sub.row(align=True)
        power.ui_units_x = 4.5
        power.prop(pin, "strength", text="")
        return
    obj = item[1]
    on = not obj.hide_render
    row.operator("render_studio.toggle_light", text="", emboss=False,
                 icon='HIDE_OFF' if on else 'HIDE_ON').name = obj.name
    sub = row.row(align=True)
    sub.active = on
    solo = env_ui.solo_light(scene) == obj.name
    sub.operator(RS_OT_light_open.bl_idname, text=obj.name, depress=is_open, translate=False,
                 icon='SOLO_ON' if solo else LIGHT_ICONS.get(obj.data.type, 'LIGHT')).name = obj.name
    power = sub.row(align=True)
    power.ui_units_x = 4.5
    power.prop(obj.data, "energy", text="")


def draw_list(box, context):
    """The lights that are on: eye, name (click: open it), brightness. The ones that are off fold into
    one line. The open light's settings are above the list."""
    scene = context.scene
    items = light_items.all_items(scene)
    if not items:
        sub = box.row()
        sub.active = False
        sub.label(text="Pick a preset or add a light", icon='INFO')
        return
    active = light_items.active_item(scene)
    on = [item for item in items if light_items.is_on(scene, item)]
    off = [item for item in items if not light_items.is_on(scene, item)]
    col = box.column(align=True)
    for item in on:
        _draw_row(col, scene, item, active)
    if off and subsection(box, "lights_off", "%d off" % len(off), default_open=not on):
        col = box.column(align=True)
        for item in off:
            _draw_row(col, scene, item, active)


def _draw_actions(box, item):
    """Mirror, mirrored copy, duplicate, delete."""
    row = box.row(align=True)
    op = row.operator(RS_OT_light_mirror.bl_idname, text="Mirror", icon='MOD_MIRROR')
    _set_item(op, item)
    op = row.operator(RS_OT_light_mirror.bl_idname, text="Mirrored Copy")
    _set_item(op, item)
    op.copy = True
    _set_item(row.operator(RS_OT_light_copy.bl_idname, text="", icon='DUPLICATE'), item)
    _set_item(row.operator(RS_OT_light_remove.bl_idname, text="", icon='TRASH'), item)


def _draw_pin(box, context, item):
    pin = context.scene.render_studio.hdri_pins[item[1]]
    light_paint.draw_place_row(box, context)
    col = box.column(align=True)
    col.prop(pin, "size", slider=True)
    col.prop(pin, "softness", slider=True)
    row = col.row(align=True)
    row.prop(pin, "kelvin", text="Kelvin", slider=True)
    sub = row.row(align=True)
    sub.ui_units_x = 2
    sub.prop(pin, "color", text="")
    if subsection(box, "light_more", "More"):
        box.row(align=True).prop(pin, "shape", expand=True)
        col = box.column(align=True)
        row = col.row(align=True)
        row.prop(pin, "width")
        row.prop(pin, "height")
        row = col.row(align=True)
        row.prop(pin, "horizontal", text="Around")
        row.prop(pin, "vertical")
        _draw_actions(box, item)


def _draw_light(box, context, item):
    scene = context.scene
    rs = scene.render_studio
    light = item[1]
    data = light.data
    own = light.render_studio_light
    light_paint.draw_place_row(box, context)
    col = box.column(align=True)
    if data.type == 'SUN':
        col.prop(data, "angle", text="Size", slider=True)
    else:
        col.prop(own, "size")
    if data.type in {'AREA', 'SPOT'}:
        col.prop(own, "softness", slider=True)
    row = col.row(align=True)
    row.prop(own, "kelvin", text="Kelvin", slider=True)
    sub = row.row(align=True)
    sub.ui_units_x = 2
    sub.prop(data, "color", text="")
    if not subsection(box, "light_more", "More"):
        return
    row = box.row(align=True)
    row.prop(data, "type", expand=True, icon_only=True)
    row.separator()
    row.operator(env_ui.RS_OT_solo_light.bl_idname, text="", icon=env_ui.solo_icon(scene, light)).name = light.name
    col = box.column(align=True)
    if data.type == 'AREA':
        col.prop(data, "shape", text="")
        if data.shape in {'RECTANGLE', 'ELLIPSE'}:
            row = col.row(align=True)
            row.prop(data, "size", text="W")
            row.prop(data, "size_y", text="H")
    elif data.type == 'SPOT':
        col.prop(data, "spot_size", text="Angle")
    box.prop(own, "target", text="", icon='PIVOT_CURSOR',
             placeholder=("Target: " + rs.light_orbit_target.name) if rs.light_orbit_target else "Target")
    target = lights.light_target(light, rs)
    if target is not None and target != light:
        col = box.column(align=True)
        col.prop(rs, "light_orbit_horizontal")
        col.prop(rs, "light_orbit_vertical")
        col.prop(rs, "light_orbit_distance")
        col.prop(own, "height")
    env_ui.draw_light_extras(box, light)
    _draw_actions(box, item)


classes = (RS_OT_light_open, RS_OT_light_new, RS_MT_light_new, RS_MT_environment, RS_OT_light_remove, RS_OT_light_copy,
           RS_OT_light_mirror, RS_OT_look_add, RS_OT_look_switch, RS_OT_look_remove)


def register():
    bpy.types.WindowManager.render_studio_place_off = BoolProperty(options={'SKIP_SAVE'})
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.render_studio_place_off

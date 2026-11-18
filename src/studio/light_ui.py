# SPDX-License-Identifier: GPL-3.0-or-later
"""The Lights list of the Environment tab: one list for the lights of the scene and of the studio World,
the looks row above it, and their operators (logic in light_items.py and looks.py)."""

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Menu, Operator

from . import env_ui, environment, light_items, light_paint, lights, lightsets, looks
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
            return ('PIN', self.pin) if self.pin < len(scene.studio.hdri_pins) else None
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
    return not getattr(context.window_manager, "studio_place_off", False)


def _opened(context, item):
    """A light was opened: select it in the viewport and make clicks on the product place it."""
    if item[0] == 'LIGHT':
        _select_only(context, item[1])
    if _placing_wanted(context):
        light_paint.activate_tool(context)


class STUDIO_OT_light_open(_ItemOperator, Operator):
    bl_idname = "studio.light_open"
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
        if item[0] == 'LIGHT':
            flash(item[1])
        return {'FINISHED'}


_flashing = {}     # light data session_uid -> (color, use_temperature) to put back


def _light_data(uid):
    return next((d for d in bpy.data.lights if d.session_uid == uid), None)


def flash(obj, seconds=0.6):
    """The opened light glows red for a moment, so you see which one it is. Found again by its data's
    session id, not by name (a rename in between left it red), and only after the undo step: the file
    never keeps the red."""
    if obj is None or obj.type != 'LIGHT':
        return
    obj[lightsets.WRANGLER_KEY] = obj.data.type          # Light Wrangler must not swap it meanwhile
    uid = obj.data.session_uid

    def red():
        data = _light_data(uid)
        if data is None:
            return None
        if uid not in _flashing:
            _flashing[uid] = (tuple(data.color), getattr(data, "use_temperature", False))
        if hasattr(data, "use_temperature"):
            data.use_temperature = False
        data.color = (1.0, 0.04, 0.02)
        bpy.app.timers.register(back, first_interval=seconds)
        return None

    def back():
        saved = _flashing.pop(uid, None)
        data = _light_data(uid)
        if data is not None and saved is not None:
            data.color = saved[0]
            if hasattr(data, "use_temperature"):
                data.use_temperature = saved[1]
        return None

    bpy.app.timers.register(red, first_interval=0.0)


NEW_KINDS = (
    ('AREA', "Area Light", "A soft light with a size, front left, aimed at the product"),
    ('SPOT', "Spot Light", "A cone of light, for accents and hard shadows"),
    ('SUN', "Sun Light", "Parallel light with sharp shadows"),
    ('POINT', "Point Light", "A bulb that shines to all sides"),
    ('PIN', "Light in the Studio World", "A softbox painted into the environment: it lights and shows in "
     "reflections, but has no place in the room. Switches the World to Studio"),
)


class STUDIO_OT_light_new(Operator):
    bl_idname = "studio.light_new"
    bl_label = "New Light"
    bl_description = "Add a light"
    bl_options = {'UNDO', 'INTERNAL'}

    kind: EnumProperty(items=NEW_KINDS, default='AREA', options={'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return dict((k, d) for k, _n, d in NEW_KINDS).get(props.kind, cls.bl_description)

    def execute(self, context):
        scene = context.scene
        rs = scene.studio
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


class STUDIO_MT_light_new(Menu):
    bl_idname = "STUDIO_MT_light_new"
    bl_label = "New Light"

    def draw(self, _context):
        layout = self.layout
        names = dict((k, n) for k, n, _d in NEW_KINDS)
        for kind, icon in (('AREA', 'LIGHT_AREA'), ('SPOT', 'LIGHT_SPOT'), ('SUN', 'LIGHT_SUN'),
                           ('POINT', 'LIGHT_POINT')):
            layout.operator(STUDIO_OT_light_new.bl_idname, icon=icon, text=names[kind]).kind = kind
        layout.separator()
        layout.operator(STUDIO_OT_light_new.bl_idname, icon=PIN_ICON, text=names['PIN']).kind = 'PIN'


class STUDIO_OT_light_remove(_ItemOperator, Operator):
    bl_idname = "studio.light_remove"
    bl_label = "Delete Light"
    bl_description = "Delete this light"
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        item = self.item(context)
        if item is None:
            return {'CANCELLED'}
        light_items.remove(context.scene, item)
        return {'FINISHED'}


class STUDIO_OT_light_copy(_ItemOperator, Operator):
    bl_idname = "studio.light_copy"
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


class STUDIO_OT_light_mirror(_ItemOperator, Operator):
    bl_idname = "studio.light_mirror"
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

class STUDIO_OT_look_add(Operator):
    bl_idname = "studio.look_add"
    bl_label = "New Look"
    bl_description = ("Keep the lighting as it is and go on in a copy: lights, World, background, floor and "
                      "haze. Switch between the looks with their buttons to compare")
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        scene = context.scene
        first = not looks.names(scene)
        name = looks.add(scene)
        if first:
            self.report({'INFO'}, "The lighting so far is variant A. You are now in %s: change it and "
                        "compare with the buttons" % name)
        return {'FINISHED'}


class STUDIO_OT_look_switch(Operator):
    bl_idname = "studio.look_switch"
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


class STUDIO_OT_look_remove(Operator):
    bl_idname = "studio.look_remove"
    bl_label = "Remove Look"
    bl_description = "Remove the open look and go back to the one next to it"
    bl_options = {'UNDO', 'INTERNAL'}

    def invoke(self, context, event):
        scene = context.scene
        index = looks.active(scene)
        if index < 0:
            return {'CANCELLED'}
        return context.window_manager.invoke_confirm(
            self, event, title="Remove variant %s?" % looks.names(scene)[index], confirm_text="Remove")

    def execute(self, context):
        looks.remove(context.scene)
        return {'FINISHED'}


class STUDIO_MT_environment(Menu):
    """The preset menu of the Environment tab: scenes (everything), light setups, looks of this file."""
    bl_idname = "STUDIO_MT_environment"
    bl_label = "Presets"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        current = scene.studio.scene_preset
        layout.label(text="Scene (lights, World, backdrop, look)")
        for name in list(environment.BUILTIN_SCENES) + sorted(environment.saved_scenes()):
            layout.operator(env_ui.STUDIO_OT_scene_preset.bl_idname, text=name, translate=False,
                            icon='CHECKMARK' if name == current else 'BLANK1').name = name
        if current and current in environment.saved_scenes():
            layout.operator(env_ui.STUDIO_OT_scene_preset_delete.bl_idname, text="Delete %s" % current,
                            icon='TRASH').name = current
        layout.separator()
        layout.menu("STUDIO_MT_light_presets", text="Only the Lights", icon='LIGHT')
        layout.separator()
        layout.operator(STUDIO_OT_look_add.bl_idname, icon='DUPLICATE',
                        text="New Look" if looks.names(scene) else "Compare Looks (A / B)")
        if looks.active(scene) >= 0:
            layout.operator(STUDIO_OT_look_remove.bl_idname, icon='REMOVE')


def draw_head(layout, scene, settings):
    """With light sets: [camera ▾ (templates, saved sets, from another camera, whole scene)] [A] [B] [−] [+]
    [settings]. Before: [presets ▾] [A] [B] … [+ save scene] [settings]."""
    rs = scene.studio
    if lightsets.enabled(scene):
        row = layout.row(align=True)
        row.scale_y = 1.2
        cam = scene.camera
        row.menu(STUDIO_MT_light_set.bl_idname, text=cam.name if cam else "No Camera", icon='CAMERA_DATA',
                 translate=False)
        current = looks.active(scene)
        found = looks.names(scene)
        for index, name in enumerate(found):
            sub = row.row(align=True)
            sub.ui_units_x = 1.4
            sub.operator(STUDIO_OT_look_switch.bl_idname, text=name, depress=index == current,
                         translate=False).index = index
        if found:
            row.operator(STUDIO_OT_look_remove.bl_idname, text="", icon='REMOVE')
        row.operator(STUDIO_OT_look_add.bl_idname, text="", icon='ADD')
        row.popover(settings, text="", icon='PREFERENCES')
        return
    row = layout.row(align=True)
    row.scale_y = 1.2
    row.menu(STUDIO_MT_environment.bl_idname, text=rs.scene_preset or "Preset", icon='PRESET')
    current = looks.active(scene)
    for index, name in enumerate(looks.names(scene)):
        sub = row.row(align=True)
        sub.ui_units_x = 1.4
        sub.operator(STUDIO_OT_look_switch.bl_idname, text=name, depress=index == current).index = index
    row.operator(env_ui.STUDIO_OT_scene_preset_save.bl_idname, text="", icon='ADD')
    row.popover(settings, text="", icon='PREFERENCES')


# ------------------------------------------------------------------ the open light and the list

def _same(a, b):
    return a is not None and b is not None and a[0] == b[0] and a[1] == b[1]


def draw_open_light(layout, context):
    """The open light: [kind] [name] [brightness] [close], how a click on the product places it, its few
    values, the rest under "More". Without an open light: what to do."""
    scene = context.scene
    rs = scene.studio
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
    else:
        obj = item[1]
        row.operator("studio.toggle_light", text="",
                     icon='HIDE_ON' if obj.hide_render else 'HIDE_OFF').name = obj.name
        row.prop(obj.studio_light, "short_name", text="", icon=LIGHT_ICONS.get(obj.data.type, 'LIGHT'))
    if item[0] != 'PIN':
        from . import keys
        keys.button(row, 'LIGHT')
    _set_item(row.operator(STUDIO_OT_light_open.bl_idname, text="", icon='X'), item)
    if item[0] == 'PIN':
        _draw_pin(box, context, item)
    else:
        _draw_light(box, context, item)


def _draw_row(col, scene, item, active, label=None):
    rs = scene.studio
    is_open = _same(item, active)
    row = col.row(align=True)
    if item[0] == 'PIN':
        pin = rs.hdri_pins[item[1]]
        row.prop(pin, "on", text="", icon='HIDE_OFF' if pin.on else 'HIDE_ON', emboss=False)
        sub = row.row(align=True)
        sub.active = pin.on
        op = sub.operator(STUDIO_OT_light_open.bl_idname, text=label or pin_label(scene, item[1]), depress=is_open,
                          icon=PIN_ICON, translate=False)
        op.pin = item[1]
        power = sub.row(align=True)
        power.ui_units_x = 4.5
        power.prop(pin, "strength", text="")
        return
    obj = item[1]
    on = not obj.hide_render
    row.operator("studio.toggle_light", text="", emboss=False,
                 icon='HIDE_OFF' if on else 'HIDE_ON').name = obj.name
    sub = row.row(align=True)
    sub.active = on
    solo = env_ui.solo_light(scene) == obj.name
    sub.operator(STUDIO_OT_light_open.bl_idname, text=label or obj.name, depress=is_open, translate=False,
                 icon='SOLO_ON' if solo else LIGHT_ICONS.get(obj.data.type, 'LIGHT')).name = obj.name
    power = sub.row(align=True)
    power.ui_units_x = 4.5
    power.prop(obj.data, "energy", text="")


def pin_label(scene, index):
    """A studio World light without a name of its own: "World Light 2", not "Light"."""
    name = scene.studio.hdri_pins[index].name
    return name if name and name != "Light" else "World Light %d" % (index + 1)


ROLE_ORDER = ("key", "fill", "rim", "kontur", "edge", "kante", "bounce", "accent", "akzent")


def role_groups(objs):
    """[(group word, [(light, short label)])] by the first word of the name's role:
    "A_rim-pod-l_01" → group "rim", label "pod-l"."""
    from ..naming import role_of
    from . import cameras
    prefixes = cameras._prefixes()
    named = [(obj, role_of(obj.name, prefixes, "light").split("-")) for obj in objs]
    # a word all of them start with ("pack-kontur-links", "pack-pods-flaeche") says nothing here
    while len(named) > 1 and all(len(w) > 1 for _o, w in named) and len({w[0] for _o, w in named}) == 1:
        named = [(obj, words[1:]) for obj, words in named]
    groups = {}
    for obj, words in named:
        label = "-".join(words[1:]) or words[0]
        groups.setdefault(words[0], []).append((obj, label))
    def order(word):
        return (ROLE_ORDER.index(word) if word in ROLE_ORDER else len(ROLE_ORDER), word)
    return [(word, groups[word]) for word in sorted(groups, key=order)]


def _draw_group(box, scene, key, title, rows, active, icon='NONE', default_open=True):
    """A title line that folds, then its lights; folded it still says how many are on."""
    on = sum(1 for item, _label in rows if light_items.is_on(scene, item))
    text = "%s   %d / %d" % (title, on, len(rows)) if on != len(rows) else "%s   %d" % (title, len(rows))
    if not subsection(box, key, text, default_open=default_open):
        return
    col = box.column(align=True)
    for item, label in rows:
        _draw_row(col, scene, item, active, label)


def draw_list(box, context):
    """The lights of the camera you look through, by role (Key, Fill, Rim …), then the lights of all cameras,
    lights without a camera and the lights of the studio World. Before light sets: one list as always."""
    scene = context.scene
    if lightsets.enabled(scene):
        _draw_sets(box, context)
        return
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


def _draw_sets(box, context):
    scene = context.scene
    active = light_items.active_item(scene)
    own, shared, loose = lightsets.groups(scene)
    mine = lightsets.set_lights(own)
    if not mine:
        col = box.column(align=True)
        sub = col.row()
        sub.active = False
        sub.label(text="This camera has no lights yet", icon='INFO')
        row = col.row(align=True)
        row.menu(STUDIO_MT_light_templates.bl_idname, text="From Template", icon='LIGHT')
        row.menu(STUDIO_MT_light_from_camera.bl_idname, text="From Camera", icon='CAMERA_DATA')
    for word, rows in role_groups(mine):
        _draw_group(box, scene, "lights_role_" + word, word.replace("-", " ").title(),
                    [(('LIGHT', obj), label) for obj, label in rows], active)
    shared_lights = lightsets.set_lights(shared)
    if shared_lights:
        _draw_group(box, scene, "lights_shared", "All Cameras",
                    [(('LIGHT', obj), obj.name) for obj in shared_lights], active)
    if loose:
        _draw_group(box, scene, "lights_loose", "Without Camera",
                    [(('LIGHT', obj), obj.name) for obj in loose], active)
    if light_items.pins_shown(scene) and len(scene.studio.hdri_pins):
        _draw_group(box, scene, "lights_world", "Studio World (all cameras)",
                    [(('PIN', i), pin_label(scene, i)) for i in range(len(scene.studio.hdri_pins))], active,
                    default_open=False)


def _draw_actions(box, item):
    """Mirror, mirrored copy, duplicate, delete."""
    row = box.row(align=True)
    op = row.operator(STUDIO_OT_light_mirror.bl_idname, text="Mirror", icon='MOD_MIRROR')
    _set_item(op, item)
    op = row.operator(STUDIO_OT_light_mirror.bl_idname, text="Mirrored Copy")
    _set_item(op, item)
    op.copy = True
    _set_item(row.operator(STUDIO_OT_light_copy.bl_idname, text="", icon='DUPLICATE'), item)
    _set_item(row.operator(STUDIO_OT_light_remove.bl_idname, text="", icon='TRASH'), item)


def _draw_pin(box, context, item):
    pin = context.scene.studio.hdri_pins[item[1]]
    light_paint.draw_place_row(box, context)
    col = box.column(align=True)
    col.prop(pin, "strength", text="Strength")
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
    rs = scene.studio
    light = item[1]
    data = light.data
    own = light.studio_light
    light_paint.draw_place_row(box, context)
    col = box.column(align=True)
    col.prop(data, "energy", text="Strength")
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
    # Kind of light, solo, only the product
    row = box.row(align=True)
    row.prop(data, "type", expand=True, icon_only=True)
    row.separator()
    row.operator(env_ui.STUDIO_OT_solo_light.bl_idname, text="", icon=env_ui.solo_icon(scene, light)).name = light.name
    row.operator(env_ui.STUDIO_OT_light_link.bl_idname, text="", icon='LINKED',
                 depress=environment.linked_to_product(light)).name = light.name
    if data.type == 'AREA':
        row = box.row(align=True)
        row.prop(data, "shape", text="")
        if data.shape in {'RECTANGLE', 'ELLIPSE'}:
            row.prop(data, "size", text="W")
            row.prop(data, "size_y", text="H")
    elif data.type == 'SPOT':
        box.prop(data, "spot_size", text="Angle")
    draw_gobo(box, context, light)
    draw_owner(box, scene, light)
    _draw_actions(box, item)


# ------------------------------------------------------------------ gobo / light texture with previews

_gobo_items = []       # kept alive for the enum (Blender keeps only pointers to the strings)


def _gobo_enum(_self, _context):
    from . import gobo_previews
    _gobo_items.clear()
    _gobo_items.append(('NONE', "None", "No gobo: the light as it is", gobo_previews.icon('NONE'), 0))
    for index, (key, name, desc) in enumerate(environment.FLAG_PATTERNS):
        _gobo_items.append((key, name, desc, gobo_previews.icon(key), index + 1))
    return _gobo_items


def _gobo_get(_self):
    scene = bpy.context.scene
    light = scene.studio.active_light if scene else None
    flag = environment.flag_of(light) if light is not None else None
    if flag is None:
        return 0
    keys = [k for k, _n, _d in environment.FLAG_PATTERNS]
    pattern = flag.get("as_pattern", 'FLAG')
    return keys.index(pattern) + 1 if pattern in keys else 0


def _gobo_set(_self, value):
    scene = bpy.context.scene
    light = scene.studio.active_light if scene else None
    if light is None or light.type != 'LIGHT':
        return
    if value <= 0:
        environment.remove_flag(light)
        return
    key = environment.FLAG_PATTERNS[value - 1][0]
    flag = environment.flag_of(light)
    if flag is None or flag.get("as_pattern") != key:
        environment.add_flag(scene, light, key)


def draw_gobo(layout, context, light):
    """Gobo / light texture: a picker with a picture of each pattern; moving ones get speed and scale."""
    col = layout.column(align=True)
    col.label(text="Gobo / Texture", icon='MOD_MASK')
    col.template_icon_view(context.window_manager, "studio_gobo", show_labels=True, scale=5.0, scale_popup=4.0)
    flag = environment.flag_of(light)
    if flag is not None:
        speed, scale = environment.gobo_nodes(flag)
        if speed is not None and scale is not None:
            row = col.row(align=True)
            row.prop(speed, "default_value", text="Speed")
            row.prop(scale, "default_value", text="Scale")


# ------------------------------------------------------------------ light sets (lightsets.py)

class STUDIO_OT_light_share(Operator):
    bl_idname = "studio.light_share"
    bl_label = "Belongs To"
    bl_description = "Which camera this light belongs to"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    shared: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        if props.shared:
            return "Light every camera with it (it moves into \"All Cameras\")"
        return "Only the camera you look through has this light"

    def execute(self, context):
        obj = bpy.data.objects.get(self.name)
        if obj is None or obj.type != 'LIGHT':
            return {'CANCELLED'}
        lightsets.make_shared(context.scene, obj, self.shared)
        return {'FINISHED'}


class STUDIO_OT_light_take(Operator):
    bl_idname = "studio.light_take"
    bl_label = "Lights From Camera"
    bl_description = ("This camera gets a copy of the lights of that camera (with its variants A / B). "
                      "Its own lights so far go to the archive")
    bl_options = {'UNDO', 'INTERNAL'}

    camera: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        scene = context.scene
        other = bpy.data.objects.get(self.camera)
        if scene.camera is None or other is None:
            return {'CANCELLED'}
        if _report_value_error(self, lambda: lightsets.take_from(scene, scene.camera, other)) is None:
            return {'CANCELLED'}
        light_items.select(scene, None)
        return {'FINISHED'}


class STUDIO_OT_light_set_clear(Operator):
    bl_idname = "studio.light_set_clear"
    bl_label = "Clear Lights of This Camera"
    bl_description = "The lights of this camera go to the archive (switched off), the camera starts empty"
    bl_options = {'UNDO', 'INTERNAL'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event, confirm_text="Clear")

    def execute(self, context):
        scene = context.scene
        coll = lightsets.set_of(scene, scene.camera)
        if coll is not None:
            lightsets.clear_set(scene, coll)
            light_items.select(scene, None)
        return {'FINISHED'}


_source_items = []      # kept alive: Blender only keeps a pointer to the strings of an enum callback


def _sources(_self, context):
    _source_items.clear()
    _source_items.extend(lightsets.sources(context.scene))
    return _source_items


class STUDIO_LightSetSource(bpy.types.PropertyGroup):
    camera: StringProperty()
    source: EnumProperty(name="Lights", items=_sources)


class STUDIO_OT_light_sets_setup(Operator):
    bl_idname = "studio.light_sets_setup"
    bl_label = "Lights per Camera"
    bl_description = ("Give every camera its own lights: pick for each camera where its lights come from "
                      "(a light collection, a look or the lights as now). Nothing is deleted: lights no camera "
                      "takes go to the archive, switched off")
    bl_options = {'UNDO', 'INTERNAL'}

    def invoke(self, context, _event):
        from . import cameras
        scene = context.scene
        rows = context.window_manager.studio_light_setup
        rows.clear()
        keys = {key for key, _n, _d in lightsets.sources(scene)}
        for cam in cameras.scene_cameras(scene):
            row = rows.add()
            row.camera = cam.name
            guess = lightsets.guess(scene, cam)
            row.source = guess if guess in keys else 'CURRENT'
        return context.window_manager.invoke_props_dialog(self, width=520, confirm_text="Set Up")

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.label(text="Each camera gets its own lights. The World stays the same for all.", icon='INFO')
        col.label(text="Lights nobody takes go to the archive (switched off), nothing is deleted.")
        grid = layout.grid_flow(row_major=True, columns=2, even_columns=False, align=True)
        for row in context.window_manager.studio_light_setup:
            grid.label(text=row.camera, icon='CAMERA_DATA', translate=False)
            grid.prop(row, "source", text="")

    def execute(self, context):
        scene = context.scene
        mapping = {row.camera: row.source for row in context.window_manager.studio_light_setup}
        lightsets.setup(scene, mapping)
        light_items.select(scene, None)
        self.report({'INFO'}, "Every camera has its own lights now: switch cameras to see them")
        return {'FINISHED'}


class STUDIO_MT_light_templates(Menu):
    bl_idname = "STUDIO_MT_light_templates"
    bl_label = "Light Templates"

    def draw(self, context):
        from .tab_light import STUDIO_OT_light_preset, current_preset
        layout = self.layout
        current = current_preset(context.scene)
        for name in lights.BUILTIN_PRESETS:
            layout.operator(STUDIO_OT_light_preset.bl_idname, text=name,
                            icon='CHECKMARK' if name == current else 'BLANK1').name = name


class STUDIO_MT_light_saved(Menu):
    bl_idname = "STUDIO_MT_light_saved"
    bl_label = "My Light Sets"

    def draw(self, context):
        from .tab_light import (STUDIO_OT_light_preset, STUDIO_OT_light_preset_delete, STUDIO_OT_light_preset_save,
                                current_preset)
        layout = self.layout
        current = current_preset(context.scene)
        saved = sorted(lights.saved_presets())
        for name in saved:
            layout.operator(STUDIO_OT_light_preset.bl_idname, text=name, translate=False,
                            icon='CHECKMARK' if name == current else 'BLANK1').name = name
        if saved:
            layout.separator()
        layout.operator(STUDIO_OT_light_preset_save.bl_idname, text="Save These Lights …", icon='ADD')
        if current and current in saved:
            layout.operator(STUDIO_OT_light_preset_delete.bl_idname, text="Delete %s" % current,
                            icon='TRASH', translate=False).name = current


class STUDIO_MT_light_from_camera(Menu):
    bl_idname = "STUDIO_MT_light_from_camera"
    bl_label = "Lights From Camera"

    def draw(self, context):
        from . import cameras
        layout = self.layout
        scene = context.scene
        others = [c for c in cameras.scene_cameras(scene)
                  if c != scene.camera and lightsets.set_lights(lightsets.set_of(scene, c))]
        if not others:
            layout.label(text="No other camera has lights yet")
        for cam in others:
            n = len(lightsets.set_lights(lightsets.set_of(scene, cam)))
            layout.operator(STUDIO_OT_light_take.bl_idname, text="%s  (%d)" % (cam.name, n), icon='CAMERA_DATA',
                            translate=False).camera = cam.name


class STUDIO_MT_light_set(Menu):
    """The menu in the head of the Light tab: where the lights of this camera come from."""
    bl_idname = "STUDIO_MT_light_set"
    bl_label = "Lights of This Camera"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        cam = scene.camera
        layout.label(text="Lights of %s" % (cam.name if cam else "?"), icon='CAMERA_DATA')
        layout.separator()
        layout.menu(STUDIO_MT_light_templates.bl_idname, text="Template", icon='LIGHT')
        layout.menu(STUDIO_MT_light_saved.bl_idname, text="My Light Sets", icon='BOOKMARKS')
        layout.menu(STUDIO_MT_light_from_camera.bl_idname, text="From Another Camera", icon='CAMERA_DATA')
        layout.separator()
        layout.menu(env_ui.STUDIO_MT_scene_presets.bl_idname, text="Whole Scene (Lights, World, Backdrop)",
                    icon='SCENE_DATA')
        layout.operator(env_ui.STUDIO_OT_scene_preset_save.bl_idname, text="Save Whole Scene …", icon='ADD')
        layout.separator()
        layout.operator(STUDIO_OT_light_set_clear.bl_idname, icon='TRASH')


def draw_setup_hint(layout, scene):
    """Files from before: one box that offers the lights per camera."""
    if not lightsets.needs_setup(scene):
        return
    box = layout.box()
    col = box.column(align=True)
    col.label(text="Every camera can have its own lights", icon='CAMERA_DATA')
    sub = col.row()
    sub.active = False
    sub.label(text="Fewer lights in the list, A / B per camera, World shared")
    box.operator(STUDIO_OT_light_sets_setup.bl_idname, text="Lights per Camera …", icon='LIGHT')


def draw_owner(layout, scene, light):
    """[this camera] [All Cameras]: which camera the open light belongs to."""
    if not lightsets.enabled(scene) or scene.camera is None:
        return
    coll = lightsets.owner_of(scene, light)
    shared = coll is not None and lightsets.is_shared(coll)
    row = layout.row(align=True)
    row.label(text="Belongs to")
    op = row.operator(STUDIO_OT_light_share.bl_idname, text=scene.camera.name, translate=False,
                      depress=coll is not None and not shared, icon='CAMERA_DATA')
    op.name, op.shared = light.name, False
    op = row.operator(STUDIO_OT_light_share.bl_idname, text="All Cameras", depress=shared, icon='LINKED')
    op.name, op.shared = light.name, True



classes = (STUDIO_LightSetSource, STUDIO_OT_light_share, STUDIO_OT_light_take, STUDIO_OT_light_set_clear,
           STUDIO_OT_light_sets_setup, STUDIO_MT_light_templates, STUDIO_MT_light_saved, STUDIO_MT_light_from_camera,
           STUDIO_MT_light_set, STUDIO_OT_light_open, STUDIO_OT_light_new, STUDIO_MT_light_new, STUDIO_MT_environment, STUDIO_OT_light_remove, STUDIO_OT_light_copy,
           STUDIO_OT_light_mirror, STUDIO_OT_look_add, STUDIO_OT_look_switch, STUDIO_OT_look_remove)


def register():
    bpy.types.WindowManager.studio_place_off = BoolProperty(options={'SKIP_SAVE'})
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.studio_light_setup = bpy.props.CollectionProperty(type=STUDIO_LightSetSource)
    bpy.types.WindowManager.studio_gobo = EnumProperty(
        name="Gobo / Texture", description="A card in front of the open light that shapes its light",
        items=_gobo_enum, get=_gobo_get, set=_gobo_set, options={'SKIP_SAVE'})
    lightsets.register()


def unregister():
    lightsets.unregister()
    from . import gobo_previews
    gobo_previews.unregister()
    del bpy.types.WindowManager.studio_gobo
    del bpy.types.WindowManager.studio_light_setup
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.studio_place_off

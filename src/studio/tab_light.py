# SPDX-License-Identifier: GPL-3.0-or-later
"""The Environment tab: scene presets, lights, World / HDRI, background, floor, other add-ons."""

import math
import os

import bpy
from bpy.props import (BoolProperty, FloatProperty, IntProperty, StringProperty)
from bpy.types import Menu, Operator, Panel

from . import addons, api, background, env_ui, hdri, lights, presets_io, runner
from .ui_common import (LIGHT_TAB_ORDER, _SidebarPanel, _report_value_error, _select_only, _unpin, section,
                        studio_tab)


class RS_OT_select_light(Operator):
    bl_idname = "render_studio.select_light"
    bl_label = "Select Light"
    bl_description = "Show the settings of this light (click again to close) and select it in the viewport"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        obj = bpy.data.objects.get(self.name)
        if obj is None or obj.type != 'LIGHT':
            return {'CANCELLED'}
        rs = context.scene.render_studio
        if rs.active_light == obj and context.active_object != obj:
            rs.active_light = None   # close the details
            return {'FINISHED'}
        rs.active_light = obj
        _select_only(context, obj)
        return {'FINISHED'}


class RS_OT_add_light(Operator):
    bl_idname = "render_studio.add_light"
    bl_label = "New Light"
    bl_description = ("Adds an area light front left, aimed at the orbit object "
                      "(or the 3D cursor if no orbit object is picked)")
    bl_options = {'UNDO'}

    def execute(self, context):
        scene = context.scene
        rs = scene.render_studio
        obj = lights.add_light(scene, rs.light_orbit_target)
        rs.active_light = obj
        _select_only(context, obj)
        return {'FINISHED'}


class RS_OT_duplicate_light(Operator):
    bl_idname = "render_studio.duplicate_light"
    bl_label = "Duplicate Light"
    bl_description = "Duplicates the light (like Shift+D) and edits the copy"
    bl_options = {'UNDO'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        scene = context.scene
        rs = scene.render_studio
        src = bpy.data.objects.get(self.name) if self.name else rs.active_light
        if src is None or src.type != 'LIGHT':
            return {'CANCELLED'}
        obj = lights.duplicate_light(scene, src)
        rs.active_light = obj
        _select_only(context, obj)
        return {'FINISHED'}


class RS_OT_light_preset(Operator):
    bl_idname = "render_studio.light_preset"
    bl_label = "Light Preset"
    bl_description = ("Places the lights of this preset around the light orbit object (or the 3D cursor), "
                      "relative to the active camera. Replaces the lights of the previous preset, "
                      "your own lights stay")
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        scene = context.scene
        rs = scene.render_studio
        spec = lights.preset(self.name)
        if spec is None:
            self.report({'ERROR'}, "Preset %r not found" % self.name)
            return {'CANCELLED'}
        created = _report_value_error(self, lambda: lights.apply_setup(scene, rs.light_orbit_target, spec))
        if not created:
            return {'CANCELLED'}
        rs.active_light = created[0]
        return {'FINISHED'}


class RS_OT_light_preset_save(Operator):
    bl_idname = "render_studio.light_preset_save"
    bl_label = "Save Light Setup"
    bl_description = ("Saves all lights of the scene as a preset (available in every blend file). "
                      "Positions are stored relative to the light orbit object and the camera")
    bl_options = {'UNDO'}

    name: StringProperty(name="Name", default="My Setup")

    @classmethod
    def poll(cls, context):
        return any(o.type == 'LIGHT' for o in context.scene.objects)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        scene = context.scene
        name = self.name.strip() or "My Setup"
        if name in lights.BUILTIN_PRESETS:
            self.report({'ERROR'}, "%r is a built-in preset, pick another name" % name)
            return {'CANCELLED'}
        spec = _report_value_error(
            self, lambda: lights.setup_from_scene(scene, scene.render_studio.light_orbit_target, name))
        if spec is None:
            return {'CANCELLED'}
        lights.save_preset(spec)
        # These lights are now the current setup: the next preset replaces them.
        for obj in lights.scene_lights(scene):
            obj[lights.PRESET_KEY] = name
        self.report({'INFO'}, "Saved %r" % name)
        return {'FINISHED'}


class RS_OT_light_preset_delete(Operator):
    bl_idname = "render_studio.light_preset_delete"
    bl_label = "Delete Preset"
    bl_description = "Delete this saved preset (the lights in the scene stay)"
    bl_options = {'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        lights.delete_preset(self.name)
        return {'FINISHED'}


class RS_OT_light_setup(Operator):
    bl_idname = "render_studio.light_setup"
    bl_label = "Apply Light Setup"
    bl_description = "Applies a light setup given as JSON (e.g. from Claude) and saves it as a preset"
    bl_options = {'UNDO'}

    spec: StringProperty(name="Setup (JSON)", options={'SKIP_SAVE'})
    save: BoolProperty(name="Save as Preset", default=True, options={'SKIP_SAVE'})

    def execute(self, context):
        created = _report_value_error(self, lambda: api.light_setup(self.spec, context.scene, self.save))
        if not created:
            return {'CANCELLED'}
        self.report({'INFO'}, "%d lights created" % len(created))
        return {'FINISHED'}


class RS_OT_hdri_apply(Operator):
    bl_idname = "render_studio.hdri_apply"
    bl_label = "Use HDRI"
    bl_description = "Use this HDRI as the world of the scene"
    bl_options = {'UNDO', 'INTERNAL'}

    path: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        try:
            hdri.apply(context.scene, self.path, context.scene.render_studio)
        except RuntimeError as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class RS_OT_hdri_favorite(Operator):
    bl_idname = "render_studio.hdri_favorite"
    bl_label = "Favorite"
    bl_description = "Mark or unmark as favorite (shared by all blend files)"
    bl_options = {'INTERNAL'}

    path: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        hdri.toggle_favorite(self.path)
        return {'FINISHED'}


class RS_OT_hdri_refresh(Operator):
    bl_idname = "render_studio.hdri_refresh"
    bl_label = "Refresh"
    bl_description = "Read the HDRI folder again and copy new BlenderKit HDRIs into it"

    def execute(self, context):
        p = runner.prefs(context)
        folder = p.hdri_folder if p else ""
        copied = hdri.import_blenderkit(folder, wait=True)
        hdri.scan(folder)
        if copied:
            self.report({'INFO'}, "%d HDRI(s) from BlenderKit added" % copied)
        return {'FINISHED'}


class RS_OT_world_restore(Operator):
    bl_idname = "render_studio.world_restore"
    bl_label = "Original World"
    bl_description = "Switch back to the world the scene had before the HDRI"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.world == hdri.hdri_world() and context.scene.world is not None

    def execute(self, context):
        hdri.restore(context.scene, context.scene.render_studio)
        return {'FINISHED'}


LIGHT_ICONS = {'POINT': 'LIGHT_POINT', 'SUN': 'LIGHT_SUN', 'SPOT': 'LIGHT_SPOT', 'AREA': 'LIGHT_AREA'}


# name: Principled BSDF values
class RS_OT_light_focus_selection(Operator):
    bl_idname = "render_studio.light_focus_selection"
    bl_label = "Aim at Selection"
    bl_description = ("Aim the light setup at the selected object. With several objects: at the center of "
                      "all of them together (an empty \"Render Studio Focus\" marks it)")
    bl_options = {'UNDO'}

    def execute(self, context):
        subject = lights.focus_selection(context.scene, context.selected_objects)
        if subject is None:
            self.report({'WARNING'}, "Select one or more objects first")
            return {'CANCELLED'}
        context.scene.render_studio.light_orbit_target = subject
        return {'FINISHED'}


class RS_OT_show_world(Operator):
    bl_idname = "render_studio.show_world"
    bl_label = "Show World"
    bl_description = ("Make the World visible as background: Film Transparent off, World visible to the camera, "
                      "strength above 0, viewport compositor off and viewports using the scene World")
    bl_options = {'UNDO'}

    def execute(self, context):
        background.fix_world(context.scene)
        return {'FINISHED'}


class RS_OT_toggle_light(Operator):
    bl_idname = "render_studio.toggle_light"
    bl_label = "Light On/Off"
    bl_description = "Switch the light on or off (viewport and render)"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        obj = bpy.data.objects.get(self.name)
        if obj is None or obj.type != 'LIGHT':
            return {'CANCELLED'}
        off = not obj.hide_render
        obj.hide_render = off
        obj.hide_viewport = off
        return {'FINISHED'}


class RS_OT_hdri_step(Operator):
    bl_idname = "render_studio.hdri_step"
    bl_label = "Next HDRI"
    bl_description = "Switch to the previous or next HDRI of the folder"
    bl_options = {'UNDO', 'INTERNAL'}

    direction: IntProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=1)

    def execute(self, context):
        p = runner.prefs(context)
        paths = hdri.files(p.hdri_folder if p else "")
        if not paths:
            return {'CANCELLED'}
        current = hdri.current_path(context.scene)
        norm = [os.path.normpath(x) for x in paths]
        index = norm.index(current) + self.direction if current in norm else (0 if self.direction > 0 else -1)
        hdri.apply(context.scene, paths[index % len(paths)], context.scene.render_studio)
        return {'FINISHED'}


class RS_OT_lights_adjust(Operator):
    bl_idname = "render_studio.lights_adjust"
    bl_label = "All Lights"
    bl_description = "Change all lights of the scene together"
    bl_options = {'UNDO', 'INTERNAL'}

    what: StringProperty(default="POWER")   # POWER / SOFT / KELVIN
    value: FloatProperty(default=1.0)

    @classmethod
    def description(cls, context, properties):
        if properties.what == "POWER":
            return "All lights × %g" % properties.value
        if properties.what == "SOFT":
            return "All shadows softer" if properties.value > 1 else "All shadows harder"
        return "All lights %d K" % properties.value if properties.value else "Color temperature off (light colors)"

    def execute(self, context):
        objs = lights.scene_lights(context.scene)
        for obj in objs:
            data = obj.data
            if self.what == "POWER":
                data.energy *= self.value
            elif self.what == "SOFT":
                if data.type == 'AREA':
                    data.size *= self.value
                    data.size_y *= self.value
                elif data.type == 'SUN':
                    data.angle = min(math.radians(30), data.angle * self.value)
                else:
                    data.shadow_soft_size *= self.value
            elif self.what == "KELVIN":
                data.use_temperature = self.value > 0
                if self.value > 0:
                    data.temperature = self.value
        self.report({'INFO'}, "%d lights changed" % len(objs))
        return {'FINISHED'}


BOUNCE_PRESETS = {
    "Fast": dict(max_bounces=4, diffuse_bounces=2, glossy_bounces=2, transmission_bounces=4, volume_bounces=0,
                 transparent_max_bounces=8, caustics_reflective=False, caustics_refractive=False),
    "Standard": dict(max_bounces=12, diffuse_bounces=4, glossy_bounces=4, transmission_bounces=12,
                     volume_bounces=0, transparent_max_bounces=8, caustics_reflective=True,
                     caustics_refractive=True),
    "Glass": dict(max_bounces=24, diffuse_bounces=4, glossy_bounces=8, transmission_bounces=24, volume_bounces=2,
                  transparent_max_bounces=16, caustics_reflective=True, caustics_refractive=True),
}


class RS_OT_bounces_preset(Operator):
    bl_idname = "render_studio.bounces_preset"
    bl_label = "Light Paths"
    bl_description = "Cycles light bounces: Fast (quick, darker glass), Standard, Glass (clean glass and liquids)"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(default="Standard")

    def execute(self, context):
        cy = context.scene.cycles
        for key, value in BOUNCE_PRESETS.get(self.name, {}).items():
            if hasattr(cy, key):
                setattr(cy, key, value)
        return {'FINISHED'}


def _bounce_preset(cy):
    return next((n for n, v in BOUNCE_PRESETS.items()
                 if all(getattr(cy, k, None) == x for k, x in v.items())), "")


class RS_PT_light_settings(Panel):
    """Gear menu in the Light tab: all lights together, shadows and light bounces."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Light Settings"
    bl_ui_units_x = 14

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        box = layout.box()
        box.label(text="All Lights (%d)" % len(lights.scene_lights(scene)), icon='LIGHT')
        row = box.row(align=True)
        split = row.split(factor=0.3, align=True)
        split.label(text="Power")
        sub = split.row(align=True)
        for factor in (0.5, 0.8, 1.25, 2.0):
            op = sub.operator(RS_OT_lights_adjust.bl_idname, text="×%g" % factor)
            op.what, op.value = "POWER", factor
        row = box.row(align=True)
        split = row.split(factor=0.3, align=True)
        split.label(text="Shadows")
        sub = split.row(align=True)
        op = sub.operator(RS_OT_lights_adjust.bl_idname, text="Harder")
        op.what, op.value = "SOFT", 1 / 1.5
        op = sub.operator(RS_OT_lights_adjust.bl_idname, text="Softer")
        op.what, op.value = "SOFT", 1.5
        row = box.row(align=True)
        split = row.split(factor=0.3, align=True)
        split.label(text="Color")
        sub = split.row(align=True)
        for kelvin in (2700, 4000, 5600, 6500, 8000):
            op = sub.operator(RS_OT_lights_adjust.bl_idname, text="%gK" % (kelvin / 1000))
            op.what, op.value = "KELVIN", kelvin
        op = sub.operator(RS_OT_lights_adjust.bl_idname, text="", icon='X')
        op.what, op.value = "KELVIN", 0

        box = layout.box()
        box.label(text="Lights aim at", icon='PIVOT_CURSOR')
        row = box.row(align=True)
        rs = scene.render_studio
        row.prop(rs, "light_orbit_target", text="", placeholder="3D Cursor")
        row.operator(RS_OT_light_focus_selection.bl_idname, text="", icon='RESTRICT_SELECT_OFF')
        current = current_preset(scene)
        row = box.row(align=True)
        row.operator(RS_OT_light_preset_save.bl_idname, text="Save Light Preset", icon='ADD')
        if current and current in lights.saved_presets():
            row.operator(RS_OT_light_preset_delete.bl_idname, text="", icon='TRASH').name = current
        presets_io.draw(layout.box())
        addons.draw_list(layout)

        box = layout.box()
        box.label(text="Shadows & Bounces", icon='LIGHT_SUN')
        if scene.render.engine != 'CYCLES':
            box.label(text="Light bounces are a Cycles setting", icon='INFO')
            return
        cy = scene.cycles
        current = _bounce_preset(cy)
        row = box.row(align=True)
        for name in BOUNCE_PRESETS:
            row.operator(RS_OT_bounces_preset.bl_idname, text=name, depress=name == current,
                         translate=False).name = name   # Blender's own catalog: "Fast" → "Einfügen"
        col = box.column(align=True)
        col.prop(cy, "max_bounces", text="Total")
        row = col.row(align=True)
        row.prop(cy, "diffuse_bounces", text="Diffuse")
        row.prop(cy, "glossy_bounces", text="Glossy")
        col.prop(cy, "transmission_bounces", text="Transmission")
        row = box.row(align=True)
        row.prop(cy, "caustics_reflective", text="Reflective Caustics", toggle=True)
        row.prop(cy, "caustics_refractive", text="Refractive Caustics", toggle=True)
        box.prop(cy, "sample_clamp_indirect", text="Clamp Indirect")


def current_preset(scene):
    """Name of the preset the lights in the scene come from ("" if none)."""
    names = [o.get(lights.PRESET_KEY) for o in lights.scene_lights(scene) if o.get(lights.PRESET_KEY)]
    return max(set(names), key=names.count) if names else ""


class RS_MT_light_presets(Menu):
    bl_idname = "RS_MT_light_presets"
    bl_label = "Light Presets"

    def draw(self, context):
        layout = self.layout
        current = current_preset(context.scene)
        for name in lights.BUILTIN_PRESETS:
            layout.operator(RS_OT_light_preset.bl_idname, text=name,
                            icon='CHECKMARK' if name == current else 'BLANK1').name = name
        saved = sorted(lights.saved_presets())
        if saved:
            layout.separator()
            for name in saved:
                layout.operator(RS_OT_light_preset.bl_idname, text=name,
                                icon='CHECKMARK' if name == current else 'BLANK1').name = name


class RS_MT_hdris(Menu):
    bl_idname = "RS_MT_hdris"
    bl_label = "Environment"

    def draw(self, context):
        layout = self.layout
        p = runner.prefs(context)
        layout.operator(RS_OT_world_restore.bl_idname, text="Original World", icon='WORLD')
        paths = hdri.files(p.hdri_folder if p else "")
        if paths:
            layout.separator()
        flow = layout.column_flow(columns=max(1, (len(paths) + 19) // 20))
        for path in paths:
            icon = hdri.preview_icon(path)
            op = flow.operator(RS_OT_hdri_apply.bl_idname, text=hdri.display_name(path),
                               icon_value=icon) if icon else \
                flow.operator(RS_OT_hdri_apply.bl_idname, text=hdri.display_name(path), icon='IMAGE')
            op.path = path
        if not paths:
            layout.label(text="No HDRIs in the folder", icon='INFO')
        layout.separator()
        layout.operator(RS_OT_hdri_refresh.bl_idname, text="Refresh (incl. BlenderKit)", icon='FILE_REFRESH')


class RS_PT_lighting(_SidebarPanel, Panel):
    """The Environment tab in one panel: Scene, Lights, World, Background, Floor, other add-ons.
    Without a header, so it stays at the top of the tab."""
    bl_label = "Lighting"
    studio_tab = 'LIGHT'
    bl_options = {'HIDE_HEADER'}
    bl_order = LIGHT_TAB_ORDER

    def draw(self, context):
        _unpin(self)
        scene = context.scene
        rs = scene.render_studio
        layout = self.layout
        p = runner.prefs(context)
        simple = p is not None and p.simple_view

        # Scene: one row, everything below as one preset
        env_ui.draw_scene_presets(layout, scene)

        # Lights: the list (eye, name, strength); click a light's icon for its details
        box, head, is_open = section(layout, "lights", "Lights", 'LIGHT')
        current = current_preset(scene)
        head.menu(RS_MT_light_presets.bl_idname, text=current or "Preset")
        head.operator(RS_OT_add_light.bl_idname, text="", icon='ADD')
        head.popover(RS_PT_light_settings.__name__, text="", icon='PREFERENCES')
        if is_open:
            all_lights = lights.scene_lights(scene)
            if not all_lights:
                sub = box.row()
                sub.active = False
                sub.label(text="Pick a preset or add a light", icon='INFO')
            active = rs.active_light
            col = box.column(align=True)
            for obj in all_lights:
                on = not obj.hide_render
                row = col.row(align=True)
                row.operator(RS_OT_toggle_light.bl_idname, text="", emboss=False,
                             icon='HIDE_OFF' if on else 'HIDE_ON').name = obj.name
                sub = row.row(align=True)
                sub.active = on
                sub.operator(RS_OT_select_light.bl_idname, text="", depress=(obj == active),
                             icon=LIGHT_ICONS.get(obj.data.type, 'LIGHT')).name = obj.name
                sub.prop(obj.render_studio_light, "short_name", text="")
                power = sub.row(align=True)
                power.ui_units_x = 4.5
                power.prop(obj.data, "energy", text="")
                if env_ui.solo_light(scene) == obj.name:
                    row.label(text="", icon='SOLO_ON')
                if obj == active:
                    details = col.box()
                    _draw_light_details(details, obj, rs, simple)
                    col.separator()

        # World: the HDRI that lights the scene (also behind a transparent or colored background)
        box, head, is_open = section(layout, "world", "World", 'WORLD')
        if is_open:
            _draw_world(box, scene, rs, simple)
            env_ui.draw_world_extras(box, scene, simple)
            if addons.has("psa"):
                addons.draw_atmosphere(box, scene)

        # Backdrop: what the camera sees behind the product, and the floor it stands on
        box, head, is_open = section(layout, "backdrop", "Backdrop", 'IMAGE_BACKGROUND')
        if is_open:
            env_ui.draw_backdrop(box, context, simple)


def _draw_world(box, scene, rs, simple=False):
    """The World: ◀ dropdown ▶ with the current HDRI, its preview (not in the simple view),
    strength and rotation."""
    path = hdri.current_path(scene)
    row = box.row(align=True)
    row.operator(RS_OT_hdri_step.bl_idname, text="", icon='TRIA_LEFT').direction = -1
    if path:
        row.menu(RS_MT_hdris.bl_idname, text=hdri.display_name(path), icon='IMAGE')
    else:
        row.menu(RS_MT_hdris.bl_idname, text=scene.world.name if scene.world else "No World", icon='WORLD')
    row.operator(RS_OT_hdri_step.bl_idname, text="", icon='TRIA_RIGHT').direction = 1
    if path:
        icon = hdri.preview_icon(path) if not simple else None
        if icon:
            box.template_icon(icon_value=icon, scale=6.0)   # preview of the HDRI
        col = box.column(align=True)
        col.prop(rs, "world_strength")
        col.prop(rs, "world_rotation")
    elif scene.world is not None and background.world_background_node(scene) is not None:
        box.prop(rs, "world_background_strength", text="Strength")


def _draw_light_details(box, light, rs, simple=False):
    data = light.data
    row = box.row(align=True)
    row.prop(data, "type", expand=True, icon_only=True)
    row.separator()
    row.operator(env_ui.RS_OT_solo_light.bl_idname, text="",
                 icon=env_ui.solo_icon(bpy.context.scene, light)).name = light.name
    row.operator(RS_OT_duplicate_light.bl_idname, text="", icon='DUPLICATE').name = light.name
    if addons.has("lightwrangler"):
        row.operator(addons.RS_OT_place_light.bl_idname, text="", icon='MOUSE_LMB_DRAG').name = light.name
    col = box.column(align=True)
    col.prop(data, "color", text="")
    if data.type == 'AREA':
        if data.shape in {'RECTANGLE', 'ELLIPSE'}:
            row = col.row(align=True)
            row.prop(data, "size", text="W")
            row.prop(data, "size_y", text="H")
        else:
            col.prop(data, "size", text="Size")
        col.prop(data, "spread", text="Spread")
    elif data.type == 'SPOT':
        col.prop(data, "spot_size", text="Angle")
        col.prop(data, "shadow_soft_size", text="Softness")
    elif data.type == 'SUN':
        col.prop(data, "angle", text="Softness")
    else:
        col.prop(data, "shadow_soft_size", text="Softness")
    own = light.render_studio_light
    box.prop(own, "target", text="", icon='PIVOT_CURSOR',
             placeholder=("Target: " + rs.light_orbit_target.name) if rs.light_orbit_target else "Target")
    target = lights.light_target(light, rs)
    if target is not None and target != light:
        col = box.column(align=True)
        col.prop(rs, "light_orbit_horizontal")
        col.prop(rs, "light_orbit_vertical")
        col.prop(rs, "light_orbit_distance")
        if not simple:
            col.prop(own, "height")
    if not simple:
        env_ui.draw_light_extras(box, light)

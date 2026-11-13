# SPDX-License-Identifier: GPL-3.0-or-later
"""Operators, menus and boxes of the Environment tab (logic in environment.py)."""

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Menu, Operator

from . import background, environment, hdri, runner


def _simple():
    p = runner.prefs()
    return p is not None and p.simple_view


def _light(name):
    obj = bpy.data.objects.get(name)
    return obj if obj is not None and obj.type == 'LIGHT' else None


# ------------------------------------------------------------------ scene presets

class RS_OT_scene_preset(Operator):
    bl_idname = "render_studio.scene_preset"
    bl_label = "Scene Preset"
    bl_description = "Apply this scene: lights, World, background, floor, haze and look together"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        try:
            notes = environment.apply_scene(context.scene, self.name)
        except (ValueError, RuntimeError, OSError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        for note in notes:
            self.report({'WARNING'}, note)
        return {'FINISHED'}


class RS_OT_scene_preset_save(Operator):
    bl_idname = "render_studio.scene_preset_save"
    bl_label = "Save Scene"
    bl_description = ("Save the whole environment as a scene preset (every blend file): lights, World, background, "
                      "floor, haze and look")

    name: StringProperty(name="Name", default="My Scene")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        name = self.name.strip() or "My Scene"
        if name in environment.BUILTIN_SCENES:
            self.report({'ERROR'}, "%r is a built-in scene, pick another name" % name)
            return {'CANCELLED'}
        environment.save_scene(context.scene, name)
        self.report({'INFO'}, "Saved %r" % name)
        return {'FINISHED'}


class RS_OT_scene_preset_delete(Operator):
    bl_idname = "render_studio.scene_preset_delete"
    bl_label = "Delete Scene Preset"
    bl_options = {'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event, title="Delete %r?" % self.name,
                                                     confirm_text="Delete")

    def execute(self, context):
        environment.delete_scene(self.name)
        if context.scene.render_studio.scene_preset == self.name:
            context.scene.render_studio.scene_preset = ""
        return {'FINISHED'}


class RS_MT_scene_presets(Menu):
    bl_idname = "RS_MT_scene_presets"
    bl_label = "Scene Presets"

    def draw(self, context):
        layout = self.layout
        current = context.scene.render_studio.scene_preset
        for name in environment.BUILTIN_SCENES:
            layout.operator(RS_OT_scene_preset.bl_idname, text=name,
                            icon='CHECKMARK' if name == current else 'BLANK1').name = name
        saved = sorted(environment.saved_scenes())
        if saved:
            layout.separator()
            for name in saved:
                layout.operator(RS_OT_scene_preset.bl_idname, text=name,
                                icon='CHECKMARK' if name == current else 'BLANK1').name = name


def draw_scene_presets(layout, scene):
    box = layout.box()
    box.label(text="Scene", icon='SCENE_DATA')
    row = box.row(align=True)
    current = scene.render_studio.scene_preset
    row.menu(RS_MT_scene_presets.bl_idname, text=current or "Choose a scene")
    row.operator(RS_OT_scene_preset_save.bl_idname, text="", icon='ADD')
    if current and current in environment.saved_scenes():
        row.operator(RS_OT_scene_preset_delete.bl_idname, text="", icon='TRASH').name = current


# ------------------------------------------------------------------ lights: solo, linking, flags

class RS_OT_solo_light(Operator):
    bl_idname = "render_studio.solo_light"
    bl_label = "Solo"
    bl_description = "Render only this light; click again to bring the others back as they were"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        light = _light(self.name)
        if light is None:
            return {'CANCELLED'}
        environment.toggle_solo(context.scene, light)
        return {'FINISHED'}


class RS_OT_light_link(Operator):
    bl_idname = "render_studio.light_link"
    bl_label = "Only the Product"
    bl_description = ("This light lights only the product, not the floor, room or background (light linking, "
                      "Cycles). Click again to light everything")
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        light = _light(self.name)
        if light is None:
            return {'CANCELLED'}
        environment.toggle_product_link(context.scene, light)
        return {'FINISHED'}


class RS_OT_add_flag(Operator):
    bl_idname = "render_studio.add_flag"
    bl_label = "Flag / Gobo"
    bl_description = "A card in front of the active light: cuts (flag) or shapes (gobo) its light"
    bl_options = {'UNDO', 'INTERNAL'}

    pattern: EnumProperty(name="Pattern", items=environment.FLAG_PATTERNS)

    @classmethod
    def poll(cls, context):
        light = context.scene.render_studio.active_light
        return light is not None and light.type == 'LIGHT'

    def execute(self, context):
        scene = context.scene
        environment.add_flag(scene, scene.render_studio.active_light, self.pattern)
        return {'FINISHED'}


class RS_OT_remove_flag(Operator):
    bl_idname = "render_studio.remove_flag"
    bl_label = "Remove Flag"
    bl_description = "Remove the flag or gobo of this light"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        light = _light(self.name)
        if light is None:
            return {'CANCELLED'}
        environment.remove_flag(light)
        return {'FINISHED'}


def draw_light_extras(box, light):
    """Under a light's details: only the product, flag / gobo."""
    row = box.row(align=True)
    row.operator(RS_OT_light_link.bl_idname, icon='LINKED', depress=environment.linked_to_product(light)).name = \
        light.name
    flag = environment.flag_of(light)
    if flag is None:
        row.operator_menu_enum(RS_OT_add_flag.bl_idname, "pattern", text="Flag / Gobo", icon='MOD_MASK')
    else:
        sub = row.row(align=True)
        sub.label(text=dict((k, n) for k, n, _d in environment.FLAG_PATTERNS).get(flag.get("as_pattern"), "Flag"),
                  icon='MOD_MASK')
        sub.operator(RS_OT_remove_flag.bl_idname, text="", icon='X').name = light.name


def solo_icon(scene, light):
    return 'SOLO_ON' if environment.solo_light(scene) == light.name else 'SOLO_OFF'


# ------------------------------------------------------------------ World extras

class RS_OT_sun_from_hdri(Operator):
    bl_idname = "render_studio.sun_from_hdri"
    bl_label = "Sun from HDRI"
    bl_description = ("A sun light from the brightest spot of the HDRI (direction and color): sharp shadows that "
                      "match the World")
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(hdri.current_path(context.scene)) or environment._env_image(context.scene) is not None

    def execute(self, context):
        try:
            obj = environment.sun_from_hdri(context.scene)
        except (ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        context.scene.render_studio.active_light = obj
        self.report({'INFO'}, "%s added" % obj.name)
        return {'FINISHED'}


def draw_world_extras(box, scene):
    rs = scene.render_studio
    box.operator(RS_OT_sun_from_hdri.bl_idname, icon='LIGHT_SUN')
    row = box.row(align=True)
    row.prop(rs, "haze_density", text="Haze")
    if rs.haze_density > 0 and not _simple():
        sub = row.row(align=True)
        sub.ui_units_x = 2.5
        sub.prop(rs, "haze_color", text="")


# ------------------------------------------------------------------ background (camera sees)

def draw_background(layout, context):
    scene = context.scene
    rq = scene.render_studio
    box = layout.box()
    box.label(text="Background", icon='IMAGE_BACKGROUND')
    col = box.column(align=True)
    col.row(align=True).prop(rq, "background_mode", expand=True)
    space = context.space_data
    if rq.background_mode == 'COLOR':
        if rq.use_gradient:
            col.prop(rq, "background_color_top", text="")
        col.prop(rq, "background_color", text="")
        col = box.column(align=True)
        col.prop(rq, "use_gradient", toggle=True)
        row = col.row(align=True)
        row.prop(rq, "noise_intensity", text="Noise")
        if rq.noise_intensity > 0:
            row.prop(rq, "noise_size", text="Size")
        shading = getattr(space, "shading", None)
        if shading is not None and shading.type in {'SOLID', 'WIREFRAME'}:
            box.label(text="Shown in Material Preview and Rendered", icon='INFO')
    elif rq.background_mode == 'NORMAL':
        problems = background.world_problems(scene, space)
        if problems:
            sub = box.column(align=True)
            for text in problems:
                sub.label(text=text, icon='ERROR')
            box.operator('render_studio.show_world', icon='WORLD')
    if rq.background_mode != 'NORMAL':
        sub = box.row()
        sub.active = False
        sub.label(text="The World still lights the scene", icon='INFO')


# ------------------------------------------------------------------ floor

def draw_floor(layout, scene):
    rs = scene.render_studio
    box = layout.box()
    box.label(text="Floor", icon='MESH_PLANE')
    box.row(align=True).prop(rs, "floor_mode", expand=True)
    if rs.floor_mode == 'NONE':
        return
    col = box.column(align=True)
    col.prop(rs, "floor_color", text="")
    col.prop(rs, "floor_roughness")
    if not _simple():
        col.prop(rs, "floor_size")
        if rs.floor_mode == 'COVE':
            col.prop(rs, "floor_curve")
    box.prop(rs, "floor_shadow_catcher", toggle=True)
    if rs.floor_shadow_catcher and rs.background_mode == 'NORMAL':
        sub = box.row()
        sub.active = False
        sub.label(text="Best with a transparent or colored background", icon='INFO')


classes = (RS_OT_scene_preset, RS_OT_scene_preset_save, RS_OT_scene_preset_delete, RS_MT_scene_presets,
           RS_OT_solo_light, RS_OT_light_link, RS_OT_add_flag, RS_OT_remove_flag, RS_OT_sun_from_hdri)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

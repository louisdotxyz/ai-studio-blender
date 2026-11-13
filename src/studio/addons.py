# SPDX-License-Identifier: GPL-3.0-or-later
"""Other add-ons next to ID Toolkit: use what adds something, name what is covered already.

- Used (their own features, reached from the Light tab and by Claude):
  Physical Starlight & Atmosphere (sky, sun, clouds, stars, fog), Light Wrangler (place a light by
  dragging on the product, gobos, IES, the scene as a 360° HDRI), Render Raw (color grading),
  Photographer (physical camera exposure).
- Covered (the Light tab does the same: presets, key/fill/rim, light list): Quick Studio Light,
  QLE (Quick Lighting Environment), Tri-lighting. They keep working; the Light tab offers to turn
  them off (Blender's own add-on switch, so it can be turned on again in the preferences).

Nothing is installed, changed or turned off without a click.
"""

import addon_utils
import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from ..i18n import tr

# key, label, module name endings (lower case), kind, what it does / what covers it
KNOWN = (
    ("psa", "Physical Starlight & Atmosphere", ("physical-starlight-atmosphere", "physical_starlight_atmosphere"),
     "use", "Sky, sun, clouds, stars and fog (World)"),
    ("lightwrangler", "Light Wrangler", ("light_wrangler", "lightwrangler"),
     "use", "Place a light by dragging on the product; gobos, IES, the scene as a 360° HDRI"),
    ("render_raw", "Render Raw", ("render_raw",),
     "use", "Color grading (its own sidebar tab \"Render Raw\")"),
    ("photographer", "Photographer", ("photographer",),
     "use", "Physical camera: exposure, white balance"),
    ("qsl", "Quick Studio Light", ("quick_studio_light",),
     "covered", "Light presets and the light list in this tab"),
    ("qle", "QLE (Quick Lighting Environment)", ("quick_lighting_environment",),
     "covered", "Light presets (key, fill, rim) in this tab"),
    ("tri", "Tri-lighting", ("tri_lighting",),
     "covered", "The three-point presets in this tab"),
)


def _enabled_modules():
    return [a.module for a in bpy.context.preferences.addons]


def found():
    """[{key, label, kind, text, module}] of the known add-ons that are turned on."""
    modules = _enabled_modules()
    out = []
    for key, label, endings, kind, text in KNOWN:
        module = next((m for m in modules if m.split(".")[-1].lower() in endings), None)
        if module:
            out.append({"key": key, "label": label, "kind": kind, "text": text, "module": module})
    return out


def has(key):
    return any(a["key"] == key for a in found())


def summary():
    """For Claude (studio_context): what is there and how to reach it."""
    hints = {
        "psa": "atmosphere: bpy.ops.psa.add_atmosphere() / remove_atmosphere() on the scene's World; settings in "
               "world.psa_atmosphere_settings (azimuth, elevation, sun_intensity, atmosphere_density, clouds_amount …); "
               "on/off: world.psa_general_settings.enabled",
        "lightwrangler": "the user places a light interactively with the place button in the light list "
                         "(lightwrangler.interactive_mode on the active light); gobos/IES in Light Wrangler's light "
                         "settings",
        "render_raw": "color grading in its own tab; do not change its settings unless asked",
        "photographer": "camera exposure lives in Photographer's camera settings when it is used",
    }
    return [{"name": a["label"], "use": hints.get(a["key"], "covered by ID Toolkit: " + a["text"])}
            for a in found()]


# ---------------------------------------------------------------- atmosphere (PSA)

ATMOSPHERE_FIELDS = (("elevation", "Sun Height"), ("azimuth", "Sun Direction"), ("sun_intensity", "Sun Strength"),
                     ("atmosphere_density", "Haze"), ("clouds_amount", "Clouds"))


def atmosphere_on(scene):
    world = scene.world
    settings = getattr(world, "psa_general_settings", None) if world else None
    return bool(settings and settings.enabled)


def draw_atmosphere(box, scene):
    """Atmosphere row in the World box (only with PSA)."""
    row = box.row(align=True)
    on = atmosphere_on(scene)
    row.operator(RS_OT_atmosphere.bl_idname, text="Atmosphere", icon='OUTLINER_DATA_LIGHTPROBE', depress=on)
    if not on:
        return
    settings = scene.world.psa_atmosphere_settings
    col = box.column(align=True)
    for prop, label in ATMOSPHERE_FIELDS:
        if hasattr(settings, prop):
            col.prop(settings, prop, text=label)


class RS_OT_atmosphere(Operator):
    bl_idname = "render_studio.atmosphere"
    bl_label = "Atmosphere"
    bl_description = ("Physical sky with sun, haze and clouds on the World (Physical Starlight & Atmosphere). "
                      "Click again to take it off")
    bl_options = {'UNDO', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.scene.world is not None and hasattr(bpy.ops, "psa")

    def execute(self, context):
        try:
            if atmosphere_on(context.scene):
                bpy.ops.psa.remove_atmosphere()
            else:
                bpy.ops.psa.add_atmosphere()
        except (RuntimeError, AttributeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


# ---------------------------------------------------------------- Light Wrangler

class RS_OT_place_light(Operator):
    bl_idname = "render_studio.place_light"
    bl_label = "Place Light"
    bl_description = "Place this light by dragging on the product (Light Wrangler). Esc or right-click ends it"
    bl_options = {'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return hasattr(bpy.ops, "lightwrangler") and context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, _event):
        obj = bpy.data.objects.get(self.name)
        if obj is None or obj.type != 'LIGHT':
            return {'CANCELLED'}
        for other in context.selected_objects:
            other.select_set(False)
        obj.hide_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        try:
            return bpy.ops.lightwrangler.interactive_mode('INVOKE_DEFAULT')
        except RuntimeError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


# ---------------------------------------------------------------- the list in the Light tab

class RS_OT_addon_off(Operator):
    bl_idname = "render_studio.addon_off"
    bl_label = "Turn Off Add-on"
    bl_description = ("Turn this add-on off: the Light tab does the same. It stays installed; turn it on again in "
                      "Edit › Preferences › Add-ons")
    bl_options = {'INTERNAL'}

    module: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    label: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title=tr("Turn Off") + " " + self.label + "?",
            message="It stays installed and can be turned on again in the preferences.", confirm_text="Turn Off")

    def execute(self, _context):
        if self.module not in _enabled_modules():
            return {'CANCELLED'}
        addon_utils.disable(self.module, default_set=True)
        self.report({'INFO'}, "%s turned off" % self.label)
        return {'FINISHED'}


def draw_list(layout):
    """"Other Add-ons" box: nothing when none of them is on."""
    items = found()
    if not items:
        return
    box = layout.box()
    box.label(text="Other Add-ons", icon='PLUGIN')
    col = box.column(align=True)
    for item in items:
        row = col.row(align=True)
        row.label(text=item["label"], icon='CHECKMARK' if item["kind"] == "use" else 'INFO', translate=False)
        if item["kind"] == "covered":
            op = row.operator(RS_OT_addon_off.bl_idname, text="", icon='CANCEL')
            op.module, op.label = item["module"], item["label"]
        sub = col.row()
        sub.active = False
        sub.label(text=(tr("Covered: ") if item["kind"] == "covered" else "") + tr(item["text"]), translate=False)


classes = (RS_OT_atmosphere, RS_OT_place_light, RS_OT_addon_off)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

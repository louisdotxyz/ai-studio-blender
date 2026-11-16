# SPDX-License-Identifier: GPL-3.0-or-later
"""The Animation tab of the Studio sidebar (between Material and Render): the film's sections, camera moves
(Motion), Orbit, the explosion view and the scene's own values (LED brightness, roughness … the custom
properties of the scene) with a key at the current frame each. The sections render and are edited in the
Video Editor (its Studio tab)."""

import bpy
from bpy.props import StringProperty
from bpy.types import Operator, Panel

from .ui_common import ANIM_TAB_ORDER, _SidebarPanel, _unpin, section

HIDDEN_VALUES = {"uuid", "assets reported", "render_preset", "Explosion"}   # internal, or shown elsewhere


def scene_values(scene):
    """The scene's own values: its custom properties that are numbers or switches (in name order)."""
    out = []
    for key in sorted(scene.keys(), key=str.lower):
        if key.startswith(("_", "studio", "cycles", "as_")) or key in HIDDEN_VALUES:
            continue
        value = scene.get(key)
        if isinstance(value, (bool, int, float)):
            out.append(key)
    return out


def is_animated(scene, key):
    ad = scene.animation_data
    if ad is None or ad.action is None:
        return False
    path = '["%s"]' % key
    try:
        from bpy_extras import anim_utils
        bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
        curves = bag.fcurves if bag is not None else []
    except (ImportError, AttributeError):
        curves = getattr(ad.action, "fcurves", [])
    return any(fc.data_path == path for fc in curves)


class STUDIO_OT_value_key(Operator):
    bl_idname = "studio.value_key"
    bl_label = "Key Value"
    bl_description = "A key of this value at the current frame (animate it over the film)"
    bl_options = {'UNDO', 'INTERNAL'}

    key: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        scene = context.scene
        if self.key not in scene.keys():
            return {'CANCELLED'}
        scene.keyframe_insert('["%s"]' % self.key, frame=scene.frame_current)
        self.report({'INFO'}, "%s keyed at frame %d" % (self.key, scene.frame_current))
        return {'FINISHED'}


def _draw_sections(layout, scene):
    from . import cut, motion
    box, head, is_open = section(layout, "anim_sections", "Sections", 'MARKER_HLT')
    head.operator("studio.section", text="", icon='ADD', emboss=False).action = 'ADD'
    head.operator("studio.cut", text="", icon='SEQUENCE', emboss=False).action = 'OPEN'
    if not is_open:
        return
    found = motion.sections(scene)
    if not found:
        hint = box.row()
        hint.active = False
        hint.label(text="+ a section from this frame (own camera)", icon='INFO')
        return
    frame = scene.frame_current
    col = box.column(align=True)
    for marker, start, end in found:
        here = start <= frame <= end
        st = cut.frame_status(marker.name, start, end)
        row = col.row(align=True)
        op = row.operator("studio.section", text="", icon='PLAY' if here else 'TRIA_RIGHT', emboss=here)
        op.action, op.name = 'GO', marker.name
        row.prop(marker, "name", text="")
        sub = row.row(align=True)
        sub.ui_units_x = 3.2
        sub.label(text=cut.status_text(st), translate=False)
        row = col.row(align=True)
        row.label(text="", icon='BLANK1')
        row.prop(marker, "camera", text="")
        info = row.row()
        info.active = False
        info.alignment = 'RIGHT'
        info.label(text="%d–%d · %.1f s" % (start, end, motion.seconds(scene, end - start + 1)), translate=False)
        col.separator(factor=0.5)


def _draw_orbit(layout, scene):
    from . import keys
    from .cameras import active_camera
    rq = scene.studio
    cam = active_camera(scene)
    box, head, is_open = section(layout, "orbit", "Orbit", 'ORIENTATION_GIMBAL', default_open=False)
    keys.button(head, 'ORBIT')
    if not is_open:
        return
    if cam is None or cam.type != 'CAMERA':
        box.label(text="No scene camera")
        return
    col = box.column(align=True)
    col.enabled = not rq.camera_locked
    col.prop(rq, "orbit_horizontal")
    col.prop(rq, "orbit_vertical")
    if cam.data.type != 'ORTHO':        # orthographic: the distance changes nothing in the picture
        col.prop(rq, "orbit_distance")
    col.prop(rq, "orbit_height")
    col.prop(rq, "orbit_side_offset")
    col.prop(rq, "orbit_up_offset")


def _draw_values(layout, scene):
    box, _head, is_open = section(layout, "anim_values", "Values", 'DRIVER')
    if not is_open:
        return
    found = scene_values(scene)
    if not found:
        hint = box.row()
        hint.active = False
        hint.label(text="No values of the scene yet (custom properties)", icon='INFO')
        return
    col = box.column(align=True)
    for key in found:
        row = col.row(align=True)
        value = scene.get(key)
        row.prop(scene, '["%s"]' % key, text=key, slider=not isinstance(value, bool), toggle=isinstance(value, bool))
        row.operator(STUDIO_OT_value_key.bl_idname, text="",
                     icon='DECORATE_KEYFRAME' if is_animated(scene, key) else 'DECORATE_ANIMATE').key = key


class STUDIO_PT_animation(_SidebarPanel, Panel):
    """Animation: sections, camera moves, orbit, explosion, the scene's values."""
    studio_tab = 'ANIM'
    bl_label = "Animation"
    bl_options = {'HIDE_HEADER'}
    bl_order = ANIM_TAB_ORDER

    def draw(self, context):
        _unpin(self)
        layout = self.layout
        scene = context.scene
        from . import explode, motion
        row = layout.row(align=True)          # the same head as every tab
        row.scale_y = 1.2
        row.operator("studio.cut", text="Film in the Video Editor", icon='SEQUENCE').action = 'OPEN'
        row.operator("studio.section", text="", icon='ADD').action = 'ADD'
        _draw_sections(layout, scene)
        box, _head, is_open = section(layout, "anim_motion", "Motion", 'ANIM')
        if is_open:
            motion.draw_presets(box, scene)
        _draw_orbit(layout, scene)
        box, _head, is_open = section(layout, "anim_explode", "Explosion", 'MOD_EXPLODE', default_open=False)
        if is_open:
            explode.draw(box, scene)
        _draw_values(layout, scene)


classes = (STUDIO_OT_value_key, STUDIO_PT_animation)

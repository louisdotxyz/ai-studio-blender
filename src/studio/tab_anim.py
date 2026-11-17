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
    """One line per section: [▶ go] [name] [camera] [render state]. The section at the playhead is lit."""
    from . import cut, motion
    box, head, is_open = section(layout, "anim_sections", "Sections", 'MARKER_HLT')
    head.operator("studio.section", text="", icon='ADD', emboss=False).action = 'ADD'
    if not is_open:
        return None
    found = motion.sections(scene)
    if not found:
        hint = box.row()
        hint.active = False
        hint.label(text="+ a section from the playhead (with its own camera)", icon='INFO')
        return None
    frame = scene.frame_current
    current = None
    col = box.column(align=True)
    for marker, start, end in found:
        here = start <= frame <= end
        if here:
            current = (marker, start, end)
        st = cut.frame_status(marker.name, start, end)
        row = col.row(align=True)
        op = row.operator("studio.section", text="", icon='PLAY' if here else 'TRIA_RIGHT', depress=here)
        op.action, op.name = 'GO', marker.name
        row.prop(marker, "name", text="")
        cam = row.row(align=True)
        cam.ui_units_x = 5
        cam.prop(marker, "camera", text="", icon='CAMERA_DATA')
        state = row.row(align=True)
        state.ui_units_x = 3
        state.alignment = 'RIGHT'
        state.active = st.get("done", 0) > 0
        state.label(text=cut.status_text(st) or "–", translate=False)
    return current


def _draw_moves(layout, scene, current):
    """The section at the playhead: its length and its moves (pull out, hold, orbit …) in order."""
    from . import motion
    rq = scene.studio
    if current is None:
        start, end, name = motion.motion_range(scene)
        title = "Whole film"
    else:
        marker, start, end = current
        name, title = marker.name, marker.name
    box = layout.box()
    head = box.row(align=True)
    head.label(text=title, icon='ANIM')
    info = head.row()
    info.alignment = 'RIGHT'
    info.active = False
    info.label(text="%d–%d · %.1f s" % (start, end, motion.seconds(scene, end - start + 1)), translate=False)
    steps = [(i, s) for i, s in enumerate(rq.motion_steps) if s.section == (name if current else name)]
    col = box.column(align=True)
    if not steps:
        hint = col.row()
        hint.active = False
        hint.label(text="No moves yet: choose one below and add it")
    for i, step in steps:
        row = col.row(align=True)
        row.prop(step, "preset", text="")
        row.prop(step, "frames", text="")
        if motion.PRESETS.get(step.preset, motion.PRESETS['HOLD'])[3][1] and step.preset not in motion.FADES:
            row.prop(step, "amount", text="")
        row.prop(step, "reverse", text="", icon='ARROW_LEFTRIGHT', toggle=True)
        up = row.operator(motion.STUDIO_OT_motion_step.bl_idname, text="", icon='TRIA_UP')
        up.action, up.index = 'UP', i
        rm = row.operator(motion.STUDIO_OT_motion_step.bl_idname, text="", icon='X')
        rm.action, rm.index = 'REMOVE', i
    add = box.row(align=True)                       # the next move: what, how strong, which way, add it
    add.prop(rq, "motion_preset", text="")
    spec = motion.PRESETS.get(rq.motion_preset, motion.PRESETS['HOLD'])
    if spec[3][1]:
        add.prop(rq, "motion_amount", text="")
    add.prop(rq, "motion_reverse", text="", icon='ARROW_LEFTRIGHT', toggle=True)
    add.operator(motion.STUDIO_OT_motion_step.bl_idname, text="Add", icon='ADD').action = 'ADD'
    row = box.row(align=True)
    row.scale_y = 1.2
    sub = row.row(align=True)
    sub.enabled = bool(steps)
    sub.operator(motion.STUDIO_OT_motion.bl_idname, text="Apply Moves", icon='PLAY').chain = True
    row.operator(motion.STUDIO_OT_key_pose.bl_idname, text="Keep Pose", icon='DECORATE_KEYFRAME')


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
    """Animation: the sections, the moves of the section at the playhead, explosion, the scene's values."""
    studio_tab = 'ANIM'
    bl_label = "Animation"
    bl_options = {'HIDE_HEADER'}
    bl_order = ANIM_TAB_ORDER

    def draw(self, context):
        _unpin(self)
        layout = self.layout
        scene = context.scene
        from . import explode
        row = layout.row(align=True)          # the same head as every tab
        row.scale_y = 1.2
        row.operator("studio.cut", text="Film in the Video Editor", icon='SEQUENCE').action = 'OPEN'
        row.operator("studio.section", text="", icon='ADD').action = 'ADD'
        current = _draw_sections(layout, scene)
        _draw_moves(layout, scene, current)
        box, _head, is_open = section(layout, "anim_explode", "Explosion", 'MOD_EXPLODE', default_open=False)
        if is_open:
            explode.draw(box, scene)
        _draw_values(layout, scene)


classes = (STUDIO_OT_value_key, STUDIO_PT_animation)

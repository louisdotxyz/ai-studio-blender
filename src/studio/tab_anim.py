# SPDX-License-Identifier: GPL-3.0-or-later
"""The Animation tab of the Studio sidebar (between Material and Render): the film's sections, what moves in
the section at the playhead (each animated thing with its frames: move it, faster or slower, a curve preset,
the Graph Editor), new camera moves, the explosion view and the scene's own values (LED brightness,
roughness … the custom properties of the scene) with a key at the current frame each. The sections render and are edited in the
Video Editor (its Studio tab)."""

import bpy
from bpy.props import StringProperty
from bpy.types import Operator, Panel

from .ui_common import ANIM_TAB_ORDER, _SidebarPanel, _unpin, section

HIDDEN_VALUES = {"uuid", "assets reported", "render_preset", "Explosion"}   # internal, or shown elsewhere
HIDDEN_PREFIXES = ("_", "studio", "cycles", "as_", "lightwrangler", "flamenco", "meshfix", "lm_", "uuid", "assets ")


def scene_values(scene):
    """The scene's own values: its custom properties that are numbers or switches (in name order)."""
    out = []
    for key in sorted(scene.keys(), key=str.lower):
        if key.lower().startswith(HIDDEN_PREFIXES) or key in HIDDEN_VALUES:
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


class STUDIO_OT_anim_track(Operator):
    bl_idname = "studio.anim_track"
    bl_label = "Track"
    bl_description = "Choose this animation to move it, make it faster or slower or give it a curve"
    bl_options = {'UNDO', 'INTERNAL'}

    action: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})      # PICK / GRAPH / BACK / CLEAR / FASTER / SLOWER
    track: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return {"GRAPH": "Show only this animation in the Graph Editor (the timeline turns into it) to shape "
                         "its curves by hand",
                "BACK": "The Graph Editor back to the timeline",
                "CLEAR": "Remove this animation's keys in this section (Ctrl+Z brings them back)",
                "FASTER": "A quarter faster (shorter)",
                "SLOWER": "A quarter slower (longer)"}.get(props.action, cls.bl_description)

    def execute(self, context):
        from . import anim_tracks
        scene = context.scene
        if self.action == 'BACK':
            anim_tracks.back_from_graph(context)
            return {'FINISHED'}
        if self.track:
            scene.studio.anim_track = self.track
        track, start, end = anim_tracks.active(scene)
        if track is None:
            return {'CANCELLED'}
        if self.action == 'GRAPH':
            if not anim_tracks.show_in_graph(context, track):
                self.report({'WARNING'}, "No timeline or Graph Editor in this workspace")
                return {'CANCELLED'}
        elif self.action == 'CLEAR':
            count = anim_tracks.clear(track, start, end)
            self.report({'INFO'}, "%d keys of %s removed" % (count, track["label"]))
        elif self.action in {'FASTER', 'SLOWER'}:
            length = track["last"] - track["first"]
            anim_tracks.retime(track, start, end, track["first"],
                               round(length * (0.75 if self.action == 'FASTER' else 1.25)))
        return {'FINISHED'}


def _draw_now(layout, scene, current):
    """What moves in the section at the playhead: one line per animated thing (camera, object, value …)
    with its frames; the chosen one below: start, length, faster/slower, curve preset, Graph Editor."""
    from . import anim_tracks, motion
    start, end, _name = anim_tracks.section_range(scene)
    title = current[0].name if current else "Whole film"
    box = layout.box()
    head = box.row(align=True)
    head.label(text=title, icon='ANIM')
    info = head.row()
    info.alignment = 'RIGHT'
    info.active = False
    info.label(text="%d–%d · %.1f s" % (start, end, motion.seconds(scene, end - start + 1)), translate=False)
    found = anim_tracks.tracks(scene, start, end)
    if not found:
        hint = box.column(align=True)
        hint.active = False
        hint.label(text="Nothing moves here yet.", icon='INFO')
        hint.label(text="Add a camera move below, key a value or an object (I)")
        return
    track, _s, _e = anim_tracks.active(scene)
    col = box.column(align=True)
    for t in found:
        row = col.row(align=True)
        chosen = track is not None and t["id"] == track["id"]
        op = row.operator(STUDIO_OT_anim_track.bl_idname, text=t["label"], icon=t["icon"], depress=chosen)
        op.action, op.track = 'PICK', t["id"]
        span = row.row(align=True)
        span.ui_units_x = 4.5
        span.alignment = 'RIGHT'
        span.active = chosen
        span.label(text="%d–%d" % (t["first"], t["last"]) if t["last"] > t["first"] else "%d" % t["first"],
                   translate=False)
    if track is None:
        return
    rq = scene.studio
    edit = box.box()
    top = edit.row(align=True)
    top.label(text=track["label"], icon=track["icon"])
    graph = anim_tracks.graph_open(bpy.context)
    op = top.operator(STUDIO_OT_anim_track.bl_idname, text="Timeline" if graph else "Graph",
                      icon='TIME' if graph else 'GRAPH', depress=graph)
    op.action, op.track = ('BACK' if graph else 'GRAPH'), track["id"]
    op = top.operator(STUDIO_OT_anim_track.bl_idname, text="", icon='TRASH')
    op.action, op.track = 'CLEAR', track["id"]
    if track["last"] > track["first"]:
        row = edit.row(align=True)
        row.prop(rq, "anim_start", text="Start")
        row.prop(rq, "anim_length", text="Frames")
        row = edit.row(align=True)
        for action, text, icon in (('FASTER', "Faster", 'FF'), ('SLOWER', "Slower", 'REW')):
            op = row.operator(STUDIO_OT_anim_track.bl_idname, text=text, icon=icon)
            op.action, op.track = action, track["id"]
        secs = row.row()
        secs.alignment = 'RIGHT'
        secs.active = False
        secs.label(text="%.1f s" % motion.seconds(scene, track["last"] - track["first"]), translate=False)
        edit.prop(rq, "anim_curve", text="Curve")
    else:
        edit.prop(rq, "anim_start", text="Frame")


def _draw_moves(layout, scene, current):
    """New camera moves for the section at the playhead (pull out, hold, orbit …) in order, then Apply."""
    from . import motion
    rq = scene.studio
    box, head, is_open = section(layout, "anim_moves", "Camera Moves", 'CON_CAMERASOLVER')
    if not is_open:
        return
    if current is None:
        start, end, name = motion.motion_range(scene)
    else:
        marker, start, end = current
        name = marker.name
    steps = [(i, s) for i, s in enumerate(rq.motion_steps) if s.section == name]
    col = box.column(align=True)
    if not steps:
        hint = col.row()
        hint.active = False
        hint.label(text="Choose a move, Add, then Apply (it replaces the camera's keys here)")
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
    box, head, is_open = section(layout, "anim_values", "Values", 'DRIVER', default_open=False)
    op = head.operator("wm.properties_add", text="", icon='ADD', emboss=False)
    op.data_path = "scene"
    if not is_open:
        return
    found = scene_values(scene)
    if not found:
        hint = box.row()
        hint.active = False
        hint.label(text="+ makes one: a value drivers and materials can read", icon='INFO')
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
        _draw_now(layout, scene, current)
        _draw_moves(layout, scene, current)
        box, _head, is_open = section(layout, "anim_explode", "Explosion", 'MOD_EXPLODE', default_open=False)
        if is_open:
            explode.draw(box, scene)
        _draw_values(layout, scene)


classes = (STUDIO_OT_value_key, STUDIO_OT_anim_track, STUDIO_PT_animation)

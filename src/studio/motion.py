# SPDX-License-Identifier: GPL-3.0-or-later
"""Animation: sections in the timeline, motion presets, which keys a section has, its quick preview.

Sections are Blender's own timeline markers: a section runs from its marker to the frame before the
next one (the last one to the scene's end), and the marker's camera is the camera of that section
(Blender switches it while playing and rendering). So the timeline shows them with their names.

Motion presets put a finished movement onto the camera (on its pivot, see orbit.rig) or the look over
the current section (or the whole scene without sections): two keys, smooth or linear between them.
"""

import math

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from . import cameras, keys, orbit

# key: (label, icon, what it does)
PRESETS = {
    'TURNTABLE': ("Turntable", 'FILE_REFRESH', "Once around the product (360°)"),
    'HALF': ("Half Orbit", 'ORIENTATION_GIMBAL', "Half way around the product (180°)"),
    'SWING': ("Swing", 'DRIVER_ROTATIONAL_DIFFERENCE', "A small swing around the product (±25°)"),
    'PUSH_IN': ("Push In", 'ZOOM_IN', "Slowly closer to the product (to 60 % of the distance)"),
    'PULL_OUT': ("Pull Out", 'ZOOM_OUT', "Slowly away from the product (to 160 % of the distance)"),
    'CRANE': ("Crane Up", 'SORT_DESC', "From low to high, looking at the product"),
    'FOCUS': ("Focus Pull", 'CAMERA_DATA', "The sharp plane moves from the front onto the product"),
    'REVEAL': ("Reveal", 'LIGHT_SUN', "From dark into the light (exposure)"),
}
EASES = (('SMOOTH', "Smooth", "Slow at the start and the end"), ('LINEAR', "Linear", "The same speed all the way "
         "(a turntable that loops)"))


# ------------------------------------------------------------------ sections

def sections(scene):
    """[(marker, start, end)] in time order."""
    markers = sorted(scene.timeline_markers, key=lambda m: m.frame)
    out = []
    for i, marker in enumerate(markers):
        end = markers[i + 1].frame - 1 if i + 1 < len(markers) else scene.frame_end
        if end >= marker.frame:
            out.append((marker, marker.frame, end))
    return out


def current_range(scene):
    """(start, end, section name or "") around the current frame."""
    frame = scene.frame_current
    for marker, start, end in sections(scene):
        if start <= frame <= end:
            return start, end, marker.name
    return scene.frame_start, scene.frame_end, ""


def seconds(scene, frames):
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    return frames / fps if fps else 0.0


# ------------------------------------------------------------------ keys: easing, overview

def fcurves_of(block):
    """The F-curves of a data block (Blender 5: the action's channel bag for its slot)."""
    ad = getattr(block, "animation_data", None)
    if ad is None or ad.action is None:
        return []
    try:
        from bpy_extras import anim_utils
        bag = anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
        return list(bag.fcurves) if bag is not None else []
    except (ImportError, AttributeError):
        return list(getattr(ad.action, "fcurves", []))


def _ease(blocks, start, end, ease):
    for block in blocks:
        for fc in fcurves_of(block):
            for point in fc.keyframe_points:
                if start - 0.5 <= point.co.x <= end + 0.5:
                    point.interpolation = 'LINEAR' if ease == 'LINEAR' else 'BEZIER'
                    if ease != 'LINEAR':
                        point.handle_left_type = point.handle_right_type = 'AUTO_CLAMPED'
            fc.update()


def _count(blocks, start, end):
    return sum(1 for block in blocks for fc in fcurves_of(block) for p in fc.keyframe_points
               if start - 0.5 <= p.co.x <= end + 0.5)


_overview_cache = {}


def key_overview(scene, start, end, camera=None):
    """{"Camera": n, "Light": n, "Look": n, "World": n} keys in the range (only those with keys);
    counted again at most every 2 s."""
    import time
    camera = camera or cameras.active_camera(scene)
    key = (scene.name, start, end, camera.name if camera else "")
    cached = _overview_cache.get(key)
    if cached is not None and time.monotonic() - cached[0] < 2.0:
        return cached[1]
    result = _key_overview(scene, start, end, camera)
    _overview_cache[key] = (time.monotonic(), result)
    return result


def _key_overview(scene, start, end, camera):
    cam_blocks = [b for b in (camera, orbit.pivot_of(camera), camera.data if camera else None) if b is not None]
    lights = [o for o in scene.objects if o.type == 'LIGHT']
    from . import background
    tree = background.compositor_tree(scene)
    world = scene.world.node_tree if scene.world is not None and scene.world.node_tree is not None else None
    counts = {"Camera": _count(cam_blocks, start, end),
              "Light": _count(lights + [o.data for o in lights], start, end),
              "Look": _count([scene] + ([tree] if tree is not None else []), start, end),
              "World": _count([world] if world is not None else [], start, end)}
    return {k: v for k, v in counts.items() if v}


# ------------------------------------------------------------------ presets

def apply(scene, camera, preset, start, end, ease='SMOOTH'):
    """Two keys of the movement, at start and end. Returns how many keys were set."""
    end = max(end, start + 1)
    if preset == 'REVEAL':
        view = scene.view_settings
        target = view.exposure
        view.exposure = target - 4.0
        n = keys._key(view, "exposure", start)
        view.exposure = target
        n += keys._key(view, "exposure", end)
        _ease([scene], start, end, ease)
        return n
    distance = orbit.camera_orbit(scene.studio, camera)[2]
    pivot = orbit.rig(camera, distance)
    h, v, d = pivot.rotation_euler.z, -pivot.rotation_euler.y, camera.location.x
    data = camera.data
    first, last = {}, {}
    if preset == 'TURNTABLE':
        first, last = {"h": h}, {"h": h + 2 * math.pi}
    elif preset == 'HALF':
        first, last = {"h": h}, {"h": h + math.pi}
    elif preset == 'SWING':
        first, last = {"h": h - math.radians(25)}, {"h": h + math.radians(25)}
    elif preset == 'PUSH_IN':
        first, last = {"d": d}, {"d": d * 0.6}
    elif preset == 'PULL_OUT':
        first, last = {"d": d}, {"d": d * 1.6}
    elif preset == 'CRANE':
        low = max(-orbit.MAX_VERTICAL, v - math.radians(20))
        high = min(orbit.MAX_VERTICAL * 0.9, v + math.radians(25))
        first, last = {"v": low}, {"v": high}
    elif preset == 'FOCUS':
        data.dof.use_dof = True
        if data.dof.focus_object is not None:
            data.dof.focus_object = None      # the distance is keyed, not an object
    count = 0
    for frame, values in ((start, first), (end, last)):
        if preset == 'FOCUS':
            data.dof.focus_distance = d * (0.55 if frame == start else 1.0)
            count += keys._key(data.dof, "focus_distance", frame)
            continue
        pivot.rotation_euler.z = values.get("h", h)
        pivot.rotation_euler.y = -values.get("v", v)
        camera.location.x = values.get("d", d)
        count += keys.key_camera(scene, camera, frame)
    _ease([pivot, camera, data], start, end, ease)
    scene.frame_set(scene.frame_current)
    return count


class STUDIO_OT_motion(Operator):
    bl_idname = "studio.motion"
    bl_label = "Motion Preset"
    bl_options = {'UNDO', 'INTERNAL'}

    preset: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, props):
        label, _icon, text = PRESETS.get(props.preset, ("", "", ""))
        start, end, name = current_range(context.scene)
        return "%s over %s (frames %d–%d): two keys, then change them as you like" % (
            text, "the section " + name if name else "the whole film", start, end)

    def execute(self, context):
        scene = context.scene
        camera = cameras.active_camera(scene)
        if camera is None and self.preset != 'REVEAL':
            self.report({'WARNING'}, "No camera")
            return {'CANCELLED'}
        start, end, _name = current_range(scene)
        count = apply(scene, camera, self.preset, start, end, scene.studio.motion_ease)
        self.report({'INFO'}, "%s: %d keys, frames %d–%d" % (PRESETS[self.preset][0], count, start, end))
        return {'FINISHED'}


class STUDIO_OT_section(Operator):
    bl_idname = "studio.section"
    bl_label = "Section"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    action: EnumProperty(items=(('ADD', "Add", ""), ('GO', "Go", ""), ('REMOVE', "Remove", ""),
                                ('PREVIEW', "Preview", ""), ('RENDER', "Render", "")),
                         options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return {'ADD': "A new section from this frame on (a marker in the timeline), with the active camera",
                'GO': "Go to this section: the timeline plays only it (preview range)",
                'REMOVE': "Remove this section (its marker; the keys stay)",
                'PREVIEW': "A quick film of this section in Low Quality to check it",
                'RENDER': "Render this section as a film in High Quality"}.get(props.action, "")

    def execute(self, context):
        scene = context.scene
        if self.action == 'ADD':
            n = len(scene.timeline_markers) + 1
            marker = scene.timeline_markers.new("Section %d" % n, frame=scene.frame_current)
            marker.camera = cameras.active_camera(scene)
            return {'FINISHED'}
        found = next(((m, s, e) for m, s, e in sections(scene) if m.name == self.name), None)
        if found is None:
            self.report({'WARNING'}, "No section %s" % self.name)
            return {'CANCELLED'}
        marker, start, end = found
        if self.action == 'GO':
            scene.frame_current = start
            if scene.frame_preview_start != start or scene.frame_preview_end != end or not scene.use_preview_range:
                scene.use_preview_range = True
                scene.frame_preview_start, scene.frame_preview_end = start, end
            else:                              # the same again: the whole film
                scene.use_preview_range = False
            if marker.camera is not None:
                scene.camera = marker.camera
        elif self.action == 'REMOVE':
            scene.timeline_markers.remove(marker)
        else:
            camera = marker.camera or cameras.active_camera(scene)
            if camera is None:
                self.report({'WARNING'}, "No camera")
                return {'CANCELLED'}
            render_section(scene, camera, start, end, 'LOW' if self.action == 'PREVIEW' else 'HIGH', marker.name)
            self.report({'INFO'}, "%s: %s, frames %d–%d" % (marker.name, "preview" if self.action == 'PREVIEW'
                                                             else "film", start, end))
        return {'FINISHED'}


def render_section(scene, camera, start, end, quality, name):
    """The section as a film in its own process (see film.py)."""
    rq = scene.studio
    mode, keep_start, keep_end = rq.mode, scene.frame_start, scene.frame_end
    rq.mode = 'ANIM'
    try:
        scene.frame_start, scene.frame_end = start, end
        bpy.ops.studio.add(camera=camera.name, quality=quality)
    finally:
        scene.frame_start, scene.frame_end = keep_start, keep_end
        rq.mode = mode
    item = rq.items[-1]
    item.parts = 1
    from .runner import RUN
    if not RUN.busy:
        bpy.ops.studio.render_queue()


def draw_presets(layout, scene):
    """Camera tab: the presets as buttons, smooth / linear, over which frames."""
    start, end, name = current_range(scene)
    col = layout.column(align=True)
    grid = col.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
    for key, (label, icon, _text) in PRESETS.items():
        grid.operator(STUDIO_OT_motion.bl_idname, text=label, icon=icon).preset = key
    row = col.row(align=True)
    row.prop(scene.studio, "motion_ease", expand=True)
    info = layout.row()
    info.active = False
    info.label(text="%s: frames %d–%d · %.1f s" % (name or "Whole film", start, end,
                                                  seconds(scene, end - start + 1)), translate=False)


def draw_sections(layout, scene):
    """Render tab: the sections with their camera, length, keys; preview, render, remove."""
    box = layout.box()
    head = box.row(align=True)
    head.label(text="Sections", icon='MARKER_HLT')
    head.operator(STUDIO_OT_section.bl_idname, text="", icon='ADD').action = 'ADD'
    found = sections(scene)
    if not found:
        hint = box.row()
        hint.active = False
        hint.label(text="+ here or in the timeline: a section from this frame on (one video each)")
        return
    frame = scene.frame_current
    for marker, start, end in found:
        col = box.column(align=True)
        row = col.row(align=True)
        here = start <= frame <= end
        go = row.operator(STUDIO_OT_section.bl_idname, text="", icon='PLAY' if here else 'TRIA_RIGHT', depress=here)
        go.action, go.name = 'GO', marker.name
        row.prop(marker, "name", text="")
        cam = row.row(align=True)
        cam.ui_units_x = 5
        cam.prop(marker, "camera", text="")
        for action, icon in (('PREVIEW', 'PLAY_SOUND'), ('RENDER', 'RENDER_ANIMATION'), ('REMOVE', 'X')):
            op = row.operator(STUDIO_OT_section.bl_idname, text="", icon=icon, emboss=action != 'REMOVE')
            op.action, op.name = action, marker.name
        overview = key_overview(scene, start, end, marker.camera)
        info = col.row(align=True)
        info.active = False
        info.label(text="%d–%d · %.1f s" % (start, end, seconds(scene, end - start + 1)), translate=False)
        sub = info.row(align=True)
        sub.alignment = 'RIGHT'
        sub.label(text="  ".join("◆ %s %d" % kv for kv in overview.items()) if overview else "no keys",
                  translate=False)


def draw_timeline(self, context):
    """In the timeline's header: + Section (a marker at this frame with the active camera)."""
    space = context.space_data
    if getattr(space, "type", "") == 'DOPESHEET_EDITOR' and getattr(space, "mode", "TIMELINE") != 'TIMELINE':
        return
    row = self.layout.row(align=True)
    row.operator(STUDIO_OT_section.bl_idname, text="Section", icon='MARKER_HLT').action = 'ADD'


def _timeline_header():
    return getattr(bpy.types, "TIME_HT_editor_buttons", None) or getattr(bpy.types, "DOPESHEET_HT_header", None)


classes = (STUDIO_OT_motion, STUDIO_OT_section)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    header = _timeline_header()
    if header is not None:
        header.append(draw_timeline)


def unregister():
    header = _timeline_header()
    if header is not None:
        try:
            header.remove(draw_timeline)
        except ValueError:
            pass
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

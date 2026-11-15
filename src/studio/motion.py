# SPDX-License-Identifier: GPL-3.0-or-later
"""Animation: sections in the timeline, motion presets (with strength, direction and their own frames),
chains of steps, poses as keys, which keys a section has.

Sections are Blender's own timeline markers named "NN Name" (01 Hero, 02 Explosion …): a section runs
from its marker to the frame before the next one (the last one to the scene's end), the marker's camera
is the section's camera (Blender switches it while playing and rendering) and gets the section's number
as its Pass Index (drivers: scene.camera.pass_index). Moving or lengthening a section moves everything
after it along (markers and, if wanted, keys), so the following sections stay as they were.

Motion presets put a movement onto the camera on its pivot (orbit.rig) or the exposure: keys at the start
and end of the chosen frames, smooth or linear. A chain is a list of steps for one section ("pull out to
110, hold to 120, orbit to the end"), applied one after the other.
"""

import math
import re

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator

from . import cameras, keys, orbit

# key: (label, icon, what it does, amount: (default, label, minimum, maximum))
PRESETS = {
    'TURNTABLE': ("Orbit", 'FILE_REFRESH', "Around the product", (360.0, "Degrees", 5.0, 720.0)),
    'SWING': ("Swing", 'DRIVER_ROTATIONAL_DIFFERENCE', "A swing around the product, there and back half",
              (25.0, "± Degrees", 2.0, 90.0)),
    'PUSH_IN': ("Push In", 'ZOOM_IN', "Closer to the product", (60.0, "To % of the distance", 30.0, 95.0)),
    'PULL_OUT': ("Pull Out", 'ZOOM_OUT', "Away from the product", (160.0, "To % of the distance", 105.0, 400.0)),
    'CRANE': ("Crane", 'SORT_DESC', "Up (or down with ⇅), looking at the product", (40.0, "Degrees", 5.0, 89.0)),
    'FOCUS': ("Focus Pull", 'CAMERA_DATA', "The sharp plane moves from the front onto the product",
              (55.0, "Start at % of the distance", 10.0, 95.0)),
    'FADE_IN': ("Fade In", 'LIGHT_SUN', "From black (white with ⇅) into the picture",
                (12.0, "Frames", 1.0, 240.0)),
    'FADE_OUT': ("Fade Out", 'LIGHT_HEMI', "From the picture to black (white with ⇅) at the end",
                 (12.0, "Frames", 1.0, 240.0)),
    'HOLD': ("Hold", 'PAUSE', "The camera stays as it is (a pause in a chain)", (0.0, "", 0.0, 0.0)),
}
PRESET_ITEMS = [(k, v[0], v[2], v[1], i) for i, (k, v) in enumerate(PRESETS.items())]
EASES = (('SMOOTH', "Smooth", "Slow at the start and the end"), ('LINEAR', "Linear", "The same speed all the way "
         "(a turntable that loops)"))
TRANSITIONS = (('CUT', "Cut", "A hard cut"), ('CROSS', "Cross", "The sections blend into each other"),
               ('BLACK', "Black", "Through black"), ('WHITE', "White", "Through white"))


# ------------------------------------------------------------------ sections

_NUMBER = re.compile(r"^\d{2,}\s+")


def plain_name(name):
    """"01 Hero" → "Hero"."""
    return _NUMBER.sub("", name).strip() or name


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


def renumber(scene):
    """Markers named "01 Name", "02 …" in time order; their camera gets the number as Pass Index; chains
    and cut settings follow the new names."""
    renamed = {}
    for i, (marker, _s, _e) in enumerate(sections(scene), 1):
        new = "%02d %s" % (i, plain_name(marker.name))
        if marker.name != new:
            renamed[marker.name] = new
            marker.name = new
        if marker.camera is not None and marker.camera.pass_index != i:
            marker.camera.pass_index = i
    if renamed:
        rq = scene.studio
        for item in list(rq.motion_steps) + list(rq.cut_items):
            if item.section in renamed:
                item.section = renamed[item.section]
    return renamed


def _animated_blocks(scene):
    """Everything in the scene that can carry keys."""
    blocks = [scene]
    for obj in scene.objects:
        blocks.append(obj)
        if obj.data is not None:
            blocks.append(obj.data)
        for slot in obj.material_slots:
            if slot.material is not None and slot.material.node_tree is not None:
                blocks.append(slot.material.node_tree)
    if scene.world is not None and scene.world.node_tree is not None:
        blocks.append(scene.world.node_tree)
    from . import background
    tree = background.compositor_tree(scene)
    if tree is not None:
        blocks.append(tree)
    return list(dict.fromkeys(blocks))


def shift_after(scene, frame, delta, move_keys=True):
    """Everything from `frame` on moves by `delta` frames: markers, the scene's end and (move_keys) the
    keys. Returns how many keys moved."""
    if not delta:
        return 0
    for marker in scene.timeline_markers:
        if marker.frame >= frame:
            marker.frame += delta
    scene.frame_end = max(scene.frame_start + 1, scene.frame_end + delta)
    moved = 0
    if move_keys:
        for block in _animated_blocks(scene):
            for fc in fcurves_of(block):
                points = [p for p in fc.keyframe_points if p.co.x >= frame - 0.001]
                for p in sorted(points, key=lambda p: p.co.x, reverse=delta > 0):
                    p.co.x += delta
                    p.handle_left.x += delta
                    p.handle_right.x += delta
                moved += len(points)
                if points:
                    fc.update()
    return moved


def new_section(scene, name, frame=None, context=None, from_view=False):
    """A section from `frame` on: a marker "NN name" with its own camera P_<name>_01 on a pivot in the
    collection Camera, starting where the active camera is (or the viewport looks from). Returns the marker."""
    frame = scene.frame_current if frame is None else frame
    name = (name or "Shot").strip()
    source = None if from_view else cameras.active_camera(scene)
    data = source.data.copy() if source is not None else bpy.data.cameras.new(name)
    if data.animation_data is not None:
        data.animation_data_clear()
    cam = bpy.data.objects.new(name, data)
    cameras.camera_collection(scene).objects.link(cam)
    cam.name = cam.data.name = cameras.scheme_name('CAMERA', data.type, name)
    if source is not None:
        matrix, distance = source.matrix_world.copy(), orbit.camera_orbit(scene.studio, source)[2]
        for key in ("use_resolution", "resolution_x", "resolution_y"):
            setattr(cam.studio_camera, key, getattr(source.studio_camera, key))
    else:
        view = _viewport(context)
        matrix = view[0].view_matrix.inverted() if view else cam.matrix_world.copy()
        distance = view[0].view_distance if view else 5.0
        if view:
            data.lens = view[1].lens
    cam.matrix_world = matrix
    bpy.context.view_layer.update()
    orbit.rig(cam, max(distance, orbit.MIN_DISTANCE))
    marker = scene.timeline_markers.new(name, frame=frame)
    marker.camera = cam
    renumber(scene)
    return marker


def _viewport(context=None):
    """(region_3d, space) of the first 3D view, or None."""
    context = context or bpy.context
    screen = getattr(context, "screen", None) or (context.window.screen if context.window else None)
    for area in (screen.areas if screen else ()):
        if area.type == 'VIEW_3D':
            space = area.spaces.active
            return space.region_3d, space
    return None


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
    """{"Camera": n, "Light": n, "Objects": n, "Props": n, "Look": n, "World": n} keys in the range;
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
    others = [o for o in scene.objects if o.type not in {'LIGHT', 'CAMERA'} and o != orbit.pivot_of(camera)]
    props = look = 0
    for fc in fcurves_of(scene):
        n = sum(1 for p in fc.keyframe_points if start - 0.5 <= p.co.x <= end + 0.5)
        if fc.data_path.startswith('["'):
            props += n                                   # the scene's own values (Glow, LED …)
        else:
            look += n
    counts = {"Camera": _count(cam_blocks, start, end),
              "Light": _count(lights + [o.data for o in lights], start, end),
              "Objects": _count(others, start, end),
              "Props": props,
              "Look": look + (_count([tree], start, end) if tree is not None else 0),
              "World": _count([world] if world is not None else [], start, end)}
    return {k: v for k, v in counts.items() if v}


# ------------------------------------------------------------------ presets

FADES = {'FADE_IN', 'FADE_OUT'}
CAMERA_MOVES = {'TURNTABLE', 'SWING', 'PUSH_IN', 'PULL_OUT', 'CRANE', 'HOLD'}


def amount_of(preset, amount=0.0):
    """The amount to use: the given one, or the preset's own (0 = the preset's own)."""
    spec = PRESETS.get(preset, PRESETS['HOLD'])[3]
    return float(amount) if amount else spec[0]


def motion_range(scene):
    """(start, end, section name) of the next movement: the current section, narrowed by From / To."""
    start, end, name = current_range(scene)
    rq = scene.studio
    first = rq.motion_from if rq.motion_from and start <= rq.motion_from < end else start
    last = rq.motion_to if rq.motion_to and first < rq.motion_to <= end else end
    return first, last, name


def _targets(scene, camera, preset):
    """(block, data paths) a preset keys, to find and replace keys that are already there."""
    if preset in FADES:
        return [(scene, ("view_settings.exposure",))]
    if camera is None:
        return []
    if preset == 'FOCUS':
        return [(camera.data, ("dof.focus_distance",))]
    return [(orbit.pivot_of(camera), ("location", "rotation_euler")), (camera, ("location",)),
            (camera.data, ("lens", "ortho_scale"))]


def _curves(targets):
    for block, paths in targets:
        if block is None:
            continue
        for fc in fcurves_of(block):
            if fc.data_path in paths:
                yield fc


def keys_in(targets, start, end):
    """How many keys these targets already have in the frames."""
    return sum(1 for fc in _curves(targets) for p in fc.keyframe_points if start - 0.5 <= p.co.x <= end + 0.5)


def clear_keys(targets, start, end):
    removed = 0
    for fc in list(_curves(targets)):
        points = fc.keyframe_points
        for i in reversed(range(len(points))):
            if start - 0.5 <= points[i].co.x <= end + 0.5:
                points.remove(points[i])
                removed += 1
        fc.update()
    return removed


def _fade_range(preset, start, end, frames):
    frames = max(1, int(round(frames)))
    return (start, min(end, start + frames)) if preset == 'FADE_IN' else (max(start, end - frames), end)


def preset_range(preset, start, end, amount=0.0):
    """The frames a preset really keys (a fade only its own frames)."""
    return _fade_range(preset, start, end, amount_of(preset, amount)) if preset in FADES else (start, end)


def conflicts(scene, camera, preset, start, end, amount=0.0):
    """Keys of this kind that a preset over the frames would meet."""
    a, b = preset_range(preset, start, end, amount)
    return keys_in(_targets(scene, camera, preset), a, b)


def _animated(*blocks):
    return any(fcurves_of(b) for b in blocks if b is not None)


def _point(block, path, frame):
    for fc in fcurves_of(block):
        if fc.data_path == path:
            for p in fc.keyframe_points:
                if abs(p.co.x - frame) < 0.5:
                    return p
    return None


def _fade(scene, preset, start, end, frames, reverse, ease):
    view = scene.view_settings
    if _animated(scene):
        scene.frame_set((start + end) // 2)
    base = view.exposure
    dark = base + (10.0 if reverse else -10.0)          # 10 stops: white / black
    a, b = _fade_range(preset, start, end, frames)
    path = "view_settings.exposure"
    count = 0
    if preset == 'FADE_IN':
        guard = a - 1
        if guard >= scene.frame_start and _point(scene, path, guard) is None:
            view.exposure = base
            count += keys._key(view, "exposure", guard)
            _point(scene, path, guard).interpolation = 'CONSTANT'
        for frame, value in ((a, dark), (b, base)):
            view.exposure = value
            count += keys._key(view, "exposure", frame)
        _ease([scene], a, b, ease)
    else:
        for frame, value in ((a, base), (b, dark)):
            view.exposure = value
            count += keys._key(view, "exposure", frame)
        _ease([scene], a, b, ease)
        _point(scene, path, b).interpolation = 'CONSTANT'
        guard = b + 1
        if guard <= scene.frame_end and _point(scene, path, guard) is None:
            view.exposure = base
            count += keys._key(view, "exposure", guard)
    return count


def apply(scene, camera, preset, start, end, ease='SMOOTH', amount=0.0, reverse=False, replace=True,
          drop_focus=False):
    """Keys of the movement over the frames. replace: keys of this kind in the frames go first (else
    they stay and the movement goes over them). A Focus Pull on a camera that focuses on an object
    only with drop_focus (it keys the distance). Returns how many keys were set."""
    end = max(end, start + 1)
    amount = amount_of(preset, amount)
    keep = scene.frame_current
    try:
        if preset in FADES:
            if replace:
                clear_keys(_targets(scene, camera, preset), *_fade_range(preset, start, end, amount))
            return _fade(scene, preset, start, end, amount, reverse, ease)
        if camera is None:
            raise ValueError("No camera")
        data = camera.data
        if preset == 'FOCUS' and data.dof.focus_object is not None and not drop_focus:
            raise ValueError("%s focuses on %s: allow dropping the focus object to pull the focus by distance"
                             % (camera.name, data.dof.focus_object.name))
        pivot = orbit.rig(camera, orbit.camera_orbit(scene.studio, camera)[2])
        if _animated(pivot, camera, data):
            scene.frame_set(start)                     # the movement starts where the camera is then
        h, v, d = pivot.rotation_euler.z, -pivot.rotation_euler.y, camera.location.x
        if replace:
            clear_keys(_targets(scene, camera, preset), start, end)
        sign = -1.0 if reverse else 1.0
        count = 0
        if preset == 'FOCUS':
            data.dof.focus_object = None
            data.dof.use_dof = True
            near, far = d * amount / 100.0, d
            for frame, value in ((start, far if reverse else near), (end, near if reverse else far)):
                data.dof.focus_distance = value
                count += keys._key(data.dof, "focus_distance", frame)
            _ease([data], start, end, ease)
            return count
        poses = [(start, {}), (end, {})]
        if preset == 'TURNTABLE':
            poses[1] = (end, {"h": h + sign * math.radians(amount)})
        elif preset == 'SWING':
            poses.insert(1, ((start + end) // 2, {"h": h + sign * math.radians(amount)}))
        elif preset in {'PUSH_IN', 'PULL_OUT'}:
            poses[1] = (end, {"d": max(orbit.MIN_DISTANCE, d * amount / 100.0)})
        elif preset == 'CRANE':
            top = orbit.MAX_VERTICAL * 0.95
            poses[1] = (end, {"v": max(-top, min(top, v + sign * math.radians(amount)))})
        for frame, values in poses:
            pivot.rotation_euler.z = values.get("h", h)
            pivot.rotation_euler.y = -values.get("v", v)
            camera.location.x = values.get("d", d)
            count += keys.key_camera(scene, camera, frame)
        _ease([pivot, camera, data], start, end, ease)
        return count
    finally:
        scene.frame_set(keep)


# ------------------------------------------------------------------ chains

def steps_of(scene, section):
    return [s for s in scene.studio.motion_steps if s.section == section]


def apply_chain(scene, camera, section, start, end, ease='SMOOTH', drop_focus=False):
    """The section's steps one after the other (a step with 0 frames lasts to the end); keys of the
    kinds used in the section are replaced. Returns how many keys were set."""
    steps = steps_of(scene, section)
    if not steps:
        return 0
    keep = scene.frame_current
    pose = None
    if camera is not None:                     # the pose at the start stays the start of the chain
        pivot = orbit.rig(camera, orbit.camera_orbit(scene.studio, camera)[2])
        scene.frame_set(start)
        pose = (pivot.location.copy(), pivot.rotation_euler.copy(), camera.location.copy())
    for preset in {s.preset for s in steps} | {'HOLD'}:
        clear_keys(_targets(scene, camera, preset), start, end)
    if pose is not None:
        pivot.location, pivot.rotation_euler, camera.location = pose
        keys.key_camera(scene, camera, start)
    count, frame = 0, start
    for step in steps:
        if frame >= end:
            break
        last = end if not step.frames else min(end, frame + step.frames)
        if step.preset in FADES or step.preset == 'FOCUS':
            if step.preset == 'FOCUS' or step.preset in FADES and camera is not None:
                count += apply(scene, camera, 'HOLD', frame, last, ease, replace=False)
            count += apply(scene, camera, step.preset, frame, last, ease, last - frame if step.preset in FADES
                           else step.amount, step.reverse, replace=False, drop_focus=drop_focus)
        else:
            count += apply(scene, camera, step.preset, frame, last, ease, step.amount, step.reverse, replace=False)
        frame = last
    scene.frame_set(keep)
    return count


def key_pose(scene, camera, context=None, from_view=True):
    """The pose at this frame as a key on the camera's pivot (with the lens): the viewport's view
    (if it does not look through the camera) or where the camera is."""
    frame = scene.frame_current
    orbit.rig(camera, orbit.camera_orbit(scene.studio, camera)[2])
    view = _viewport(context) if from_view else None
    if view is not None and view[0].view_perspective != 'CAMERA':
        r3d, space = view
        orbit.put(camera, r3d.view_matrix.inverted(), r3d.view_distance)
        if camera.data.type == 'PERSP':
            camera.data.lens = space.lens
    return keys.key_camera(scene, camera, frame)


# ------------------------------------------------------------------ operators

class STUDIO_OT_motion(Operator):
    bl_idname = "studio.motion"
    bl_label = "Motion Preset"
    bl_options = {'UNDO', 'INTERNAL'}

    preset: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    chain: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})
    mode: EnumProperty(name="Keys already there", items=(
        ('REPLACE', "Replace", "Remove the keys of this kind in these frames first"),
        ('ADD', "Add", "Keep them: the movement goes over them")), default='REPLACE', options={'SKIP_SAVE'})
    drop_focus: BoolProperty(name="Drop Focus Object", description="The camera focuses on an object: drop it and "
                             "pull the focus by distance", default=False, options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, props):
        start, end, name = motion_range(context.scene)
        where = "the section " + name if name else "the whole film"
        if props.chain:
            return "Apply the steps of %s one after the other (frames %d–%d)" % (where, start, end)
        label, _icon, text, _amount = PRESETS.get(props.preset, PRESETS['HOLD'])
        return "%s over %s (frames %d–%d), with the strength and direction set below" % (text, where, start, end)

    def _check(self, context):
        scene = context.scene
        camera = cameras.active_camera(scene)
        start, end, name = motion_range(scene)
        if self.chain:
            presets = {s.preset for s in steps_of(scene, name)}
            hits = sum(keys_in(_targets(scene, camera, p), start, end) for p in presets | {'HOLD'})
        else:
            presets = {self.preset}
            hits = conflicts(scene, camera, self.preset, start, end, scene.studio.motion_amount)
        focus = camera is not None and camera.data.dof.focus_object is not None and 'FOCUS' in presets
        return hits, focus

    def invoke(self, context, _event):
        self._hits, self._focus = self._check(context)
        if self._hits or self._focus:
            return context.window_manager.invoke_props_dialog(self, width=320)
        return self.execute(context)

    def draw(self, context):
        layout = self.layout
        hits, focus = getattr(self, "_hits", 0), getattr(self, "_focus", False)
        if hits:
            layout.label(text="%d keys of this kind are already in these frames" % hits, icon='DECORATE_KEYFRAME')
            if not self.chain:
                layout.row().prop(self, "mode", expand=True)
        if focus:
            camera = cameras.active_camera(context.scene)
            layout.label(text="Focuses on %s" % camera.data.dof.focus_object.name, icon='ERROR')
            layout.prop(self, "drop_focus")

    def execute(self, context):
        scene = context.scene
        rq = scene.studio
        camera = cameras.active_camera(scene)
        start, end, name = motion_range(scene)
        try:
            if self.chain:
                count = apply_chain(scene, camera, name, *current_range(scene)[:2], rq.motion_ease, self.drop_focus)
                label = "Steps"
            else:
                count = apply(scene, camera, self.preset, start, end, rq.motion_ease, rq.motion_amount,
                              rq.motion_reverse, self.mode == 'REPLACE', self.drop_focus)
                label = PRESETS[self.preset][0]
        except ValueError as ex:
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
        self.report({'INFO'}, "%s: %d keys, frames %d–%d" % (label, count, start, end))
        return {'FINISHED'}


class STUDIO_OT_motion_step(Operator):
    bl_idname = "studio.motion_step"
    bl_label = "Step"
    bl_options = {'UNDO', 'INTERNAL'}

    action: EnumProperty(items=(('ADD', "Add", "Add the preset set above as the next step of this section"),
                                ('REMOVE', "Remove", "Remove this step"),
                                ('UP', "Earlier", "Move this step earlier")), options={'HIDDEN', 'SKIP_SAVE'})
    index: IntProperty(default=-1, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return {'ADD': "Add the preset chosen above (with its strength) as the next step of this section: "
                       "e.g. pull out, hold, orbit",
                'REMOVE': "Remove this step", 'UP': "Move this step earlier"}[props.action]

    def execute(self, context):
        scene = context.scene
        rq = scene.studio
        steps = rq.motion_steps
        if self.action == 'ADD':
            _start, _end, name = current_range(scene)
            step = steps.add()
            step.section, step.preset = name, rq.motion_preset
            step.amount, step.reverse = rq.motion_amount, rq.motion_reverse
            step.frames = 12 if rq.motion_preset == 'HOLD' else 24
        elif 0 <= self.index < len(steps):
            if self.action == 'REMOVE':
                steps.remove(self.index)
            else:
                mine = [i for i, s in enumerate(steps) if s.section == steps[self.index].section]
                at = mine.index(self.index)
                if at:
                    steps.move(self.index, mine[at - 1])
        return {'FINISHED'}


class STUDIO_OT_key_pose(Operator):
    bl_idname = "studio.key_pose"
    bl_label = "Keep Pose"
    bl_description = ("Keep the pose at this frame as a key: the camera moves to the viewport's view (with its lens) "
                      "and is keyed on its pivot; looking through the camera: where it is")
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        scene = context.scene
        camera = cameras.active_camera(scene)
        if camera is None:
            self.report({'WARNING'}, "No camera")
            return {'CANCELLED'}
        count = key_pose(scene, camera, context)
        self.report({'INFO'}, "Pose at frame %d: %d keys on %s" % (scene.frame_current, count, camera.name))
        return {'FINISHED'}


class STUDIO_OT_section(Operator):
    bl_idname = "studio.section"
    bl_label = "Section"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(name="Name", options={'SKIP_SAVE'})
    source: EnumProperty(name="Start Pose", items=(('CAMERA', "Active Camera", "Where the active camera is"),
                                                   ('VIEW', "Viewport", "The viewport's view")),
                         default='CAMERA', options={'SKIP_SAVE'})
    action: EnumProperty(items=(('ADD', "Add", ""), ('GO', "Go", ""), ('REMOVE', "Remove", ""),
                                ('PREVIEW', "Preview", ""), ('RENDER', "Render", "")),
                         options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return {'ADD': "A new section from this frame on: a marker \"NN Name\" in the timeline with its own camera "
                       "(P_<name>_01 on a pivot), starting from the active camera or the view",
                'GO': "Go to this section: the timeline plays only it (again: the whole film)",
                'REMOVE': "Remove this section (its marker and steps; the camera and the keys stay)",
                'PREVIEW': "LQ: a quick film of this section in Low Quality (watch it in the Render tab)",
                'RENDER': "HQ: this section as a film in High Quality (watch it in the Render tab)"}.get(props.action, "")

    def invoke(self, context, _event):
        if self.action == 'ADD':
            if not self.name:
                self.name = "Shot %d" % (len(context.scene.timeline_markers) + 1)
            return context.window_manager.invoke_props_dialog(self, width=280)
        return self.execute(context)

    def draw(self, _context):
        self.layout.prop(self, "name")
        self.layout.row().prop(self, "source", expand=True)

    def execute(self, context):
        scene = context.scene
        if self.action == 'ADD':
            marker = new_section(scene, self.name, context=context, from_view=self.source == 'VIEW')
            scene.camera = marker.camera
            self.report({'INFO'}, "%s with %s" % (marker.name, marker.camera.name))
            return {'FINISHED'}
        found = find(scene, self.name)
        if found is None:
            self.report({'WARNING'}, "No section %s" % self.name)
            return {'CANCELLED'}
        marker, start, end = found
        if self.action == 'GO':
            go(scene, marker, start, end)
        elif self.action == 'REMOVE':
            rq = scene.studio
            for items in (rq.motion_steps, rq.cut_items):
                for i in reversed(range(len(items))):
                    if items[i].section == marker.name:
                        items.remove(i)
            scene.timeline_markers.remove(marker)
            renumber(scene)
        else:
            camera = marker.camera or cameras.active_camera(scene)
            if camera is None:
                self.report({'WARNING'}, "No camera")
                return {'CANCELLED'}
            render_section(scene, camera, start, end, 'LOW' if self.action == 'PREVIEW' else 'HIGH', marker.name)
            self.report({'INFO'}, "%s: %s, frames %d–%d" % (marker.name, "preview" if self.action == 'PREVIEW'
                                                             else "film", start, end))
        return {'FINISHED'}


def find(scene, name):
    return next(((m, s, e) for m, s, e in sections(scene) if m.name == name), None)


def go(scene, marker, start, end):
    scene.frame_current = start
    if scene.frame_preview_start != start or scene.frame_preview_end != end or not scene.use_preview_range:
        scene.use_preview_range = True
        scene.frame_preview_start, scene.frame_preview_end = start, end
    else:                              # the same again: the whole film
        scene.use_preview_range = False
    if marker.camera is not None:
        scene.camera = marker.camera


def set_length(scene, name, start=None, length=None, move_keys=True):
    """Move a section to `start` (it and everything after it moves; the section before gets longer or
    shorter) and / or give it `length` frames (everything after it moves). With move_keys the keys move
    along, so the following sections stay as they were. Returns how many keys moved."""
    found = find(scene, name)
    if found is None:
        raise ValueError("No section %s" % name)
    _marker, old_start, old_end = found
    moved = 0
    if start is not None and start != old_start:
        delta = max(scene.frame_start, int(start)) - old_start
        moved += shift_after(scene, old_start, delta, move_keys)
        old_start, old_end = old_start + delta, old_end + delta
    if length is not None:
        new_end = old_start + max(1, int(length)) - 1
        moved += shift_after(scene, old_end + 1, new_end - old_end, move_keys)
    renumber(scene)
    return moved


class STUDIO_OT_section_edit(Operator):
    bl_idname = "studio.section_edit"
    bl_label = "Section Frames"
    bl_description = ("Move or lengthen this section: the sections after it move along (with their keys), so they "
                      "stay as they were")
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    start: IntProperty(name="Start", options={'SKIP_SAVE'})
    length: IntProperty(name="Frames", min=1, options={'SKIP_SAVE'})
    move_keys: BoolProperty(name="Move Keys Along", description="The keys after the change move with their "
                            "sections (off: only the markers move)", default=True, options={'SKIP_SAVE'})

    def invoke(self, context, _event):
        found = find(context.scene, self.name)
        if found is None:
            return {'CANCELLED'}
        _m, self.start, end = found
        self.length = end - self.start + 1
        return context.window_manager.invoke_props_dialog(self, width=260)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "start")
        col.prop(self, "length")
        sub = col.row()
        sub.active = False
        sub.label(text="%.2f s" % seconds(context.scene, self.length), translate=False)
        col.prop(self, "move_keys")

    def execute(self, context):
        try:
            moved = set_length(context.scene, self.name, self.start, self.length, self.move_keys)
        except ValueError as ex:
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
        self.report({'INFO'}, "%s: %d keys moved along" % (self.name, moved))
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


# ------------------------------------------------------------------ drawing

def draw_presets(layout, scene):
    """Animation tab: a preset with its strength and direction over chosen frames; the section's steps."""
    rq = scene.studio
    start, end, name = motion_range(scene)
    col = layout.column(align=True)
    row = col.row(align=True)
    row.prop(rq, "motion_preset", text="")
    row.prop(rq, "motion_reverse", text="", icon='ARROW_LEFTRIGHT', toggle=True)
    spec = PRESETS.get(rq.motion_preset, PRESETS['HOLD'])
    if spec[3][1]:
        col.prop(rq, "motion_amount", text=spec[3][1])
    row = col.row(align=True)
    row.prop(rq, "motion_from", text="From Frame")
    row.prop(rq, "motion_to", text="To Frame")
    col.row(align=True).prop(rq, "motion_ease", expand=True)
    row = layout.row(align=True)
    row.scale_y = 1.2
    row.operator(STUDIO_OT_motion.bl_idname, text="Apply", icon=spec[1]).preset = rq.motion_preset
    row.operator(STUDIO_OT_key_pose.bl_idname, text="Keep Pose", icon='DECORATE_KEYFRAME')
    info = layout.row()
    info.active = False
    info.label(text="%s: frames %d–%d · %.1f s" % (name or "Whole film", start, end,
                                                  seconds(scene, end - start + 1)), translate=False)

    steps = [(i, s) for i, s in enumerate(rq.motion_steps) if s.section == name]
    box = layout.box()
    head = box.row(align=True)
    head.label(text="Steps of %s" % (name or "the film"), icon='LINENUMBERS_ON')
    head.operator(STUDIO_OT_motion_step.bl_idname, text="", icon='ADD').action = 'ADD'
    if not steps:
        hint = box.row()
        hint.active = False
        hint.label(text="+ the preset above as a step: pull out, hold, orbit …")
        return
    for i, step in steps:
        row = box.row(align=True)
        row.prop(step, "preset", text="")
        row.prop(step, "frames", text="")
        if PRESETS.get(step.preset, PRESETS['HOLD'])[3][1] and step.preset not in FADES:
            row.prop(step, "amount", text="")
        row.prop(step, "reverse", text="", icon='ARROW_LEFTRIGHT', toggle=True)
        up = row.operator(STUDIO_OT_motion_step.bl_idname, text="", icon='TRIA_UP', emboss=False)
        up.action, up.index = 'UP', i
        rm = row.operator(STUDIO_OT_motion_step.bl_idname, text="", icon='X', emboss=False)
        rm.action, rm.index = 'REMOVE', i
    op = box.operator(STUDIO_OT_motion.bl_idname, text="Apply Steps", icon='PLAY')
    op.chain = True


_SHORT = {"Camera": "Cam", "Objects": "Obj"}


def draw_sections(layout, scene):
    """Animation tab: the sections with their camera, frames, keys; go, preview, render, remove."""
    box = layout.box()
    head = box.row(align=True)
    head.label(text="Sections", icon='MARKER_HLT')
    head.operator(STUDIO_OT_section.bl_idname, text="", icon='ADD').action = 'ADD'
    found = sections(scene)
    if not found:
        hint = box.row()
        hint.active = False
        hint.label(text="+ here or in the timeline: a section from this frame on, with its own camera")
        return
    frame = scene.frame_current
    for marker, start, end in found:
        col = box.column(align=True)
        row = col.row(align=True)
        here = start <= frame <= end
        go_op = row.operator(STUDIO_OT_section.bl_idname, text="", icon='PLAY' if here else 'TRIA_RIGHT',
                             depress=here)
        go_op.action, go_op.name = 'GO', marker.name
        row.prop(marker, "name", text="")
        cam = row.row(align=True)
        cam.ui_units_x = 5
        cam.prop(marker, "camera", text="")
        for action, text in (('PREVIEW', "LQ"), ('RENDER', "HQ")):
            op = row.operator(STUDIO_OT_section.bl_idname, text=text)
            op.action, op.name = action, marker.name
        op = row.operator(STUDIO_OT_section.bl_idname, text="", icon='X', emboss=False)
        op.action, op.name = 'REMOVE', marker.name
        info = col.split(factor=0.4, align=True)
        edit = info.operator(STUDIO_OT_section_edit.bl_idname, text="%d–%d · %.1f s" % (
            start, end, seconds(scene, end - start + 1)), icon='TIME', emboss=False, translate=False)
        edit.name = marker.name
        overview = key_overview(scene, start, end, marker.camera)
        sub = info.row(align=True)
        sub.active = False
        sub.alignment = 'RIGHT'
        sub.label(text="◆ " + " · ".join("%s %d" % (_SHORT.get(k, k), n) for k, n in overview.items())
                  if overview else "no keys", translate=False)


def draw_timeline(self, context):
    """In the timeline's header: + Section, a viewport preview, the film."""
    space = context.space_data
    if getattr(space, "type", "") == 'DOPESHEET_EDITOR' and getattr(space, "mode", "TIMELINE") != 'TIMELINE':
        return
    row = self.layout.row(align=True)
    row.operator(STUDIO_OT_section.bl_idname, text="Section", icon='MARKER_HLT').action = 'ADD'
    row.operator("studio.anim_preview", text="", icon='PLAY')
    row.operator("studio.add", text="", icon='RENDER_ANIMATION').mode = 'ANIM'


def _timeline_header():
    return getattr(bpy.types, "TIME_HT_editor_buttons", None) or getattr(bpy.types, "DOPESHEET_HT_header", None)


classes = (STUDIO_OT_motion, STUDIO_OT_motion_step, STUDIO_OT_key_pose, STUDIO_OT_section, STUDIO_OT_section_edit)


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

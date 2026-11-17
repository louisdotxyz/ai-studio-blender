# SPDX-License-Identifier: GPL-3.0-or-later
"""What moves in a section (Animation tab): one track per thing with keys there (a camera, an object, a
light, a value of the scene like Explosion, a material, the look, the world), with where its keys start,
how long they take and their curve. A track is moved, made faster or slower and given a curve preset
(soft in and out, a magnet that snaps, an overshoot …) without the Graph Editor; the Graph Editor
opens on it for the fine work."""

import time

import bpy

from . import orbit
from .motion import fcurves_of

TOLERANCE = 0.5

# key: (label, icon, description, interpolation, easing)
CURVES = {
    'SMOOTH': ("Smooth", 'IPO_BEZIER', "Slow in and out: an orbit that fades in and out", 'BEZIER', 'AUTO'),
    'LINEAR': ("Linear", 'IPO_LINEAR', "The same speed all the way (a turntable that loops)", 'LINEAR', 'AUTO'),
    'EASE_IN': ("Ease In", 'IPO_EASE_IN', "Starts slowly and gets faster", 'CUBIC', 'EASE_IN'),
    'EASE_OUT': ("Ease Out", 'IPO_EASE_OUT', "Starts fast and arrives softly", 'CUBIC', 'EASE_OUT'),
    'SNAP': ("Snap", 'SNAP_ON', "Like a magnet: pulled in faster and faster, then it clicks shut", 'EXPO', 'EASE_IN'),
    'OVERSHOOT': ("Overshoot", 'IPO_BACK', "Goes a little past the end and comes back", 'BACK', 'EASE_OUT'),
    'BOUNCE': ("Bounce", 'IPO_BOUNCE', "Bounces off the end a few times", 'BOUNCE', 'EASE_OUT'),
    'ELASTIC': ("Spring", 'IPO_ELASTIC', "Springs around the end before it settles", 'ELASTIC', 'EASE_OUT'),
    'STEP': ("Hold", 'IPO_CONSTANT', "Jumps from key to key (no movement in between)", 'CONSTANT', 'AUTO'),
}
MIXED = 'MIXED'
CURVE_ITEMS = [(k, v[0], v[2], v[1], i) for i, (k, v) in enumerate(CURVES.items())] + [
    (MIXED, "Mixed", "The keys have different curves: choose one for all", 'QUESTION', len(CURVES))]


def section_range(scene):
    """(start, end, name) of the section at the playhead, else of the whole film."""
    from . import motion
    frame = scene.frame_current
    for marker, start, end in motion.sections(scene):
        if start <= frame <= end:
            return start, end, marker.name
    return motion.motion_range(scene)


def _in(point, start, end):
    return start - TOLERANCE <= point.co.x <= end + TOLERANCE


def _curves_with_keys(blocks, start, end, only=None):
    out = []
    for block in blocks:
        for fc in fcurves_of(block):
            if only is not None and not only(fc):
                continue
            if any(_in(p, start, end) for p in fc.keyframe_points):
                out.append(fc)
    return out


def _raw_tracks(scene, start, end):
    tracks = []

    def add(tid, label, icon, curves, select=()):
        if curves:
            frames = [p.co.x for fc in curves for p in fc.keyframe_points if _in(p, start, end)]
            tracks.append({"id": tid, "label": label, "icon": icon, "curves": curves, "select": list(select),
                           "first": int(round(min(frames))), "last": int(round(max(frames))),
                           "keys": len(set(round(f) for f in frames))})

    pivots = set()
    for obj in scene.objects:
        if obj.type == 'CAMERA':
            pivot = orbit.pivot_of(obj)
            if pivot is not None:
                pivots.add(pivot.name)
            blocks = [b for b in (obj, pivot, obj.data) if b is not None]
            add("CA:" + obj.name, obj.name, 'CAMERA_DATA', _curves_with_keys(blocks, start, end),
                [b for b in (pivot, obj) if b is not None])
    for obj in scene.objects:
        if obj.type == 'CAMERA' or obj.name in pivots:
            continue
        blocks = [obj] + ([obj.data] if obj.data is not None else [])
        add("OB:" + obj.name, obj.name, 'LIGHT' if obj.type == 'LIGHT' else 'OBJECT_DATA',
            _curves_with_keys(blocks, start, end), [obj])
    by_prop = {}
    look = []
    for fc in _curves_with_keys([scene], start, end):
        if fc.data_path.startswith('["') and fc.data_path.endswith('"]'):
            by_prop.setdefault(fc.data_path[2:-2], []).append(fc)
        else:
            look.append(fc)
    for name in sorted(by_prop, key=str.lower):
        add("PR:" + name, name, 'DRIVER', by_prop[name])
    from . import background
    tree = background.compositor_tree(scene)
    if tree is not None:
        look += _curves_with_keys([tree], start, end)
    add("LOOK", "Look", 'SHADING_RENDERED', look)
    materials = []
    for obj in scene.objects:
        for slot in obj.material_slots:
            if slot.material is not None and slot.material not in materials:
                materials.append(slot.material)
    for mat in materials:
        blocks = [mat] + ([mat.node_tree] if mat.node_tree is not None else [])
        add("MA:" + mat.name, mat.name, 'MATERIAL', _curves_with_keys(blocks, start, end))
    if scene.world is not None:
        blocks = [scene.world] + ([scene.world.node_tree] if scene.world.node_tree is not None else [])
        add("WORLD", "World", 'WORLD', _curves_with_keys(blocks, start, end))
    return tracks


_cache = {}


def tracks(scene, start=None, end=None):
    """The tracks with keys in the range (default: the section at the playhead), counted again at most
    every second (the panel draws often)."""
    if start is None:
        start, end, _name = section_range(scene)
    key = (scene.name, start, end)
    hit = _cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < 1.0:
        return hit[1]
    found = _raw_tracks(scene, start, end)
    _cache.clear()
    _cache[key] = (time.monotonic(), found)
    return found


def forget():
    _cache.clear()


def active(scene):
    """The chosen track (or the first one when none is chosen) with the range it lives in."""
    start, end, _name = section_range(scene)
    found = tracks(scene, start, end)
    want = scene.studio.anim_track
    track = next((t for t in found if t["id"] == want), None) or (found[0] if found else None)
    return track, start, end


def _points(track, start, end):
    return [(fc, p) for fc in track["curves"] for p in fc.keyframe_points if _in(p, start, end)]


def retime(track, start, end, first, length):
    """The track's keys in the range: the first one to `first`, spread over `length` frames (the
    handles go along). Kept inside the range. Returns (first, length) as set."""
    old_first, old_last = track["first"], track["last"]
    old_len = max(1, old_last - old_first)
    length = max(1, min(int(length), end - start))
    first = max(start, min(int(first), end - length))
    scale = length / old_len if old_last > old_first else 1.0
    moved = set()
    for fc, point in _points(track, start, end):
        for co in (point.co, point.handle_left, point.handle_right):
            co.x = first + (co.x - old_first) * scale
        moved.add(fc)
    for fc in moved:
        fc.update()
    forget()
    track["first"], track["last"] = first, first + (length if old_last > old_first else 0)
    return first, length


def curve_of(track, start, end):
    """The preset the track's keys have (MIXED when they differ)."""
    found = set()
    for _fc, p in _points(track, start, end):
        if p.co.x >= track["last"] - TOLERANCE:          # the last key leads nowhere in the range
            continue
        for key, spec in CURVES.items():
            if p.interpolation == spec[3] and (spec[3] in {'BEZIER', 'LINEAR', 'CONSTANT'} or p.easing == spec[4]):
                found.add(key)
                break
        else:
            found.add(MIXED)
    if not found:
        return 'SMOOTH'
    return found.pop() if len(found) == 1 else MIXED


def set_curve(track, start, end, curve):
    spec = CURVES[curve]
    touched = set()
    for fc, p in _points(track, start, end):
        p.interpolation = spec[3]
        p.easing = spec[4]
        if spec[3] == 'BEZIER':
            p.handle_left_type = p.handle_right_type = 'AUTO_CLAMPED'
        touched.add(fc)
    for fc in touched:
        fc.update()
    forget()


def clear(track, start, end):
    """The track's keys in the range go (Ctrl+Z brings them back). Returns how many."""
    count = 0
    for fc in track["curves"]:
        for p in sorted((p for p in fc.keyframe_points if _in(p, start, end)), key=lambda p: -p.co.x):
            fc.keyframe_points.remove(p, fast=True)
            count += 1
        fc.update()
    forget()
    return count


# ------------------------------------------------------------------ the properties (Animation tab)

def get_start(settings):
    track, _s, _e = active(settings.id_data)
    return track["first"] if track else 0


def set_start(settings, value):
    track, start, end = active(settings.id_data)
    if track:
        retime(track, start, end, value, track["last"] - track["first"])


def get_length(settings):
    track, _s, _e = active(settings.id_data)
    return max(1, track["last"] - track["first"]) if track else 1


def set_length(settings, value):
    track, start, end = active(settings.id_data)
    if track:
        retime(track, start, end, track["first"], value)


def get_curve(settings):
    track, start, end = active(settings.id_data)
    key = curve_of(track, start, end) if track else 'SMOOTH'
    return list(CURVES).index(key) if key in CURVES else len(CURVES)


def set_curve_index(settings, index):
    track, start, end = active(settings.id_data)
    if track and 0 <= index < len(CURVES):
        set_curve(track, start, end, list(CURVES)[index])


# ------------------------------------------------------------------ the Graph Editor

_was = {}                 # area → what it was before it became the Graph Editor (timeline …)

def _graph_area(context):
    """The Graph Editor if one is open, else the timeline / dope sheet (turned into one), else None."""
    screen = context.window.screen if context.window else context.screen
    areas = list(screen.areas) if screen else []
    for area in areas:
        if area.type == 'GRAPH_EDITOR':
            return area
    for area in areas:
        if area.type == 'DOPESHEET_EDITOR':
            return area
    return None


def show_in_graph(context, track):
    """Selects the track's objects and shows only its curves in the Graph Editor, framed."""
    area = _graph_area(context)
    if area is None:
        return False
    if track["select"]:
        for obj in context.view_layer.objects:
            if obj.select_get():
                obj.select_set(False)
        for obj in track["select"]:
            if obj.name in context.view_layer.objects:
                obj.select_set(True)
        if track["select"][-1].name in context.view_layer.objects:
            context.view_layer.objects.active = track["select"][-1]
    if area.ui_type != 'FCURVES':
        _was[area.as_pointer()] = area.ui_type
        area.ui_type = 'FCURVES'
    space = area.spaces.active
    space.dopesheet.show_only_selected = bool(track["select"])
    space.dopesheet.filter_text = track["label"] if track["id"].startswith("PR:") else ""
    for fc in track["curves"]:
        fc.select = True
        fc.hide = False
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if region is not None:
        try:
            with context.temp_override(area=area, region=region):
                bpy.ops.graph.view_all()
        except RuntimeError:
            pass
    return True


def back_from_graph(context):
    """The area turned into the Graph Editor goes back to what it was (timeline)."""
    screen = context.window.screen if context.window else context.screen
    for area in (screen.areas if screen else ()):
        was = _was.get(area.as_pointer())
        if was and area.ui_type == 'FCURVES':
            area.spaces.active.dopesheet.filter_text = ""
            area.ui_type = was
            del _was[area.as_pointer()]
            return True
    return False


def graph_open(context):
    screen = context.window.screen if context.window else context.screen
    return any(a.as_pointer() in _was and a.ui_type == 'FCURVES' for a in (screen.areas if screen else ()))

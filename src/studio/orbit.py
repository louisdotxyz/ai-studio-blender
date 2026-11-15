# SPDX-License-Identifier: GPL-3.0-or-later
"""Let the camera orbit around an object, or around the 3D cursor if none is picked.

Horizontal, vertical and distance are computed from the camera position
relative to the orbit center (center of the object's bounding box, or the 3D
cursor). When changed, the camera moves on a sphere around that center and
looks at it. Height moves camera and center up or down (world Z), Left / Right moves them
sideways (along the camera's horizontal axis) and Up / Down along the image's up direction
(the camera's Y axis: in a straight top view this is the only way to move the picture up or
down). Vertical goes up to ±90°: the roll comes from the horizontal angle, so a straight top
view keeps its direction.
"""

import math

import bpy
from mathutils import Matrix, Vector

from . import cameras

MAX_VERTICAL = math.radians(90.0)
MIN_DISTANCE = 0.001


FOCUS_RADIUS = "studio_radius"   # on the focus empty of several objects (lights.focus_selection)


def target_center(target, lift=0.0):
    """Center of the object's bounding box, raised by `lift` (the camera's Height)."""
    if lift:
        return target_center(target) + Vector((0.0, 0.0, lift))
    if target.type == 'EMPTY' and FOCUS_RADIUS in target:
        return target.matrix_world.translation.copy()
    corners = [target.matrix_world @ Vector(c) for c in target.bound_box]
    if not corners:
        return target.matrix_world.translation.copy()
    return sum(corners, Vector()) / len(corners)


def _right(horizontal):
    """The horizontal axis pointing right for a view from `horizontal` (no roll)."""
    return Vector((-math.sin(horizontal), math.cos(horizontal), 0.0))


def _up(horizontal, vertical):
    """The image's up direction for a view from (horizontal, vertical) without roll."""
    direction = Vector((math.cos(vertical) * math.cos(horizontal),
                        math.cos(vertical) * math.sin(horizontal),
                        math.sin(vertical)))
    return direction.cross(_right(horizontal))


def pitch(obj):
    """Vertical angle of the direction the object looks back along (its Z axis)."""
    z = obj.matrix_world.to_3x3().normalized().col[2]
    return math.asin(max(-1.0, min(1.0, z.z)))


def heading(obj):
    """Horizontal angle from the object's X axis (exact also when it looks straight down)."""
    x = obj.matrix_world.to_3x3().normalized().col[0]
    return math.atan2(-x.x, x.y)


def orbit_values(obj, center):
    """(horizontal, vertical, distance) of `obj` around the point `center`."""
    offset = obj.matrix_world.translation - center
    distance = offset.length
    if distance < 1e-9:
        return heading(obj), 0.0, 0.0
    flat = math.hypot(offset.x, offset.y)
    horizontal = math.atan2(offset.y, offset.x) if flat > 1e-6 * distance else heading(obj)
    return horizontal, math.atan2(offset.z, flat), distance


def orbit_matrix(center, horizontal, vertical, distance, scale):
    """Position on the sphere around `center`, looking at it without roll."""
    direction = Vector((math.cos(vertical) * math.cos(horizontal),
                        math.cos(vertical) * math.sin(horizontal),
                        math.sin(vertical)))
    z = direction
    x = _right(horizontal)
    y = z.cross(x)
    rotation = Matrix((x, y, z)).transposed().to_4x4()
    return Matrix.Translation(center + direction * distance) @ rotation @ Matrix.Diagonal(scale.to_4d())


def get_orbit(camera, target, lift=0.0):
    """(horizontal, vertical, distance) in radians and Blender units."""
    return orbit_values(camera, target_center(target, lift))


def look_at_matrix(position, center, scale):
    direction = center - position
    if direction.length < 1e-9:
        direction = Vector((0.0, 0.0, -1.0))
    rotation = direction.to_track_quat('-Z', 'Y').to_matrix().to_4x4()
    return Matrix.Translation(position) @ rotation @ Matrix.Diagonal(scale.to_4d())


def set_orbit(camera, target, horizontal=None, vertical=None, distance=None, lift=0.0):
    """Sets one or more values, the others stay as they are."""
    cur_h, cur_v, cur_d = get_orbit(camera, target, lift)
    h = cur_h if horizontal is None else horizontal
    v = cur_v if vertical is None else vertical
    d = cur_d if distance is None else distance
    v = max(-MAX_VERTICAL, min(MAX_VERTICAL, v))
    d = max(MIN_DISTANCE, d)
    put(camera, orbit_matrix(target_center(target, lift), h, v, d, camera.matrix_world.to_scale()), d)


def aim(camera, target, lift=0.0):
    """Camera stays where it is and looks at the object."""
    center = target_center(target, lift)
    put(camera, look_at_matrix(camera.matrix_world.translation.copy(), center, camera.matrix_world.to_scale()),
        (camera.matrix_world.translation - center).length)


# The pivot (for keyframes): the camera hangs on an empty in the middle of its orbit, so keys on the
# empty's turn and the camera's distance give an arc between two keys (a turntable), not a straight
# line. The empty: X turned by −vertical around Y, then by horizontal around Z; the camera sits at
# (distance, 0, 0) on it and looks back at it.
PIVOT_KEY = "studio_pivot"
_ON_PIVOT = Matrix(((0, 0, 1, 0), (1, 0, 0, 0), (0, 1, 0, 0), (0, 0, 0, 1)))


def pivot_of(camera):
    parent = camera.parent if camera is not None else None
    return parent if parent is not None and parent.get(PIVOT_KEY) else None


def _angles(matrix):
    z = matrix.to_3x3().normalized().col[2]
    vertical = math.asin(max(-1.0, min(1.0, z.z)))
    if math.hypot(z.x, z.y) > 1e-6:
        return math.atan2(z.y, z.x), vertical
    x = matrix.to_3x3().normalized().col[0]
    return math.atan2(-x.x, x.y), vertical


def put(camera, matrix, distance=None):
    """Place the camera (its world matrix, no roll); on a pivot the pivot turns and moves instead."""
    pivot = pivot_of(camera)
    if pivot is None:
        camera.matrix_world = matrix
        return
    d = max(MIN_DISTANCE, distance if distance is not None else (camera.location.x or 1.0))
    h, v = _angles(matrix)
    before = pivot.rotation_euler.z                     # no jump 359° → 1° between keys
    h = before + (h - before + math.pi) % (2 * math.pi) - math.pi
    direction = Vector((math.cos(v) * math.cos(h), math.cos(v) * math.sin(h), math.sin(v)))
    pivot.rotation_mode = 'XYZ'
    pivot.location = matrix.translation - direction * d
    pivot.rotation_euler = (0.0, -v, h)
    scale = camera.scale.copy()
    camera.matrix_parent_inverse = Matrix.Identity(4)
    camera.rotation_mode = 'XYZ'
    camera.location = (d, 0.0, 0.0)
    camera.rotation_euler = _ON_PIVOT.to_euler('XYZ')
    camera.scale = scale


def rig(camera, distance):
    """The camera's pivot (made the first time, in the camera's collection)."""
    pivot = pivot_of(camera)
    if pivot is not None:
        return pivot
    matrix = camera.matrix_world.copy()
    pivot = bpy.data.objects.new(camera.name + "_pivot", None)
    pivot[PIVOT_KEY] = True
    pivot.empty_display_type = 'SPHERE'
    pivot.empty_display_size = 0.01
    pivot.hide_render = True
    for coll in camera.users_collection or [bpy.context.scene.collection]:
        coll.objects.link(pivot)
    camera.parent = pivot
    put(camera, matrix, distance)
    return pivot


# Getters/setters for the scene properties (STUDIO_Settings) ---------------------

def _camera(settings):
    camera = settings.id_data.camera
    return camera if camera is not None and not cameras.is_locked(camera) else None


def _base(settings, camera):
    """The picked object's center, or the 3D cursor; raised by Height."""
    target = settings.orbit_target
    if target is not None and target != camera:
        center = target_center(target)
    else:
        center = settings.id_data.cursor.location.copy()
    center.z += settings.orbit_lift
    return center


def _center(settings, camera, horizontal, vertical=None):
    """The point the camera looks at: the base, shifted sideways and along the image's up."""
    center = _base(settings, camera) + _right(horizontal) * settings.orbit_side
    if settings.orbit_up:
        if vertical is None:
            stored = stored_orbit(camera)
            vertical = stored[1] if stored is not None else pitch(camera)
        center += _up(horizontal, vertical) * settings.orbit_up
    return center


# The values set here are kept on the camera with the matrix they produced: as long as the camera
# was not moved otherwise, the fields show exactly what was set (0° stays 0°, 90° stays 90° also
# after Height, Left / Right …). Recomputing them from the position drifts (rounding, the
# straight top view) and made the values jump.
ORBIT_KEY = "_studio_orbit"


def _flat(matrix):
    return [value for row in matrix for value in row]


def stored_orbit(camera):
    """(horizontal, vertical, distance) as last set, or None when the camera moved since."""
    data = camera.get(ORBIT_KEY)
    try:
        data = list(data) if data is not None else None
    except TypeError:
        return None
    if not data or len(data) != 19:
        return None
    tolerance = 1e-5 * max(1.0, abs(data[2]))
    if any(abs(a - b) > tolerance for a, b in zip(data[3:], _flat(camera.matrix_world))):
        return None
    return tuple(data[:3])


def remember(camera, values):
    camera[ORBIT_KEY] = [float(v) for v in values] + _flat(camera.matrix_world)


def camera_orbit(settings, camera=None):
    camera = camera or settings.id_data.camera
    if camera is None:
        return 0.0, 0.0, 0.0
    pivot = pivot_of(camera)
    if pivot is not None:                 # exact also while keys play
        return pivot.rotation_euler.z, -pivot.rotation_euler.y, camera.location.x
    stored = stored_orbit(camera)
    if stored is not None:
        return stored
    return orbit_values(camera, _center(settings, camera, heading(camera)))


def _place(settings, camera, horizontal, vertical, distance):
    vertical = max(-MAX_VERTICAL, min(MAX_VERTICAL, vertical))
    distance = max(MIN_DISTANCE, distance)
    put(camera, orbit_matrix(_center(settings, camera, horizontal, vertical), horizontal, vertical,
                             distance, camera.matrix_world.to_scale()), distance)
    remember(camera, (horizontal, vertical, distance))


def _getter(index):
    def get(settings):
        return camera_orbit(settings)[index]
    return get


def _setter(index):
    def set_(settings, value):
        camera = _camera(settings)
        if camera is None:
            return
        pivot = pivot_of(camera)
        if pivot is not None:             # on the pivot: only its turn or the distance changes
            if index == 0:
                pivot.rotation_euler.z = value
            elif index == 1:
                pivot.rotation_euler.y = -max(-MAX_VERTICAL, min(MAX_VERTICAL, value))
            else:
                camera.location.x = max(MIN_DISTANCE, value)
            return
        values = list(camera_orbit(settings, camera))
        values[index] = value
        _place(settings, camera, *values)
    return set_


def get_height(settings):
    return settings.orbit_lift


def set_height(settings, value):
    """Height: camera and the point it looks at move up or down together."""
    camera = _camera(settings)
    if camera is None:
        return
    values = camera_orbit(settings, camera)
    matrix = camera.matrix_world.copy()
    matrix.translation.z += value - settings.orbit_lift
    put(camera, matrix)
    settings.orbit_lift = value
    remember(camera, values)


def get_side(settings):
    return settings.orbit_side


def set_side(settings, value):
    """Left / Right: camera and the point it looks at move sideways together."""
    camera = _camera(settings)
    if camera is None:
        return
    values = camera_orbit(settings, camera)
    matrix = camera.matrix_world.copy()
    matrix.translation += _right(values[0]) * (value - settings.orbit_side)
    put(camera, matrix)
    settings.orbit_side = value
    remember(camera, values)


def get_up(settings):
    return settings.orbit_up


def set_up(settings, value):
    """Up / Down: camera and the point it looks at move along the image's up direction together
    (also in a straight top view, where Height only changes the distance)."""
    camera = _camera(settings)
    if camera is None:
        return
    values = camera_orbit(settings, camera)
    matrix = camera.matrix_world.copy()
    matrix.translation += _up(values[0], values[1]) * (value - settings.orbit_up)
    put(camera, matrix)
    settings.orbit_up = value
    remember(camera, values)


get_horizontal, set_horizontal = _getter(0), _setter(0)
get_vertical, set_vertical = _getter(1), _setter(1)
get_distance, set_distance = _getter(2), _setter(2)


def on_target(settings, _context):
    """New orbit object picked: the active camera looks at it right away
    (cleared: the camera stays, the 3D cursor is the center from now on)."""
    settings.orbit_lift = 0.0
    settings.orbit_side = 0.0
    settings.orbit_up = 0.0
    camera = _camera(settings)
    if camera is not None and settings.orbit_target is not None and settings.orbit_target != camera:
        center = _base(settings, camera)
        h, v, d = orbit_values(camera, center)
        _place(settings, camera, h, v, d)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Let the camera orbit around an object, or around the 3D cursor if none is picked.

Horizontal, vertical and distance are computed from the camera position
relative to the orbit center (center of the object's bounding box, or the 3D
cursor). When changed, the camera moves on a sphere around that center and
looks at it. Height moves camera and center up or down, Left / Right moves them
sideways (along the camera's horizontal axis). Vertical goes up to ±90°: the
roll comes from the horizontal angle, so a straight top view keeps its direction.
"""

import math

from mathutils import Matrix, Vector

from . import cameras

MAX_VERTICAL = math.radians(90.0)
MIN_DISTANCE = 0.001


FOCUS_RADIUS = "render_studio_radius"   # on the focus empty of several objects (lights.focus_selection)


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
    camera.matrix_world = orbit_matrix(target_center(target, lift), h, v, d, camera.matrix_world.to_scale())


def aim(camera, target, lift=0.0):
    """Camera stays where it is and looks at the object."""
    camera.matrix_world = look_at_matrix(camera.matrix_world.translation.copy(), target_center(target, lift),
                                         camera.matrix_world.to_scale())


# Getters/setters for the scene properties (RS_Settings) ---------------------

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


def _center(settings, camera, horizontal):
    return _base(settings, camera) + _right(horizontal) * settings.orbit_side


def camera_orbit(settings, camera=None):
    camera = camera or settings.id_data.camera
    if camera is None:
        return 0.0, 0.0, 0.0
    return orbit_values(camera, _center(settings, camera, heading(camera)))


def _place(settings, camera, horizontal, vertical, distance):
    vertical = max(-MAX_VERTICAL, min(MAX_VERTICAL, vertical))
    distance = max(MIN_DISTANCE, distance)
    camera.matrix_world = orbit_matrix(_center(settings, camera, horizontal), horizontal, vertical, distance,
                                       camera.matrix_world.to_scale())


def _getter(index):
    def get(settings):
        return camera_orbit(settings)[index]
    return get


def _setter(index):
    def set_(settings, value):
        camera = _camera(settings)
        if camera is None:
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
    matrix = camera.matrix_world.copy()
    matrix.translation.z += value - settings.orbit_lift
    camera.matrix_world = matrix
    settings.orbit_lift = value


def get_side(settings):
    return settings.orbit_side


def set_side(settings, value):
    """Left / Right: camera and the point it looks at move sideways together."""
    camera = _camera(settings)
    if camera is None:
        return
    matrix = camera.matrix_world.copy()
    matrix.translation += _right(heading(camera)) * (value - settings.orbit_side)
    camera.matrix_world = matrix
    settings.orbit_side = value


get_horizontal, set_horizontal = _getter(0), _setter(0)
get_vertical, set_vertical = _getter(1), _setter(1)
get_distance, set_distance = _getter(2), _setter(2)


def on_target(settings, _context):
    """New orbit object picked: the active camera looks at it right away
    (cleared: the camera stays, the 3D cursor is the center from now on)."""
    settings.orbit_lift = 0.0
    settings.orbit_side = 0.0
    camera = _camera(settings)
    if camera is not None and settings.orbit_target is not None and settings.orbit_target != camera:
        center = _base(settings, camera)
        h, v, d = orbit_values(camera, center)
        _place(settings, camera, h, v, d)

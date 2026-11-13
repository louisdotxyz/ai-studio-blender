# SPDX-License-Identifier: GPL-3.0-or-later
"""One list for all lights (Environment tab › Lights, light map, placing on the product).

Two kinds of light share it:
- ('LIGHT', object): a light in the scene.
- ('PIN', index): a light painted into the studio World (world_modes.py), shown while World is "Studio".

Every light has a place on the light map: `angles` gives it as (around, height) seen from the product,
relative to the camera: around 0 = from the camera, positive = from the camera's right, ±180° = from behind.
"""

import math

import bpy
from mathutils import Vector

from . import hdri, hdri_gen, lights, orbit, world_modes

MAX_PINS = 16


def pins_shown(scene):
    return scene.render_studio.world_mode == 'STUDIO'


def all_items(scene):
    """The lights of the scene, then the lights of the studio World."""
    rs = scene.render_studio
    found = [('LIGHT', obj) for obj in lights.scene_lights(scene)]
    if pins_shown(scene):
        found += [('PIN', i) for i in range(len(rs.hdri_pins))]
    return found


def active_item(scene):
    rs = scene.render_studio
    if rs.active_light is not None and rs.active_light.name in scene.objects:
        return ('LIGHT', rs.active_light)
    if pins_shown(scene) and 0 <= rs.hdri_pin_index < len(rs.hdri_pins):
        return ('PIN', rs.hdri_pin_index)
    return None


def select(scene, item):
    """Makes this light the open one (None: none)."""
    rs = scene.render_studio
    if item is None:
        rs.active_light = None
        rs.hdri_pin_index = -1
    elif item[0] == 'PIN':
        rs.active_light = None
        rs.hdri_pin_index = item[1]
    else:
        rs.hdri_pin_index = -1
        rs.active_light = item[1]


def pin_of(scene, item):
    pins = scene.render_studio.hdri_pins
    return pins[item[1]] if item is not None and item[0] == 'PIN' and 0 <= item[1] < len(pins) else None


def name_of(scene, item):
    if item[0] == 'PIN':
        pin = pin_of(scene, item)
        return pin.name if pin is not None else ""
    return item[1].name


def is_on(scene, item):
    if item[0] == 'PIN':
        pin = pin_of(scene, item)
        return bool(pin is not None and pin.on)
    return not item[1].hide_render


# ------------------------------------------------------------------ where a light is

def center_of(scene, light=None):
    """The point the lights are arranged around (the light's own target, otherwise the setup's)."""
    rs = scene.render_studio
    target = lights.light_target(light, rs) if light is not None else rs.light_orbit_target
    if target is not None and target == light:
        target = None
    if target is not None and light is not None:
        return orbit.target_center(target, light.render_studio_light.lift)
    return lights.subject(scene, target)[0]


def _wrap(angle):
    return (angle + math.pi) % (2 * math.pi) - math.pi


def direction_of(scene, item):
    """Unit vector from the product towards the light."""
    if item[0] == 'PIN':
        return world_modes.pin_direction(scene, pin_of(scene, item))
    obj = item[1]
    if obj.data.type == 'SUN':
        return (obj.matrix_world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    offset = obj.matrix_world.translation - center_of(scene, obj)
    return offset.normalized() if offset.length > 1e-9 else Vector((0.0, -1.0, 0.0))


def angles(scene, item):
    """(around, height) of a light on the light map, in radians."""
    d = direction_of(scene, item)
    front = lights.front_angle(scene, center_of(scene, item[1] if item[0] == 'LIGHT' else None))
    return _wrap(math.atan2(d.y, d.x) - front), math.asin(max(-1.0, min(1.0, d.z)))


def vector_from(scene, around, height, light=None):
    front = lights.front_angle(scene, center_of(scene, light))
    a = front + around
    return Vector((math.cos(height) * math.cos(a), math.cos(height) * math.sin(a), math.sin(height)))


def set_direction(scene, item, direction):
    """Moves the light so it shines from `direction` (kept: its distance, size and what it looks at)."""
    d = direction.normalized()
    if item[0] == 'PIN':
        world_modes.set_pin_direction(scene, pin_of(scene, item), d)
        return
    obj = item[1]
    if obj.data.type == 'SUN':
        obj.rotation_euler = d.to_track_quat('Z', 'Y').to_euler()
        return
    center = center_of(scene, obj)
    distance = max((obj.matrix_world.translation - center).length, 0.05)
    obj.matrix_world = orbit.look_at_matrix(center + d * distance, center, obj.matrix_world.to_scale())


def set_angles(scene, item, around, height):
    height = max(math.radians(-89.0), min(math.radians(89.0), height))
    set_direction(scene, item, vector_from(scene, around, height, item[1] if item[0] == 'LIGHT' else None))


def angular_size(scene, item):
    """(width, height) the light covers seen from the product, in radians (for the light map)."""
    if item[0] == 'PIN':
        pin = pin_of(scene, item)
        return pin.width, pin.height
    obj, data = item[1], item[1].data
    if data.type == 'SUN':
        return max(data.angle, math.radians(4.0)), max(data.angle, math.radians(4.0))
    distance = max((obj.matrix_world.translation - center_of(scene, obj)).length, 0.05)
    if data.type == 'AREA':
        w = data.size
        h = data.size_y if data.shape in {'RECTANGLE', 'ELLIPSE'} else data.size
    else:
        w = h = max(data.shadow_soft_size * 2.0, 0.02)
    return 2.0 * math.atan(w / 2.0 / distance), 2.0 * math.atan(h / 2.0 / distance)


def is_round(scene, item):
    if item[0] == 'PIN':
        return pin_of(scene, item).shape == 'ROUND'
    data = item[1].data
    return data.type != 'AREA' or data.shape in {'DISK', 'ELLIPSE'}


def color_of(scene, item):
    """Display color (sRGB) of the light."""
    if item[0] == 'PIN':
        return tuple(pin_of(scene, item).color)
    data = item[1].data
    if getattr(data, "use_temperature", False):
        return hdri_gen.kelvin_to_rgb(data.temperature)
    return tuple(lights.linear_to_srgb(c) for c in data.color)


# ------------------------------------------------------------------ the same few values for every light

def scale_size(scene, item, factor):
    if item[0] == 'PIN':
        pin = pin_of(scene, item)
        pin.width, pin.height = pin.width * factor, pin.height * factor
        return
    data = item[1].data
    if data.type == 'AREA':
        data.size *= factor
        data.size_y *= factor
    elif data.type == 'SPOT':
        data.spot_size = max(math.radians(1.0), min(math.pi, data.spot_size * factor))
    elif data.type == 'SUN':
        data.angle = max(math.radians(0.1), min(math.pi, data.angle * factor))
    else:
        data.shadow_soft_size = max(0.001, data.shadow_soft_size * factor)


def scale_power(scene, item, factor):
    if item[0] == 'PIN':
        pin_of(scene, item).strength *= factor
    else:
        item[1].data.energy *= factor


def scale_distance(scene, item, factor):
    """Nearer or farther; the brightness on the product stays the same."""
    if item[0] != 'LIGHT' or item[1].data.type == 'SUN':
        return
    obj = item[1]
    center = center_of(scene, obj)
    offset = obj.matrix_world.translation - center
    old = max(offset.length, 0.02)
    new = max(0.02, old * factor)
    obj.matrix_world = orbit.look_at_matrix(center + offset.normalized() * new, center, obj.matrix_world.to_scale())
    obj.data.energy *= (new / old) ** 2


def get_light_size(settings):
    data = settings.id_data.data
    return data.size if data.type == 'AREA' else data.shadow_soft_size * 2.0


def set_light_size(settings, value):
    data = settings.id_data.data
    value = max(0.001, value)
    if data.type == 'AREA':
        factor = value / max(data.size, 1e-6)
        data.size = value
        data.size_y = max(0.001, data.size_y * factor)
    else:
        data.shadow_soft_size = value / 2.0


def get_light_softness(settings):
    data = settings.id_data.data
    if data.type == 'AREA':
        return max(0.0, min(1.0, data.spread / math.pi))
    if data.type == 'SPOT':
        return data.spot_blend
    return 0.0


def set_light_softness(settings, value):
    data = settings.id_data.data
    value = max(0.0, min(1.0, value))
    if data.type == 'AREA':
        data.spread = max(math.radians(1.0), value * math.pi)
    elif data.type == 'SPOT':
        data.spot_blend = value


def get_light_kelvin(settings):
    data = settings.id_data.data
    if getattr(data, "use_temperature", False):
        return data.temperature
    return float(settings.get("kelvin", 6500.0))


def set_light_kelvin(settings, value):
    data = settings.id_data.data
    settings["kelvin"] = value
    if hasattr(data, "use_temperature"):
        data.use_temperature = True
        data.temperature = value
    else:
        data.color = [lights.srgb_to_linear(c) for c in hdri_gen.kelvin_to_rgb(value)]


def get_pin_size(pin):
    return pin.width


def set_pin_size(pin, value):
    factor = value / max(pin.width, 1e-6)
    pin["height"] = max(0.02, min(math.pi, pin.height * factor))
    pin.width = value          # update= renders the studio again


def get_pin_kelvin(pin):
    return float(pin.get("kelvin", 6500.0))


def set_pin_kelvin(pin, value):
    pin["kelvin"] = value
    pin.color = hdri_gen.kelvin_to_rgb(value)


# ------------------------------------------------------------------ new, copy, mirror, remove

def add_pin(scene, name=""):
    rs = scene.render_studio
    if rs.world_mode != 'STUDIO':
        rs.world_mode = 'STUDIO'        # fills in the three default lights when there are none
    if len(rs.hdri_pins) >= MAX_PINS:
        raise ValueError("At most %d lights in the studio World" % MAX_PINS)
    pin = rs.hdri_pins.add()
    pin["name"] = name or "Light %d" % len(rs.hdri_pins)
    index = len(rs.hdri_pins) - 1
    select(scene, ('PIN', index))
    world_modes.on_change(scene)
    return index


def add_like(scene, item):
    """A new light of the same kind as `item` (a scene light when there is none). Returns the new item."""
    if item is not None and item[0] == 'PIN':
        return ('PIN', add_pin(scene))
    obj = lights.add_light(scene, scene.render_studio.light_orbit_target)
    select(scene, ('LIGHT', obj))
    return ('LIGHT', obj)


def duplicate(scene, item):
    rs = scene.render_studio
    if item[0] == 'PIN':
        if len(rs.hdri_pins) >= MAX_PINS:
            raise ValueError("At most %d lights in the studio World" % MAX_PINS)
        new = rs.hdri_pins.add()
        src = pin_of(scene, item)      # adding can move the collection in memory
        new["on"] = src.on
        new["shape"] = 1 if src.shape == 'ROUND' else 0
        new["color"] = tuple(src.color)
        for key in ("horizontal", "vertical", "width", "height", "strength", "softness"):
            new[key] = getattr(src, key)
        if "kelvin" in src:
            new["kelvin"] = src["kelvin"]
        new["name"] = src.name + " 2"
        result = ('PIN', len(rs.hdri_pins) - 1)
    else:
        result = ('LIGHT', lights.duplicate_light(scene, item[1]))
    select(scene, result)
    return result


def mirror(scene, item, copy=False):
    """Flips the light to the other side of the product (left ↔ right as the camera sees it).
    copy=True leaves the light and puts a copy on the other side."""
    if copy:
        item = duplicate(scene, item)
    around, height = angles(scene, item)
    set_angles(scene, item, -around, height)
    if item[0] == 'PIN':
        world_modes.on_change(scene)
    return item


def remove(scene, item):
    rs = scene.render_studio
    if item[0] == 'PIN':
        if 0 <= item[1] < len(rs.hdri_pins):
            rs.hdri_pins.remove(item[1])
            rs.hdri_pin_index = -1
            world_modes.on_change(scene)
        return
    obj = item[1]
    from . import environment
    environment.remove_flag(obj)
    if rs.active_light == obj:
        rs.active_light = None
    data = obj.data
    bpy.data.objects.remove(obj)
    if data is not None and data.users == 0:
        bpy.data.lights.remove(data)


def world_image(scene):
    """The image that lights the scene (None without an HDRI)."""
    world = scene.world
    if world is None or world.node_tree is None:
        return None
    node = world.node_tree.nodes.get(hdri.NODE_ENV)
    if node is not None and node.image is not None:
        return node.image
    return next((n.image for n in world.node_tree.nodes if n.type == 'TEX_ENVIRONMENT' and n.image is not None), None)

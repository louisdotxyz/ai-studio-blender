# SPDX-License-Identifier: GPL-3.0-or-later
"""Lights: the light bar, new lights, orbit and light setups.

A light setup is a plain dict (JSON) that describes lights relative to the
subject (the light orbit object or the 3D cursor) and the active camera, so it
works for any object size and camera position:

    {"name": "Warm Product",
     "lights": [{"name": "Key", "type": "AREA",
                 "horizontal": -45, "vertical": 35, "distance": 3.0,
                 "size": 1.2, "power": 1.0, "color": [1.0, 0.9, 0.8]}]}

- horizontal: degrees around the subject. 0 = camera side, negative = left,
  positive = right, 180 = behind.
- vertical: degrees above (+) or below (−) the subject.
- distance, size: in subject radii (half the bounding box diagonal).
- power: 1.0 = a standard key light. The watts are scaled with distance², so a
  setup looks the same for small and large objects.
- color: sRGB 0..1, or "kelvin" for a color temperature instead.

Built-in presets, saved presets, the "+" button and the Claude integration
(see api.py) all use this format.
"""

import json
import math
import os

import bpy
from mathutils import Matrix, Vector

from . import cameras, orbit

COLLECTION_NAME = "RS_Lights"
PRESET_KEY = "render_studio_preset"
TYPES = ('POINT', 'SUN', 'SPOT', 'AREA')
SHAPES = ('SQUARE', 'RECTANGLE', 'DISK', 'ELLIPSE')

# Watts per square meter of distance for power 1.0 (irradiance falls off with distance²).
KEY_POWER = 100.0
# A sun has no distance; strength 1.0 = this many W/m².
SUN_STRENGTH = 4.0

BUILTIN_PRESETS = {
    "3-Point": {"name": "3-Point", "lights": [
        {"name": "Key", "horizontal": -45, "vertical": 35, "distance": 3.0, "size": 1.2, "power": 1.0},
        {"name": "Fill", "horizontal": 50, "vertical": 15, "distance": 3.5, "size": 2.0, "power": 0.35},
        {"name": "Rim", "horizontal": 160, "vertical": 40, "distance": 3.0, "size": 0.8, "power": 0.8},
    ]},
    "High Key": {"name": "High Key", "lights": [
        {"name": "Soft Front", "horizontal": 0, "vertical": 20, "distance": 3.0, "size": 2.5, "power": 0.5},
        {"name": "Soft Left", "horizontal": -90, "vertical": 20, "distance": 3.0, "size": 2.5, "power": 0.5},
        {"name": "Soft Back", "horizontal": 180, "vertical": 20, "distance": 3.0, "size": 2.5, "power": 0.5},
        {"name": "Soft Right", "horizontal": 90, "vertical": 20, "distance": 3.0, "size": 2.5, "power": 0.5},
        {"name": "Soft Top", "horizontal": 0, "vertical": 89, "distance": 3.0, "size": 3.0, "power": 0.6},
    ]},
}


# ------------------------------------------------------------------ Basics

def scene_lights(scene):
    return sorted((o for o in scene.objects if o.type == 'LIGHT'), key=lambda o: o.name.lower())


def light_collection(scene):
    return cameras.named_collection(scene, COLLECTION_NAME)


def subject(scene, target):
    """(center, radius) of the orbit object, otherwise of the 3D cursor with radius 0.5."""
    if target is not None and target.type == 'EMPTY' and orbit.FOCUS_RADIUS in target:
        return target.matrix_world.translation.copy(), max(float(target[orbit.FOCUS_RADIUS]), 0.05)
    if target is not None:
        center = orbit.target_center(target)
        corners = [target.matrix_world @ Vector(c) for c in target.bound_box]
        radius = max((c - center).length for c in corners) if corners else 0.5
        return center, max(radius, 0.05)
    return scene.cursor.location.copy(), 0.5


def front_angle(scene, center):
    """Horizontal angle of the active camera seen from the center (front side)."""
    cam = scene.camera
    if cam is None:
        return -math.pi / 2   # default front view looks from -Y
    offset = cam.matrix_world.translation - center
    if offset.xy.length < 1e-6:
        return -math.pi / 2
    return math.atan2(offset.y, offset.x)


def place(obj, center, horizontal, vertical, distance):
    position = center + Vector((
        distance * math.cos(vertical) * math.cos(horizontal),
        distance * math.cos(vertical) * math.sin(horizontal),
        distance * math.sin(vertical),
    ))
    obj.matrix_world = orbit.look_at_matrix(position, center, Vector((1.0, 1.0, 1.0)))


def srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def linear_to_srgb(c):
    return c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055


def add_light(scene, target):
    """New area light as a key light: front left, a bit above, aimed at the subject."""
    return apply_setup(scene, target, {"lights": [
        {"name": "Light", "horizontal": -45, "vertical": 35, "distance": 3.0, "size": 1.2, "power": 1.0}
    ]}, replace=False, tag=False)[0]


def duplicate_light(scene, src):
    new = src.copy()
    new.data = src.data.copy()
    collections = [c for c in src.users_collection
                   if c in scene.collection.children_recursive or c == scene.collection]
    for coll in collections or [light_collection(scene)]:
        coll.objects.link(new)
    return new


def remove_setup_lights(scene):
    for obj in [o for o in scene.objects if o.type == 'LIGHT' and PRESET_KEY in o]:
        data = obj.data
        bpy.data.objects.remove(obj)
        if data.users == 0:
            bpy.data.lights.remove(data)


# ------------------------------------------------------------ Light setups

def _number(spec, key, default, lo, hi):
    value = spec.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("%r must be a number" % key)
    return max(lo, min(hi, float(value)))


def _color(spec, key="color"):
    value = spec.get(key)
    if value is None:
        return None
    if not (isinstance(value, (list, tuple)) and len(value) == 3
            and all(isinstance(c, (int, float)) for c in value)):
        raise ValueError("%r must be three numbers between 0 and 1" % key)
    return tuple(max(0.0, min(1.0, float(c))) for c in value)


def parse_setup(spec):
    """Checks a setup (dict or JSON text) and fills in defaults. Raises ValueError."""
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except ValueError as ex:
            raise ValueError("Not valid JSON: %s" % ex)
    if not isinstance(spec, dict) or not isinstance(spec.get("lights"), list) or not spec["lights"]:
        raise ValueError("A light setup needs a non-empty \"lights\" list")
    if len(spec["lights"]) > 16:
        raise ValueError("At most 16 lights per setup")
    lights = []
    for i, item in enumerate(spec["lights"]):
        if not isinstance(item, dict):
            raise ValueError("Light %d must be an object" % (i + 1))
        kind = str(item.get("type", "AREA")).upper()
        if kind not in TYPES:
            raise ValueError("Light %d: type must be one of %s" % (i + 1, ", ".join(TYPES)))
        shape = str(item.get("shape", "RECTANGLE")).upper()
        if shape not in SHAPES:
            raise ValueError("Light %d: shape must be one of %s" % (i + 1, ", ".join(SHAPES)))
        light = {
            "name": str(item.get("name") or "Light %d" % (i + 1))[:60],
            "type": kind,
            "shape": shape,
            "horizontal": _number(item, "horizontal", 0.0, -360.0, 360.0),
            "vertical": _number(item, "vertical", 30.0, -89.5, 89.5),
            "distance": _number(item, "distance", 3.0, 0.5, 50.0),
            "size": _number(item, "size", 1.0, 0.01, 20.0),
            "power": _number(item, "power", 1.0, 0.0, 100.0),
            "spot_angle": _number(item, "spot_angle", 45.0, 1.0, 180.0),
            "spot_blend": _number(item, "spot_blend", 0.15, 0.0, 1.0),
            "sun_angle": _number(item, "sun_angle", 2.0, 0.0, 90.0),
            "color": _color(item),
        }
        if "kelvin" in item:
            light["kelvin"] = _number(item, "kelvin", 6500.0, 800.0, 20000.0)
        lights.append(light)
    return {"name": str(spec.get("name") or "Setup")[:60], "lights": lights}


def apply_setup(scene, target, spec, replace=True, tag=True):
    """Creates the lights of a setup around the subject. With replace=True the
    lights of the previous setup go away first; your own lights stay."""
    spec = parse_setup(spec)
    if replace:
        remove_setup_lights(scene)
    center, radius = subject(scene, target)
    front = front_angle(scene, center)
    created = []
    for item in spec["lights"]:
        data = bpy.data.lights.new(item["name"], item["type"])
        distance = item["distance"] * radius
        if item["type"] == 'SUN':
            data.energy = item["power"] * SUN_STRENGTH
            data.angle = math.radians(item["sun_angle"])
        else:
            data.energy = item["power"] * KEY_POWER * distance ** 2
        if item["type"] == 'AREA':
            data.shape = item["shape"]
            data.size = item["size"] * radius
            data.size_y = item["size"] * radius * 0.75
        elif item["type"] in {'POINT', 'SPOT'}:
            data.shadow_soft_size = item["size"] * radius * 0.5
        if item["type"] == 'SPOT':
            data.spot_size = math.radians(item["spot_angle"])
            data.spot_blend = item["spot_blend"]
        if item["color"] is not None:
            data.color = tuple(srgb_to_linear(c) for c in item["color"])
        if "kelvin" in item:
            data.use_temperature = True
            data.temperature = item["kelvin"]
        obj = bpy.data.objects.new(item["name"], data)
        light_collection(scene).objects.link(obj)
        if tag:
            obj[PRESET_KEY] = spec["name"]
        place(obj, center, front + math.radians(item["horizontal"]), math.radians(item["vertical"]), distance)
        created.append(obj)
    remember_focus(scene, center, radius)
    return created


def setup_from_scene(scene, target, name):
    """Describes all lights of the scene as a setup (for the "+" button)."""
    center, radius = subject(scene, target)
    front = front_angle(scene, center)
    lights = []
    for obj in scene_lights(scene):
        data = obj.data
        h, v, d = _orbit_from(obj, center)
        distance = max(d / radius, 0.5)
        item = {
            "name": obj.name,
            "type": data.type,
            "horizontal": round((math.degrees(h - front) + 180.0) % 360.0 - 180.0, 2),
            "vertical": round(math.degrees(v), 2),
            "distance": round(distance, 3),
            "color": [round(linear_to_srgb(c), 4) for c in data.color],
        }
        if data.type == 'SUN':
            item["power"] = round(data.energy / SUN_STRENGTH, 4)
            item["sun_angle"] = round(math.degrees(data.angle), 3)
        else:
            item["power"] = round(data.energy / (KEY_POWER * (distance * radius) ** 2), 4)
        if data.type == 'AREA':
            item["shape"] = data.shape
            item["size"] = round(data.size / radius, 4)
        elif data.type in {'POINT', 'SPOT'}:
            item["size"] = round(data.shadow_soft_size * 2 / radius, 4)
        if data.type == 'SPOT':
            item["spot_angle"] = round(math.degrees(data.spot_size), 3)
            item["spot_blend"] = round(data.spot_blend, 4)
        if getattr(data, "use_temperature", False):
            item["kelvin"] = round(data.temperature, 1)
        lights.append(item)
    if not lights:
        raise ValueError("There are no lights in the scene")
    spec = {"name": name, "lights": lights}
    parse_setup(spec)   # same checks as for setups from Claude
    return spec


def _orbit_from(obj, center):
    offset = obj.matrix_world.translation - center
    d = offset.length
    if d < 1e-9:
        return 0.0, 0.0, 0.0
    return math.atan2(offset.y, offset.x), math.atan2(offset.z, offset.xy.length), d


# ---------------------------------------------------------- Saved presets

def _presets_file():
    from .paths import data_dir
    base = data_dir()
    return os.path.join(base, "light_presets.json")


def saved_presets():
    try:
        with open(_presets_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_preset(spec):
    spec = parse_setup(spec)
    presets = saved_presets()
    presets[spec["name"]] = spec
    with open(_presets_file(), "w", encoding="utf-8") as f:
        json.dump(presets, f, indent=1)
    return spec["name"]


def delete_preset(name):
    presets = saved_presets()
    if presets.pop(name, None) is not None:
        with open(_presets_file(), "w", encoding="utf-8") as f:
            json.dump(presets, f, indent=1)


def preset(name):
    return BUILTIN_PRESETS.get(name) or saved_presets().get(name)


# Getters/setters for the light orbit (RS_Settings) ---------------------------

def light_target(light, settings):
    """The light's own target (Light tab, per light), otherwise the setup's target."""
    own = light.render_studio_light.target if light is not None else None
    return own if own is not None else settings.light_orbit_target


def _pair(settings):
    light = settings.active_light
    target = light_target(light, settings) if light is not None else None
    if light is None or target is None or light == target:
        return None
    return light, target, light.render_studio_light.lift


def _getter(index):
    def get(settings):
        pair = _pair(settings)
        return orbit.get_orbit(*pair)[index] if pair else 0.0
    return get


def _setter(key):
    def set_(settings, value):
        pair = _pair(settings)
        if pair:
            orbit.set_orbit(*pair[:2], lift=pair[2], **{key: value})
    return set_


# Height of a light: the light and the point it looks at move up or down together
def get_light_height(light_settings):
    return light_settings.lift


def set_light_height(light_settings, value):
    light = light_settings.id_data
    delta = value - light_settings.lift
    matrix = light.matrix_world.copy()
    matrix.translation.z += delta
    light.matrix_world = matrix
    light_settings.lift = value


def aim(light, settings):
    """Point the light at its target (center + height), keeping its position."""
    target = light_target(light, settings)
    if target is None or target == light:
        return
    center = orbit.target_center(target, light.render_studio_light.lift)
    scale = light.matrix_world.to_scale()
    light.matrix_world = orbit.look_at_matrix(light.matrix_world.translation.copy(), center, scale)


def on_light_target(light_settings, context):
    aim(light_settings.id_data, context.scene.render_studio)


# The light selected in the viewport is the one shown open in the Light tab
_owner = object()


def _active_changed():
    try:
        obj = bpy.context.view_layer.objects.active
        scene = bpy.context.scene
    except AttributeError:
        return
    if obj is not None and obj.type == 'LIGHT' and scene.render_studio.active_light != obj:
        scene.render_studio.active_light = obj


def subscribe():
    bpy.msgbus.clear_by_owner(_owner)
    bpy.msgbus.subscribe_rna(key=(bpy.types.LayerObjects, "active"), owner=_owner, args=(),
                             notify=_active_changed, options={'PERSISTENT'})


@bpy.app.handlers.persistent
def _on_load_post(*_args):
    subscribe()


get_horizontal, set_horizontal = _getter(0), _setter("horizontal")
get_vertical, set_vertical = _getter(1), _setter("vertical")
get_distance, set_distance = _getter(2), _setter("distance")


FOCUS_KEY = "light_focus"            # [x, y, z, radius] the lights are arranged around
FOCUS_NAME = "Render Studio Focus"


def remember_focus(scene, center, radius):
    scene.render_studio[FOCUS_KEY] = [center.x, center.y, center.z, radius]


def setup_lights(scene):
    """The lights of the current setup (from a preset), otherwise all lights."""
    tagged = [o for o in scene_lights(scene) if PRESET_KEY in o]
    return tagged or scene_lights(scene)


def retarget(scene, target):
    """Moves the light setup to a new subject: same arrangement, scaled with its size."""
    new_center, new_radius = subject(scene, target)
    old = scene.render_studio.get(FOCUS_KEY)
    if old is not None and len(old) == 4 and old[3] > 0:
        old_center, old_radius = Vector(old[:3]), float(old[3])
        k = new_radius / old_radius
        for obj in setup_lights(scene):
            data = obj.data
            position = new_center + (obj.matrix_world.translation - old_center) * k
            if data.type == 'SUN':
                matrix = obj.matrix_world.copy()
                matrix.translation = position
                obj.matrix_world = matrix
            else:
                obj.matrix_world = orbit.look_at_matrix(position, new_center, obj.matrix_world.to_scale())
            if data.type == 'AREA':
                data.size *= k
                data.size_y *= k
            elif data.type in {'POINT', 'SPOT'}:
                data.shadow_soft_size *= k
            if data.type != 'SUN':
                data.energy *= k * k   # same brightness on the subject at the scaled distance
    remember_focus(scene, new_center, new_radius)


def focus_selection(scene, objects):
    """Subject for several objects: an empty at the center of their combined bounding box,
    its radius stored on it. One object is used directly. Returns the subject or None."""
    objects = [o for o in objects if o.type not in {'LIGHT', 'CAMERA'} and o.name != FOCUS_NAME]
    if not objects:
        return None
    if len(objects) == 1:
        return objects[0]
    corners = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    empty = bpy.data.objects.get(FOCUS_NAME)
    if empty is None or empty.type != 'EMPTY':
        empty = bpy.data.objects.new(FOCUS_NAME, None)
        empty.empty_display_type = 'SPHERE'
        empty.hide_render = True
    if empty.name not in scene.objects:
        light_collection(scene).objects.link(empty)
    empty.parent = None
    empty.matrix_world = Matrix.Translation((lo + hi) / 2)   # valid right away, not only after an update
    radius = max((hi - lo).length / 2, 0.05)
    empty[orbit.FOCUS_RADIUS] = radius
    empty.empty_display_size = radius
    return empty


def on_target(settings, _context):
    """New subject picked: the light setup moves to it (and the open light points at it)."""
    retarget(settings.id_data, settings.light_orbit_target)


def register():
    subscribe()
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    bpy.msgbus.clear_by_owner(_owner)
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)

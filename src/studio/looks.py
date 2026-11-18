# SPDX-License-Identifier: GPL-3.0-or-later
"""Looks: several lighting moods in one blend file, switched with one click (Environment tab, top row).

A look holds the exact state of every light (place, brightness, size, color, on / off, gobo), the World
(image, studio lights or sky), background, floor, haze and the image look. The look that is open follows
what you change; switching stores it and brings the other one back. A light added in one look exists in
the others too, switched off there.

With light sets (lightsets.py) the looks are per camera ("variants" A / B …) and hold only its lights.
"""

import json

import bpy
from mathutils import Matrix

from . import environment, light_items, lights

LIGHT_VALUES = ("energy", "color", "use_temperature", "temperature", "shape", "size", "size_y", "spread",
                "spot_size", "spot_blend", "shadow_soft_size", "angle")
PIN_VALUES = ("horizontal", "vertical", "width", "height", "strength", "softness")
NAMES = "ABCDEFGH"


def _light_state(obj):
    data = obj.data
    state = {"type": data.type, "on": not obj.hide_render,
             "matrix": [list(row) for row in obj.matrix_world], "lift": obj.studio_light.lift}
    for key in LIGHT_VALUES:
        if hasattr(data, key):
            value = getattr(data, key)
            state[key] = value if isinstance(value, (bool, int, float, str)) else list(value)
    flag = environment.flag_of(obj)
    if flag is not None:
        state["gobo"] = flag.get("as_pattern", 'FLAG')
        state["gobo_values"] = environment.gobo_values(flag)
    return state


def _restore_light(scene, obj, state):
    if obj.data.type != state.get("type", obj.data.type):
        obj.data.type = state["type"]
    data = obj.data
    if "use_temperature" in state and hasattr(data, "use_temperature"):
        data.use_temperature = state["use_temperature"]
    for key in LIGHT_VALUES:
        if key in state and key != "use_temperature" and hasattr(data, key):
            try:
                setattr(data, key, state[key])
            except (TypeError, ValueError, AttributeError):
                pass
    obj.matrix_world = Matrix(state["matrix"])
    obj.studio_light.lift = state.get("lift", 0.0)
    obj.hide_render = obj.hide_viewport = not state.get("on", True)
    flag = environment.flag_of(obj)
    have = flag.get("as_pattern") if flag is not None else None
    if state.get("gobo") != have:
        if state.get("gobo"):
            flag = environment.add_flag(scene, obj, state["gobo"])
        else:
            environment.remove_flag(obj)
            flag = None
    if flag is not None and state.get("gobo_values"):
        environment.set_gobo_values(flag, state["gobo_values"])


def _pin_state(pin):
    state = {key: getattr(pin, key) for key in PIN_VALUES}
    state.update(name=pin.name, on=pin.on, shape=pin.shape, color=list(pin.color))
    if "kelvin" in pin:
        state["kelvin"] = pin["kelvin"]
    return state


def capture(scene):
    """Everything a look holds, as plain data."""
    rs = scene.studio
    data = environment.capture(scene, "")
    data.pop("name", None)
    data["lights"] = {obj.name: _light_state(obj) for obj in lights.scene_lights(scene)}
    data["world"].pop("pins", None)
    data["world"]["rotation"] = rs.world_rotation
    data["world"]["strength"] = rs.world_strength
    data["pins"] = [_pin_state(pin) for pin in rs.hdri_pins]
    return data


def apply(scene, data):
    rs = scene.studio
    solo = environment.solo_light(scene)
    if solo and bpy.data.objects.get(solo) is not None:
        environment.toggle_solo(scene, bpy.data.objects[solo])
    saved = data.get("lights") or {}
    for obj in lights.scene_lights(scene):
        state = saved.get(obj.name)
        if state is None:
            obj.hide_render = obj.hide_viewport = True      # made in another look
        else:
            _restore_light(scene, obj, state)
    if "pins" in data:
        rs.hdri_pins.clear()
        for state in data["pins"]:
            pin = rs.hdri_pins.add()
            for key in PIN_VALUES:
                pin[key] = state.get(key, getattr(pin, key))
            pin["name"] = state.get("name", "Light")
            pin["on"] = bool(state.get("on", True))
            pin["shape"] = 1 if state.get("shape") == 'ROUND' else 0
            pin["color"] = state.get("color", [1.0, 1.0, 1.0])[:3]
            if "kelvin" in state:
                pin["kelvin"] = state["kelvin"]
        rs.hdri_pin_index = min(rs.hdri_pin_index, len(rs.hdri_pins) - 1)
    preset = dict(data)
    preset["lights"] = None
    notes = environment.apply_preset(scene, preset)
    world = data.get("world") or {}
    if world.get("mode") in ('STUDIO', 'IMAGE') and "rotation" in world:
        rs.world_rotation = world["rotation"]
    if rs.active_light is not None and rs.active_light.hide_render:
        rs.active_light = None
    return notes


# ------------------------------------------------------------------ the looks of a scene
# With light sets (lightsets.py) every camera has its own variants A / B …: they hold only the lights of
# its set (the World is the same for all cameras) and live on the set. Before that: the looks of the
# scene (Scene.studio.light_looks), with World, background and floor.

def _set(scene):
    from . import lightsets
    if not lightsets.enabled(scene) or scene.camera is None:
        return None
    return lightsets.set_of(scene, scene.camera)


def _set_looks(coll):
    from . import lightsets
    try:
        found = json.loads(coll.get(lightsets.LOOKS_KEY) or "[]")
        return found if isinstance(found, list) else []
    except ValueError:
        return []


def _save_set_looks(coll, found, index):
    from . import lightsets
    coll[lightsets.LOOKS_KEY] = json.dumps(found)
    coll[lightsets.LOOK_INDEX_KEY] = index


def _set_index(coll, found):
    from . import lightsets
    index = int(coll.get(lightsets.LOOK_INDEX_KEY, -1))
    return index if 0 <= index < len(found) else -1


def capture_set(coll):
    from . import lightsets
    return {"lights": {obj.name: _light_state(obj) for obj in lightsets.set_lights(coll)}}


def apply_set(scene, coll, data):
    from . import lightsets
    saved = data.get("lights") or {}
    for obj in lightsets.set_lights(coll):
        state = saved.get(obj.name)
        if state is None:
            obj.hide_render = obj.hide_viewport = True      # made in another variant
        else:
            _restore_light(scene, obj, state)
    rs = scene.studio
    if rs.active_light is not None and rs.active_light.hide_render:
        rs.active_light = None
    return []


def names(scene):
    coll = _set(scene)
    if coll is not None:
        return [look.get("name", "?") for look in _set_looks(coll)]
    from . import lightsets
    if lightsets.enabled(scene):
        return []
    return [look.name for look in scene.studio.light_looks]


def active(scene):
    coll = _set(scene)
    if coll is not None:
        return _set_index(coll, _set_looks(coll))
    from . import lightsets
    if lightsets.enabled(scene):
        return -1
    rs = scene.studio
    return rs.light_look_index if 0 <= rs.light_look_index < len(rs.light_looks) else -1


def store(scene):
    """Writes the current state into the open look."""
    coll = _set(scene)
    if coll is not None:
        found = _set_looks(coll)
        index = _set_index(coll, found)
        if index >= 0:
            found[index]["data"] = capture_set(coll)
            _save_set_looks(coll, found, index)
        return
    index = active(scene)
    if index >= 0:
        scene.studio.light_looks[index].data = json.dumps(capture(scene))


def _next_name(scene):
    used = set(names(scene))
    return next((n for n in NAMES if n not in used), "Look %d" % (len(used) + 1))


def add(scene):
    """A new look as a copy of what is there now; it becomes the open one. The first call also keeps the
    state so far as look A."""
    if _set(scene) is None and scene.camera is not None:
        from . import lightsets
        if lightsets.enabled(scene):
            lightsets.active_collection(scene)          # the camera gets its set first
    coll = _set(scene)
    if coll is not None:
        found = _set_looks(coll)
        data = capture_set(coll)
        if not found:
            found.append({"name": _next_name(scene), "data": data})
        else:
            index = _set_index(coll, found)
            if index >= 0:
                found[index]["data"] = data
        used = {look.get("name") for look in found}
        name = next((n for n in NAMES if n not in used), "Look %d" % (len(used) + 1))
        found.append({"name": name, "data": data})
        _save_set_looks(coll, found, len(found) - 1)
        return name
    rs = scene.studio
    data = json.dumps(capture(scene))
    if not len(rs.light_looks):
        name = _next_name(scene)        # before adding: a new entry already carries a default name
        first = rs.light_looks.add()
        first.name, first.data = name, data
    else:
        store(scene)
    name = _next_name(scene)
    look = rs.light_looks.add()
    look.name, look.data = name, data
    rs.light_look_index = len(rs.light_looks) - 1
    return look.name


def switch(scene, index):
    coll = _set(scene)
    if coll is not None:
        found = _set_looks(coll)
        if not 0 <= index < len(found):
            raise ValueError("No variant %r" % index)
        current = _set_index(coll, found)
        if index == current:
            return []
        if current >= 0:
            found[current]["data"] = capture_set(coll)
        _save_set_looks(coll, found, index)
        return apply_set(scene, coll, found[index].get("data") or {})
    rs = scene.studio
    if not 0 <= index < len(rs.light_looks):
        raise ValueError("No look %r" % index)
    if index == active(scene):
        return []
    store(scene)
    rs.light_look_index = index
    try:
        data = json.loads(rs.light_looks[index].data or "{}")
    except ValueError:
        data = {}
    return apply(scene, data) if data else []


def switch_name(scene, name):
    if name not in names(scene):
        raise ValueError("No look %r (there are: %s)" % (name, ", ".join(names(scene)) or "none"))
    return switch(scene, names(scene).index(name))


def remove(scene, index=None):
    """Removes a look (default: the open one) and opens its neighbor."""
    coll = _set(scene)
    if coll is not None:
        found = _set_looks(coll)
        current = _set_index(coll, found)
        index = current if index is None else index
        if not 0 <= index < len(found):
            return
        found.pop(index)
        if not found:
            _save_set_looks(coll, [], -1)
            return
        if index == current:
            target = min(index, len(found) - 1)
            _save_set_looks(coll, found, target)
            apply_set(scene, coll, found[target].get("data") or {})
        else:
            _save_set_looks(coll, found, current - 1 if index < current else current)
        return
    rs = scene.studio
    current = active(scene)
    index = current if index is None else index
    if not 0 <= index < len(rs.light_looks):
        return
    rs.light_looks.remove(index)
    if not len(rs.light_looks):
        rs.light_look_index = -1
        return
    if index == current:
        rs.light_look_index = -1           # nothing to store: the removed look is gone
        switch(scene, min(index, len(rs.light_looks) - 1))
    elif index < current:
        rs.light_look_index = current - 1


def mirror_all(scene):
    """Flips the whole lighting left ↔ right."""
    for item in light_items.all_items(scene):
        around, height = light_items.angles(scene, item)
        light_items.set_angles(scene, item, -around, height)

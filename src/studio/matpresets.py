# SPDX-License-Identifier: GPL-3.0-or-later
"""Material presets for the Principled BSDF (built-in and your own).

A preset sets the inputs below; inputs it does not name go back to the
neutral values of BASE, so e.g. "Matte" after "Glass" is matte again. Inputs
driven by a texture are left alone. Your own presets are stored as JSON in the
extension's user folder and are available in every blend file.
"""

import json
import os

import bpy

BASE = {
    "Metallic": 0.0, "Roughness": 0.5, "IOR": 1.5, "Specular IOR Level": 0.5,
    "Coat Weight": 0.0, "Coat Roughness": 0.03, "Transmission Weight": 0.0,
    "Sheen Weight": 0.0, "Sheen Roughness": 0.5, "Subsurface Weight": 0.0, "Subsurface Scale": 0.05,
    "Anisotropic": 0.0, "Emission Strength": 0.0,
}

BUILTIN = {
    "Matte": {"Roughness": 0.8},
    "Satin": {"Roughness": 0.45},
    "Gloss": {"Roughness": 0.12},
    "Plastic": {"Roughness": 0.35, "Specular IOR Level": 0.5},
    "Rubber": {"Roughness": 0.9, "Specular IOR Level": 0.3},
    "Ceramic": {"Roughness": 0.25, "Coat Weight": 1.0, "Coat Roughness": 0.03},
    "Car Paint": {"Roughness": 0.35, "Coat Weight": 1.0, "Coat Roughness": 0.01},
    "Metal": {"Metallic": 1.0, "Roughness": 0.25},
    "Brushed": {"Metallic": 1.0, "Roughness": 0.35, "Anisotropic": 0.8},
    "Chrome": {"Metallic": 1.0, "Roughness": 0.04},
    "Glass": {"Transmission Weight": 1.0, "Roughness": 0.02, "IOR": 1.45},
    "Frosted": {"Transmission Weight": 1.0, "Roughness": 0.35, "IOR": 1.45},
    "Fabric": {"Roughness": 0.9, "Sheen Weight": 1.0, "Sheen Roughness": 0.4, "Specular IOR Level": 0.2},
    "Wax": {"Roughness": 0.4, "Subsurface Weight": 0.6},
    "Glow": {"Emission Strength": 5.0},
}

SAVED_INPUTS = ("Base Color",) + tuple(BASE)

_saved = None


def _file():
    """material_presets.json in the folder from the Material settings, else the Studio data folder."""
    from .paths import data_dir
    from .runner import prefs
    p = prefs()
    folder = bpy.path.abspath(p.material_preset_folder) if p is not None and p.material_preset_folder else ""
    base = os.path.expanduser(folder) if folder else data_dir()
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "material_presets.json")


def reload():
    global _saved
    _saved = None


def saved():
    global _saved
    if _saved is None:
        try:
            with open(_file(), encoding="utf-8") as f:
                data = json.load(f)
            _saved = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _saved = {}
    return _saved


def _write():
    with open(_file(), "w", encoding="utf-8") as f:
        json.dump(saved(), f, indent=1)


def values(name):
    """All input values of a preset (BASE completed), or None."""
    if name in BUILTIN:
        return dict(BASE, **BUILTIN[name])
    return saved().get(name)


def apply(node, name):
    """Sets the preset on a Principled BSDF. Returns False if it does not exist."""
    preset = values(name)
    if preset is None:
        return False
    for socket_name, value in preset.items():
        socket = node.inputs.get(socket_name)
        if socket is None or socket.is_linked:
            continue
        if isinstance(value, list):
            value = tuple(value)
        socket.default_value = value
    if name == "Glow":   # glow in the base color
        color, emission = node.inputs.get("Base Color"), node.inputs.get("Emission Color")
        if color is not None and emission is not None and not emission.is_linked:
            emission.default_value = tuple(color.default_value)
    return True


def save(node, name):
    data = {}
    for socket_name in SAVED_INPUTS:
        socket = node.inputs.get(socket_name)
        if socket is None or socket.is_linked:
            continue
        value = socket.default_value
        data[socket_name] = list(value) if hasattr(value, "__len__") else float(value)
    saved()[name] = data
    _write()


def delete(name):
    if saved().pop(name, None) is not None:
        _write()


def current(node):
    """Name of the preset whose values the node has right now ("" if none)."""
    for name in list(BUILTIN) + sorted(saved()):
        preset = values(name)
        match = True
        for socket_name, value in preset.items():
            if socket_name == "Base Color" and name in BUILTIN:
                continue
            socket = node.inputs.get(socket_name)
            if socket is None or socket.is_linked:
                continue
            current_value = socket.default_value
            if hasattr(current_value, "__len__"):
                if any(abs(a - b) > 1e-3 for a, b in zip(current_value, value)):
                    match = False
                    break
            elif abs(current_value - value) > 1e-3:
                match = False
                break
        if match:
            return name
    return ""


# ------------------------------------------------------------ Material picker

def assign(material, objects):
    """Puts the material into the active slot of each object (a new slot when it has
    none). The other slots stay as they are. Returns the number of changed objects."""
    count = 0
    for obj in objects:
        if obj.library is not None or not hasattr(obj.data, "materials") or obj.data.library is not None:
            continue
        if not obj.material_slots:
            obj.data.materials.append(material)
        else:
            obj.material_slots[obj.active_material_index].material = material
        count += 1
    return count


def _color_input(material):
    """The color input of the shader on the material output: Principled "Base Color" or a
    node group input named like a color ("Base Color", "Color", "Farbe", …)."""
    if material is None or material.node_tree is None:
        return None
    outs = [n for n in material.node_tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial']
    out = next((n for n in outs if n.is_active_output), outs[0] if outs else None)
    if out is None or not out.inputs["Surface"].is_linked:
        return None
    node = out.inputs["Surface"].links[0].from_node
    for name in ("Base Color", "Color", "Farbe", "Grundfarbe", "Beizfarbe"):
        socket = node.inputs.get(name)
        if socket is not None and socket.type == 'RGBA':
            return socket
    return next((i for i in node.inputs if i.type == 'RGBA'), None)


def _principled(material):
    """The Principled BSDF of a material (the one connected to the output first)."""
    if material is None or material.node_tree is None:
        return None
    nodes = material.node_tree.nodes
    for out in (n for n in nodes if n.bl_idname == 'ShaderNodeOutputMaterial'):
        surface = out.inputs.get("Surface")
        if surface is not None and surface.is_linked:
            node = surface.links[0].from_node
            if node.bl_idname == 'ShaderNodeBsdfPrincipled':
                return node
    return next((n for n in nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled'), None)

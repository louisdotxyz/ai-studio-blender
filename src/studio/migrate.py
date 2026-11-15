# SPDX-License-Identifier: GPL-3.0-or-later
"""Files saved before 3.0 keep their settings: the add-on was "Render Studio" inside and stored its data
as "render_studio…" (scene.render_studio, object.render_studio_camera, material["render_studio_base"] …)
and named its nodes "RS …". Since 3.0 it is "studio…" and "Studio …". When a file is opened, the old
data is moved under the new names (the raw stored values, no update callbacks run) and the old names
are removed. Files saved since 3.0 have nothing to move."""

import bpy

OLD, NEW = "render_studio", "studio"
OLD_NODE, NEW_NODE = "RS ", "Studio "
OLD_IMAGE, NEW_IMAGE = "Render Studio", "AI Studio"
_COLLECTIONS = ("scenes", "objects", "materials", "worlds", "lights", "cameras", "collections", "node_groups",
                "images", "meshes", "texts", "window_managers")


def _new_key(key):
    if key == OLD:
        return NEW
    if key.startswith(OLD + "_"):
        return NEW + key[len(OLD):]
    return None


def _groups(block):
    """The block's own custom properties and (Blender 5) the separate storage of add-on properties."""
    yield block
    get = getattr(block, "bl_system_properties_get", None)
    if get is not None:
        try:
            system = get()
        except (TypeError, RuntimeError):
            system = None
        if system is not None:
            yield system


def _move_keys(block):
    moved = 0
    for group in _groups(block):
        try:
            keys = list(group.keys())
        except (TypeError, AttributeError):
            continue
        for key in keys:
            new = _new_key(key)
            if new is None:
                continue
            value = group[key]
            data = value.to_dict() if hasattr(value, "to_dict") else value
            target = _rna_pointer(block, new)
            if target is not None and isinstance(data, dict):
                _copy_rna(data, target)            # registered settings: through their properties
            else:
                group[new] = data
            try:
                del group[key]
            except (KeyError, TypeError):
                pass
            moved += 1
    return moved


def _rna_pointer(block, name):
    prop = block.bl_rna.properties.get(name)
    return getattr(block, name) if prop is not None and prop.type == 'POINTER' else None


def _copy_rna(data, target):
    """Stored values (enums as numbers, collections as lists) onto registered properties."""
    props = target.bl_rna.properties
    for key, value in data.items():
        prop = props.get(key)
        if prop is None or prop.is_readonly and prop.type not in {'POINTER', 'COLLECTION'}:
            continue
        try:
            if prop.type == 'POINTER':
                if isinstance(value, dict):
                    _copy_rna(value, getattr(target, key))
                elif value is None or isinstance(value, bpy.types.ID):
                    setattr(target, key, value)
            elif prop.type == 'COLLECTION':
                items = getattr(target, key)
                items.clear()
                for entry in value or []:
                    _copy_rna(entry if isinstance(entry, dict) else {}, items.add())
            elif prop.type == 'ENUM':
                if isinstance(value, str):
                    setattr(target, key, {value} if prop.is_enum_flag else value)
                elif prop.is_enum_flag:
                    setattr(target, key, {i.identifier for i in prop.enum_items if i.value & int(value)})
                else:
                    match = next((i.identifier for i in prop.enum_items if i.value == int(value)), None)
                    if match is not None:
                        setattr(target, key, match)
            elif prop.type == 'BOOLEAN' and getattr(prop, "array_length", 0) == 0:
                setattr(target, key, bool(value))
            else:
                setattr(target, key, tuple(value) if isinstance(value, (list, tuple)) else value)
        except (TypeError, ValueError, AttributeError) as ex:
            print("AI Studio: setting %s not taken over: %s" % (key, ex))


def _trees():
    for material in bpy.data.materials:
        if material.node_tree is not None:
            yield material.node_tree
    for world in bpy.data.worlds:
        if world.node_tree is not None:
            yield world.node_tree
    for scene in bpy.data.scenes:
        tree = getattr(scene, "compositing_node_group", None) or getattr(scene, "node_tree", None)
        if tree is not None:
            yield tree
    yield from bpy.data.node_groups


def run():
    """Move old data under the new names. Returns how many entries moved."""
    moved = 0
    for name in _COLLECTIONS:
        for block in getattr(bpy.data, name, ()):
            if getattr(block, "library", None) is not None:
                continue
            moved += _move_keys(block)
    for tree in _trees():
        if getattr(tree, "library", None) is not None:
            continue
        for node in tree.nodes:
            if node.name.startswith(OLD_NODE):
                node.name = NEW_NODE + node.name[len(OLD_NODE):]
                moved += 1
    for image in bpy.data.images:
        if image.name.startswith(OLD_IMAGE) and image.library is None:
            image.name = NEW_IMAGE + image.name[len(OLD_IMAGE):]
            moved += 1
    if moved:
        print("AI Studio: %d settings from before 3.0 taken over" % moved)
    return moved

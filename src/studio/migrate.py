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
            group[new] = value.to_dict() if hasattr(value, "to_dict") else value
            del group[key]
            moved += 1
    return moved


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

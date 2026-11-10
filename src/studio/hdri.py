# SPDX-License-Identifier: GPL-3.0-or-later
"""HDRIs from a folder, favorites and the HDRI world.

Favorites are stored in the extension's user folder, so they are shared by
all blend files. The world is a regular World named "Render Studio HDRI";
the previous world of the scene is remembered and can be restored.
"""

import json
import math
import os

import bpy

EXTENSIONS = (".hdr", ".exr")
WORLD_NAME = "Render Studio HDRI"
NODE_COORD, NODE_MAPPING, NODE_ENV, NODE_BG = "RS Coord", "RS Mapping", "RS Environment", "RS Background"

_cache = {"folder": None, "files": []}
_favorites = None


# ------------------------------------------------------------------- Files

def default_folder():
    """Where HDRIs go when no folder is set: <add-on data>/studio/HDRI."""
    from . import paths
    return os.path.join(paths.data_dir(), "HDRI")


def folder_path(folder):
    return os.path.abspath(os.path.expanduser(bpy.path.abspath(folder))) if folder else default_folder()


def scan(folder):
    """All HDRI files in the folder and its direct subfolders (BlenderKit style), sorted by name."""
    root = folder_path(folder)
    found = []
    if root and os.path.isdir(root):
        for entry in os.scandir(root):
            if entry.is_dir():
                try:
                    found += [e.path for e in os.scandir(entry.path)
                              if e.is_file() and e.name.lower().endswith(EXTENSIONS)]
                except OSError:
                    pass
            elif entry.is_file() and entry.name.lower().endswith(EXTENSIONS):
                found.append(entry.path)
    found.sort(key=lambda p: os.path.basename(p).lower())
    _cache["folder"], _cache["files"] = root, found
    return found


def files(folder):
    """Cached file list; scans again only when the folder changed."""
    if _cache["folder"] != folder_path(folder):
        scan(folder)
    return _cache["files"]


def display_name(path):
    return os.path.splitext(os.path.basename(path))[0]


# --------------------------------------------------------------- Favorites

def _favorites_file():
    from .paths import data_dir
    base = data_dir()
    return os.path.join(base, "hdri_favorites.json")


def favorites():
    global _favorites
    if _favorites is None:
        try:
            with open(_favorites_file(), encoding="utf-8") as f:
                _favorites = set(json.load(f))
        except (OSError, ValueError):
            _favorites = set()
    return _favorites


def toggle_favorite(path):
    favs = favorites()
    favs.symmetric_difference_update({path})
    with open(_favorites_file(), "w", encoding="utf-8") as f:
        json.dump(sorted(favs), f, indent=1)
    return path in favs


# ------------------------------------------------------------------- World

def hdri_world():
    return bpy.data.worlds.get(WORLD_NAME)


def _build_world():
    world = bpy.data.worlds.new(WORLD_NAME)
    nt = world.node_tree
    nt.nodes.clear()
    coord = nt.nodes.new('ShaderNodeTexCoord')
    coord.name = NODE_COORD
    mapping = nt.nodes.new('ShaderNodeMapping')
    mapping.name = NODE_MAPPING
    env = nt.nodes.new('ShaderNodeTexEnvironment')
    env.name = NODE_ENV
    bg = nt.nodes.new('ShaderNodeBackground')
    bg.name = NODE_BG
    out = nt.nodes.new('ShaderNodeOutputWorld')
    for i, node in enumerate((coord, mapping, env, bg, out)):
        node.location = (i * 250 - 500, 0)
    nt.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    nt.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
    nt.links.new(env.outputs["Color"], bg.inputs["Color"])
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])
    return world


def _node(name):
    world = hdri_world()
    if world is None or world.node_tree is None:
        return None
    return world.node_tree.nodes.get(name)


def apply(scene, path, settings):
    """Uses the HDRI as the scene's world. The previous world is remembered."""
    world = hdri_world()
    if world is None or _node(NODE_ENV) is None:
        if world is not None:
            world.name = WORLD_NAME + " (old)"
        world = _build_world()
    image = bpy.data.images.load(path, check_existing=True)
    _node(NODE_ENV).image = image
    if scene.world != world:
        settings.previous_world = scene.world
        scene.world = world
    return world


def current_path(scene):
    env = _node(NODE_ENV)
    if scene.world is None or scene.world != hdri_world() or env is None or env.image is None:
        return ""
    return os.path.normpath(bpy.path.abspath(env.image.filepath))


def restore(scene, settings):
    scene.world = settings.previous_world
    settings.previous_world = None


# Getters/setters for strength and rotation (RS_Settings) ---------------------

def get_strength(_settings):
    bg = _node(NODE_BG)
    return bg.inputs["Strength"].default_value if bg else 1.0


def set_strength(_settings, value):
    bg = _node(NODE_BG)
    if bg:
        bg.inputs["Strength"].default_value = value


def get_rotation(_settings):
    mapping = _node(NODE_MAPPING)
    return mapping.inputs["Rotation"].default_value[2] if mapping else 0.0


def set_rotation(_settings, value):
    mapping = _node(NODE_MAPPING)
    if mapping:
        mapping.inputs["Rotation"].default_value[2] = math.remainder(value, 2 * math.pi)


# --------------------------------------------------------------- BlenderKit

def blenderkit_dirs():
    """Where BlenderKit keeps downloaded HDRIs (its "global_dir", default ~/blenderkit_data)."""
    roots = []
    for name, addon in bpy.context.preferences.addons.items():
        if name.rsplit(".", 1)[-1] == "blenderkit":
            global_dir = getattr(addon.preferences, "global_dir", "") if addon.preferences else ""
            if global_dir:
                roots.append(bpy.path.abspath(global_dir))
    roots.append(os.path.expanduser("~/blenderkit_data"))
    seen, dirs = set(), []
    for root in roots:
        path = os.path.join(os.path.abspath(root), "hdris")
        if path not in seen and os.path.isdir(path):
            seen.add(path)
            dirs.append(path)
    return dirs


def _world_image_paths():
    """HDRI files used by Worlds in the open file (BlenderKit puts them there when you add one)."""
    paths = set()
    for world in bpy.data.worlds:
        if world.node_tree is None:
            continue
        for node in world.node_tree.nodes:
            image = getattr(node, "image", None) if node.bl_idname == 'ShaderNodeTexEnvironment' else None
            if image is not None and image.filepath:
                path = os.path.normpath(bpy.path.abspath(image.filepath, library=image.library))
                if path.lower().endswith(EXTENSIONS) and os.path.isfile(path):
                    paths.add(path)
    return paths


def blenderkit_candidates(folder):
    """HDRI files from BlenderKit (its folder and Worlds of this file) that are not in `folder` yet."""
    root = folder_path(folder)
    if not root:
        return []
    have = {os.path.basename(p).lower() for p in scan(folder)}
    found = set(_world_image_paths())
    for base in blenderkit_dirs():
        for dirpath, _dirs, names in os.walk(base):
            found.update(os.path.join(dirpath, n) for n in names if n.lower().endswith(EXTENSIONS))
    return sorted(p for p in found
                  if not os.path.normpath(p).startswith(root + os.sep) and os.path.basename(p).lower() not in have)


_copying = {"busy": False, "done": 0}


def import_blenderkit(folder, wait=False):
    """Copies new BlenderKit HDRIs into `folder`/BlenderKit (in a thread, files only, no bpy).
    Returns how many files are copied."""
    import shutil
    import threading
    sources = blenderkit_candidates(folder)
    if not sources or _copying["busy"]:
        return 0
    target = os.path.join(folder_path(folder), "BlenderKit")

    def copy():
        try:
            os.makedirs(target, exist_ok=True)
            for src in sources:
                dst = os.path.join(target, os.path.basename(src))
                if not os.path.exists(dst):
                    shutil.copy2(src, dst + ".part")
                    os.replace(dst + ".part", dst)
                    _copying["done"] += 1
        except OSError as ex:
            print("Render Studio: could not copy BlenderKit HDRIs:", ex)
        finally:
            _copying["busy"] = False
            _cache["folder"] = None   # scan again

    _copying["busy"] = True
    if wait:
        copy()
    else:
        threading.Thread(target=copy, daemon=True).start()
    return len(sources)


# ----------------------------------------------------------------- Previews

_previews = {"collection": None, "items": []}


def _collection():
    if _previews["collection"] is None:
        import bpy.utils.previews
        _previews["collection"] = bpy.utils.previews.new()
    return _previews["collection"]


def enum_items(settings, context):
    """Thumbnails of all HDRIs in the folder for the gallery (template_icon_view)."""
    from . import runner
    p = runner.prefs(context)
    pcoll = _collection()
    items = [("NONE", "Original World", "The World of the scene before an HDRI was picked", 'WORLD', 0)]
    for i, path in enumerate(files(p.hdri_folder if p else ""), start=1):
        if path not in pcoll:
            pcoll.load(path, path, 'IMAGE')
        items.append((path, display_name(path), path, pcoll[path].icon_id, i))
    _previews["items"] = items   # Blender needs the strings to stay alive
    return items


def get_choice(settings):
    current = current_path(settings.id_data)
    for identifier, _name, _desc, _icon, number in _previews["items"]:
        if identifier == current:
            return number
    return 0


def set_choice(settings, value):
    for identifier, _name, _desc, _icon, number in _previews["items"]:
        if number == value:
            scene = settings.id_data
            if identifier == "NONE":
                if settings.previous_world is not None:
                    restore(scene, settings)
            else:
                apply(scene, identifier, settings)
            return


def clear_previews():
    if _previews["collection"] is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(_previews["collection"])
        _previews["collection"] = None
    _previews["items"] = []


# ------------------------------------------------------------- Watch timer

_watch = {"state": None}


def _watch_state():
    dirs = blenderkit_dirs()
    mtimes = []
    for base in dirs:
        try:
            mtimes.append(max((os.path.getmtime(dp) for dp, _d, _n in os.walk(base)), default=0))
        except OSError:
            pass
    return (len(bpy.data.images), tuple(mtimes))


def _watch_timer():
    """Every few seconds: new BlenderKit HDRI? Then copy it into the HDRI folder."""
    try:
        state = _watch_state()
        if state != _watch["state"]:
            _watch["state"] = state
            from . import runner
            p = runner.prefs()
            if p is not None:
                import_blenderkit(p.hdri_folder)
    except Exception as ex:
        print("Render Studio: BlenderKit check:", ex)
    return 5.0


def register():
    bpy.app.timers.register(_watch_timer, first_interval=2.0, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_watch_timer):
        bpy.app.timers.unregister(_watch_timer)
    clear_previews()


def preview_icon(path):
    """Icon id of the thumbnail of an HDRI (0 if not available)."""
    try:
        pcoll = _collection()
        if path not in pcoll:
            pcoll.load(path, path, 'IMAGE')
        return pcoll[path].icon_id
    except Exception:
        return 0

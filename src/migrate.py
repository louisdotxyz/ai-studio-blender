# SPDX-License-Identifier: GPL-3.0-or-later
"""From the old extension id ``claude_assistant`` (up to 0.24) to ``AI_Studio`` (1.0).

Both register the same operators and panels, so they must not run together. At the
start of register(): read the old add-on's preferences, turn it off, and copy its
data folder (chats, memory, skills, Studio presets) next to the new one. After the
new add-on is registered, its preferences get the old values. The old folder stays
as it was; the user removes the old extension in Preferences › Get Extensions.
"""

import os
import shutil

import bpy

OLD_ID = "claude_assistant"
MARKER = "migrated_from_" + OLD_ID
TEXT_EXTS = (".json", ".md", ".txt")


def _old_module():
    for name in bpy.context.preferences.addons.keys():
        if name.rsplit(".", 1)[-1] == OLD_ID:
            return name
    return None


def values(group):
    """The values set in a property group (preferences), nested groups as dicts."""
    out = {}
    for prop in group.bl_rna.properties:
        key = prop.identifier
        if key in ("rna_type", "bl_idname"):
            continue
        if prop.type == 'POINTER':
            sub = getattr(group, key, None)
            if isinstance(sub, bpy.types.PropertyGroup):
                out[key] = values(sub)
        elif prop.type == 'COLLECTION' or prop.is_readonly:
            continue
        elif group.is_property_set(key):
            value = getattr(group, key)
            out[key] = value if isinstance(value, (str, int, float, bool)) else tuple(value)
    return out


def apply(group, saved):
    for key, value in saved.items():
        if isinstance(value, dict):
            sub = getattr(group, key, None)
            if sub is not None:
                apply(sub, value)
        elif hasattr(group, key):
            try:
                setattr(group, key, value)
            except (TypeError, AttributeError, ValueError) as exc:
                print("AI Studio: setting %s not taken over: %s" % (key, exc))


def _copy_new_only(src, dst):
    if not os.path.exists(dst):
        shutil.copy2(src, dst)
    return dst


def copy_data(new_dir, user_root=None):
    """Copy <.user>/<repo>/claude_assistant into new_dir once (files that exist stay), and point
    paths inside the copied chats and notes at the new folder. Returns the old folder or None."""
    if os.path.exists(os.path.join(new_dir, MARKER)):
        return None
    user_root = user_root or os.path.dirname(os.path.dirname(new_dir))
    try:
        repos = sorted(os.listdir(user_root))
    except OSError:
        return None
    old_dir = next((os.path.join(user_root, r, OLD_ID) for r in repos
                    if os.path.isdir(os.path.join(user_root, r, OLD_ID))), None)
    if old_dir is None:
        return None
    shutil.copytree(old_dir, new_dir, copy_function=_copy_new_only, dirs_exist_ok=True)
    for folder, _dirs, files in os.walk(new_dir):
        for name in files:
            if not name.endswith(TEXT_EXTS):
                continue
            path = os.path.join(folder, name)
            try:
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                if old_dir in text:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(text.replace(old_dir, new_dir))
            except (OSError, UnicodeDecodeError):
                pass
    with open(os.path.join(new_dir, MARKER), "w", encoding="utf-8") as f:
        f.write(old_dir + "\n")
    return old_dir


def before_register():
    """Read and turn off the old add-on. Returns what after_register() needs."""
    result = {"prefs": None, "old": None, "data": None}
    old = _old_module()
    if old is not None:
        result["old"] = old
        try:
            result["prefs"] = values(bpy.context.preferences.addons[old].preferences)
        except Exception as exc:   # an old add-on without preferences, or a broken one
            print("AI Studio: old preferences not read:", exc)
        import addon_utils
        try:
            addon_utils.disable(old, default_set=True)
        except Exception as exc:
            print("AI Studio: could not turn the old version off:", exc)
        bpy.context.preferences.is_dirty = True
    try:
        new_dir = bpy.utils.extension_path_user(__package__, path="", create=True)
        result["data"] = copy_data(new_dir)
    except Exception as exc:
        print("AI Studio: old data not copied:", exc)
    return result


def after_register(result):
    if result["prefs"]:
        addon = bpy.context.preferences.addons.get(__package__)
        if addon is not None:
            apply(addon.preferences, result["prefs"])
            bpy.context.preferences.is_dirty = True
    if result["old"] or result["data"]:
        from . import core
        from .i18n import tr
        core.add_message("info", tr("AI Studio has a new name inside Blender (AI_Studio). Settings and chats "
                                    "were taken over and the old version 'claude_assistant' is off: remove it "
                                    "in Preferences › Get Extensions."))

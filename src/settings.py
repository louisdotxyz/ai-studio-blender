# SPDX-License-Identifier: GPL-3.0-or-later
"""One-time settings step for people who used the add-on before folders and names became
settings (0.19): their projects keep the names they already have. New users keep the
neutral defaults. Runs once (preferences.settings_version)."""

import os

import bpy

VERSION = 3
# Collection names before 1.3 (with the "AS_" prefix): saved as settings, they become the new defaults.
OLD_COLLECTIONS = {"camera_collection": "AS_camera", "light_collection": "AS_lights", "archive_collection": "AS_archiv"}
# Defaults that became neutral in 1.2 (new users are asked in the setup card): people who used
# the add-on before keep what they had.
OLD_DEFAULTS = {"project_folders": True, "autosave": True}
# Names used before they were settings
LEGACY = {"backup_dir": "autosave", "archive_dir": "archiv", "main_suffix": "_now", "backup_stamp": 'HMD'}
LEGACY_STUDIO = {"output_suffix": "_output", "low_dir": "preview", "high_dir": "HQ",
                 "render_archive_dir": "archiv"}


def apply_once():
    from . import core, lessons
    from .preferences import get_prefs
    prefs = get_prefs()
    if prefs is None or prefs.settings_version >= VERSION:
        return None
    used_before = os.path.exists(os.path.join(core.workdir(), "chats.json"))
    if used_before and prefs.settings_version < 2:
        for key, value in OLD_DEFAULTS.items():
            if not prefs.is_property_set(key):   # never changed: the old default stays
                setattr(prefs, key, value)
    if used_before and prefs.settings_version < 1:   # used before 0.19: keep its names
        for key, value in LEGACY.items():
            setattr(prefs, key, value)
        for key, value in LEGACY_STUDIO.items():
            setattr(prefs.studio, key, value)
        vault = lessons.find_vault()
        if vault and not prefs.lessons_dir:
            prefs.lessons_dir = os.path.join(vault, lessons.AREA_DIR, lessons.PROJECT)
        if not prefs.studio.material_library:
            for library in bpy.context.preferences.filepaths.asset_libraries:
                path = os.path.join(bpy.path.abspath(library.path), "Grundmaterialien.blend")
                if os.path.isfile(path):
                    prefs.studio.material_library = path
                    break
    if prefs.settings_version < 3:
        for key, old in OLD_COLLECTIONS.items():
            if getattr(prefs.studio, key, "") == old:
                prefs.studio.property_unset(key)
    prefs.settings_version = VERSION
    bpy.context.preferences.is_dirty = True   # saved with the preferences
    return None


def register():
    if not bpy.app.timers.is_registered(apply_once):
        bpy.app.timers.register(apply_once, first_interval=0.5)


def unregister():
    if bpy.app.timers.is_registered(apply_once):
        bpy.app.timers.unregister(apply_once)

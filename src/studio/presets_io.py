# SPDX-License-Identifier: GPL-3.0-or-later
"""All presets in one file: scene, light and material presets together, to back up or share.

Format: {"id_toolkit_presets": 1, "scene": {...}, "light": {...}, "material": {...}}.
Import merges: presets with the same name are replaced, the others stay.
"""

import json

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from . import environment, lights, matpresets

KINDS = ("scene", "light", "material")


def collect():
    return {"id_toolkit_presets": 1,
            "scene": environment.saved_scenes(),
            "light": lights.saved_presets(),
            "material": dict(matpresets.saved())}


def merge(data):
    """Merges a presets file. Returns {kind: count}. Raises ValueError for anything else."""
    if not isinstance(data, dict) or not data.get("id_toolkit_presets"):
        raise ValueError("Not an ID Toolkit presets file")
    counts = {}
    scenes = data.get("scene") or {}
    if isinstance(scenes, dict) and scenes:
        merged = environment.saved_scenes()
        merged.update(scenes)
        with open(environment._scenes_file(), "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=1)
        counts["scene"] = len(scenes)
    light_presets = data.get("light") or {}
    if isinstance(light_presets, dict):
        done = 0
        for spec in light_presets.values():
            try:
                lights.save_preset(spec)
                done += 1
            except (ValueError, TypeError):
                pass
        if done:
            counts["light"] = done
    materials = data.get("material") or {}
    if isinstance(materials, dict) and materials:
        matpresets.saved().update(materials)
        matpresets._write()
        counts["material"] = len(materials)
    return counts


class RS_OT_presets_io(Operator):
    bl_idname = "render_studio.presets_io"
    bl_label = "All Presets"
    bl_description = "Export all your presets (scenes, lights, materials) into one file, or import such a file"
    bl_options = {'INTERNAL'}

    action: StringProperty(default="EXPORT")   # EXPORT / IMPORT
    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        if self.action == "EXPORT":
            self.filepath = "ID Toolkit Presets.json"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if self.action == "EXPORT":
            data = collect()
            with open(bpy.path.ensure_ext(self.filepath, ".json"), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1)
            self.report({'INFO'}, "Exported: %s" % ", ".join("%d %s" % (len(data[k]), k) for k in KINDS))
            return {'FINISHED'}
        try:
            with open(self.filepath, encoding="utf-8") as f:
                counts = merge(json.load(f))
        except (OSError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, "Imported: %s" % (", ".join("%d %s" % (n, k) for k, n in counts.items()) or "nothing"))
        return {'FINISHED'}


def draw(layout):
    row = layout.row(align=True)
    row.label(text="All Presets", icon='PRESET')
    row.operator(RS_OT_presets_io.bl_idname, text="Export", icon='EXPORT').action = "EXPORT"
    row.operator(RS_OT_presets_io.bl_idname, text="Import", icon='IMPORT').action = "IMPORT"


classes = (RS_OT_presets_io,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

# SPDX-License-Identifier: GPL-3.0-or-later
"""A / B / + in the head of the Material, Camera and Render tab, like the looks of the Light tab.

Each tab has its own variants (they mix freely):
- MATERIAL: which material is in which slot of which object (color and material variants);
- CAMERA:   the camera looked through, with its place, lens, depth of field and the aspect ratio;
- RENDER:   quality, still or film, the size (1K/2K/4K) and the file format.

The variant that is open follows what you change; switching stores it and brings the other one back.
Kept in the blend file (scene["id_variants_<kind>"] as JSON).
"""

import json

import bpy
from bpy.props import EnumProperty, IntProperty
from bpy.types import Operator
from mathutils import Matrix

KINDS = (('MATERIAL', "Material", ""), ('CAMERA', "Camera", ""), ('RENDER', "Render", ""))
NAMES = "ABCDEFGH"
RENDER_KEYS = ("quality", "mode", "still_format", "still_depth", "anim_format")


def _key(kind):
    return "id_variants_" + kind.lower()


def _data(scene, kind):
    try:
        data = json.loads(scene.get(_key(kind), "") or "{}")
    except ValueError:
        data = {}
    data.setdefault("items", [])
    data.setdefault("active", -1)
    return data


def _save(scene, kind, data):
    scene[_key(kind)] = json.dumps(data)


def names(scene, kind):
    return [item["name"] for item in _data(scene, kind)["items"]]


def active(scene, kind):
    data = _data(scene, kind)
    return data["active"] if 0 <= data["active"] < len(data["items"]) else -1


# ------------------------------------------------------------------ what a variant holds

def _capture_material(scene):
    return {obj.name: [slot.material.name if slot.material else "" for slot in obj.material_slots]
            for obj in scene.objects if obj.type in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'} and obj.material_slots}


def _apply_material(scene, state):
    for name, mats in state.items():
        obj = scene.objects.get(name)
        if obj is None:
            continue
        for slot, mat_name in zip(obj.material_slots, mats):
            mat = bpy.data.materials.get(mat_name) if mat_name else None
            if slot.material != mat and (mat is not None or not mat_name):
                slot.material = mat


def _capture_camera(scene):
    cam = scene.camera
    state = {"resolution": [scene.render.resolution_x, scene.render.resolution_y]}
    if cam is None or cam.type != 'CAMERA':
        return state
    data = cam.data
    state.update(camera=cam.name, matrix=[list(r) for r in cam.matrix_world], type=data.type, lens=data.lens,
                 ortho_scale=data.ortho_scale, shift=[data.shift_x, data.shift_y],
                 dof=[data.dof.use_dof, data.dof.aperture_fstop, data.dof.focus_distance,
                      data.dof.focus_object.name if data.dof.focus_object else ""])
    return state


def _apply_camera(scene, state):
    res = state.get("resolution")
    if res:
        scene.render.resolution_x, scene.render.resolution_y = res
    cam = bpy.data.objects.get(state.get("camera", ""))
    if cam is None or cam.type != 'CAMERA':
        return
    if scene.objects.get(cam.name) is not None:
        scene.camera = cam
    cam.matrix_world = Matrix(state["matrix"])
    data = cam.data
    data.type = state.get("type", data.type)
    data.lens = state.get("lens", data.lens)
    data.ortho_scale = state.get("ortho_scale", data.ortho_scale)
    data.shift_x, data.shift_y = state.get("shift", (data.shift_x, data.shift_y))
    use, fstop, dist, focus = state.get("dof", (data.dof.use_dof, data.dof.aperture_fstop,
                                                data.dof.focus_distance, ""))
    data.dof.use_dof, data.dof.aperture_fstop, data.dof.focus_distance = use, fstop, dist
    data.dof.focus_object = bpy.data.objects.get(focus) if focus else None


def _capture_render(scene):
    rq = scene.render_studio
    state = {key: getattr(rq, key) for key in RENDER_KEYS if hasattr(rq, key)}
    state["long_side"] = scene.get("id_render_long", 0)
    state["percentage"] = scene.render.resolution_percentage
    return state


def _apply_render(scene, state):
    rq = scene.render_studio
    for key in RENDER_KEYS:
        if key in state and hasattr(rq, key):
            try:
                setattr(rq, key, state[key])
            except (TypeError, ValueError):
                pass
    if state.get("long_side"):
        from . import render_size
        render_size.set_long_side(scene, state["long_side"])
    elif state.get("percentage"):
        scene.render.resolution_percentage = state["percentage"]


CAPTURE = {'MATERIAL': _capture_material, 'CAMERA': _capture_camera, 'RENDER': _capture_render}
APPLY = {'MATERIAL': _apply_material, 'CAMERA': _apply_camera, 'RENDER': _apply_render}


# ------------------------------------------------------------------ add, switch, remove

def store(scene, kind):
    data = _data(scene, kind)
    if 0 <= data["active"] < len(data["items"]):
        data["items"][data["active"]]["data"] = CAPTURE[kind](scene)
        _save(scene, kind, data)


def add(scene, kind):
    """A new variant as a copy of what is there now; it becomes the open one (the first call also keeps
    the state so far as A). Returns its name."""
    data = _data(scene, kind)
    state = CAPTURE[kind](scene)
    if not data["items"]:
        data["items"].append({"name": NAMES[0], "data": state})
    elif 0 <= data["active"] < len(data["items"]):
        data["items"][data["active"]]["data"] = state
    used = {item["name"] for item in data["items"]}
    name = next((n for n in NAMES if n not in used), "V%d" % (len(used) + 1))
    data["items"].append({"name": name, "data": json.loads(json.dumps(state))})
    data["active"] = len(data["items"]) - 1
    _save(scene, kind, data)
    return name


def switch(scene, kind, index):
    data = _data(scene, kind)
    if not 0 <= index < len(data["items"]):
        raise ValueError("No variant %r" % index)
    if index == data["active"]:
        return
    if 0 <= data["active"] < len(data["items"]):
        data["items"][data["active"]]["data"] = CAPTURE[kind](scene)
    data["active"] = index
    _save(scene, kind, data)
    APPLY[kind](scene, data["items"][index].get("data") or {})


def remove(scene, kind):
    """Removes the open variant and opens its neighbor; with one left, the variants are gone."""
    data = _data(scene, kind)
    index = data["active"]
    if not 0 <= index < len(data["items"]):
        return
    data["items"].pop(index)
    if len(data["items"]) <= 1:            # one left: it stays as the scene is, the letters go away
        rest = data["items"][0].get("data") if data["items"] else None
        _save(scene, kind, {"items": [], "active": -1})
        if rest:
            APPLY[kind](scene, rest)
        return
    data["active"] = -1
    _save(scene, kind, data)
    switch(scene, kind, min(index, len(data["items"]) - 1))


# ------------------------------------------------------------------ UI

class RS_OT_variant_switch(Operator):
    bl_idname = "render_studio.variant_switch"
    bl_label = "Variant"
    bl_description = "Open this variant (the open one keeps what you changed)"
    bl_options = {'UNDO', 'INTERNAL'}

    kind: EnumProperty(items=KINDS, options={'HIDDEN', 'SKIP_SAVE'})
    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        try:
            switch(context.scene, self.kind, self.index)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class RS_OT_variant_add(Operator):
    bl_idname = "render_studio.variant_add"
    bl_label = "New Variant"
    bl_description = "Compare: a new variant (B, C …) as a copy of what is there now; switch with the letters"
    bl_options = {'UNDO', 'INTERNAL'}

    kind: EnumProperty(items=KINDS, options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        self.report({'INFO'}, "Variant %s" % add(context.scene, self.kind))
        return {'FINISHED'}


class RS_OT_variant_remove(Operator):
    bl_idname = "render_studio.variant_remove"
    bl_label = "Remove Variant"
    bl_description = "Remove the open variant (with one left, the letters go away)"
    bl_options = {'UNDO', 'INTERNAL'}

    kind: EnumProperty(items=KINDS, options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        remove(context.scene, self.kind)
        return {'FINISHED'}


def draw(row, scene, kind):
    """[A] [B] … [+] into a head row; with variants also [−] for the open one."""
    current = active(scene, kind)
    items = names(scene, kind)
    for index, name in enumerate(items):
        sub = row.row(align=True)
        sub.ui_units_x = 1.4
        op = sub.operator(RS_OT_variant_switch.bl_idname, text=name, depress=index == current)
        op.kind, op.index = kind, index
    if items:
        row.operator(RS_OT_variant_remove.bl_idname, text="", icon='REMOVE').kind = kind
    row.operator(RS_OT_variant_add.bl_idname, text="", icon='ADD').kind = kind


classes = (RS_OT_variant_switch, RS_OT_variant_add, RS_OT_variant_remove)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

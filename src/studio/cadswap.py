# SPDX-License-Identifier: GPL-3.0-or-later
"""CAD versions: import a model with checks, take a new version over from the old one
(materials and collections by part name), keep replaced objects in the archive collection,
and the temporary collection for helper objects.

Names are compared without the endings importers add: "Gabel.001", "Gabel <1>", "Gabel (2)".
Replaced objects are never deleted unless asked: they go into the archive collection
(Preferences › Studio, default "AS_archiv"), hidden in viewport and render.
"""

import os
import re

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from .cameras import archive_collection_name, temp_collection_name

ARCHIVED_FROM = "as_archived_from"   # collections the object was in before it was archived
TEMP_KEY = "as_temp"                 # marks the temporary collection the add-on empties
_ENDINGS = re.compile(r"(\.\d{3}|\s*<\d+>|\s*\(\d+\))$")

IMPORTERS = {
    ".glb": lambda p: bpy.ops.import_scene.gltf(filepath=p),
    ".gltf": lambda p: bpy.ops.import_scene.gltf(filepath=p),
    ".obj": lambda p: bpy.ops.wm.obj_import(filepath=p),
    ".stl": lambda p: bpy.ops.wm.stl_import(filepath=p, global_scale=0.001),   # STL is in mm
    ".fbx": lambda p: bpy.ops.import_scene.fbx(filepath=p),
    ".ply": lambda p: bpy.ops.wm.ply_import(filepath=p),
    ".usd": lambda p: bpy.ops.wm.usd_import(filepath=p),
    ".usdc": lambda p: bpy.ops.wm.usd_import(filepath=p),
    ".usdz": lambda p: bpy.ops.wm.usd_import(filepath=p),
    ".abc": lambda p: bpy.ops.wm.alembic_import(filepath=p),
}


def part_name(name):
    """The name without the endings importers and Blender add (lower case)."""
    name = name.strip()
    while True:
        stripped = _ENDINGS.sub("", name)
        if stripped == name or not stripped:
            return name.lower()
        name = stripped


def _archived(obj):
    coll = bpy.data.collections.get(archive_collection_name())
    return coll is not None and obj.name in coll.objects


def match(new_objects, old_objects=None, scene=None):
    """Pairs (new, old) by part name; parts with the same name are paired in name order.
    Returns (pairs, new without partner, old without partner). Default old objects: the
    other meshes of the scene that are not archived."""
    scene = scene or bpy.context.scene
    new_objects = sorted((o for o in new_objects if o.type == 'MESH'), key=lambda o: o.name)
    if old_objects is None:
        new_set = set(new_objects)
        old_objects = [o for o in scene.objects if o.type == 'MESH' and o not in new_set and not _archived(o)]
    old_by_part = {}
    for obj in sorted(old_objects, key=lambda o: o.name):
        old_by_part.setdefault(part_name(obj.name), []).append(obj)
    pairs, lonely_new = [], []
    for obj in new_objects:
        olds = old_by_part.get(part_name(obj.name))
        if olds:
            # Same name apart from Blender's ".001" first ("Gabel <2>" keeps to "Gabel <2>").
            plain = re.sub(r"\.\d{3}$", "", obj.name)
            previous = next((o for o in olds if re.sub(r"\.\d{3}$", "", o.name) == plain), olds[0])
            olds.remove(previous)
            pairs.append((obj, previous))
        else:
            lonely_new.append(obj)
    lonely_old = [o for olds in old_by_part.values() for o in olds]
    return pairs, lonely_new, lonely_old


def copy_materials(source, target):
    """The material slots of `source` onto `target` (slot by slot; extra slots are added)."""
    mats = [s.material for s in source.material_slots]
    if not mats or not hasattr(target.data, "materials"):
        return False
    while len(target.material_slots) < len(mats):
        target.data.materials.append(None)
    for slot, material in zip(target.material_slots, mats):
        slot.material = material
    for i, slot in enumerate(target.material_slots[len(mats):]):
        slot.material = mats[-1]
    return True


def archive_collection(scene=None):
    scene = scene or bpy.context.scene
    name = archive_collection_name()
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        coll.hide_viewport = coll.hide_render = True
    if coll not in scene.collection.children_recursive:
        scene.collection.children.link(coll)
    return coll


def archive(objects, scene=None):
    """Move objects into the archive collection (hidden; delete them there yourself).
    Returns how many were moved."""
    coll = archive_collection(scene)
    count = 0
    for obj in objects:
        if obj.name in coll.objects:
            continue
        obj[ARCHIVED_FROM] = ", ".join(c.name for c in obj.users_collection)
        for other in list(obj.users_collection):
            other.objects.unlink(obj)
        coll.objects.link(obj)
        count += 1
    return count


def take_over(pairs, materials=True, collections=True, old="ARCHIVE", scene=None):
    """New version takes over from the old one: materials, collections; the old objects go into
    the archive (old='ARCHIVE'), are deleted (old='DELETE') or stay ('KEEP')."""
    scene = scene or bpy.context.scene
    done = 0
    for new, previous in pairs:
        if materials:
            copy_materials(previous, new)
        if collections:
            targets = [c for c in previous.users_collection if c.name != archive_collection_name()]
            if targets:
                for coll in list(new.users_collection):
                    coll.objects.unlink(new)
                for coll in targets:
                    coll.objects.link(new)
        done += 1
    olds = [previous for _new, previous in pairs]
    if old == 'ARCHIVE':
        archive(olds, scene)
    elif old == 'DELETE':
        for obj in olds:
            bpy.data.objects.remove(obj)
    return done


def import_model(path):
    """Import a CAD export by its extension. Returns the new objects (selected)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".step", ".stp", ".iges", ".igs"):
        raise ValueError("Blender cannot import STEP/IGES: export GLB from the CAD program, or convert "
                         "with build123d (see the CAD workflow note)")
    if ext not in IMPORTERS:
        raise ValueError("Unknown format %s" % ext)
    before = set(bpy.data.objects)
    IMPORTERS[ext](path)
    return [o for o in bpy.data.objects if o not in before]


def check(objects):
    """Short report: size, floor, triangles, generic names."""
    meshes = [o for o in objects if o.type == 'MESH']
    if not meshes:
        return ["No meshes imported"]
    from mathutils import Vector
    corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    low = Vector(tuple(min(c[i] for c in corners) for i in range(3)))
    high = Vector(tuple(max(c[i] for c in corners) for i in range(3)))
    size = high - low
    tris = sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in meshes)
    lines = ["%d parts, %.3f × %.3f × %.3f m, %s triangles" % (len(meshes), size.x, size.y, size.z,
                                                               format(tris, ",").replace(",", "'"))]
    if max(size) > 20:
        lines.append("Very large: was it exported in mm? (scale 0.001)")
    elif max(size) < 0.005:
        lines.append("Very small: check the unit")
    if abs(low.z) > 0.001:
        lines.append("Lowest point at Z = %.3f m (not on the floor)" % low.z)
    generic = [o.name for o in meshes if re.match(r"^(mesh|object|body|part|solid|körper)\b", o.name, re.I)]
    if generic:
        lines.append("%d parts with generic names (e.g. %s): name them in the CAD program" % (len(generic), generic[0]))
    if tris > 3_000_000:
        lines.append("Many triangles: export coarser for faster renders")
    return lines


# ---------------------------------------------------------------- temporary collection

def temp_collection(scene=None):
    """The collection for helper objects; the add-on empties it after each answer of Claude."""
    scene = scene or bpy.context.scene
    name = temp_collection_name()
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    coll[TEMP_KEY] = True
    if coll not in scene.collection.children_recursive:
        scene.collection.children.link(coll)
    return coll


def empty_temp():
    """Delete what is in the temporary collection (only one the add-on marked) and the collection.
    Returns the number of removed objects."""
    coll = bpy.data.collections.get(temp_collection_name())
    if coll is None or not coll.get(TEMP_KEY) or coll.library is not None:
        return 0
    objects = list(coll.all_objects)
    for obj in objects:
        data = obj.data
        bpy.data.objects.remove(obj)
        if data is not None and data.users == 0 and isinstance(data, bpy.types.Mesh):
            bpy.data.meshes.remove(data)
    for child in list(coll.children_recursive):
        bpy.data.collections.remove(child)
    bpy.data.collections.remove(coll)
    return len(objects)


# ---------------------------------------------------------------- operators

class RS_OT_cad_import(bpy.types.Operator):
    bl_idname = "render_studio.cad_import"
    bl_label = "Import CAD"
    bl_description = ("Import a CAD export (GLB, OBJ, STL, FBX …) and check size, floor, names and triangles. "
                      "If parts of an older version are in the scene, take their materials over")
    bl_options = {'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_glob: StringProperty(default="*.glb;*.gltf;*.obj;*.stl;*.fbx;*.ply;*.usd;*.usdc;*.usdz;*.abc",
                                          options={'HIDDEN'})

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = os.path.expanduser("~/Downloads/")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            objects = import_model(self.filepath)
        except Exception as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        for obj in context.view_layer.objects:
            obj.select_set(obj in objects)
        meshes = [o for o in objects if o.type == 'MESH']
        if meshes:
            context.view_layer.objects.active = meshes[0]
        lines = check(objects)
        self.report({'INFO'}, " · ".join(lines))
        pairs, _new, _old = match(objects, scene=context.scene)
        if pairs:
            bpy.ops.render_studio.cad_take_over('INVOKE_DEFAULT')
        return {'FINISHED'}


class RS_OT_cad_take_over(bpy.types.Operator):
    bl_idname = "render_studio.cad_take_over"
    bl_label = "Take Over New Version"
    bl_description = ("The selected objects are the new CAD version: they get the materials and collections "
                      "of the parts with the same name; the old parts go into the archive collection")
    bl_options = {'UNDO'}

    materials: BoolProperty(name="Materials", default=True)
    collections: BoolProperty(name="Collections", description="Into the collections of the old parts",
                                        default=True)
    old: EnumProperty(name="Old Parts", items=[
        ('ARCHIVE', "Archive", "Into the archive collection (hidden); you delete them yourself"),
        ('KEEP', "Keep", "Leave them where they are"),
        ('DELETE', "Delete", "Delete them now"),
    ], default='ARCHIVE')

    @classmethod
    def poll(cls, context):
        return any(o.type == 'MESH' for o in context.selected_objects)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        pairs, lonely_new, lonely_old = match(context.selected_objects, scene=context.scene)
        col = self.layout.column()
        col.label(text="%d parts found in the old version" % len(pairs), icon='LINKED')
        for new, previous in pairs[:6]:
            col.label(text="%s  ←  %s" % (new.name, previous.name), translate=False)
        if len(pairs) > 6:
            col.label(text="… and %d more" % (len(pairs) - 6))
        if lonely_new:
            col.label(text="%d new parts without a partner (e.g. %s)" % (len(lonely_new), lonely_new[0].name),
                      icon='INFO', translate=False)
        if lonely_old:
            col.label(text="%d old parts not in the new version" % len(lonely_old), icon='INFO')
        col.separator()
        col.prop(self, "materials")
        col.prop(self, "collections")
        col.row().prop(self, "old", expand=True)

    def execute(self, context):
        pairs, _new, _old = match(context.selected_objects, scene=context.scene)
        if not pairs:
            self.report({'WARNING'}, "No parts with the same name in the scene")
            return {'CANCELLED'}
        take_over(pairs, self.materials, self.collections, self.old, context.scene)
        where = {"ARCHIVE": "old parts in %s" % archive_collection_name(), "KEEP": "old parts kept",
                 "DELETE": "old parts deleted"}[self.old]
        self.report({'INFO'}, "%d parts taken over, %s" % (len(pairs), where))
        return {'FINISHED'}


def draw(layout):
    row = layout.row(align=True)
    row.operator(RS_OT_cad_import.bl_idname, text="Import CAD", icon='IMPORT')
    row.operator(RS_OT_cad_take_over.bl_idname, text="New Version", icon='FILE_REFRESH')

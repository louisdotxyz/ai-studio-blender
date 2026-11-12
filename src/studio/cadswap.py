# SPDX-License-Identifier: GPL-3.0-or-later
"""CAD versions: import a model with checks, take a new version over from the old one
(materials, collections, modifiers and settings by part), keep replaced objects in the archive
collection (one child collection per CAD version), and the temporary collection for helper objects.

Parts are paired by their CAD id (obj["cad_id"], from the sidecar, see cadfiles) or else by name,
compared without the endings importers add: "Gabel.001", "Gabel <1>", "Gabel (2)". Older exports
that had the material in the name ("Leuchte | Front PETG milchig") still pair with the id
"Leuchte | Front". Replaced objects are never deleted unless asked: they go into the archive
collection (Preferences › Studio, default "Archive"), hidden in viewport and render.
The whole import with the sidecar is in cadimport.
"""

import os
import re

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import cadfiles
from .cadfiles import part_name   # noqa: F401  (kept here for older callers)
from .cameras import archive_collection_name, temp_collection_name

ARCHIVED_FROM = "as_archived_from"   # collections the object was in before it was archived
TEMP_KEY = "as_temp"                 # marks the temporary collection the add-on empties
CAD_ID = "cad_id"                    # the part's stable id from the CAD side
CAD_VERSION = "cad_version"          # "V3": the CAD version the object came with
# What the user set on a part that a new version keeps (besides materials and collections).
VISIBILITY = ("hide_render", "visible_camera", "visible_diffuse", "visible_glossy", "visible_transmission",
              "visible_volume_scatter", "visible_shadow", "is_shadow_catcher", "is_holdout", "pass_index",
              "display_type", "show_wire")

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


def part_key(obj):
    """What a part is paired by: its CAD id, else its name (both without importer endings)."""
    return part_name(obj.get(CAD_ID) or obj.name)


def _archived(obj):
    coll = bpy.data.collections.get(archive_collection_name())
    return coll is not None and obj.name in coll.all_objects


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
        old_by_part.setdefault(part_key(obj), []).append(obj)
    pairs, lonely_new = [], []
    for obj in new_objects:
        olds = old_by_part.get(part_key(obj))
        if olds:
            # Same name apart from Blender's ".001" first ("Gabel <2>" keeps to "Gabel <2>").
            plain = re.sub(r"\.\d{3}$", "", obj.name)
            previous = next((o for o in olds if re.sub(r"\.\d{3}$", "", o.name) == plain), olds[0])
            olds.remove(previous)
            pairs.append((obj, previous))
        else:
            lonely_new.append(obj)
    # Older exports had the material in the name: "Leuchte | Front PETG milchig" is the part with
    # the id "Leuchte | Front" when it is the only such old part. Longer ids pick first.
    still = []
    for obj in sorted(lonely_new, key=lambda o: -len(part_key(o))):
        key = part_key(obj) + " "
        hits = [(k, o) for k, olds in old_by_part.items() if k.startswith(key)
                for o in olds if not o.get(CAD_ID)] if obj.get(CAD_ID) else []
        if len(hits) == 1:
            k, previous = hits[0]
            old_by_part[k].remove(previous)
            pairs.append((obj, previous))
        else:
            still.append(obj)
    lonely_new = sorted(still, key=lambda o: o.name)
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


def copy_modifier(source, target):
    """A copy of the modifier `source` on the object `target` (settings and node inputs)."""
    new = target.modifiers.new(source.name, source.type)
    for prop in source.bl_rna.properties:
        if prop.is_readonly or prop.identifier in ("name", "type"):
            continue
        try:
            setattr(new, prop.identifier, getattr(source, prop.identifier))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass
    if source.type == 'NODES':   # the group's inputs are ID properties of the modifier
        for key in source.keys():
            try:
                new[key] = source[key]
            except (TypeError, ValueError):
                pass
    return new


def copy_setup(source, target, skip_parents=()):
    """What the user set on the old part besides materials: modifiers (when the new part has
    none), smooth shading, the parent, render visibility and custom properties.
    Returns the names of what was copied."""
    done = []
    if source.modifiers and not target.modifiers:
        for mod in source.modifiers:
            copy_modifier(mod, target)
        done.append("modifiers")
    src_mesh, dst_mesh = getattr(source, "data", None), getattr(target, "data", None)
    if (isinstance(src_mesh, bpy.types.Mesh) and isinstance(dst_mesh, bpy.types.Mesh) and src_mesh.polygons
            and dst_mesh.polygons and not dst_mesh.has_custom_normals):
        smooth = sum(p.use_smooth for p in src_mesh.polygons) / len(src_mesh.polygons)
        if smooth > 0.5 and not any(p.use_smooth for p in dst_mesh.polygons):
            dst_mesh.shade_smooth()
            done.append("smooth")
    parent = source.parent
    if parent is not None and target.parent is None and parent not in skip_parents and not _archived(parent):
        world = target.matrix_world.copy()
        target.parent = parent
        target.matrix_world = world
        done.append("parent")
    changed = False
    for attr in VISIBILITY:
        if hasattr(source, attr) and getattr(source, attr) != getattr(target, attr):
            setattr(target, attr, getattr(source, attr))
            changed = True
    if changed:
        done.append("visibility")
    keys = [k for k in source.keys() if not k.startswith(("cad_", "_")) and k != ARCHIVED_FROM and k not in target]
    for key in keys:
        target[key] = source[key]
    if keys:
        done.append("properties")
    return done


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


def archive(objects, scene=None, version=""):
    """Move objects into the archive collection (hidden; delete them there yourself), with a
    version ("V2") into its child collection "Archive V2". Returns how many were moved."""
    coll = archive_collection(scene)
    target = coll
    if version:
        name = "%s %s" % (coll.name, version)
        target = bpy.data.collections.get(name) or bpy.data.collections.new(name)
        if target.name not in coll.children:
            coll.children.link(target)
    count = 0
    for obj in objects:
        if obj.name in coll.all_objects:
            continue
        obj[ARCHIVED_FROM] = ", ".join(c.name for c in obj.users_collection)
        for other in list(obj.users_collection):
            other.objects.unlink(obj)
        target.objects.link(obj)
        count += 1
    return count


def take_over(pairs, materials=True, collections=True, old="ARCHIVE", scene=None, setup=True):
    """New version takes over from the old one: materials, collections and (setup) modifiers,
    smooth shading, parent, visibility, custom properties; the old objects go into the archive
    (old='ARCHIVE', by their CAD version), are deleted (old='DELETE') or stay ('KEEP')."""
    scene = scene or bpy.context.scene
    done = 0
    olds = [previous for _new, previous in pairs]
    for new, previous in pairs:
        if materials:
            copy_materials(previous, new)
        if setup:
            copy_setup(previous, new, skip_parents=olds)
        if collections:
            targets = [c for c in previous.users_collection if c.name != archive_collection_name()]
            if targets:
                for coll in list(new.users_collection):
                    coll.objects.unlink(new)
                for coll in targets:
                    coll.objects.link(new)
        done += 1
    if old == 'ARCHIVE':
        by_version = {}
        for obj in olds:
            by_version.setdefault(str(obj.get(CAD_VERSION, "")), []).append(obj)
        for version, objects in sorted(by_version.items()):
            archive(objects, scene, version)
    elif old == 'DELETE':
        for obj in olds:
            bpy.data.objects.remove(obj)
    return done


def import_model(path):
    """Import a CAD export by its extension (STEP through OCP, see cadfiles.step_to_glb; Onshape's
    glTF text saved as .glb works too). Returns the new objects."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".iges", ".igs"):
        raise ValueError("Blender cannot import IGES: export STEP or GLB from the CAD program")
    if ext in cadfiles.STEP_EXTS:
        try:
            path = cadfiles.step_to_glb(path)
        except RuntimeError as ex:
            raise ValueError(str(ex))
        ext = ".glb"
    if ext not in IMPORTERS:
        raise ValueError("Unknown format %s" % ext)
    path = cadfiles.importable_copy(path)
    ext = os.path.splitext(path)[1].lower()
    before = set(bpy.data.objects)
    IMPORTERS[ext](path)
    return [o for o in bpy.data.objects if o not in before]


def check(objects, floor=True):
    """Short report: size, floor (floor=False: the model is not meant to stand on Z = 0),
    triangles, generic names."""
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
    if floor and abs(low.z) > 0.001:
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
    bl_description = ("Bring a model from CAD into Blender (Onshape export, STEP, GLB, OBJ, STL …). Checks size, "
                      "floor and names. Is an older version already here, the new parts keep its materials. "
                      "A <name>.cad.json next to the file adds part names, materials and mounting points")
    bl_options = {'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_glob: StringProperty(default="*.glb;*.gltf;*.obj;*.stl;*.fbx;*.ply;*.usd;*.usdc;*.usdz;*.abc;*.step;*.stp",
                                          options={'HIDDEN'})

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = os.path.expanduser("~/Downloads/")
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from . import cadimport
        try:
            objects, sidecar = cadimport.import_tagged(self.filepath)
        except Exception as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        for obj in context.view_layer.objects:
            obj.select_set(obj in objects)
        meshes = [o for o in objects if o.type == 'MESH']
        if meshes:
            context.view_layer.objects.active = meshes[0]
        lines = check(objects, floor=bool((sidecar or {}).get("floor", sidecar is None)))
        off, missing = cadimport.size_check(objects, sidecar)
        lines += ["%s off by %.2f mm" % (o["part"], o["max_mm"]) for o in off[:3]]
        if missing:
            lines.append("%d parts of the sidecar missing" % len(missing))
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
    setup: BoolProperty(name="Modifiers and Settings",
                        description="Modifiers, smooth shading, parent, render visibility and custom properties "
                                    "of the old parts", default=True)
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
        col.prop(self, "setup")
        col.row().prop(self, "old", expand=True)

    def execute(self, context):
        from . import cadimport
        pairs, lonely_new, _old = match(context.selected_objects, scene=context.scene)
        if not pairs:
            self.report({'WARNING'}, "No parts with the same name in the scene")
            return {'CANCELLED'}
        take_over(pairs, self.materials, self.collections, self.old, context.scene, setup=self.setup)
        if self.materials:
            cadimport.assign_materials(lonely_new)   # new parts: base material from the sidecar
        where = {"ARCHIVE": "old parts in %s" % archive_collection_name(), "KEEP": "old parts kept",
                 "DELETE": "old parts deleted"}[self.old]
        self.report({'INFO'}, "%d parts taken over, %s" % (len(pairs), where))
        return {'FINISHED'}


def draw(layout):
    """The CAD box (Render tab): bring a CAD model in, swap in a new version, talk to the CAD side."""
    box = layout.box()
    row = box.row(align=True)
    row.operator(RS_OT_cad_import.bl_idname, text="Import CAD", icon='IMPORT')
    row.operator(RS_OT_cad_take_over.bl_idname, text="New Version", icon='FILE_REFRESH')
    from . import onshape_ops
    onshape_ops.draw_comments(box, bpy.context)

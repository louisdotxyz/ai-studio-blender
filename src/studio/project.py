# SPDX-License-Identifier: GPL-3.0-or-later
"""Tools for a whole project file (several scenes, CAD versions, a big archive), for the Project menu
(the gear next to the tabs) and for Claude (api.*):

- sync_scenes: the look, the scene's own properties (Glow …) and light values from one scene into the
  others (lights matched by name without the scene's suffix: "light present" ↔ "light pack");
- copy_scene: a full copy named with a word ("present") instead of ".001", also its collections,
  objects and materials;
- CAD rules, kept in the file (text "AI Studio CAD Rules"): which parts get which material and which
  empty they hang on; every CAD import applies them;
- the archive: what is in it per version, and old versions moved out into their own .blend;
- light_link: a light that only lights some objects, nested collections made flat for it;
- material_slider: one slider (a property of every scene) that drives a value in several materials.
"""

import json
import os
import re

import bpy
from bpy.props import StringProperty
from bpy.types import Operator, Panel

RULES_TEXT = "AI Studio CAD Rules"


# ------------------------------------------------------------------ scenes

def _base(name, suffix):
    """"light present" → "light" (suffix "present"), "Licht.001" → "Licht"."""
    name = re.sub(r"\.\d{3}$", "", name)
    if suffix and name.lower().endswith(" " + suffix.lower()):
        return name[:-len(suffix) - 1]
    return name


def _suffix(scene):
    """The word a scene's own objects end with: the last word of the scene's name ("VELO present" →
    "present"); "" for one word."""
    words = scene.name.split()
    return words[-1] if len(words) > 1 else ""


def sync_scenes(source=None, targets=None, look=True, props=True, lights=True):
    """Copy from `source` (default: the open scene) into `targets` (default: all others). Returns lines."""
    source = source or bpy.context.scene
    targets = [t for t in (targets or bpy.data.scenes) if t != source]
    report = []
    src_lights = {_base(o.name, _suffix(source)): o for o in source.objects if o.type == 'LIGHT'}
    for scene in targets:
        done = []
        if props:
            for key, value in source.items():
                if key.startswith(("_", "studio", "cycles")):
                    continue
                scene[key] = value
            done.append("properties")
        if look:
            for attr in ("look_glow", "look_contrast", "look_vignette", "look_saturation"):
                setattr(scene.studio, attr, getattr(source.studio, attr))
            view, src_view = scene.view_settings, source.view_settings
            for attr in ("exposure", "gamma", "view_transform", "look"):
                try:
                    setattr(view, attr, getattr(src_view, attr))
                except (TypeError, AttributeError):
                    pass
            done.append("look")
        if lights:
            n = 0
            for obj in scene.objects:
                if obj.type != 'LIGHT':
                    continue
                twin = src_lights.get(_base(obj.name, _suffix(scene)))
                if twin is None or twin.data == obj.data:
                    continue
                for attr in ("energy", "color", "size", "size_y", "spot_size", "spot_blend", "shadow_soft_size"):
                    if hasattr(obj.data, attr) and hasattr(twin.data, attr):
                        setattr(obj.data, attr, getattr(twin.data, attr))
                n += 1
            done.append("%d lights" % n)
        report.append("%s: %s" % (scene.name, ", ".join(done)))
    return report


def copy_scene(word, scene=None):
    """A full copy of the scene named "<name> <word>"; its new objects, collections, materials and
    lights end with " <word>" instead of ".001". Returns the new scene."""
    scene = scene or bpy.context.scene
    word = word.strip()
    if not word:
        raise ValueError("A word for the copy is needed, e.g. present")
    before = {kind: set(getattr(bpy.data, kind)) for kind in ("objects", "collections", "materials", "lights",
                                                              "cameras", "meshes", "worlds")}
    window = bpy.context.window
    if window:
        window.scene = scene
    bpy.ops.scene.new(type='FULL_COPY')
    new = bpy.context.scene
    new.name = "%s %s" % (_base(scene.name, _suffix(scene)), word)
    for kind, old in before.items():
        for block in set(getattr(bpy.data, kind)) - old:
            base = _base(block.name, _suffix(scene))
            block.name = "%s %s" % (base, word)
    return new


# ------------------------------------------------------------------ CAD rules

def cad_rules():
    """[{"match": "Huelle", "material": "TPU", "parent": "Empty Pod"}, …] kept in the file."""
    text = bpy.data.texts.get(RULES_TEXT)
    if text is None:
        return []
    try:
        rules = json.loads(text.as_string() or "[]")
    except ValueError:
        return []
    return [r for r in rules if isinstance(r, dict) and r.get("match")]


def set_cad_rules(rules):
    text = bpy.data.texts.get(RULES_TEXT) or bpy.data.texts.new(RULES_TEXT)
    text.clear()
    text.write(json.dumps(rules, indent=1, ensure_ascii=False))
    return rules


def apply_cad_rules(objects):
    """Material and parent for new CAD parts after the rules. Returns lines of what was done."""
    done = []
    for rule in cad_rules():
        try:
            pattern = re.compile(rule["match"], re.I)
        except re.error:
            pattern = re.compile(re.escape(rule["match"]), re.I)
        material = bpy.data.materials.get(rule.get("material", "")) if rule.get("material") else None
        parent = bpy.data.objects.get(rule.get("parent", "")) if rule.get("parent") else None
        hits = [o for o in objects if pattern.search(o.name)]
        for obj in hits:
            if material is not None and obj.type == 'MESH':
                if obj.material_slots:
                    for slot in obj.material_slots:
                        slot.material = material
                else:
                    obj.data.materials.append(material)
            if parent is not None and obj.parent is None:
                matrix = obj.matrix_world.copy()
                obj.parent = parent
                obj.matrix_world = matrix
        if hits:
            done.append("%s: %d parts%s%s" % (rule["match"], len(hits), " → " + material.name if material else "",
                                              " on " + parent.name if parent else ""))
    return done


# ------------------------------------------------------------------ archive

def archive_overview(scene=None):
    """[(collection name, objects, triangles)] of the archive and its versions."""
    from .cadswap import archive_collection_name
    root = bpy.data.collections.get(archive_collection_name())
    if root is None:
        return []
    out = []
    for coll in [root] + list(root.children_recursive):
        objects = list(coll.objects)
        tris = sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in objects
                   if o.type == 'MESH' and o.data is not None)
        out.append((coll.name, len(objects), tris))
    return out


def export_archive(names, path=None, remove=False):
    """Write the archive collections `names` (with their objects) into their own .blend (next to the file:
    <file>_archiv.blend, kept up to date). remove=True then takes them out of this file. Returns the path."""
    colls = [bpy.data.collections[n] for n in names if n in bpy.data.collections]
    if not colls:
        raise ValueError("No such archive collection")
    if path is None:
        base = os.path.splitext(bpy.data.filepath)[0] if bpy.data.filepath else os.path.join(
            os.path.expanduser("~"), "untitled")
        path = base + "_archiv.blend"
    blocks = set(colls)
    for coll in colls:
        blocks.update(coll.all_objects)
    bpy.data.libraries.write(path, blocks, fake_user=True, compress=True)
    if remove:
        for coll in colls:
            for obj in list(coll.all_objects):
                bpy.data.objects.remove(obj)
            bpy.data.collections.remove(coll)
        bpy.data.orphans_purge(do_recursive=True)
    return path


# ------------------------------------------------------------------ light linking, sliders

def light_link(light, targets, name=None):
    """`light` lights only `targets` (objects or collections, nested ones made flat into one collection
    of their objects). Returns the receiver collection."""
    objects = []
    for target in targets:
        if isinstance(target, bpy.types.Collection):
            objects.extend(target.all_objects)
        else:
            objects.append(target)
    coll_name = name or "Light Linking | %s" % light.name
    coll = bpy.data.collections.get(coll_name) or bpy.data.collections.new(coll_name)
    for obj in list(coll.objects):
        coll.objects.unlink(obj)
    for child in list(coll.children):
        coll.children.unlink(child)
    for obj in dict.fromkeys(objects):
        coll.objects.link(obj)
    light.light_linking.receiver_collection = coll
    return coll


def material_slider(name, materials, socket, value=0.5, minimum=0.0, maximum=1.0):
    """One slider for several materials: a property `name` on every scene (shown under Scene ›
    Custom Properties) read by an Attribute node (View Layer) in each material, connected to `socket`
    (input name of the material's main shader, e.g. "Roughness"). Returns the materials changed."""
    changed = []
    for scene in bpy.data.scenes:
        if name not in scene:
            scene[name] = float(value)
        ui = scene.id_properties_ui(name)
        ui.update(min=minimum, max=maximum, soft_min=minimum, soft_max=maximum)
    for material in materials:
        if isinstance(material, str):
            material = bpy.data.materials.get(material)
        if material is None or not material.use_nodes:
            continue
        tree = material.node_tree
        shader = next((n for n in tree.nodes if n.type == 'BSDF_PRINCIPLED'), None) or next(
            (n for n in tree.nodes if socket in n.inputs), None)
        if shader is None or socket not in shader.inputs:
            continue
        node = tree.nodes.get("Slider " + name) or tree.nodes.new('ShaderNodeAttribute')
        node.name = node.label = "Slider " + name
        node.attribute_type = 'VIEW_LAYER'
        node.attribute_name = name
        node.location = (shader.location.x - 300, shader.location.y - 200)
        tree.links.new(node.outputs["Fac"], shader.inputs[socket])
        changed.append(material.name)
    return changed


# ------------------------------------------------------------------ UI: the Project menu

class STUDIO_OT_project(Operator):
    bl_idname = "studio.project"
    bl_label = "Project"
    bl_options = {'UNDO', 'INTERNAL'}

    action: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})     # SYNC / COPY / EXPORT
    value: StringProperty(name="Word", default="present", options={'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return {"SYNC": "Copy this scene's look, its own properties and light values into the other scenes",
                "COPY": "A full copy of this scene named with a word (\"present\") instead of .001",
                "EXPORT": "Write this archive version into <file>_archiv.blend and take it out of this file "
                          "(the file gets smaller; the parts stay in the archive file)"}.get(
                              props.action.split(":")[0], "")

    def invoke(self, context, event):
        if self.action == 'COPY':
            return context.window_manager.invoke_props_dialog(self)
        if self.action.startswith('EXPORT:'):
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        try:
            if self.action == 'SYNC':
                lines = sync_scenes()
                self.report({'INFO'}, "; ".join(lines) or "No other scene")
            elif self.action == 'COPY':
                new = copy_scene(self.value)
                self.report({'INFO'}, "Scene %s" % new.name)
            elif self.action.startswith('EXPORT:'):
                path = export_archive([self.action[7:]], remove=True)
                self.report({'INFO'}, "Moved into %s" % os.path.basename(path))
        except (ValueError, RuntimeError, KeyError) as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class STUDIO_PT_project(Panel):
    """The gear next to the tabs: scenes, archive, CAD rules of this file."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Project"
    bl_ui_units_x = 16

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Scenes (%d)" % len(bpy.data.scenes), icon='SCENE_DATA')
        row = box.row(align=True)
        row.operator(STUDIO_OT_project.bl_idname, text="Sync Into the Others", icon='FILE_REFRESH').action = 'SYNC'
        row.operator(STUDIO_OT_project.bl_idname, text="Copy Scene", icon='DUPLICATE').action = 'COPY'

        box = layout.box()
        overview = archive_overview()
        box.label(text="Archive: %d objects" % sum(n for _c, n, _t in overview[:1]) if overview else "Archive: empty",
                  icon='FILE_FOLDER')
        for name, count, tris in overview[1:12]:
            row = box.row(align=True)
            row.label(text="%s · %d objects · %s tris" % (name, count, format(tris, ",").replace(",", "'")),
                      translate=False)
            row.operator(STUDIO_OT_project.bl_idname, text="", icon='EXPORT').action = 'EXPORT:' + name

        box = layout.box()
        rules = cad_rules()
        box.label(text="CAD Rules (%d)" % len(rules), icon='FILE_REFRESH')
        for rule in rules[:8]:
            sub = box.row()
            sub.active = False
            sub.label(text="%s → %s%s" % (rule["match"], rule.get("material", "–"),
                                         " · " + rule["parent"] if rule.get("parent") else ""), translate=False)
        hint = box.row()
        hint.active = False
        hint.label(text="Ask Claude: \"…Huelle… always TPU\" (kept in this file)")


classes = (STUDIO_OT_project, STUDIO_PT_project)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

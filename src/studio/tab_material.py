# SPDX-License-Identifier: GPL-3.0-or-later
"""The Material tab: the active material, quick settings, the library."""

import json
import os
import re
import uuid

import bpy
from bpy.props import (BoolProperty, EnumProperty, IntProperty, StringProperty)
from bpy.types import Menu, Operator, Panel

from . import cadswap, matkinds, matpresets, matstructures, runner
from .matpresets import _color_input, _principled
from .ui_common import MATERIAL_TAB_ORDER, _SidebarPanel, _unpin, section, studio_tab, subsection, tab_head


# Objects linked with the eyedropper form a group (same id in GROUP_KEY): Duplicate gives the
# whole group the copy, Unlink takes one object out of its group.
GROUP_KEY = "render_studio_material_group"


def material_group(context, obj, material):
    """The objects that change together with `obj`: its eyedropper group, else the selection."""
    gid = obj.get(GROUP_KEY)
    group = [o for o in context.scene.objects
             if gid and o.get(GROUP_KEY) == gid and getattr(o, "active_material", None) == material]
    if not group:
        group = [o for o in set(context.selected_objects) | {obj}
                 if hasattr(o.data, "materials") and o.active_material == material]
    return sorted(group, key=lambda o: o.name)


class RS_OT_material_duplicate(Operator):
    bl_idname = "render_studio.material_duplicate"
    bl_label = "Duplicate Material"
    bl_description = ("A copy of this material for this object and the objects linked with it "
                      "(eyedropper group, otherwise the selected objects)")
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.active_material is None:
            return {'CANCELLED'}
        group = material_group(context, obj, obj.active_material)
        copy = obj.active_material.copy()
        matpresets.assign(copy, group)
        self.report({'INFO'}, "%s for %d object%s" % (copy.name, len(group), "" if len(group) == 1 else "s"))
        return {'FINISHED'}


class RS_OT_material_unlink(Operator):
    bl_idname = "render_studio.material_unlink"
    bl_label = "Unlink Material"
    bl_description = ("Take this object out of its group: it gets its own copy of the material, "
                      "changes no longer affect the others")
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        obj = context.active_object
        material = obj.active_material if obj is not None else None
        if material is None:
            return {'CANCELLED'}
        if material.users <= 1:
            self.report({'INFO'}, "Only this object uses the material")
            return {'CANCELLED'}
        copy = material.copy()
        matpresets.assign(copy, [obj])
        obj.pop(GROUP_KEY, None)
        self.report({'INFO'}, "%s uses %s now" % (obj.name, copy.name))
        return {'FINISHED'}


class RS_OT_open_shading(Operator):
    bl_idname = "render_studio.open_shading"
    bl_label = "Open in Shading"
    bl_description = "Switch to the Shading workspace to edit the material nodes in depth"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        workspace = bpy.data.workspaces.get("Shading") or next(
            (w for w in bpy.data.workspaces if "shad" in w.name.lower()), None)
        if workspace is None:
            self.report({'WARNING'}, "No Shading workspace in this file")
            return {'CANCELLED'}
        context.window.workspace = workspace
        return {'FINISHED'}


class RS_OT_material_slot(Operator):
    bl_idname = "render_studio.material_slot"
    bl_label = "Material Slot"
    bl_description = "Edit this material of the object"
    bl_options = {'UNDO', 'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        obj = context.active_object
        if obj is None or not (0 <= self.index < len(obj.material_slots)):
            return {'CANCELLED'}
        obj.active_material_index = self.index
        return {'FINISHED'}


class RS_OT_material_preset(Operator):
    bl_idname = "render_studio.material_preset"
    bl_label = "Material Preset"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        values = matpresets.BUILTIN.get(props.name)
        if values is None:
            return "Your preset %s" % props.name
        return "%s: %s" % (props.name, ", ".join("%s %g" % kv for kv in values.items()))

    def execute(self, context):
        obj = context.active_object
        node = _principled(obj.active_material) if obj is not None else None
        if node is None or not matpresets.apply(node, self.name):
            return {'CANCELLED'}
        return {'FINISHED'}


class RS_OT_material_preset_save(Operator):
    bl_idname = "render_studio.material_preset_save"
    bl_label = "Save Material Preset"
    bl_description = "Save the settings of this Principled BSDF as a preset (available in every blend file)"
    bl_options = {'INTERNAL'}

    name: StringProperty(name="Name", default="My Material")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and _principled(obj.active_material) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        name = self.name.strip() or "My Material"
        if name in matpresets.BUILTIN:
            self.report({'ERROR'}, "%r is a built-in preset, pick another name" % name)
            return {'CANCELLED'}
        matpresets.save(_principled(context.active_object.active_material), name)
        return {'FINISHED'}


class RS_OT_material_preset_delete(Operator):
    bl_idname = "render_studio.material_preset_delete"
    bl_label = "Delete Material Preset"
    bl_description = "Delete this saved preset (materials keep their settings)"
    bl_options = {'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        matpresets.delete(self.name)
        return {'FINISHED'}


class RS_OT_material_viewport(Operator):
    bl_idname = "render_studio.material_viewport"
    bl_label = "Material Preview"
    bl_description = "Switch the 3D viewport to Material Preview shading"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        for area in context.screen.areas if context.screen else []:
            if area.type == 'VIEW_3D':
                area.spaces.active.shading.type = 'MATERIAL'
        return {'FINISHED'}


class RS_OT_material_presets_io(Operator):
    bl_idname = "render_studio.material_presets_io"
    bl_label = "Material Presets"
    bl_description = "Export your material presets to a .json file, or import presets from one"
    bl_options = {'INTERNAL'}

    action: StringProperty(default="EXPORT")   # EXPORT / IMPORT / OPEN
    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_glob: StringProperty(default="*.json", options={'HIDDEN'})

    def invoke(self, context, event):
        if self.action == "OPEN":
            return self.execute(context)
        if self.action == "EXPORT":
            self.filepath = "material_presets.json"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        import json
        if self.action == "OPEN":
            bpy.ops.wm.path_open(filepath=os.path.dirname(matpresets._file()))
            return {'FINISHED'}
        if self.action == "EXPORT":
            with open(bpy.path.ensure_ext(self.filepath, ".json"), "w", encoding="utf-8") as f:
                json.dump(matpresets.saved(), f, indent=1)
            self.report({'INFO'}, "%d presets exported" % len(matpresets.saved()))
            return {'FINISHED'}
        try:
            with open(self.filepath, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        if not isinstance(data, dict):
            self.report({'ERROR'}, "Not a presets file")
            return {'CANCELLED'}
        matpresets.saved().update(data)
        matpresets._write()
        self.report({'INFO'}, "%d presets imported" % len(data))
        return {'FINISHED'}


class RS_PT_material_settings(Panel):
    """Gear menu in the Material tab: shown values, preview, preset library."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Material Settings"
    bl_ui_units_x = 13

    def draw(self, context):
        layout = self.layout
        p = runner.prefs(context)
        if p is None:
            return
        box = layout.box()
        box.label(text="Preview", icon='MATERIAL')
        row = box.row(align=True)
        row.prop(p, "mat_preview", text="Show", toggle=True)
        sub = row.row(align=True)
        sub.active = p.mat_preview
        sub.prop(p, "mat_preview_size", expand=True)
        obj = context.active_object
        mat = obj.active_material if obj is not None else None
        if mat is not None:
            box.prop(mat, "preview_render_type", text="Shape")
        box.operator(RS_OT_material_viewport.bl_idname, icon='SHADING_TEXTURE')

        box = layout.box()
        box.label(text="Preset Library", icon='PRESET')
        box.prop(p, "material_preset_folder", text="")
        row = box.row(align=True)
        row.operator(RS_OT_material_presets_io.bl_idname, text="Import", icon='IMPORT').action = "IMPORT"
        row.operator(RS_OT_material_presets_io.bl_idname, text="Export", icon='EXPORT').action = "EXPORT"
        row.operator(RS_OT_material_presets_io.bl_idname, text="", icon='FILE_FOLDER').action = "OPEN"


class RS_OT_material_kind(Operator):
    bl_idname = "render_studio.material_kind"
    bl_label = "Material Kind"
    bl_description = ("What this material is. Sets what the kind fixes (a metal is metallic, glass lets light "
                      "through) and shows only the controls that fit")
    bl_options = {'UNDO', 'INTERNAL'}

    kind: EnumProperty(items=[(k, k, "") for k in matkinds.KINDS], options={'SKIP_SAVE'})

    def execute(self, context):
        material = current_material(context)
        if material is None:
            return {'CANCELLED'}
        matkinds.apply(material, _principled(material), self.kind)
        return {'FINISHED'}


def _value_row(col, socket, label):
    """One control: label on the left, value on the right. Something plugged in shows what feeds it."""
    split = col.split(factor=0.42, align=True)
    split.label(text=label)
    if socket.is_linked:
        src = socket.links[0].from_node
        sub = split.row(align=True)
        sub.active = False
        sub.label(text=src.label or src.name, icon='NODE_TEXTURE', translate=False)
    else:
        split.prop(socket, "default_value", text="", slider=socket.type == 'VALUE')


def _draw_kind_controls(box, material, node):
    """[Kind ▾], then the same few controls for every material, only the ones the kind has."""
    kind = material_kind(material)
    row = box.row(align=True)
    row.operator_menu_enum(RS_OT_material_kind.bl_idname, "kind", text=kind, icon='MATERIAL')
    col = box.column(align=True)
    for name, label in matkinds.CONTROLS.get(kind, matkinds.CONTROLS["Other"]):
        socket = node.inputs.get(name)
        if socket is not None:
            _value_row(col, socket, label)
    normal = node.inputs.get("Normal")
    if normal is not None and normal.is_linked:
        src = normal.links[0].from_node
        known = {matstructures.NODE_BUMP, matstructures.NODE_WEAR_BUMP}
        if "Strength" in src.inputs and src.name not in known:     # Normal Map / Bump node
            _value_row(col, src.inputs["Strength"], "Surface Depth")


def _draw_structure(layout, material):
    """The values of a structure (its node group's inputs) and how deep its surface goes."""
    for node in matstructures.control_nodes(material, wear=False):
        col = layout.column(align=True)
        col.separator(factor=0.5)
        col.label(text=node.label or node.name, icon='NODE_MATERIAL')
        for socket in node.inputs:
            if socket.is_linked or socket.type == 'VECTOR' or not socket.enabled:
                continue
            _value_row(col, socket, socket.name)
        bump = material.node_tree.nodes.get(matstructures.NODE_BUMP)
        if bump is not None:
            _value_row(col, bump.inputs["Strength"], "Surface Depth")


_wear_upgrading = set()


def _upgrade_wear_later(material):
    """An older wear layer is rebuilt outside of drawing (drawing must not change data)."""
    name = material.name
    if name in _wear_upgrading:
        return
    _wear_upgrading.add(name)

    def run():
        _wear_upgrading.discard(name)
        mat = bpy.data.materials.get(name)
        if mat is not None:
            matstructures.wear_node(mat)
        runner.tag_redraw()
    bpy.app.timers.register(run, first_interval=0.05)


WEAR_GROUPS = (
    ("Scratches", 'MOD_NOISE', "Scratches", (("Scratch Length", "Length"), ("One Direction", "One Direction"),
                                             ("Scratch Depth", "Depth"))),
    ("Fingerprints", 'HAND', "Fingerprints", (("Print Size", "Size"), ("Smear", "Smeared"))),
    ("Dust", 'PARTICLES', "Dust", (("Dust On Top", "Only on Top"), ("Dust Size", "Size"), ("Dust Color", "Color"))),
)


def draw_wear(layout, material):
    """Wear and tear: scratches, fingerprints and dust, each with its strength and its own kind, and where
    on the object they are (height from / to)."""
    has = matstructures.has_wear(material)
    box, head, is_open = section(layout, "wear", "Wear", 'MOD_NOISE', default_open=has)
    if not has:
        head.operator(RS_OT_material_wear.bl_idname, text="Add", icon='ADD')
        if is_open:
            sub = box.row()
            sub.active = False
            sub.label(text="Scratches, fingerprints and dust, procedural", icon='INFO')
        return
    head.operator(RS_OT_material_wear.bl_idname, text="", icon='X').remove = True
    if not is_open:
        return
    node = material.node_tree.nodes[matstructures.NODE_WEAR]
    if node.node_tree is None or node.node_tree.get(matstructures.VERSION_KEY) != matstructures.VERSION:
        _upgrade_wear_later(material)
    inputs = node.inputs
    for key, icon, label, extra in WEAR_GROUPS:
        if key not in inputs:
            continue
        col = box.column(align=True)
        strength = inputs[key]
        row = col.row(align=True)
        row.prop(strength, "default_value", text=label, slider=True, icon=icon)
        if strength.default_value > 0.001:
            for name, text in extra:
                if name in inputs and not inputs[name].is_linked:
                    _value_row(col, inputs[name], text)
    if "From" in inputs:
        col = box.column(align=True)
        col.label(text="Where (height on the object)", icon='EMPTY_SINGLE_ARROW')
        row = col.row(align=True)
        row.prop(inputs["From"], "default_value", text="From", slider=True)
        row.prop(inputs["To"], "default_value", text="To", slider=True)
    col = box.column(align=True)
    if "Scale" in inputs:
        _value_row(col, inputs["Scale"], "Scale")
    bump = material.node_tree.nodes.get(matstructures.NODE_WEAR_BUMP)
    if bump is not None:
        _value_row(col, bump.inputs["Strength"], "Depth")


class RS_OT_material_structure(Operator):
    bl_idname = "render_studio.material_structure"
    bl_label = "New Structure"
    bl_description = "A new procedural material to go on from, for the selected objects"
    bl_options = {'UNDO', 'INTERNAL'}

    kind: EnumProperty(items=matstructures.ITEMS, options={'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        text = dict((k, d) for k, _n, d in matstructures.ITEMS).get(props.kind, "")
        return text + ". Procedural (no images, no UVs): change its values here or open it in the Shader Editor"

    def execute(self, context):
        material = matstructures.create(self.kind)
        objects = set(context.selected_objects)
        if context.active_object is not None:
            objects.add(context.active_object)
        matpresets.assign(material, sorted(objects, key=lambda o: o.name))
        remember(material)
        return {'FINISHED'}


class RS_OT_material_wear(Operator):
    bl_idname = "render_studio.material_wear"
    bl_label = "Wear"
    bl_description = ("Scratches, fingerprints and dust over this material, each with its own amount "
                      "(procedural; what the material had stays)")
    bl_options = {'UNDO', 'INTERNAL'}

    remove: BoolProperty(default=False, options={'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return "Take the wear layer off this material" if props.remove else cls.bl_description

    @classmethod
    def poll(cls, context):
        return _principled(current_material(context)) is not None

    def execute(self, context):
        material = current_material(context)
        if self.remove:
            matstructures.remove_wear(material)
        else:
            matstructures.add_wear(material)
        return {'FINISHED'}


class RS_MT_material_new(Menu):
    """The "+" of the material library: a new material, empty or from a preset."""
    bl_idname = "RS_MT_material_new"
    bl_label = "New Material"

    def draw(self, context):
        layout = self.layout
        layout.operator(RS_OT_material_new.bl_idname, text="Empty", icon='MATERIAL').preset = ""
        layout.separator()
        layout.label(text="Structures (procedural)")
        for kind, name, _text in matstructures.ITEMS:
            layout.operator(RS_OT_material_structure.bl_idname, text=name, icon='NODE_MATERIAL').kind = kind
        layout.separator()
        layout.label(text="Simple")
        for name in matpresets.BUILTIN:
            layout.operator(RS_OT_material_new.bl_idname, text=name, icon='BLANK1').preset = name
        saved = sorted(matpresets.saved())
        if saved:
            layout.separator()
            for name in saved:
                layout.operator(RS_OT_material_new.bl_idname, text=name, icon='USER').preset = name
        layout.separator()
        layout.operator(RS_OT_material_preset_save.bl_idname, text="Save Current as Preset…", icon='ADD')
        if saved:
            layout.menu(RS_MT_material_presets_delete.bl_idname, icon='TRASH')


class RS_MT_material_more(Menu):
    """⋯ next to the material's name: what is needed less often."""
    bl_idname = "RS_MT_material_more"
    bl_label = "Material"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        material = current_material(context)
        has_slot = obj is not None and hasattr(obj.data, "materials")
        if has_slot:
            layout.operator(RS_OT_material_duplicate.bl_idname, text="Own Copy for This Object", icon='DUPLICATE')
        if material is not None:
            layout.operator(RS_OT_material_variant.bl_idname, text="New Variant", icon='COLOR')
        if has_slot and material is not None and material.users > 1:
            layout.operator(RS_OT_material_unlink.bl_idname, text="Unlink from the Others", icon='UNLINKED')
        layout.operator(RS_OT_pick_from_object.bl_idname, text="Pick Color from Object",
                        icon='EYEDROPPER').mode = 'COLOR'
        layout.separator()
        layout.menu(RS_MT_material_new.bl_idname, icon='ADD')
        if material is not None:
            layout.operator(RS_OT_material_to_base.bl_idname, icon='EXPORT')
        layout.operator(RS_OT_open_shading.bl_idname, text="Shader Editor", icon='NODE_MATERIAL')


class RS_OT_material_new(Operator):
    bl_idname = "render_studio.material_new"
    bl_label = "New Material"
    bl_description = "A new material for the selected objects"
    bl_options = {'UNDO', 'INTERNAL'}

    preset: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        if not props.preset:
            return "A new, plain material for the selected objects"
        values = matpresets.BUILTIN.get(props.preset)
        detail = ", ".join("%s %g" % kv for kv in values.items()) if values else "your preset"
        return "A new %s material for the selected objects (%s)" % (props.preset, detail)

    def execute(self, context):
        material = bpy.data.materials.new(self.preset or "Material")
        node = _principled(material)
        if self.preset and node is not None:
            matpresets.apply(node, self.preset)
        objects = set(context.selected_objects)
        if context.active_object is not None:
            objects.add(context.active_object)
        matpresets.assign(material, sorted(objects, key=lambda o: o.name))
        return {'FINISHED'}


class RS_OT_pick_from_object(Operator):
    """Eyedropper: click an object in the viewport. Material: the selected objects get the
    material of the face you clicked. Color: only its color goes into the active material."""
    bl_idname = "render_studio.pick_from_object"
    bl_label = "Pick from Object"
    bl_options = {'UNDO', 'INTERNAL'}

    mode: EnumProperty(items=(('MATERIAL', "Material", ""), ('COLOR', "Color", "")),
                       options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        if props.mode == 'COLOR':
            return "Take the color of another object: click it in the viewport (Esc cancels)"
        return "Use the material of another object: click it in the viewport (Esc cancels)"

    def invoke(self, context, event):
        context.window.cursor_modal_set('EYEDROPPER')
        context.workspace.status_text_set(
            "Click an object to take its %s · Esc cancels" % ("color" if self.mode == 'COLOR' else "material"))
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _end(self, context):
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._end(context)
            return {'CANCELLED'}
        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'RUNNING_MODAL'}
        self._end(context)
        material, source = _material_under_mouse(context, event)
        if material is None:
            self.report({'WARNING'}, "No object with a material under the mouse (click inside a 3D viewport)")
            return {'CANCELLED'}
        obj = context.active_object
        if self.mode == 'MATERIAL':
            objects = set(context.selected_objects)
            if obj is not None:
                objects.add(obj)
            objects.discard(source)
            if not objects:
                self.report({'WARNING'}, "Select the objects that should get the material first")
                return {'CANCELLED'}
            # Linked objects form a group with the one picked from (see material_group).
            gid = source.get(GROUP_KEY) or uuid.uuid4().hex[:12]
            source[GROUP_KEY] = gid
            for target in objects:
                target[GROUP_KEY] = gid
            matpresets.assign(material, sorted(objects, key=lambda o: o.name))
            self.report({'INFO'}, "%s → %d object%s" % (material.name, len(objects), "" if len(objects) == 1 else "s"))
        else:
            if obj is None or obj.active_material is None or obj.active_material == material:
                return {'CANCELLED'}
            _copy_color(material, obj.active_material)
        return {'FINISHED'}


def _material_under_mouse(context, event):
    """(material, object) of the face under the mouse in any 3D viewport of the window."""
    ray = _ray_under_mouse(context, event)
    return material_at_ray(context, *ray) if ray else (None, None)


def _ray_under_mouse(context, event):
    """(origin, direction) of the mouse in the 3D viewport it is over, or None."""
    from bpy_extras import view3d_utils
    x, y = event.mouse_x, event.mouse_y
    for area in context.window.screen.areas:
        if area.type != 'VIEW_3D':
            continue
        for region in area.regions:
            if region.type != 'WINDOW':
                continue
            if not (region.x <= x < region.x + region.width and region.y <= y < region.y + region.height):
                continue
            rv3d = area.spaces.active.region_3d
            coord = (x - region.x, y - region.y)
            origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
            direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)
            camera = context.scene.camera
            if rv3d.view_perspective == 'CAMERA' and not rv3d.is_perspective and camera is not None:
                # Orthographic camera view: Blender puts the ray start in the middle of the clip
                # range (500 m ahead with a clip end of 1 km) – move it back onto the camera plane.
                origin = origin - direction * (origin - camera.matrix_world.translation).dot(direction)
            return origin, direction
    return None


def slot_at_ray(context, origin, direction):
    """(object, slot index) of the face a ray hits first, or (None, -1)."""
    depsgraph = context.evaluated_depsgraph_get()
    hit, _loc, _normal, index, obj, _matrix = context.scene.ray_cast(depsgraph, origin, direction)
    if not hit or obj is None:
        return None, -1
    original = getattr(obj, "original", obj)
    data = getattr(original.evaluated_get(depsgraph), "data", None)
    slot = 0
    if isinstance(data, bpy.types.Mesh) and 0 <= index < len(data.polygons):
        slot = data.polygons[index].material_index
    return original, slot


def give_material(obj, slot, material):
    """Puts `material` into the slot of `obj` (a new slot if it has none)."""
    if not hasattr(obj.data, "materials"):
        return False
    if not obj.material_slots:
        obj.data.materials.append(material)
    else:
        obj.material_slots[min(slot, len(obj.material_slots) - 1)].material = material
    return True


def material_at_ray(context, origin, direction):
    """(material, object) of the face a ray hits first ((None, None) if it hits nothing)."""
    depsgraph = context.evaluated_depsgraph_get()
    hit, _loc, _normal, index, obj, _matrix = context.scene.ray_cast(depsgraph, origin, direction)
    if not hit or obj is None:
        return None, None
    original = getattr(obj, "original", obj)
    evaluated = original.evaluated_get(depsgraph)
    material = None
    data = getattr(evaluated, "data", None)
    if isinstance(data, bpy.types.Mesh) and 0 <= index < len(data.polygons):
        slot = data.polygons[index].material_index
        if slot < len(evaluated.material_slots):
            material = evaluated.material_slots[slot].material
    material = material or original.active_material
    return (getattr(material, "original", material) if material is not None else None), original


def _copy_color(source, target):
    """Only the color: the shader's color input (or the viewport color without nodes)."""
    src, dst = _color_input(source), _color_input(target)
    if src is not None and dst is not None:
        if dst.is_linked:
            dst.links[0].from_socket.node.id_data.links.remove(dst.links[0])
        dst.default_value = tuple(src.default_value)
    else:
        target.diffuse_color = source.diffuse_color


class RS_OT_material_to_base(Operator):
    bl_idname = "render_studio.material_to_base"
    bl_label = "Save to Library"
    bl_description = ("Save the material into your library, so every project can use it "
                      "(replaces one of the same name)")
    bl_options = {'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})   # empty: the current material

    def _material(self, context):
        return bpy.data.materials.get(self.name) if self.name else current_material(context)

    @classmethod
    def poll(cls, context):
        return bool(base_library_file())

    def invoke(self, context, event):
        material = self._material(context)
        if material is None:
            self.report({'WARNING'}, "No material to save")
            return {'CANCELLED'}
        return context.window_manager.invoke_confirm(
            self, event, title="Save to your library?",
            message="%s goes into %s" % (material.get(BASE_KEY) or material.name,
                                         os.path.basename(base_library_file())),
            confirm_text="Save")

    def execute(self, context):
        from . import baselib
        try:
            name = baselib.save(self._material(context))
        except Exception as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        self.report({'INFO'}, "%s saved to your library" % name)
        return {'FINISHED'}


class RS_OT_material_group_toggle(Operator):
    bl_idname = "render_studio.material_group_toggle"
    bl_label = "Show / Hide"
    bl_description = "Show or hide the materials of this group"
    bl_options = {'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        wm = context.window_manager
        names = set(filter(None, wm.render_studio_mat_open.split("\n")))
        names.symmetric_difference_update({self.name})
        wm.render_studio_mat_open = "\n".join(sorted(names))
        return {'FINISHED'}


class RS_MT_material_presets_delete(Menu):
    bl_idname = "RS_MT_material_presets_delete"
    bl_label = "Delete Preset"

    def draw(self, context):
        for name in sorted(matpresets.saved()):
            self.layout.operator(RS_OT_material_preset_delete.bl_idname, text=name, icon='X').name = name


class RS_OT_material_open(Operator):
    """A library material: click opens it to change it; then click an object in the viewport to give it
    the material (the face's slot). Esc, right-click or a click anywhere else: only open."""
    bl_idname = "render_studio.material_open"
    bl_label = "Open Material"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        material = bpy.data.materials.get(props.name)
        count = len(material_users().get(material, ())) if material is not None else 0
        used = "Used by %d object%s" % (count, "" if count == 1 else "s") if count else "Not used"
        return (props.name + "\n" + used + ".\nClick: open it to change it, then click an object in the viewport "
                "to give it this material. Ctrl: select the objects that use it. Right-click: more")

    def invoke(self, context, event):
        material = bpy.data.materials.get(self.name)
        if material is None:
            return {'CANCELLED'}
        if event.ctrl or event.oskey:
            return bpy.ops.render_studio.material_select(name=material.name)
        open_material(context, material)
        if context.window is None:
            return {'FINISHED'}
        context.window.cursor_modal_set('PAINT_BRUSH')
        context.workspace.status_text_set("Click an object to give it %s · Esc or a click elsewhere: just change it"
                                          % material.name)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        material = bpy.data.materials.get(self.name)
        if material is None:
            return {'CANCELLED'}
        open_material(context, material)
        return {'FINISHED'}

    def _end(self, context):
        context.window.cursor_modal_restore()
        context.workspace.status_text_set(None)

    def modal(self, context, event):
        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._end(context)
            return {'FINISHED'}
        if event.type != 'LEFTMOUSE' or event.value != 'PRESS':
            return {'PASS_THROUGH'}
        self._end(context)
        ray = _ray_under_mouse(context, event)
        if ray is None:                       # a click in a panel: that click counts, we are done
            return {'FINISHED', 'PASS_THROUGH'}
        obj, slot = slot_at_ray(context, *ray)
        material = bpy.data.materials.get(self.name)
        if obj is None or material is None or not give_material(obj, slot, material):
            return {'FINISHED'}
        _keep(material)
        self.report({'INFO'}, "%s → %s" % (material.name, obj.name))
        return {'FINISHED'}


class RS_OT_material_assign(Operator):
    bl_idname = "render_studio.material_assign"
    bl_label = "Assign Material"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    only_selected: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})
    select: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        material = bpy.data.materials.get(props.name)
        count = len(material_users().get(material, ())) if material is not None else 0
        used = "Used by %d object%s" % (count, "" if count == 1 else "s") if count else "Not used"
        return (props.name + "\n" + used + ".\nClick: every object with the current material switches to this one. "
                "Shift: only the selected objects. Ctrl: select the objects that use it. Right-click: more")

    def invoke(self, context, event):
        self.only_selected = event.shift
        self.select = event.ctrl or event.oskey
        return self.execute(context)

    def execute(self, context):
        material = bpy.data.materials.get(self.name)
        if material is None:
            return {'CANCELLED'}
        if self.select:
            return bpy.ops.render_studio.material_select(name=material.name)
        obj = context.active_object
        old = current_material(context)
        bare = obj is not None and obj.select_get() and hasattr(obj.data, "materials") and obj.active_material is None
        if (old is not None and old != material and not self.only_selected and not bare
                and old in material_users()):
            count = matpresets.replace(old, material)
            _keep(old)
            remember(material)
            self.report({'INFO'}, "%s → %s on %d object%s" % (old.name, material.name, count,
                                                              "" if count == 1 else "s"))
            return {'FINISHED'}
        objects = set(context.selected_objects)
        if obj is not None:
            objects.add(obj)
        matpresets.assign(material, sorted(objects, key=lambda o: o.name))
        remember(material)
        return {'FINISHED'}


# The material the tab works on when no object with a material is active (after a switch or
# a click in the library), so a library click can switch it without selecting an object first.
_current = [""]


def remember(material):
    _current[0] = material.name if material is not None else ""
    if material is not None:
        add_recent(material.name)


# A material opened from the library to change it, while this object was active (another object: gone)
_editing = {"name": "", "object": ""}


def open_material(context, material):
    obj = context.active_object
    _editing.update(name=material.name, object=obj.name if obj is not None else "")
    remember(material)


def current_material(context):
    obj = context.active_object
    opened = bpy.data.materials.get(_editing["name"]) if _editing["name"] else None
    if opened is not None and _editing["object"] == (obj.name if obj is not None else ""):
        return opened
    if obj is not None and hasattr(obj.data, "materials") and obj.active_material is not None:
        return obj.active_material
    return bpy.data.materials.get(_current[0]) if _current[0] else None


def material_users():
    """{material: {objects that have it in a slot}} for the whole file."""
    users = {}
    for obj in bpy.data.objects:
        for slot in obj.material_slots:
            if slot.material is not None:
                users.setdefault(slot.material, set()).add(obj)
    return users


def _keep(material):
    """A project material nobody uses any more stays in the file (to switch back);
    unused base copies come from the library again anyway."""
    if not material.users and not material.get(BASE_KEY):
        material.use_fake_user = True


def unused_materials():
    """Project materials no object uses (base copies and favorites excluded)."""
    used, favs = material_users(), favorites()
    return [m for m in _library_materials()
            if m not in used and m.library is None and not m.get(BASE_KEY) and m.name not in favs]


def _favorites_file():
    from .paths import data_dir
    return os.path.join(data_dir(), "material_favorites.json")


_favorites = []


def favorites():
    """Names of favorite materials, for every project (a base material stays a favorite)."""
    if not _favorites:
        try:
            with open(_favorites_file(), encoding="utf-8") as f:
                _favorites.append(set(json.load(f)))
        except (OSError, ValueError):
            _favorites.append(set())
    return _favorites[0]


def toggle_favorite(name):
    favs = favorites()
    favs.symmetric_difference_update({name})
    with open(_favorites_file(), "w", encoding="utf-8") as f:
        json.dump(sorted(favs), f, indent=1)
    return name in favs


RECENT_MAX = 8
_recent = []


def _recent_file():
    from .paths import data_dir
    return os.path.join(data_dir(), "material_recent.json")


def recent():
    """Names of the materials used last (newest first), for every project."""
    if not _recent:
        try:
            with open(_recent_file(), encoding="utf-8") as f:
                _recent.append([n for n in json.load(f) if isinstance(n, str)])
        except (OSError, ValueError):
            _recent.append([])
    return _recent[0]


def add_recent(name):
    names = recent()
    if names[:1] == [name]:
        return
    if name in names:
        names.remove(name)
    names.insert(0, name)
    del names[RECENT_MAX:]
    try:
        with open(_recent_file(), "w", encoding="utf-8") as f:
            json.dump(names, f, indent=1)
    except OSError:
        pass


# What a material is, for the library groups: a word in its name first, then its shader values.
KINDS = matkinds.KINDS
_KIND_WORDS = (   # (kind, exact words, word beginnings)
    ("Glass", {"pc", "pmma"}, ("glass", "glas", "acryl", "plexi", "clear", "klar", "transpar", "frosted",
                                "milchig", "satin", "diffus", "lens", "linse")),
    ("Light", {"led"}, ("light", "emiss", "leucht", "lamp", "glow")),
    ("Rubber", {"tpu", "tpe", "epdm"}, ("rubber", "gummi", "silikon", "silicon", "foam", "schaum")),
    ("Wood", {"mdf", "osb"}, ("wood", "holz", "oak", "eiche", "birch", "birke", "ash", "esche", "walnut",
                              "nuss", "plywood", "sperrholz", "furnier", "veneer", "bamboo", "bambus", "cork", "kork")),
    ("Fabric", set(), ("fabric", "stoff", "textil", "cloth", "felt", "filz", "leather", "leder", "mesh", "gewebe")),
    ("Metal", {"alu", "inox", "zink", "zinc"}, ("metal", "steel", "stahl", "alumin", "chrom", "brass", "messing",
                                                 "copper", "kupfer", "iron", "eisen", "titan", "gold", "silber",
                                                 "silver", "bronze", "nickel", "anodi", "eloxi", "kupfer")),
    ("Plastic", {"abs", "pla", "petg", "pp", "pe", "pa", "pa12", "pom", "asa", "pvc", "nylon"},
     ("plastic", "kunststoff", "polycarb", "resin", "harz", "lack", "paint", "powder", "pulver")),
)


def material_kind(material):
    """One of KINDS: words in the name (German or English) decide first, then the shader."""
    picked = matkinds.stored(material)
    if picked:
        return picked
    words = [w for w in re.split(r"[^a-z0-9äöü]+", material.name.lower()) if w]
    for kind, exact, starts in _KIND_WORDS:
        if any(w in exact or w.startswith(starts) for w in words):
            return kind
    node = _principled(material)
    if node is None:
        return "Other"

    def value(name, default=0.0):
        socket = node.inputs.get(name)
        if socket is None or socket.is_linked:
            return default
        try:
            return float(socket.default_value)
        except TypeError:
            return default
    if value("Transmission Weight") > 0.5:
        return "Glass"
    color = node.inputs.get("Emission Color")
    if value("Emission Strength") > 0 and color is not None and max(color.default_value[:3]) > 0.01:
        return "Light"
    if value("Metallic") > 0.5:
        return "Metal"
    return "Other"


def variant_name(material):
    base = re.sub(r" V\d+$", "", material.name)
    n = 2
    while bpy.data.materials.get("%s V%d" % (base, n)):
        n += 1
    return "%s V%d" % (base, n)


class RS_OT_material_variant(Operator):
    bl_idname = "render_studio.material_variant"
    bl_label = "Color Variant"
    bl_description = ("A copy of the current material to try another color: every object with it switches "
                      "to the copy, the original stays in the library")
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        old = current_material(context)
        if old is None:
            return {'CANCELLED'}
        copy = old.copy()
        copy.name = variant_name(old)
        copy.pop(BASE_KEY, None)    # a variant of a base material belongs to the project
        copy.use_fake_user = False
        count = matpresets.replace(old, copy)
        _keep(old)
        if not count:
            copy.use_fake_user = True
        remember(copy)
        self.report({'INFO'}, "%s for %d object%s" % (copy.name, count, "" if count == 1 else "s"))
        return {'FINISHED'}


class RS_OT_material_select(Operator):
    bl_idname = "render_studio.material_select"
    bl_label = "Select Objects"
    bl_description = "Select the objects that use this material and make it the current one"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        material = bpy.data.materials.get(self.name)
        if material is None:
            return {'CANCELLED'}
        remember(material)
        if context.mode != 'OBJECT':
            self.report({'INFO'}, "Selecting objects works in Object Mode")
            return {'FINISHED'}
        objects = [o for o in context.view_layer.objects if any(s.material == material for s in o.material_slots)]
        for obj in context.view_layer.objects:
            try:
                obj.select_set(obj in objects)
            except RuntimeError:
                pass
        if objects:
            first = sorted(objects, key=lambda o: o.name)[0]
            context.view_layer.objects.active = first
            first.active_material_index = next(i for i, s in enumerate(first.material_slots)
                                               if s.material == material)
        self.report({'INFO'}, "%d object%s with %s" % (len(objects), "" if len(objects) == 1 else "s",
                                                        material.name))
        return {'FINISHED'}


class RS_OT_material_favorite(Operator):
    bl_idname = "render_studio.material_favorite"
    bl_label = "Favorite"
    bl_description = "Add to or remove from the favorites at the top of the library (in every project)"
    bl_options = {'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        toggle_favorite(self.name)
        runner.tag_redraw()
        return {'FINISHED'}


class RS_OT_material_rename(Operator):
    bl_idname = "render_studio.material_rename"
    bl_label = "Rename Material"
    bl_description = "Rename this material (\"Part | Material\" puts it into the group \"Part\")"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    new_name: StringProperty(name="Name")

    def invoke(self, context, event):
        self.new_name = self.name
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        material = bpy.data.materials.get(self.name)
        if material is None or not self.new_name.strip():
            return {'CANCELLED'}
        was_current = _current[0] == material.name
        if self.name in favorites():
            toggle_favorite(self.name)
            material.name = self.new_name.strip()
            toggle_favorite(material.name)
        else:
            material.name = self.new_name.strip()
        if was_current:
            remember(material)
        return {'FINISHED'}


class RS_OT_material_delete(Operator):
    bl_idname = "render_studio.material_delete"
    bl_label = "Delete Material"
    bl_description = "Remove this material from the file"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        material = bpy.data.materials.get(self.name)
        if material is None:
            return {'CANCELLED'}
        count = len(material_users().get(material, ()))
        message = ("%d object%s lose%s it" % (count, "" if count == 1 else "s", "s" if count == 1 else "")
                   if count else "Not used by any object")
        return context.window_manager.invoke_confirm(self, event, title="Delete %s?" % material.name,
                                                     message=message, confirm_text="Delete")

    def execute(self, context):
        material = bpy.data.materials.get(self.name)
        if material is None:
            return {'CANCELLED'}
        bpy.data.materials.remove(material)
        return {'FINISHED'}


class RS_OT_material_clean_unused(Operator):
    bl_idname = "render_studio.material_clean_unused"
    bl_label = "Remove Unused"
    bl_description = "Remove the project materials no object uses (base materials and favorites stay)"
    bl_options = {'UNDO', 'INTERNAL'}

    def invoke(self, context, event):
        names = [m.name for m in unused_materials()]
        if not names:
            return {'CANCELLED'}
        return context.window_manager.invoke_confirm(
            self, event, title="Remove %d unused material%s?" % (len(names), "" if len(names) == 1 else "s"),
            message=", ".join(names[:8]) + (" …" if len(names) > 8 else ""), confirm_text="Remove")

    def execute(self, context):
        unused = unused_materials()
        for material in unused:
            bpy.data.materials.remove(material)
        self.report({'INFO'}, "%d material%s removed" % (len(unused), "" if len(unused) == 1 else "s"))
        return {'FINISHED'}


def draw_tile_menu(self, context):
    """Right-click on a library tile: the material's own entries in Blender's button menu."""
    op = getattr(context, "button_operator", None)
    if op is None or op.bl_rna.identifier not in {"RENDER_STUDIO_OT_material_assign", "RENDER_STUDIO_OT_material_open"}:
        return
    material = bpy.data.materials.get(op.name)
    if material is None:
        return
    layout = self.layout
    layout.separator()
    give = layout.operator(RS_OT_material_assign.bl_idname, text="Give to Selected Objects", icon='MATERIAL')
    give.name, give.only_selected = material.name, True
    layout.operator(RS_OT_material_assign.bl_idname, text="Replace the Current Material Everywhere",
                    icon='FILE_REFRESH').name = material.name
    layout.operator(RS_OT_material_select.bl_idname, icon='RESTRICT_SELECT_OFF').name = material.name
    fav = material.name in favorites()
    layout.operator(RS_OT_material_favorite.bl_idname, icon='SOLO_ON' if fav else 'SOLO_OFF',
                    text="Remove from Favorites" if fav else "Add to Favorites").name = material.name
    layout.operator(RS_OT_material_rename.bl_idname, text="Rename…", icon='GREASEPENCIL').name = material.name
    if base_library_file():
        layout.operator(RS_OT_material_to_base.bl_idname, icon='EXPORT').name = material.name
    if not material.get(BASE_KEY):
        layout.operator(RS_OT_material_delete.bl_idname, text="Delete…", icon='TRASH').name = material.name


def _library_materials():
    """Every material of the file that can go on a mesh (no Grease Pencil)."""
    return sorted((m for m in bpy.data.materials if not m.is_grease_pencil), key=lambda m: m.name.lower())


PROJECT_GROUP = "Project"


# Base materials for every project: a .blend with material assets (Preferences › Studio, or a
# "Base Materials.blend" in one of your asset libraries; tools/make_base_materials.py makes one).
# Copied into the file when the group is opened; the copies nobody uses are not saved with the file.
BASE_FILE = "Base Materials.blend"
BASE_KEY = "render_studio_base"


def base_library_file():
    p = runner.prefs()
    chosen = bpy.path.abspath(p.material_library) if p is not None and p.material_library else ""
    if chosen and os.path.isfile(os.path.expanduser(chosen)):
        return os.path.expanduser(chosen)
    for library in bpy.context.preferences.filepaths.asset_libraries:
        path = os.path.join(bpy.path.abspath(library.path), BASE_FILE)
        if os.path.isfile(path):
            return path
    return ""


def base_group():
    """The group's name in the library: the file's name, e.g. "Base Materials"."""
    path = base_library_file()
    return os.path.splitext(os.path.basename(path))[0] if path else "Base Materials"


_base_pending = [False]
_base_made = [False]   # tried once this session


def _ensure_base_library():
    """No base materials yet: make them in the background the first time the tab shows its
    library (glass, metals, plastics … in the add-on's data folder). Not part of the setup."""
    if _base_made[0] or bpy.app.background or base_library_file():
        return
    _base_made[0] = True

    def start():
        from .. import setup_flow
        setup_flow.start(("materials",))
        return None
    bpy.app.timers.register(start, first_interval=0.5)


_base_loaded = set()   # (blend file, library file, its mtime) already copied in


def _schedule_base_append():
    """Copy the base materials that are not in the file yet (after this redraw, once per
    library version and blend file)."""
    path = base_library_file()
    if _base_pending[0] or not path:
        return
    key = (bpy.data.filepath, path, os.path.getmtime(path))
    if key in _base_loaded and any(m.get(BASE_KEY) for m in bpy.data.materials):
        return
    _base_pending[0] = True

    def append():
        _base_pending[0] = False
        _base_loaded.add(key)
        have = {m.get(BASE_KEY) for m in bpy.data.materials if m.get(BASE_KEY)}
        with bpy.data.libraries.load(path, link=False, assets_only=True) as (source, target):
            target.materials = [name for name in source.materials if name not in have]
        for material in target.materials:
            if material is None:
                continue
            material[BASE_KEY] = material.name
            material.use_fake_user = False   # unused copies are not saved
            if material.asset_data is not None:
                material.asset_clear()
        runner.tag_redraw()
        return None
    bpy.app.timers.register(append, first_interval=0.0)


FAVORITES_GROUP = "Favorites"
RECENT_GROUP = "Recent"


def _in_source(material, source, used, favs=()):
    """Project: the file's own materials and the base copies it uses. Library: the base copies.
    Favorites: the ones with a heart."""
    if source == 'FAVORITES':
        return material.name in favs
    if source == 'LIBRARY':
        return bool(material.get(BASE_KEY))
    return not material.get(BASE_KEY) or material in used


def _short(material):
    """ "Driver X | Rubber" shows as "Rubber" in a tile (the full name is in its tooltip)."""
    head, sep, rest = material.name.partition(" | ")
    return rest.strip() if sep and rest.strip() else material.name


def library_kinds(source='PROJECT'):
    """The kinds that have materials in this source, in KINDS order (for the filter row)."""
    favs, used = favorites(), material_users()
    found = {material_kind(m) for m in _library_materials() if _in_source(m, source, used, favs)}
    return [kind for kind in KINDS if kind in found]


class RS_OT_material_kind_filter(Operator):
    bl_idname = "render_studio.material_kind_filter"
    bl_label = "Show Kind"
    bl_description = "Show only the materials of this kind (click again: all kinds)"
    bl_options = {'INTERNAL'}

    kind: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        wm = context.window_manager
        wm.render_studio_mat_kind = "" if self.kind in {wm.render_studio_mat_kind, "-"} else self.kind
        return {'FINISHED'}


class RS_MT_material_kind_filter(Menu):
    bl_idname = "RS_MT_material_kind_filter"
    bl_label = "Kind"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        layout.operator(RS_OT_material_kind_filter.bl_idname, text="All",
                        icon='CHECKMARK' if not wm.render_studio_mat_kind else 'BLANK1').kind = wm.render_studio_mat_kind or "-"
        for name in library_kinds(wm.render_studio_mat_source):
            layout.operator(RS_OT_material_kind_filter.bl_idname, text=name,
                            icon='CHECKMARK' if name == wm.render_studio_mat_kind else 'BLANK1').kind = name


def _library_groups(search="", source='PROJECT', kind=""):
    """[(group, [(material, short name)])]: favorites and recently used ones on top (from both sources),
    then the source's materials by kind (KINDS order)."""
    search = search.strip().lower()
    favs, used = favorites(), material_users()
    mats = [m for m in _library_materials() if not search or search in m.name.lower()]
    by_name = {m.name: m for m in mats}
    groups = []
    if kind:          # one kind: its materials, nothing else
        own = [(m, _short(m)) for m in mats if _in_source(m, source, used, favs) and material_kind(m) == kind]
        return [(kind, own)] if own else []
    top = [(m, _short(m)) for m in mats if m.name in favs]
    if top and source != 'FAVORITES':
        groups.append((FAVORITES_GROUP, top))
    last = [(by_name[n], _short(by_name[n])) for n in recent() if n in by_name and n not in favs]
    if last and not search and source != 'FAVORITES':
        groups.append((RECENT_GROUP, last))
    kinds = {}
    for m in mats:
        if _in_source(m, source, used, favs):
            kinds.setdefault(material_kind(m), []).append((m, _short(m)))
    groups += [(kind, kinds[kind]) for kind in KINDS if kind in kinds]
    return groups


LIBRARY_COLUMNS = 3


LIBRARY_ICON_SCALE = 4.4


class RS_PT_material(_SidebarPanel, Panel):
    """The Material tab: 1. preview and the active material, 2. quick settings,
    3. library (every material of the file) with the presets."""
    studio_tab = 'MATERIAL'
    bl_label = "Material"
    bl_options = {'HIDE_HEADER'}
    bl_order = MATERIAL_TAB_ORDER

    def draw(self, context):
        _unpin(self)
        layout = self.layout
        obj = context.active_object
        p = runner.prefs(context)
        has_slot = obj is not None and hasattr(obj.data, "materials")
        material = current_material(context)
        # the same head in every tab: [new ▾] [A] [B] [−] [+ variant] [settings]
        from . import variants_ab
        head, _props = tab_head(layout, RS_MT_material_new.bl_idname, "New Material", icon='MATERIAL')
        variants_ab.draw(head, context.scene, 'MATERIAL')
        head.popover(RS_PT_material_settings.__name__, text="", icon='PREFERENCES')
        box = layout.box()

        # 1. The material: [pick ▾][name][n×][eyedropper][⋯][gear], its preview, the slots
        row = box.row(align=True)
        if has_slot:
            browse = row.row(align=True)
            if material is not None:
                browse.ui_units_x = 1.6   # just the material's icon: the list of materials to pick from
            browse.prop(obj, "active_material", text="")
        if material is not None:
            row.prop(material, "name", text="", icon='NONE' if has_slot else 'MATERIAL')
            if material.users > 1:
                users = row.row(align=True)
                users.enabled = False
                users.label(text="%d×" % material.users)
        elif not has_slot:
            row.label(text="Click a material or an object", icon='INFO')
        row.operator(RS_OT_pick_from_object.bl_idname, text="", icon='EYEDROPPER').mode = 'MATERIAL'
        row.menu(RS_MT_material_more.bl_idname, text="", icon='THREE_DOTS')
        if has_slot and material is not None and material != obj.active_material:
            # opened from the library, not on the selected object: give it to it, or click an object
            hint = box.row(align=True)
            sub = hint.row(align=True)
            sub.active = False
            sub.label(text="Not on %s" % obj.name, icon='INFO', translate=False)
            give = hint.operator(RS_OT_material_assign.bl_idname, text="Give to It", icon='MATERIAL')
            give.name, give.only_selected = material.name, True
        if material is not None and (p is None or p.mat_preview):
            box.template_icon(icon_value=layout.icon(material),
                              scale=float(p.mat_preview_size) if p is not None else 8.0)
        if has_slot and len(obj.material_slots) > 1:
            grid = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
            for i, slot in enumerate(obj.material_slots):
                grid.operator(RS_OT_material_slot.bl_idname, text=slot.material.name if slot.material else "(empty)",
                              depress=(i == obj.active_material_index)).index = i

        # 2. Its values, right under it; its wear in a section of its own
        if material is not None:
            self._draw_settings(box, material, _principled(material), p)
            if _principled(material) is not None:
                draw_wear(layout, material)

        # 3. Library
        self._draw_library(layout, context, material, None)

    @staticmethod
    def _draw_settings(layout, material, node, p):
        tree = material.node_tree
        outputs = [n for n in tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial'] if tree else []
        out = next((n for n in outputs if n.is_active_output), outputs[0] if outputs else None)
        box = layout.column()
        box.separator(factor=0.5)
        if out is None:
            box.prop(material, "diffuse_color", text="Color")
            return
        surface = out.inputs["Surface"]
        principled = (surface.links[0].from_node if surface.is_linked
                      and surface.links[0].from_node.bl_idname == 'ShaderNodeBsdfPrincipled' else None)
        if principled is None:
            # no Principled BSDF (an emission stripe, a mix of shaders …): the shader's own settings
            box.template_node_view(tree, out, surface)
            return
        _draw_kind_controls(box, material, principled)
        _draw_structure(box, material)
        # The same view as Properties > Material > Surface: every setting of the shader, including
        # the nodes plugged into it.
        if subsection(box, "mat_all", "All Settings", default_open=p is not None and p.mat_view == 'FULL'):
            box.template_node_view(tree, out, surface)

    @staticmethod
    def _draw_library(layout, context, material, node):
        from ..i18n import tr
        wm = context.window_manager
        p = runner.prefs(context)
        box, head, is_open = section(layout, "library", "Library", 'ASSET_MANAGER')
        if p is not None:
            head.prop(p, "mat_library_view", expand=True, icon_only=True)
        if not is_open:
            return
        row = box.row(align=True)
        row.prop(wm, "render_studio_mat_source", expand=True)
        source = wm.render_studio_mat_source
        row = box.row(align=True)
        row.prop(wm, "render_studio_mat_search", text="", icon='VIEWZOOM')
        kinds_here = library_kinds(source)
        if len(kinds_here) > 1:     # filter by kind: a dropdown with only the kinds this source has
            sub = row.row(align=True)
            sub.ui_units_x = 4.5
            current = wm.render_studio_mat_kind if wm.render_studio_mat_kind in kinds_here else ""
            sub.menu(RS_MT_material_kind_filter.bl_idname, text=current or "All", icon='FILTER')
        unused = len(unused_materials()) if source == 'PROJECT' else 0
        if unused:
            row.operator(RS_OT_material_clean_unused.bl_idname, text=str(unused), icon='TRASH')
        search = wm.render_studio_mat_search
        kinds = library_kinds(source)
        kind = wm.render_studio_mat_kind if wm.render_studio_mat_kind in kinds else ""
        _ensure_base_library()
        from .. import setup_flow
        if setup_flow.running() and setup_flow.flow["step"] == "materials":
            box.label(text="Creating the base materials…", icon='TIME')
        if source in {'LIBRARY', 'FAVORITES'}:
            _schedule_base_append()   # copies the library's materials in (unused copies are not saved)
        groups = _library_groups(search, source, kind)
        if not groups:
            if source == 'LIBRARY' and base_library_file() and not search:
                box.label(text="Loading…", icon='TIME')
            else:
                box.label(text="No match" if search else "No materials yet", icon='INFO')
            return
        users = material_users()
        favs = favorites()
        closed = set(wm.render_studio_mat_open.split("\n"))
        tiles = p is None or p.mat_library_view == 'TILES'
        for group, items in groups:
            is_open = bool(search) or bool(kind) or group not in closed
            head = box.row(align=True)
            left = head.row(align=True)
            left.alignment = 'LEFT'
            left.emboss = 'NONE'
            left.operator(RS_OT_material_group_toggle.bl_idname, text=tr(group),
                          icon='DOWNARROW_HLT' if is_open else 'RIGHTARROW').name = group
            right = head.row(align=True)
            right.alignment = 'RIGHT'
            right.active = False
            right.label(text=str(len(items)))
            if not is_open:
                continue
            if tiles:
                grid = box.grid_flow(row_major=True, columns=LIBRARY_COLUMNS, even_columns=True, align=False)
                for mat, short in items:
                    count = len(users.get(mat, ()))
                    cell = grid.column(align=True)
                    cell.active = bool(count) or mat == material or bool(mat.get(BASE_KEY))
                    pic = cell.box() if mat == material else cell.column()
                    pic.template_icon(icon_value=layout.icon(mat), scale=LIBRARY_ICON_SCALE)
                    name = cell.row(align=True)
                    name.scale_y = 0.75
                    name.operator(RS_OT_material_open.bl_idname,
                                  text="%d× %s" % (count, short) if count else short,
                                  depress=(mat == material), emboss=(mat == material)).name = mat.name
                    heart = name.row(align=True)
                    heart.active = mat.name in favs
                    heart.operator(RS_OT_material_favorite.bl_idname, text="", emboss=False,
                                   icon='FUND' if mat.name in favs else 'HEART').name = mat.name
            else:
                col = box.column(align=True)
                for mat, _short_name in items:
                    count = len(users.get(mat, ()))
                    line = col.row(align=True)
                    line.active = bool(count) or mat == material or bool(mat.get(BASE_KEY))
                    line.operator(RS_OT_material_open.bl_idname, text=mat.name.replace(" | ", " · "),
                                  icon_value=layout.icon(mat), depress=(mat == material)).name = mat.name
                    info = line.row(align=True)
                    info.ui_units_x = 1.6
                    info.alignment = 'RIGHT'
                    info.label(text="%d×" % count if count else "")

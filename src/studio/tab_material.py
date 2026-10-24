# SPDX-License-Identifier: GPL-3.0-or-later
"""The Material tab: the active material, quick settings, the library."""

import os

import bpy
from bpy.props import (EnumProperty, IntProperty, StringProperty)
from bpy.types import Menu, Operator, Panel

from . import matpresets, runner
from .matpresets import _color_input, _principled
from .ui_common import MATERIAL_TAB_ORDER, _SidebarPanel, _unpin, studio_tab


class RS_OT_material_duplicate(Operator):
    bl_idname = "render_studio.material_duplicate"
    bl_label = "Duplicate Material"
    bl_description = "A copy of this material for the active object only"
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        obj = context.active_object
        if obj is None or obj.active_material is None:
            return {'CANCELLED'}
        copy = obj.active_material.copy()
        matpresets.assign(copy, [obj])
        self.report({'INFO'}, "%s created" % copy.name)
        return {'FINISHED'}


class RS_OT_material_unlink(Operator):
    bl_idname = "render_studio.material_unlink"
    bl_label = "Unlink Material"
    bl_description = ("The selected objects get their own copy of this material (shared among them), "
                      "so changes no longer affect the other objects")
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        obj = context.active_object
        material = obj.active_material if obj is not None else None
        if material is None:
            return {'CANCELLED'}
        group = [o for o in set(context.selected_objects) | {obj}
                 if hasattr(o.data, "materials") and o.active_material == material]
        if material.users <= len(group):
            self.report({'INFO'}, "Only these objects use the material")
            return {'CANCELLED'}
        copy = material.copy()
        matpresets.assign(copy, group)
        self.report({'INFO'}, "%d objects use %s now" % (len(group), copy.name))
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
        box.label(text="Shown Values", icon='PROPERTIES')
        box.row(align=True).prop(p, "mat_view", expand=True)
        grid = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
        grid.active = p.mat_view == 'COMPACT'
        for key in ("color", "roughness", "metallic", "normal", "emission", "transmission", "coat"):
            grid.prop(p, "mat_show_" + key, toggle=True)

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


_COMPACT_INPUTS = (
    ("color", ("Base Color",)),
    ("roughness", ("Roughness",)),
    ("metallic", ("Metallic",)),
    ("normal", ("Normal",)),
    ("emission", ("Emission Color", "Emission Strength")),
    ("transmission", ("Transmission Weight", "IOR")),
    ("coat", ("Coat Weight", "Coat Roughness")),
)


def _draw_compact_material(box, node, p):
    """Only the Principled inputs chosen in the Material settings; textures show as linked."""
    col = box.column(align=True)
    for key, names in _COMPACT_INPUTS:
        if not getattr(p, "mat_show_" + key):
            continue
        for name in names:
            socket = node.inputs.get(name)
            if socket is None:
                continue
            if socket.is_linked:
                src = socket.links[0].from_node
                if name == "Normal" and "Strength" in src.inputs:   # Normal Map / Bump node
                    col.prop(src.inputs["Strength"], "default_value", text="Normal Strength")
                else:
                    col.label(text="%s: %s" % (name, src.label or src.name), icon='NODE_TEXTURE')
            elif name != "Normal":
                col.prop(socket, "default_value", text=name)


class RS_MT_material_new(Menu):
    """The "+" of the material library: a new material, empty or from a preset."""
    bl_idname = "RS_MT_material_new"
    bl_label = "New Material"

    def draw(self, context):
        layout = self.layout
        layout.operator(RS_OT_material_new.bl_idname, text="Empty", icon='MATERIAL').preset = ""
        layout.separator()
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
        material.use_nodes = True
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
        material = _material_under_mouse(context, event)
        if material is None:
            self.report({'INFO'}, "No material there")
            return {'CANCELLED'}
        obj = context.active_object
        if self.mode == 'MATERIAL':
            objects = set(context.selected_objects)
            if obj is not None:
                objects.add(obj)
            matpresets.assign(material, sorted(objects, key=lambda o: o.name))
        else:
            if obj is None or obj.active_material is None or obj.active_material == material:
                return {'CANCELLED'}
            _copy_color(material, obj.active_material)
        return {'FINISHED'}


def _material_under_mouse(context, event):
    """The material of the face under the mouse in any 3D viewport of the window."""
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
            depsgraph = context.evaluated_depsgraph_get()
            hit, _loc, _normal, index, obj, _matrix = context.scene.ray_cast(depsgraph, origin, direction)
            if not hit or obj is None:
                return None
            evaluated = obj.evaluated_get(depsgraph)
            material = None
            data = getattr(evaluated, "data", None)
            if isinstance(data, bpy.types.Mesh) and 0 <= index < len(data.polygons):
                slot = data.polygons[index].material_index
                if slot < len(evaluated.material_slots):
                    material = evaluated.material_slots[slot].material
            material = material or evaluated.active_material
            return material.original if material is not None else None
    return None


def _copy_color(source, target):
    """Only the color: the shader's color input (or the viewport color without nodes)."""
    src, dst = _color_input(source), _color_input(target)
    if src is not None and dst is not None:
        if dst.is_linked:
            dst.links[0].from_socket.node.id_data.links.remove(dst.links[0])
        dst.default_value = tuple(src.default_value)
    else:
        target.diffuse_color = source.diffuse_color


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


class RS_OT_material_assign(Operator):
    bl_idname = "render_studio.material_assign"
    bl_label = "Assign Material"
    bl_description = "Put this material on the selected objects (their active slot)"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        material = bpy.data.materials.get(self.name)
        if material is None:
            return {'CANCELLED'}
        objects = set(context.selected_objects)
        if context.active_object is not None:
            objects.add(context.active_object)
        matpresets.assign(material, sorted(objects, key=lambda o: o.name))
        return {'FINISHED'}


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


def _library_groups():
    """[(group, [(material, short name)])]: the project's own materials, the base materials,
    and components: "Driver X | Rubber" belongs to the group "Driver X"."""
    groups = {}
    base = base_group()
    for mat in _library_materials():
        head, sep, rest = mat.name.partition(" | ")
        if mat.get(BASE_KEY):
            group, short = base, mat.name
        elif sep and rest.strip():
            group, short = head.strip(), rest.strip()
        else:
            group, short = PROJECT_GROUP, mat.name
        groups.setdefault(group, []).append((mat, short))
    if base not in groups and base_library_file():
        groups[base] = []   # shown closed; opening it copies the materials in
    order = sorted(groups, key=lambda g: (g != PROJECT_GROUP, g != base, g.lower()))
    return [(g, groups[g]) for g in order]


LIBRARY_COLUMNS = 4


LIBRARY_ICON_SCALE = 3.2


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
        box = layout.box()
        if obj is None or not hasattr(obj.data, "materials"):
            row = box.row()
            row.label(text="Select an object", icon='INFO')
            row.popover(RS_PT_material_settings.__name__, text="", icon='PREFERENCES')
            self._draw_library(layout, context, None, None)
            return
        material = obj.active_material
        node = _principled(material)

        # 1. Preview, the active material and how it is shared
        row = box.row()
        row.label(text=obj.name, icon='OBJECT_DATA')
        row.operator(RS_OT_open_shading.bl_idname, text="", icon='NODE_MATERIAL')
        row.popover(RS_PT_material_settings.__name__, text="", icon='PREFERENCES')
        if material is not None and (p is None or p.mat_preview):
            box.template_icon(icon_value=layout.icon(material),
                              scale=float(p.mat_preview_size) if p is not None else 8.0)
        if len(obj.material_slots) > 1:
            grid = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
            for i, slot in enumerate(obj.material_slots):
                grid.operator(RS_OT_material_slot.bl_idname, text=slot.material.name if slot.material else "(empty)",
                              depress=(i == obj.active_material_index)).index = i
        col = box.column(align=True)
        row = col.row(align=True)
        row.prop(obj, "active_material", text="")
        if material is not None and material.users > 1:
            users = row.row(align=True)
            users.enabled = False
            users.label(text="%d×" % material.users)
        row.operator(RS_OT_material_duplicate.bl_idname, text="", icon='DUPLICATE')
        sub = row.row(align=True)
        sub.enabled = material is not None and material.users > 1
        sub.operator(RS_OT_material_unlink.bl_idname, text="", icon='UNLINKED')
        row.operator(RS_OT_pick_from_object.bl_idname, text="", icon='EYEDROPPER').mode = 'MATERIAL'

        # 2. Quick settings
        if material is not None:
            self._draw_settings(layout, material, node, p)

        # 3. Library
        self._draw_library(layout, context, material, node)

    @staticmethod
    def _draw_settings(layout, material, node, p):
        tree = material.node_tree
        outputs = [n for n in tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial'] if tree else []
        out = next((n for n in outputs if n.is_active_output), outputs[0] if outputs else None)
        box = layout.box()
        row = box.row()
        row.label(text="Quick Settings", icon='PROPERTIES')
        if p is None or p.mat_view != 'COMPACT' or not p.mat_show_color:
            row.operator(RS_OT_pick_from_object.bl_idname, text="", icon='EYEDROPPER').mode = 'COLOR'
        if out is None:
            box.prop(material, "diffuse_color", text="Color")
            return
        surface = out.inputs["Surface"]
        principled = (surface.links[0].from_node if surface.is_linked
                      and surface.links[0].from_node.bl_idname == 'ShaderNodeBsdfPrincipled' else None)
        if p is not None and p.mat_view == 'COMPACT' and principled is not None:
            _draw_compact_material(box, principled, p)
            return
        # The same view as Properties > Material > Surface: every setting of the shader or
        # node group that feeds the output, including the nodes plugged into it.
        box.template_node_view(tree, out, surface)

    @staticmethod
    def _draw_library(layout, context, material, node):
        box = layout.box()
        row = box.row()
        row.label(text="Library", icon='ASSET_MANAGER')
        row.menu(RS_MT_material_new.bl_idname, text="", icon='ADD')
        groups = _library_groups()
        if not groups:
            box.label(text="No materials in this file", icon='INFO')
            return
        opened = set(context.window_manager.render_studio_mat_open.split("\n"))
        for group, items in groups:
            # The project's own materials are open unless closed, components closed unless opened.
            is_open = (group == PROJECT_GROUP) != (group in opened)
            if len(groups) > 1:
                head = box.row(align=True)
                head.emboss = 'NONE'
                head.operator(RS_OT_material_group_toggle.bl_idname,
                              text="%s  (%d)" % (group, len(items)) if items else group,
                              icon='DOWNARROW_HLT' if is_open else 'RIGHTARROW').name = group
            if not is_open:
                continue
            if group == base_group():
                _schedule_base_append()   # new ones in the library file
                if not items:
                    box.label(text="Loading…", icon='TIME')
                    continue
            grid = box.grid_flow(row_major=True, columns=LIBRARY_COLUMNS, even_columns=True, align=False)
            for mat, short in items:
                cell = grid.column(align=True)
                pic = cell.box() if mat == material else cell.column()
                pic.template_icon(icon_value=layout.icon(mat), scale=LIBRARY_ICON_SCALE)
                name = cell.row(align=True)
                name.scale_y = 0.75
                name.operator(RS_OT_material_assign.bl_idname, text=short, depress=(mat == material),
                              emboss=(mat == material)).name = mat.name

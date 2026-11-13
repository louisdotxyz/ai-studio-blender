# SPDX-License-Identifier: GPL-3.0-or-later
"""The Material tab: the active material, quick settings, the library."""

import json
import os
import re
import uuid

import bpy
from bpy.props import (BoolProperty, EnumProperty, IntProperty, StringProperty)
from bpy.types import Menu, Operator, Panel

from . import cadswap, matpresets, runner
from .matpresets import _color_input, _principled
from .ui_common import MATERIAL_TAB_ORDER, _SidebarPanel, _unpin, studio_tab


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
            return material_at_ray(context, origin, direction)
    return None, None


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


def current_material(context):
    obj = context.active_object
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
KINDS = ("Metal", "Plastic", "Glass", "Rubber", "Wood", "Fabric", "Light", "Other")
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
    if op is None or op.bl_rna.identifier != "RENDER_STUDIO_OT_material_assign":
        return
    material = bpy.data.materials.get(op.name)
    if material is None:
        return
    layout = self.layout
    layout.separator()
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


def _in_source(material, source, used):
    """Project: the file's own materials and the base copies it uses. Library: the base copies."""
    if source == 'LIBRARY':
        return bool(material.get(BASE_KEY))
    return not material.get(BASE_KEY) or material in used


def _short(material):
    """ "Driver X | Rubber" shows as "Rubber" in a tile (the full name is in its tooltip)."""
    head, sep, rest = material.name.partition(" | ")
    return rest.strip() if sep and rest.strip() else material.name


def _library_groups(search="", source='PROJECT'):
    """[(group, [(material, short name)])]: favorites and recently used ones on top (from both sources),
    then the source's materials by kind (KINDS order)."""
    search = search.strip().lower()
    favs, used = favorites(), material_users()
    mats = [m for m in _library_materials() if not search or search in m.name.lower()]
    by_name = {m.name: m for m in mats}
    groups = []
    top = [(m, _short(m)) for m in mats if m.name in favs]
    if top:
        groups.append((FAVORITES_GROUP, top))
    last = [(by_name[n], _short(by_name[n])) for n in recent() if n in by_name and n not in favs]
    if last and not search:
        groups.append((RECENT_GROUP, last))
    kinds = {}
    for m in mats:
        if _in_source(m, source, used):
            kinds.setdefault(material_kind(m), []).append((m, _short(m)))
    groups += [(kind, kinds[kind]) for kind in KINDS if kind in kinds]
    return groups


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
            # No object with materials: the current material (last switched or clicked) stays editable.
            material = current_material(context)
            row = box.row(align=True)
            if material is None:
                row.label(text="Select an object or click a material", icon='INFO')
            else:
                row.prop(material, "name", text="", icon='MATERIAL')
                row.operator(RS_OT_material_variant.bl_idname, text="", icon='COLOR')
            row.popover(RS_PT_material_settings.__name__, text="", icon='PREFERENCES')
            if material is not None:
                if p is None or p.mat_preview:
                    box.template_icon(icon_value=layout.icon(material),
                                      scale=float(p.mat_preview_size) if p is not None else 8.0)
                self._draw_settings(layout, material, _principled(material), p)
            self._draw_library(layout, context, material, None)
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
        browse = row.row(align=True)
        if material is not None:
            browse.ui_units_x = 1.6   # just the material's icon: the list of materials to pick from
        browse.prop(obj, "active_material", text="")
        if material is not None:
            row.prop(material, "name", text="")   # rename right here
        if material is not None and material.users > 1:
            users = row.row(align=True)
            users.enabled = False
            users.label(text="%d×" % material.users)
        row.operator(RS_OT_material_duplicate.bl_idname, text="", icon='DUPLICATE')
        sub = row.row(align=True)
        sub.enabled = material is not None
        sub.operator(RS_OT_material_variant.bl_idname, text="", icon='COLOR')
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
        from ..i18n import tr
        box = layout.box()
        wm = context.window_manager
        p = runner.prefs(context)
        row = box.row()
        row.label(text="Library", icon='ASSET_MANAGER')
        row.operator(RS_OT_material_to_base.bl_idname, text="", icon='EXPORT')
        row.menu(RS_MT_material_new.bl_idname, text="", icon='ADD')
        row = box.row(align=True)
        row.prop(wm, "render_studio_mat_source", expand=True)
        if p is not None:
            sub = row.row(align=True)
            sub.prop(p, "mat_library_view", expand=True, icon_only=True)
        source = wm.render_studio_mat_source
        row = box.row(align=True)
        row.prop(wm, "render_studio_mat_search", text="", icon='VIEWZOOM')
        unused = len(unused_materials()) if source == 'PROJECT' else 0
        if unused:
            row.operator(RS_OT_material_clean_unused.bl_idname, text=str(unused), icon='TRASH')
        search = wm.render_studio_mat_search
        _ensure_base_library()
        from .. import setup_flow
        if setup_flow.running() and setup_flow.flow["step"] == "materials":
            box.label(text="Creating the base materials…", icon='TIME')
        if source == 'LIBRARY':
            _schedule_base_append()   # copies the library's materials in (unused copies are not saved)
        groups = _library_groups(search, source)
        if not groups:
            if source == 'LIBRARY' and base_library_file() and not search:
                box.label(text="Loading…", icon='TIME')
            else:
                box.label(text="No match" if search else "No materials yet", icon='INFO')
            return
        users = material_users()
        closed = set(wm.render_studio_mat_open.split("\n"))
        tiles = p is None or p.mat_library_view == 'TILES'
        for group, items in groups:
            is_open = bool(search) or group not in closed
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
                    name.operator(RS_OT_material_assign.bl_idname,
                                  text="%d× %s" % (count, short) if count else short,
                                  depress=(mat == material), emboss=(mat == material)).name = mat.name
            else:
                col = box.column(align=True)
                for mat, _short_name in items:
                    count = len(users.get(mat, ()))
                    line = col.row(align=True)
                    line.active = bool(count) or mat == material or bool(mat.get(BASE_KEY))
                    line.operator(RS_OT_material_assign.bl_idname, text=mat.name.replace(" | ", " · "),
                                  icon_value=layout.icon(mat), depress=(mat == material)).name = mat.name
                    info = line.row(align=True)
                    info.ui_units_x = 1.6
                    info.alignment = 'RIGHT'
                    info.label(text="%d×" % count if count else "")

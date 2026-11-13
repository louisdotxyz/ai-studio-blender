"""Headless UI check: run every panel/popover/menu draw() of the extension with a fake
layout that validates property names, enum values and operator idnames.

    Blender -b --python tests/ui_check.py
(the extension must be installed and enabled in the profile Blender uses)
"""
import importlib
import sys
import traceback

import bpy

PKG = next(n for n in bpy.context.preferences.addons.keys() if n.endswith(".AI_Studio"))
errors = []


ICONS = {i.identifier for i in bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items}


class Fake:
    """Accepts any layout call; checks the ones that reference data, operators or icons."""

    def __init__(self, where):
        self._where = where

    def __getattribute__(self, name):
        attr = object.__getattribute__(self, name)
        if name.startswith("_") and name not in ("_any", "_prop", "_operator", "_menu"):
            return attr
        def checked(*args, **kwargs):
            icon = kwargs.get("icon")
            if isinstance(icon, str) and icon not in ICONS:
                errors.append(f"{object.__getattribute__(self, '_where')}: unknown icon {icon!r}")
            return attr(*args, **kwargs)
        return checked if callable(attr) else attr

    def __getattr__(self, name):
        if name in ("prop", "prop_enum", "prop_menu_enum", "props_enum"):
            return self._prop
        if name == "operator":
            return self._operator
        if name in ("menu", "popover"):
            return self._menu
        return self._any

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)

    def _any(self, *args, **kwargs):
        return Fake(self._where)

    def _prop(self, data, prop, *args, **kwargs):
        if data is None:
            errors.append(f"{self._where}: prop {prop!r} on None")
        elif prop not in data.bl_rna.properties:
            errors.append(f"{self._where}: {type(data).__name__} has no property {prop!r}")
        elif args and isinstance(args[0], str) and data.bl_rna.properties[prop].type == 'ENUM':
            items = [i.identifier for i in data.bl_rna.properties[prop].enum_items]
            if items and args[0] not in items:
                errors.append(f"{self._where}: {prop!r} has no item {args[0]!r}")
        return Fake(self._where)

    def _operator(self, idname, *args, **kwargs):
        mod, _, op = idname.partition(".")
        if not hasattr(getattr(bpy.ops, mod, None), op):
            errors.append(f"{self._where}: unknown operator {idname}")
        return OpProps(self._where, idname)

    def _menu(self, name, *args, **kwargs):
        if not hasattr(bpy.types, name):
            errors.append(f"{self._where}: unknown menu/panel {name}")
        return Fake(self._where)


class OpProps:
    def __init__(self, where, idname):
        object.__setattr__(self, "_where", where)
        mod, _, op = idname.partition(".")
        object.__setattr__(self, "_rna", getattr(getattr(bpy.ops, mod), op).get_rna_type())

    def __setattr__(self, name, value):
        if name not in self._rna.properties:
            errors.append(f"{self._where}: operator {self._rna.identifier} has no property {name!r}")


class FakeRegion:
    width, height, type, x, y = 420, 1300, 'UI', 0, 0
    active_panel_category = "Claude"


class Ctx:
    """bpy.context with a few UI members the panels expect (no window in background mode)."""

    def __getattr__(self, name):
        if name == "region":
            return FakeRegion()
        if name in ("space_data", "area", "window"):
            return None
        return getattr(bpy.context, name)


def _owner(cls, where):
    """Stands in for the panel instance: class attributes, methods bound to it."""
    import inspect

    class Owner:
        def __getattr__(self, name):
            static = inspect.getattr_static(cls, name)
            value = getattr(cls, name)
            if inspect.isfunction(static):
                return value.__get__(self)
            return value

    owner = Owner()
    owner.layout = Fake(where)
    return owner


def run(cls, ctx):
    owner = _owner(cls, cls.__name__)
    try:
        cls.draw(owner, ctx)
    except Exception:
        errors.append(f"{cls.__name__}: " + traceback.format_exc(limit=3).strip().splitlines()[-1])


ctx = Ctx()
classes = [c for c in list(bpy.types.Panel.__subclasses__()) + list(bpy.types.Menu.__subclasses__())
           if c.__module__.startswith(PKG)]
wm = bpy.context.window_manager
tabs = [i.identifier for i in wm.bl_rna.properties["render_studio_tab"].enum_items]
for tab in tabs:          # every Studio sub-tab
    wm.render_studio_tab = tab
    for cls in classes:
        run(cls, ctx)
scene = bpy.context.scene
for engine in ("CYCLES", "BLENDER_EEVEE"):   # engine-specific parts of the output popover
    try:
        scene.render.engine = engine
    except TypeError:
        continue
    for mode in ('NORMAL', 'TRANSPARENT'):
        scene.render_studio.background_mode = mode
        for fmt in ('PNG', 'JPEG', 'TIFF', 'EXR'):
            scene.render_studio.still_format = fmt
            for anim in ('PNG_SEQ', 'MP4', 'PRORES'):
                scene.render_studio.anim_format = anim
                run(bpy.types.RS_PT_output, ctx)
# gear menus with and without an active object / material
# chat with messages, tool steps (expanded and not), a render, and a version timeline
core = sys.modules[PKG + ".core"]
core.session_id = "uicheck"
core.messages[:] = [
    {"role": "user", "text": "Mach die Beize rot", "attachments": []},
    {"role": "tool", "text": "Beize auf Rot setzen", "code": True, "tool": "execute_python", "step": 1},
    {"role": "tool", "text": "Viewport angeschaut", "tool": "get_viewport_screenshot"},
    {"role": "assistant", "text": "Erledigt.", "version": "Beize", "stats": "12s · 240 geschrieben"},
    {"role": "user", "text": "Kamera frontal", "attachments": [], "expanded": True},
    {"role": "tool", "text": "Kamera frontal stellen", "code": True, "tool": "execute_python", "step": 2},
    {"role": "assistant", "text": "Kamera steht frontal.\nVersion: Kamera"},
]
core._take_version_word()
wm.render_studio_tab = 'RENDER'
for cls in [bpy.types.CLAUDE_PT_chat, bpy.types.CLAUDE_PT_settings]:
    run(cls, ctx)
for popover in ("RS_PT_frame", "RS_PT_light_settings", "RS_PT_material_settings", "RS_PT_output"):
    run(getattr(bpy.types, popover), ctx)
for view in ('COMPACT', 'FULL'):
    bpy.context.preferences.addons[PKG].preferences.studio.mat_view = view
    cube = bpy.data.objects.get("Cube")
    if cube is not None:
        bpy.context.view_layer.objects.active = cube
        if cube.active_material is None:
            cube.data.materials.append(bpy.data.materials.new("Test"))
        wm.render_studio_tab = 'MATERIAL'
        run(bpy.types.RS_PT_material, ctx)
# library: project / library, tiles / list; every tab in the simple view too
studio_prefs = bpy.context.preferences.addons[PKG].preferences.studio
for source in ('PROJECT', 'LIBRARY'):
    for lib_view in ('TILES', 'LIST'):
        wm.render_studio_mat_source = source
        studio_prefs.mat_library_view = lib_view
        wm.render_studio_tab = 'MATERIAL'
        run(bpy.types.RS_PT_material, ctx)
scene_rs = bpy.context.scene.render_studio
scene_rs.floor_mode = 'COVE'
scene_rs.haze_density = 0.02
scene_rs.background_mode = 'COLOR'
bpy.ops.render_studio.shot_add()
for simple in (False, True):
    studio_prefs.simple_view = simple
    for tab, panel in (('RENDER', "RS_PT_camera"), ('LIGHT', "RS_PT_lighting"), ('QUEUE', "RS_PT_render_queue")):
        wm.render_studio_tab = tab
        if hasattr(bpy.types, panel):
            run(getattr(bpy.types, panel), ctx)
    run(bpy.types.RS_PT_tabs, ctx)
studio_prefs.simple_view = False
wm.render_studio_sections = "lights\nworld\nbackdrop\nlibrary"   # every section closed
for tab, panel in (('LIGHT', "RS_PT_lighting"), ('MATERIAL', "RS_PT_material")):
    wm.render_studio_tab = tab
    run(getattr(bpy.types, panel), ctx)
wm.render_studio_sections = ""
scene_rs.floor_mode = 'NONE'
scene_rs.haze_density = 0.0
scene_rs.background_mode = 'NORMAL'
# the setup card while "Set Up Everything" runs (progress bar, then a text line)
import threading
flow, cli = sys.modules[PKG + ".setup_flow"], sys.modules[PKG + ".claude_cli"]
gate = threading.Event()
prefs = bpy.context.preferences.addons[PKG].preferences
prefs.setup_done = False
flow.flow["thread"] = threading.Thread(target=gate.wait)
flow.flow["thread"].start()
installer = cli.Installer(command="true", download_dir="/nonexistent")
installer.size = 300e6
for step in ("install", "login", "materials", "connect"):
    flow.flow.update(step=step, installer=installer)
    if flow.status() is None or (step == "install" and flow.status()[1] is None):
        errors.append("setup progress missing for " + step)
    run(bpy.types.CLAUDE_PT_chat, ctx)
gate.set()
flow.flow["thread"].join()
flow.flow.update(step="", installer=None)
run(bpy.types.CLAUDE_PT_chat, ctx)
# every section of the add-on's preferences (Chat, Files, Renders, Studio with Onshape, …)
addon_prefs = bpy.context.preferences.addons[PKG].preferences


class PrefsProxy:
    """The real preferences with a checking layout (their own layout is read-only)."""
    def __init__(self):
        object.__setattr__(self, "layout", Fake("AddonPreferences"))

    def __getattr__(self, name):
        attr = getattr(type(addon_prefs), name, None)
        if callable(attr) and not isinstance(attr, type):
            return attr.__get__(self)
        return getattr(addon_prefs, name)


# the CAD link is a skill now: no CAD box in the chat
if hasattr(wm, "claude_show_cad") or hasattr(importlib.import_module(PKG + ".studio.cadswap"), "draw_chat"):
    errors.append("the CAD box is still there")

# defaults (1.5): auto mode and the viewport screenshot are on for new users
rna = addon_prefs.bl_rna.properties
for key in ("auto_approve", "auto_screenshot"):
    if rna[key].default is not True:
        errors.append("default of %s should be on" % key)

keep_tab = addon_prefs.prefs_tab
for tab in [i.identifier for i in addon_prefs.bl_rna.properties["prefs_tab"].enum_items]:
    addon_prefs.prefs_tab = tab
    try:
        type(addon_prefs).draw(PrefsProxy(), ctx)
    except Exception:
        errors.append("preferences %s: " % tab + traceback.format_exc(limit=3).strip().splitlines()[-1])
addon_prefs.prefs_tab = keep_tab
print("UI_CHECKED", len(classes), "classes")
for e in sorted(set(errors)):
    print("UI_ERROR", e)
print("UI_OK" if not errors else f"UI_FAILED {len(set(errors))}")

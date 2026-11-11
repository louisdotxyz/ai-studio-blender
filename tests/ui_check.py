"""Headless UI check: run every panel/popover/menu draw() of the extension with a fake
layout that validates property names, enum values and operator idnames.

    Blender -b --python tests/ui_check.py
(the extension must be installed and enabled in the profile Blender uses)
"""
import sys
import traceback

import bpy

PKG = next(n for n in bpy.context.preferences.addons.keys() if n.endswith(".AI_Studio"))
errors = []


class Fake:
    """Accepts any layout call; checks the ones that reference data or operators."""

    def __init__(self, where):
        self._where = where

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
print("UI_CHECKED", len(classes), "classes")
for e in sorted(set(errors)):
    print("UI_ERROR", e)
print("UI_OK" if not errors else f"UI_FAILED {len(set(errors))}")

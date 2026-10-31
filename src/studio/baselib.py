# SPDX-License-Identifier: GPL-3.0-or-later
"""Change the base material library (the .blend with material assets, see tab_material).

The library is another file, so it is edited by a short background Blender: the material
is written to a temporary .blend, the library is opened in `Blender -b`, the material is
put in (replacing one of the same name), marked as an asset with a preview and saved.
Used by Claude (api.save_base_material / api.remove_base_material) and the Material tab.
"""

import json
import os
import subprocess
import tempfile

import bpy

# Runs inside the background Blender with the library open; arguments as JSON after "--".
_SCRIPT = r'''
import bpy, json, sys
args = json.loads(sys.argv[sys.argv.index("--") + 1])
name = args["name"]
old = bpy.data.materials.get(name)
catalog = old.asset_data.catalog_id if old is not None and old.asset_data else None
if catalog is None:
    catalog = next((m.asset_data.catalog_id for m in bpy.data.materials if m.asset_data), None)
if args.get("remove"):
    if old is not None:
        bpy.data.materials.remove(old)
else:
    with bpy.data.libraries.load(args["source"], link=False) as (src, dst):
        dst.materials = [args["source_name"]]
    new = dst.materials[0]
    if old is not None:
        old.user_remap(new)
        bpy.data.materials.remove(old)
    new.name = name
    new.use_fake_user = True
    for key in ("render_studio_base",):
        new.pop(key, None)
    new.asset_mark()
    if catalog:
        new.asset_data.catalog_id = catalog
    new.asset_generate_preview()
bpy.ops.wm.save_mainfile(compress=True)
print("BASELIB_OK")
'''


def _run(library, settings):
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(_SCRIPT)
        script = f.name
    try:
        result = subprocess.run(
            [bpy.app.binary_path, "-b", "--factory-startup", "-noaudio", library, "--python-exit-code", "3",
             "--python", script, "--", json.dumps(settings)],
            capture_output=True, text=True, timeout=180)
    finally:
        os.remove(script)
    if "BASELIB_OK" not in result.stdout:
        raise RuntimeError("The base material library could not be changed: "
                           + (result.stderr or result.stdout)[-400:])


def library_file():
    from .tab_material import base_library_file
    path = base_library_file()
    if not path:
        raise RuntimeError("No base material library: set one in Preferences › Studio")
    return path


def save(material, name=None):
    """Put `material` into the base library (replacing one of the same name). Returns the name."""
    from .tab_material import BASE_KEY
    name = name or material.get(BASE_KEY) or material.name
    library = library_file()
    tmp = os.path.join(tempfile.gettempdir(), "ai_studio_base_material.blend")
    bpy.data.libraries.write(tmp, {material}, fake_user=True, compress=False)
    try:
        _run(library, {"name": name, "source": tmp, "source_name": material.name})
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    material[BASE_KEY] = name     # this copy belongs to the base group now
    _forget_loaded()
    return name


def remove(name):
    """Take a material out of the base library."""
    _run(library_file(), {"name": name, "remove": True})
    _forget_loaded()


def _forget_loaded():
    from . import tab_material
    tab_material._base_loaded.clear()   # copy the changed library in again when the group opens

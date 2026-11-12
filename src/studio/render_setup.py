# SPDX-License-Identifier: GPL-3.0-or-later
"""One button: Blender set up for product renders (Render tab and setup card), and back.

What it sets, each with a sentence for people new to Blender:
Cycles on the graphics chip (Metal), samples for the viewport preview and the final image,
noise removal, AgX colors, millimeters, light bounces. The values before are kept in the
scene (and Cycles' device choice in this module's record in the scene too), so "Undo"
puts everything back as it was – also after saving and opening the file again.
"""

import json

import bpy

KEY = "as_product_setup_before"     # scene: {path: value before} as JSON

# (path on the scene, value, what it means)
SETTINGS = [
    ("render.engine", 'CYCLES', "Cycles: light behaves like in reality (reflections, soft shadows)"),
    ("cycles.device", 'GPU', "Renders on the graphics chip (much faster than the processor)"),
    ("cycles.preview_samples", 64, "Viewport preview: 64 samples, enough to judge light and materials"),
    ("cycles.use_preview_denoising", True, "Viewport preview without noise"),
    ("cycles.samples", 512, "Final image: up to 512 samples per pixel"),
    ("cycles.use_adaptive_sampling", True, "Stops early where the image is already clean (saves time)"),
    ("cycles.adaptive_threshold", 0.01, None),
    ("cycles.use_denoising", True, "Final image without noise (denoiser)"),
    ("cycles.max_bounces", 12, "Light bounces: 12 in all, enough for glass and shiny parts"),
    ("cycles.diffuse_bounces", 4, None),
    ("cycles.glossy_bounces", 8, None),
    ("cycles.transmission_bounces", 12, None),
    ("cycles.transparent_max_bounces", 16, None),
    ("cycles.sample_clamp_indirect", 10.0, "No single bright dots (fireflies)"),
    ("view_settings.view_transform", 'AgX', "AgX colors: bright parts stay natural instead of white"),
    ("view_settings.look", 'None', None),
    ("unit_settings.system", 'METRIC', None),
    ("unit_settings.length_unit", 'MILLIMETERS', "Lengths shown in millimeters, like in CAD"),
    ("unit_settings.scale_length", 1.0, None),
]


def _resolve(scene, path):
    owner = scene
    parts = path.split(".")
    for part in parts[:-1]:
        owner = getattr(owner, part, None)
        if owner is None:
            return None, parts[-1]
    return owner, parts[-1]


def _get(scene, path):
    owner, attr = _resolve(scene, path)
    return getattr(owner, attr) if owner is not None and hasattr(owner, attr) else None


def _set(scene, path, value):
    owner, attr = _resolve(scene, path)
    if owner is None or not hasattr(owner, attr):
        return False
    try:
        setattr(owner, attr, value)
        return True
    except (TypeError, ValueError, AttributeError):
        return False


def _cycles_prefs():
    try:
        return bpy.context.preferences.addons["cycles"].preferences
    except (KeyError, AttributeError):
        return None


def _gpu_on():
    """Cycles' device: Metal (on a Mac) with the GPUs on. Returns (before, sentence) or (None, None)."""
    cprefs = _cycles_prefs()
    if cprefs is None:
        return None, None
    before = {"type": cprefs.compute_device_type,
              "use": {d.id: bool(d.use) for d in getattr(cprefs, "devices", [])}}
    kind = None
    for candidate in ("METAL", "OPTIX", "CUDA", "HIP", "ONEAPI"):   # the list is dynamic: try them in turn
        try:
            cprefs.compute_device_type = candidate
            kind = candidate
            break
        except (TypeError, ValueError):
            continue
    if kind is None:
        return None, None
    try:
        if hasattr(cprefs, "refresh_devices"):
            cprefs.refresh_devices()
        devices = list(cprefs.devices)
        gpus = [d for d in devices if d.type != 'CPU']
        for d in devices:
            d.use = (d.type != 'CPU') if gpus else True
    except (TypeError, ValueError, AttributeError):
        return None, None
    bpy.context.preferences.is_dirty = True
    names = ", ".join(d.name for d in gpus) or "the processor"
    return before, "Graphics chip for Cycles: %s (%s)" % (names, kind.title())


def _gpu_back(before):
    cprefs = _cycles_prefs()
    if cprefs is None or not before:
        return
    try:
        cprefs.compute_device_type = before.get("type", 'NONE')
        for d in getattr(cprefs, "devices", []):
            if d.id in before.get("use", {}):
                d.use = before["use"][d.id]
    except (TypeError, ValueError, AttributeError):
        pass
    bpy.context.preferences.is_dirty = True


def is_set(scene):
    return KEY in scene


def apply(scene, gpu=True):
    """Set everything. Returns the sentences of what was set (for the chat / the report)."""
    before = json.loads(scene[KEY]) if KEY in scene else {}   # pressed twice: the first "before" stays
    said = []
    for path, value, sentence in SETTINGS:
        old = _get(scene, path)
        if old is None:
            continue
        if path not in before:
            before[path] = old if isinstance(old, (bool, int, float, str)) else str(old)
        if _set(scene, path, value) and sentence:
            said.append(sentence)
    if gpu and "_gpu" not in before:
        gpu_before, sentence = _gpu_on()
        if gpu_before is not None:
            before["_gpu"] = gpu_before
            said.insert(2, sentence)
    scene[KEY] = json.dumps(before)
    return said


def undo(scene):
    """Everything back to how it was before apply(). True when there was something to undo."""
    if KEY not in scene:
        return False
    before = json.loads(scene[KEY])
    for path, value in before.items():
        if path == "_gpu":
            _gpu_back(value)
        else:
            _set(scene, path, value)
    del scene[KEY]
    return True


class RS_OT_product_setup(bpy.types.Operator):
    bl_idname = "render_studio.product_setup"
    bl_label = "Set Up for Product Renders"
    bl_description = ("Sets Blender up for product images in one go: Cycles on the graphics chip, samples for "
                      "preview and final image, no noise, natural colors (AgX), millimeters, enough light "
                      "bounces for glass and metal. Undo puts everything back")
    bl_options = {'UNDO'}

    def execute(self, context):
        said = apply(context.scene)
        from .. import core
        core.add_message("info", "Set up for product renders:\n- " + "\n- ".join(said)
                         + "\n(Undo: the arrow next to the button in the Render tab)")
        self.report({'INFO'}, "Set up for product renders (%d settings)" % len(said))
        return {'FINISHED'}


class RS_OT_product_setup_undo(bpy.types.Operator):
    bl_idname = "render_studio.product_setup_undo"
    bl_label = "Undo Product Render Setup"
    bl_description = "Puts the render settings back to how they were before 'Set Up for Product Renders'"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return is_set(context.scene)

    def execute(self, context):
        undo(context.scene)
        self.report({'INFO'}, "Render settings as before")
        return {'FINISHED'}


def draw(layout, scene):
    """The button (or, once set, a line with Undo)."""
    row = layout.row(align=True)
    if is_set(scene):
        sub = row.row(align=True)
        sub.active = False
        sub.label(text="Set up for product renders", icon='CHECKMARK')
        row.operator(RS_OT_product_setup_undo.bl_idname, text="", icon='LOOP_BACK')
    else:
        row.scale_y = 1.2
        row.operator(RS_OT_product_setup.bl_idname, icon='RESTRICT_RENDER_OFF')


classes = (RS_OT_product_setup, RS_OT_product_setup_undo)

# SPDX-License-Identifier: GPL-3.0-or-later
"""The Refine tab: the finished render. Its preview, refining it with Krea, then adjusting it like a raw
photo (image_edit; saved as a new file next to it). The look of the live camera (exposure, glow …) is in the
Camera tab (look.draw); its saved looks and the viewport switch are defined here."""

import json
import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Menu, Operator, Panel

from . import image_edit, krea, krea_scene, look, runner, tab_queue
from .ui_common import section, subsection, tab_head

LOOK_KEYS = ("look_glow", "look_contrast", "look_vignette", "look_saturation")


# ------------------------------------------------------------------ look presets (built in + saved)

def _looks_file():
    from .paths import data_dir
    return os.path.join(data_dir(), "look_presets.json")


def saved_looks():
    try:
        with open(_looks_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def capture_look(scene):
    rq, view = scene.studio, scene.view_settings
    data = {key: getattr(rq, key) for key in LOOK_KEYS}
    data["exposure"] = view.exposure
    if hasattr(view, "use_white_balance"):
        data["white_balance"] = view.white_balance_temperature if view.use_white_balance else 0.0
        data["tint"] = view.white_balance_tint if view.use_white_balance else 0.0
    return data


def apply_look(scene, data):
    rq, view = scene.studio, scene.view_settings
    for key in LOOK_KEYS:
        if key in data:
            setattr(rq, key, data[key])
    if "exposure" in data:
        view.exposure = data["exposure"]
    if hasattr(view, "use_white_balance") and "white_balance" in data:
        view.use_white_balance = bool(data["white_balance"])
        if data["white_balance"]:
            view.white_balance_temperature = data["white_balance"]
            view.white_balance_tint = data.get("tint", 0.0)


class STUDIO_OT_look_saved(Operator):
    bl_idname = "studio.look_saved"
    bl_label = "Saved Look"
    bl_description = "Apply this saved look"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    delete: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        looks = saved_looks()
        if self.delete:
            looks.pop(self.name, None)
            with open(_looks_file(), "w", encoding="utf-8") as f:
                json.dump(looks, f, indent=1)
            return {'FINISHED'}
        if self.name not in looks:
            return {'CANCELLED'}
        apply_look(context.scene, looks[self.name])
        return {'FINISHED'}


class STUDIO_OT_look_save(Operator):
    bl_idname = "studio.look_save"
    bl_label = "Save Look"
    bl_description = "Save the look as it is now (for every project)"

    name: StringProperty(name="Name", default="My Look")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        name = self.name.strip() or "My Look"
        looks = saved_looks()
        looks[name] = capture_look(context.scene)
        with open(_looks_file(), "w", encoding="utf-8") as f:
            json.dump(looks, f, indent=1)
        self.report({'INFO'}, "Saved %r" % name)
        return {'FINISHED'}


class STUDIO_MT_look_presets(Menu):
    bl_idname = "STUDIO_MT_look_presets"
    bl_label = "Looks"

    def draw(self, context):
        layout = self.layout
        for name in look.PRESETS:
            layout.operator(look.STUDIO_OT_look_preset.bl_idname, text=name).name = name
        saved = sorted(saved_looks())
        if saved:
            layout.separator()
            for name in saved:
                layout.operator(STUDIO_OT_look_saved.bl_idname, text=name, translate=False, icon='USER').name = name
            layout.separator()
            for name in saved:
                op = layout.operator(STUDIO_OT_look_saved.bl_idname, text="Delete %s" % name, icon='TRASH')
                op.name, op.delete = name, True
        layout.separator()
        layout.operator(STUDIO_OT_look_save.bl_idname, text="Save Look…", icon='ADD')
        layout.operator(look.STUDIO_OT_look_reset.bl_idname, icon='LOOP_BACK')


# ------------------------------------------------------------------ live preview in the viewport

def _shading(context):
    space = context.space_data
    return space.shading if space is not None and space.type == 'VIEW_3D' else None


class STUDIO_OT_look_live(Operator):
    bl_idname = "studio.look_live"
    bl_label = "Look in the Viewport"
    bl_description = ("Show the look in the viewport (Blender's viewport compositor, in the camera view): "
                      "exposure and white balance are always visible in Rendered view")
    bl_options = {'INTERNAL'}

    def execute(self, context):
        shading = _shading(context)
        if shading is None or not hasattr(shading, "use_compositor"):
            self.report({'WARNING'}, "This Blender has no viewport compositor")
            return {'CANCELLED'}
        on = shading.use_compositor != 'DISABLED'
        shading.use_compositor = 'DISABLED' if on else 'CAMERA'
        if not on and shading.type not in {'MATERIAL', 'RENDERED'}:
            shading.type = 'RENDERED'
        return {'FINISHED'}


# ------------------------------------------------------------------ adjusting the finished image

def _edits_file():
    from .paths import data_dir
    return os.path.join(data_dir(), "edit_presets.json")


def saved_edits():
    try:
        with open(_edits_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_edits(data):
    with open(_edits_file(), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


class STUDIO_OT_edit_reset(Operator):
    bl_idname = "studio.edit_reset"
    bl_label = "Reset Adjustments"
    bl_description = "All adjustments back to 0: the preview shows the render as it is"
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        rs = context.scene.studio
        for key in image_edit.KEYS:
            rs.property_unset(key)
        image_edit.changed()
        return {'FINISHED'}


class STUDIO_OT_edit_look(Operator):
    bl_idname = "studio.edit_look"
    bl_label = "Look"
    bl_description = "A starting point for the picture: every slider can be moved afterwards"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        vals = dict(image_edit.LOOKS).get(self.name)
        if vals is None:
            return {'CANCELLED'}
        rs = context.scene.studio
        for key in image_edit.KEYS:
            setattr(rs, key, float(vals.get(key, 0.0)))
        return {'FINISHED'}


class STUDIO_OT_edit_saved(Operator):
    bl_idname = "studio.edit_saved"
    bl_label = "Saved Adjustments"
    bl_description = "Use these saved adjustments"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    delete: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        edits = saved_edits()
        if self.delete:
            edits.pop(self.name, None)
            _write_edits(edits)
            return {'FINISHED'}
        if self.name not in edits:
            return {'CANCELLED'}
        rs = context.scene.studio
        for key in image_edit.KEYS:
            setattr(rs, key, float(edits[self.name].get(key, 0.0)))
        return {'FINISHED'}


class STUDIO_OT_edit_save_preset(Operator):
    bl_idname = "studio.edit_save_preset"
    bl_label = "Save Adjustments"
    bl_description = "Save the adjustments as they are now (for every project)"

    name: StringProperty(name="Name", default="My Edit")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        name = self.name.strip() or "My Edit"
        edits = saved_edits()
        edits[name] = image_edit.values(context.scene)
        _write_edits(edits)
        self.report({'INFO'}, "Saved %r" % name)
        return {'FINISHED'}


class STUDIO_MT_edit_presets(Menu):
    bl_idname = "STUDIO_MT_edit_presets"
    bl_label = "Adjustments"

    def draw(self, context):
        layout = self.layout
        saved = sorted(saved_edits())
        for name in saved:
            layout.operator(STUDIO_OT_edit_saved.bl_idname, text=name, translate=False, icon='USER').name = name
        if saved:
            layout.separator()
            for name in saved:
                op = layout.operator(STUDIO_OT_edit_saved.bl_idname, text="Delete %s" % name, icon='TRASH')
                op.name, op.delete = name, True
            layout.separator()
        else:
            sub = layout.row()
            sub.active = False
            sub.label(text="No saved adjustments yet (+)")
        layout.operator(STUDIO_OT_edit_reset.bl_idname, icon='LOOP_BACK')


class STUDIO_OT_edit_export(Operator):
    bl_idname = "studio.edit_export"
    bl_label = "Save Image"
    bl_description = ("Save the render with these adjustments as a new file next to it (…_edit_01.png), at full "
                      "size; the render stays as it is")

    def execute(self, context):
        try:
            path = image_edit.save(context.scene, current_image(context.scene))
        except (ValueError, OSError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        runner._images_cache["time"] = 0.0
        self.report({'INFO'}, "Saved %s" % os.path.basename(path))
        return {'FINISHED'}


# ------------------------------------------------------------------ Krea

class STUDIO_OT_krea_product(Operator):
    bl_idname = "studio.krea_product"
    bl_label = "Product = Selection"
    bl_description = ("Remember the selected objects as the product: Krea gets their true size and the mask keeps "
                      "exactly them. Nothing selected: forget it (everything visible counts again)")
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        objects = [o for o in context.selected_objects
                   if o.type in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'}]
        krea_scene.mark_product(context.scene, objects)
        self.report({'INFO'}, "Product: %s" % (", ".join(o.name for o in objects[:4]) if objects else
                                                "everything visible"))
        return {'FINISHED'}


# Pictures for Krea by what they are for (Mockup › Pictures): stored as "ROLE|path" lines
ROLES = (
    ('ENV', "Environment", 'WORLD', "the place: put the product into this environment, with its light and perspective"),
    ('VIBE', "Vibe & Look", 'COLOR', "the mood: take its colors, contrast, grading and atmosphere, not its content"),
    ('LIGHT', "Light", 'LIGHT_SUN', "the light: match its direction, softness and time of day"),
    ('PRODUCT', "Product", 'MESH_CUBE', "product details: keep these materials, textures and details exactly"),
)
STYLES = (
    ('studio', "Studio", "A clean studio product photo on a seamless backdrop."),
    ('lifestyle', "Lifestyle", "A natural lifestyle photo, candid and believable."),
    ('editorial', "Editorial", "An editorial magazine look with a bold composition."),
    ('night', "Night", "At night, with city lights and cool tones."),
    ('film', "Film", "An analog film look: subtle grain, soft contrast, gentle colors."),
    ('minimal', "Minimal", "Minimal and calm, with a lot of empty space."),
    ('outdoor', "Outdoor", "Outdoors in natural daylight."),
    ('macro', "Macro", "A macro close-up with shallow depth of field."),
)


def references_with_roles(wm=None):
    """[(role, file)] of the pictures, in the order of ROLES (old lists without a role: Environment)."""
    wm = wm or bpy.context.window_manager
    found = []
    for line in krea._refs(wm.studio_krea_refs):
        role, _sep, path = line.partition("|") if "|" in line[:9] else ("ENV", "", line)
        found.append((role if role in dict((r[0], r) for r in ROLES) else 'ENV', path))
    order = [r[0] for r in ROLES]
    return sorted(found, key=lambda item: order.index(item[0]))


def references(wm=None):
    """The pictures added for Krea (files), in order."""
    return [path for _role, path in references_with_roles(wm)]


def _store_refs(wm, items):
    wm.studio_krea_refs = "\n".join("%s|%s" % (role, path) for role, path in items[:krea.MAX_REFERENCES])


def has_environment(wm):
    return any(role == 'ENV' for role, _p in references_with_roles(wm))


def wish(wm, prompt=None):
    """What Krea should do: the sentences of the pressed toggles, then the typed text."""
    parts = [text for key, _label, text, _p in krea.SCENE if getattr(wm, "studio_krea_" + key)]
    parts += [text for key, _label, text in STYLES if getattr(wm, "studio_krea_style_" + key, False)]
    roles = dict((r[0], r[3]) for r in ROLES)
    for number, (role, _path) in enumerate(references_with_roles(wm), 1):
        parts.append("Reference image %d is %s." % (number, roles[role]))
    typed = (wm.studio_krea_prompt if prompt is None else prompt).strip()
    if typed:
        parts.append(typed.rstrip(".") + ".")
    return " ".join(parts)


class STUDIO_OT_krea_frame(Operator):
    bl_idname = "studio.krea_frame"
    bl_label = "Camera for Krea"
    bl_description = "Click again for the same as the render"
    bl_options = {'INTERNAL'}

    target: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})      # "zoom" or "view"
    value: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        wm = context.window_manager
        prop = "studio_krea_" + self.target
        setattr(wm, prop, 'KEEP' if getattr(wm, prop) == self.value else self.value)
        return {'FINISHED'}


class STUDIO_OT_krea_reference(Operator):
    bl_idname = "studio.krea_reference"
    bl_label = "Add Pictures"
    bl_description = ("Pictures for Krea: a room or place (with \"Room\" the product goes into it), a hand pose, "
                      "props, a style")
    bl_options = {'INTERNAL'}

    directory: StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={'SKIP_SAVE'})
    filter_image: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    filter_folder: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    remove: IntProperty(default=-1, options={'HIDDEN', 'SKIP_SAVE'})
    role: StringProperty(default='ENV', options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        if props.remove >= 0:
            return "Take this picture out"
        return "Add a picture for %s" % next((r[3] for r in ROLES if r[0] == props.role), "Krea")

    def invoke(self, context, event):
        if self.remove >= 0:
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        wm = context.window_manager
        items = references_with_roles(wm)
        if self.remove >= 0:
            if self.remove < len(items):
                items.pop(self.remove)
        else:
            known = {p for _r, p in items}
            for f in self.files:
                path = os.path.join(self.directory, f.name)
                if f.name and os.path.isfile(path) and path not in known:
                    items.append((self.role, path))
        _store_refs(wm, items)
        return {'FINISHED'}


def _choices(prompt, model, environment, zoom, view, protect, region=None):
    """The tab's choices where an argument is None, the model Auto picks, the render and its size.
    environment: the reference pictures (a list, a path or None for the tab's)."""
    wm = bpy.context.window_manager
    refs = references(wm) if environment is None else krea._refs(environment)
    refs = [bpy.path.abspath(r) if not r.startswith("http") else r for r in refs]
    zoom = krea.zoom_key(zoom or 'KEEP')          # the camera moves in Blender (Adjust › Camera), not in Krea
    view = krea.view_key(view or 'KEEP')
    protect = wm.studio_krea_protect if protect is None else bool(protect)
    room = wm.studio_krea_room or has_environment(wm) or environment is not None   # a place to go into
    used = krea.pick_model((model or wm.studio_krea_model).upper(), bool(refs) and room, zoom, view, region)
    source = current_image(bpy.context.scene)
    if not source or not os.path.isfile(source):
        raise ValueError("Render an image first")
    for path in refs:
        if not path.startswith("http") and not os.path.isfile(path):
            raise ValueError("The picture is missing: %s" % path)
    width, height = krea.image_size(source)
    return prompt, used, refs, zoom, view, protect, source, width, height, room


def _touches_product(wm):
    """A hand or a person with the product: they may cover it, so it is not pasted back over them."""
    return any(getattr(wm, "studio_krea_" + key) for key, _l, _t, protect in krea.SCENE if not protect)


def _plan_secure(scene, source, used, protect):
    """Nano paints the whole picture anew: with "Keep Product" the product is put back from the render."""
    wm = bpy.context.window_manager
    mask = ""
    if used == 'NANO' and protect and not _touches_product(wm):
        try:
            folder = os.path.dirname(krea_scene.mask_path())
            mask = krea_scene.render_mask(scene, source, os.path.join(
                folder, "secure_%08x.png" % (hash(os.path.normpath(source)) & 0xffffffff)))
        except (ValueError, OSError, RuntimeError) as exc:
            print("ID Toolkit: product mask:", exc)
            mask = ""
    krea.plan_secure(source, mask)


def _scale_and_mask(scene, source, width, height, used, zoom, view, protect):
    """The sentence about the product's true size ("Lock Size") and, for Precise with "keep the product",
    the mask."""
    info = krea_scene.product_scale(scene, krea_scene.render_camera(scene, source), width, height)
    scale = ""
    if bpy.context.window_manager.studio_krea_scale:
        scale = krea_scene.scale_text(info, width, height, framing_kept=zoom == 'KEEP' and view == 'KEEP')
    mask = ""
    if used == 'PRECISE' and protect and info:
        mask = krea_scene.render_mask(scene, source)
    return scale, mask


def refine(scene, prompt, model=None, environment=None, zoom=None, view=None, protect=None, region=None):
    """Refines the render shown in the Refine tab with the API token; the choices of the tab where an
    argument is None. Returns (result path, model used, width, height)."""
    prompt, used, refs, zoom, view, protect, source, width, height, room = _choices(
        prompt, model, environment, zoom, view, protect, region)
    if not krea.token():
        raise ValueError("Add your Krea API token in Preferences › Studio (or refine with Claude: Krea plan)")
    if krea.busy():
        raise ValueError("Krea is still working on the last image")
    scale, mask = _scale_and_mask(scene, source, width, height, used, zoom, view, protect)
    _plan_secure(scene, source, used, protect)
    target, used = krea.start(used, prompt, source, width, height, refs, zoom, view, scale, mask, room, region)
    return target, used, width, height


_plan = {}     # the run being prepared with Claude: source, model, size
_plan_region = {"box": None}     # the marks' box when "With Krea" was sent from the Adjust tab


def prepare(scene, prompt, upload_url, model=None, environment=None, zoom=None, view=None, protect=None,
            region=None):
    """For Claude with the Krea connector: the full prompt (with the product's true scale), the render, the
    mask and the reference pictures uploaded to `upload_url` (from the connector's get_upload_url).
    Returns {"model": Krea model id, "input": for generate_image, "credits_about", "scale", "size"}."""
    if region is None:
        region = _plan_region.get("box")         # the marks of the Adjust tab, when sent from there
    prompt, used, refs, zoom, view, protect, source, width, height, room = _choices(
        prompt, model, environment, zoom, view, protect, region)
    if not prompt.strip() and not refs and zoom == 'KEEP' and view == 'KEEP':
        raise ValueError("Write what Krea should change")
    scale, mask = _scale_and_mask(scene, source, width, height, used, zoom, view, protect)
    image_url = krea.upload_to(upload_url, source)
    ref_urls = [r if r.startswith("http") else krea.upload_to(upload_url, r) for r in refs[:krea.MAX_REFERENCES]]
    mask_url = krea.upload_to(upload_url, mask) if mask else ""
    _plan_secure(scene, source, used, protect)
    body = krea.request_body(used, prompt, image_url, width, height, ref_urls, zoom, view, scale, mask_url, room,
                             region)
    _plan.clear()
    _plan.update(source=source, model=used, size=[width, height])
    krea.job.update(state="", text="", quote=None)
    return {"model": krea.model_path(used), "key": used, "input": body, "credits_about": krea.UNITS.get(used),
            "scale": scale, "size": [width, height], "keeps_product": bool(mask) or used == 'NANO' and protect,
            "only_marked": bool(region)}


_variants = {"left": 0, "scene": "", "wish": ""}


def _next_variant():
    """More than one variant: the next starts once Krea has finished the one before (one job at a time)."""
    if _variants["left"] <= 0:
        return None
    if krea.busy():
        return 2.0
    if krea.job.get("state") == "error":
        _variants["left"] = 0
        return None
    scene = bpy.data.scenes.get(_variants["scene"])
    if scene is None:
        return None
    _variants["left"] -= 1
    try:
        refine(scene, _variants["wish"], region=_variants.get("region"))
    except (ValueError, OSError, RuntimeError) as exc:
        print("ID Toolkit: Krea variant:", exc)
        _variants["left"] = 0
        return None
    return 2.0 if _variants["left"] > 0 else None


# ------------------------------------------------------------------ Krea presets (Mockup › Preset)

def _presets_file():
    from .paths import data_dir
    return os.path.join(data_dir(), "krea_presets.json")


KREA_KEYS = ("prompt", "model", "zoom", "view", "protect", "scale", "count", "refs")


def krea_presets():
    try:
        with open(_presets_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def capture_krea(wm):
    data = {key: getattr(wm, "studio_krea_" + key) for key in KREA_KEYS}
    data["styles"] = [k for k, _l, _t in STYLES if getattr(wm, "studio_krea_style_" + k)]
    data["scene"] = [k for k, _l, _t, _p in krea.SCENE if getattr(wm, "studio_krea_" + k)]
    return data


def apply_krea(wm, data):
    for key in KREA_KEYS:
        if key in data:
            try:
                setattr(wm, "studio_krea_" + key, data[key])
            except (TypeError, ValueError):
                pass
    for k, _l, _t in STYLES:
        setattr(wm, "studio_krea_style_" + k, k in data.get("styles", ()))
    for k, _l, _t, _p in krea.SCENE:
        setattr(wm, "studio_krea_" + k, k in data.get("scene", ()))


class STUDIO_OT_krea_preset(Operator):
    bl_idname = "studio.krea_preset"
    bl_label = "Mockup Preset"
    bl_description = "Load (or save / delete) a combination of pictures, styles, model and wish"
    bl_options = {'INTERNAL'}

    name: StringProperty(name="Name", options={'SKIP_SAVE'})
    action: StringProperty(default='LOAD', options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, _event):
        if self.action == 'SAVE':
            return context.window_manager.invoke_props_dialog(self, confirm_text="Save")
        return self.execute(context)

    def execute(self, context):
        wm = context.window_manager
        data = krea_presets()
        if self.action == 'LOAD':
            if self.name not in data:
                return {'CANCELLED'}
            apply_krea(wm, data[self.name])
            wm.studio_krea_preset = self.name
            return {'FINISHED'}
        if self.action == 'DELETE':
            data.pop(self.name, None)
        else:
            if not self.name.strip():
                self.report({'ERROR'}, "Give the preset a name")
                return {'CANCELLED'}
            data[self.name.strip()] = capture_krea(wm)
            wm.studio_krea_preset = self.name.strip()
        try:
            with open(_presets_file(), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=1, sort_keys=True)
        except OSError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        return {'FINISHED'}


class STUDIO_MT_krea_presets(Menu):
    bl_idname = "STUDIO_MT_krea_presets"
    bl_label = "Mockup Presets"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        names = sorted(krea_presets())
        for name in names:
            layout.operator(STUDIO_OT_krea_preset.bl_idname, text=name, translate=False,
                            icon='CHECKMARK' if name == wm.studio_krea_preset else 'BLANK1').name = name
        if names:
            layout.separator()
        op = layout.operator(STUDIO_OT_krea_preset.bl_idname, text="Save as Preset …", icon='ADD')
        op.action = 'SAVE'
        if wm.studio_krea_preset in names:
            op = layout.operator(STUDIO_OT_krea_preset.bl_idname, text="Delete %s" % wm.studio_krea_preset,
                                 icon='TRASH', translate=False)
            op.action, op.name = 'DELETE', wm.studio_krea_preset


def marks_region(context):
    """The box of the marks drawn on the picture, when the Adjust tab sends to Krea; else None."""
    wm = context.window_manager
    if getattr(wm, "studio_adjust_target", 'BLENDER') != 'KREA' or context.window is None:
        return None
    from . import photo_view
    return photo_view.marks_box(context.window)


def estimate(context):
    """(model Krea will use, about how many credits this click costs)."""
    wm = context.window_manager
    refs = references(wm)
    room = wm.studio_krea_room or has_environment(wm)
    used = krea.pick_model(wm.studio_krea_model, bool(refs) and room, 'KEEP', 'KEEP', marks_region(context))
    return used, krea.UNITS.get(used, 150) * max(1, wm.studio_krea_count)


def _ask_price(operator, context, event):
    """More than krea.ASK_ABOVE credits for one click: asked first, with the price."""
    used, cost = estimate(context)
    if cost <= krea.ASK_ABOVE:
        return operator.execute(context)
    name = dict((k, n) for k, _p, n, _d in krea.MODELS).get(used, used)
    try:
        return context.window_manager.invoke_confirm(
            operator, event, title="Krea: about %d credits" % cost, confirm_text="Make it", icon='WARNING',
            message="%s%s. Cheaper: Auto with 1 variant (~%d)." % (
                name, " · %d variants" % context.window_manager.studio_krea_count
                if context.window_manager.studio_krea_count > 1 else "", krea.UNITS['NANO']))
    except TypeError:
        return context.window_manager.invoke_confirm(operator, event)


class STUDIO_OT_krea_refine(Operator):
    bl_idname = "studio.krea_refine"
    bl_label = "Refine with Krea"
    bl_description = ("Send the image shown above to Krea as it is written (without Claude, with the API token). "
                      "The result is saved next to it (…_krea_01.png) at exactly its size")

    @classmethod
    def poll(cls, context):
        return not krea.busy()

    def invoke(self, context, event):
        return _ask_price(self, context, event)

    def execute(self, context):
        wm = context.window_manager
        region = marks_region(context)
        _variants.update(left=max(0, wm.studio_krea_count - 1), scene=context.scene.name, wish=wish(wm),
                         region=region)
        if _variants["left"] and not bpy.app.timers.is_registered(_next_variant):
            bpy.app.timers.register(_next_variant, first_interval=2.0)
        try:
            target, used, width, height = refine(context.scene, wish(context.window_manager), region=region)
        except (ValueError, OSError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, "Krea (%s): %s, %d × %d" % (dict((k, n) for k, _p, n, _d in krea.MODELS)[used],
                                                         os.path.basename(target), width, height))
        return {'FINISHED'}


class STUDIO_OT_krea_claude(Operator):
    bl_idname = "studio.krea_claude"
    bl_label = "Send"
    bl_description = ("Claude looks at the render and your pictures, writes the prompt for Krea from your wish and "
                      "the pressed buttons, with the product's true size, shows the price and makes it with your Krea "
                      "plan (the Krea connector of your Claude account). You see it in the chat")

    @classmethod
    def poll(cls, context):
        return not krea.busy()

    def invoke(self, context, event):
        return _ask_price(self, context, event)

    def execute(self, context):
        wm = context.window_manager
        source = current_image(context.scene)
        if not source:
            self.report({'ERROR'}, "Render an image first")
            return {'CANCELLED'}
        text_wish = wish(wm)
        refs = references(wm)
        region = marks_region(context)
        _plan_region["box"] = region
        if not text_wish and not refs:
            self.report({'WARNING'}, "Write what should change, or press a button above")
            return {'CANCELLED'}
        from .. import core
        if core.is_local():
            self.report({'ERROR'}, "Krea through Claude needs Claude (not a local model)")
            return {'CANCELLED'}
        lines = ["Refine this render with Krea: %s" % (text_wish or "(see below)"), "Render: %s" % source]
        try:
            width, height = krea.image_size(source)
            info = krea_scene.product_scale(context.scene, krea_scene.render_camera(context.scene, source),
                                            width, height)
            if info:
                lines.append("Product: %s cm (true size%s), %d × %d px render" % (
                    " × ".join("%g" % v for v in info["size_cm"]),
                    ", keep it" if wm.studio_krea_scale else ", size may change", width, height))
        except (OSError, ValueError):
            pass
        lines.append("Keep the product exactly (put back from the render afterwards): %s"
                     % ("yes" if wm.studio_krea_protect else "no"))
        if region:
            lines.append("Only inside the region I marked on the picture (box %s from the top left, 0-1): "
                         "api.krea_prepare uses it by itself (Krea 3, regional edit)" % ", ".join("%g" % v for v in region))
        labels = dict((r[0], r[3]) for r in ROLES)
        for role, path in references_with_roles(wm):
            lines.append("Picture (%s): %s" % (labels[role], path))
        if wm.studio_krea_count > 1:
            lines.append("Make %d variants (one after another), each saved next to the render." % wm.studio_krea_count)
        if wm.studio_krea_model != 'AUTO':
            lines.append("Model: %s" % wm.studio_krea_model)
        try:
            core.add_attachment(source)
            for path in refs:
                core.add_attachment(path)
        except Exception as exc:
            print("ID Toolkit: Krea attachment:", exc)
        text = "\n".join(lines)
        from . import photo_tools
        photo_tools.remember_wish(wm.studio_krea_prompt)       # for "Again"
        wm.studio_krea_prompt = ""
        if core.is_busy():
            # a run that is going cannot get the connector any more: a new chat after it
            names = [s["name"] for s in core.skills.list_skills(core.workdir()) if s.get("studio_tab") == 'FINISH']
            core.queue_chat(text, names, connectors=("Krea",))
            self.report({'INFO'}, "Claude is busy: Krea comes as the next chat")
            return {'FINISHED'}
        core.request_connector("Krea")
        if not core.studio_prompt('FINISH', text):
            core.connectors_next.discard("Krea")
            return {'CANCELLED'}
        return {'FINISHED'}


class STUDIO_OT_krea_usage(Operator):
    bl_idname = "studio.krea_usage"
    bl_label = "Krea Balance"
    bl_description = "Open your Krea API balance and usage (Krea shows it only on its website)"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        bpy.ops.wm.url_open(url=krea.USAGE_URL)
        return {'FINISHED'}


class STUDIO_OT_krea_token(Operator):
    bl_idname = "studio.krea_token"
    bl_label = "Get a Krea API Token"
    bl_description = "Open krea.ai to create an API token (the API has its own balance, from $5)"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        bpy.ops.wm.url_open(url=krea.TOKENS_URL)
        return {'FINISHED'}


# ------------------------------------------------------------------ the panel

def current_image(scene):
    """The render shown in the Render tab (◀ ▶ there and here): the newest unless you browsed."""
    images = runner.queue_images(scene)
    return tab_queue._selected_image(images) if images else ""


def _draw_image(layout, context):
    scene = context.scene
    images = runner.queue_images(scene)
    path = tab_queue._selected_image(images) if images else ""
    box = layout.box()
    if not path:
        sub = box.row()
        sub.active = False
        sub.label(text="Render an image first", icon='INFO')
        return ""
    # with adjustments: a small adjusted copy (made in a timer), else the render itself
    shown = image_edit.preview_file(scene, path)
    image = bpy.data.images.get(runner.PREVIEW_IMAGE)
    if image is None or os.path.normpath(bpy.path.abspath(image.filepath)) != os.path.normpath(shown):
        tab_queue._schedule_preview(shown)
        image = None
    head = box.row(align=True)
    index = images.index(path) if path in images else 0
    older = head.row(align=True)
    older.enabled = index < len(images) - 1
    older.operator(tab_queue.STUDIO_OT_preview_step.bl_idname, text="", icon='TRIA_LEFT').direction = 1
    name = head.row(align=True)
    name.alignment = 'CENTER'
    name.label(text=os.path.basename(path), translate=False)
    newer = head.row(align=True)
    newer.enabled = index > 0
    newer.operator(tab_queue.STUDIO_OT_preview_step.bl_idname, text="", icon='TRIA_RIGHT').direction = -1
    head.operator(tab_queue.STUDIO_OT_show_render.bl_idname, text="", icon='ZOOM_IN')
    scale = max(6.0, min(18.0, context.region.width / (20 * (context.preferences.system.ui_scale or 1.0)) - 2.5))
    if image is not None:
        box.template_icon(icon_value=layout.icon(image), scale=scale)
    try:
        width, height = krea.image_size(path)
        sub = box.row()
        sub.alignment = 'CENTER'
        sub.active = False
        sub.label(text="%d × %d px" % (width, height), translate=False)
    except OSError:
        pass
    return path


def _labeled(layout, text):
    """A row with a small title on the left; returns the right part."""
    split = layout.split(factor=0.22, align=True)
    sub = split.row()
    sub.active = False
    sub.label(text=text)
    return split.row(align=True)


def _product_info(layout, context, path):
    """All visible · 32×38×32 cm: what is measured (grey, under the product buttons)."""
    scene = context.scene
    text = "All visible"
    if krea_scene.is_marked(scene):
        names = [o.name for o in krea_scene.product_objects(scene)]
        text = names[0] + (" +%d" % (len(names) - 1) if len(names) > 1 else "")
    try:
        width, height = krea.image_size(path) if path else (0, 0)
        info = krea_scene.product_scale(scene, krea_scene.render_camera(scene, path), width, height)
    except (OSError, ValueError):
        info = None
    if info:
        text += " · %s cm" % "×".join("%g" % round(v) if v >= 10 else "%g" % v for v in info["size_cm"])
    row = layout.split(factor=0.22, align=True)
    row.label(text="")
    sub = row.row()
    sub.active = False
    sub.label(text=text, translate=False)


def _cost_lines(layout, used, names):
    """Before: the model and about what it costs; while Claude makes it: Krea's quote; then the month."""
    quote = krea.job.get("quote")
    sub = layout.row()
    sub.active = False
    if quote and krea.busy():
        credits, balance = quote
        text = "Krea: %s credits" % ("%g" % round(float(credits), 1) if credits is not None else "?")
        if balance is not None:
            text += " · %g left" % round(float(balance))
    else:
        text = "%s · ~%d credits" % (names[used], krea.UNITS.get(used, 150))
        balance = krea.last_balance()
        if balance is not None:
            text += " · %g left" % round(balance)
    sub.label(text=text, translate=False)


FAVOURITE_STYLES = ('night', 'outdoor', 'studio', 'lifestyle')     # in the row; the others under "More"


class STUDIO_MT_krea_more(Menu):
    bl_idname = "STUDIO_MT_krea_more"
    bl_label = "More for Krea"

    def draw(self, context):
        layout = self.layout
        wm = context.window_manager
        layout.label(text="Style")
        for key, label, _text in STYLES:
            if key not in FAVOURITE_STYLES:
                layout.prop(wm, "studio_krea_style_" + key, text=label)
        layout.separator()
        layout.label(text="Add to the picture")
        for key, label, _text, _protect in krea.SCENE:
            layout.prop(wm, "studio_krea_" + key, text=label)
        layout.separator()
        layout.label(text="A picture for Krea")
        for role, label, icon, _text in ROLES:
            layout.operator(STUDIO_OT_krea_reference.bl_idname, text=label, icon=icon).role = role


def _krea_status(layout):
    state = krea.job["state"]
    if state:
        line = layout.row()
        line.active = state != "error"
        if krea.busy():
            import time
            words = {"upload": "Uploading…", "working": "Krea is working…", "download": "Downloading…"}
            line.label(text="%s %d s" % (words[state], time.monotonic() - krea.job["started"]), icon='TIME')
        elif state == "done":
            line.label(text=krea.job["text"], icon='CHECKMARK', translate=False)
        elif state == "error":
            line.alert = True
            line.label(text=krea.job["text"], icon='ERROR', translate=False)
    from .. import core
    status, reply = core.studio_reply('FINISH')
    if status:
        sub = layout.row()
        sub.active = False
        sub.label(text=reply[:90], icon='TIME' if status == "working" else 'OUTLINER_DATA_GP_LAYER', translate=False)


def _draw_krea(layout, context, path):
    """Krea, short: the wish, four styles (the rest and "add to the picture" under More), pictures, the
    product kept, then the price and go. Model and variants under Advanced; the camera moves in Blender
    (Adjust › Camera), not here. Marks drawn on the picture (Adjust): Krea changes only there."""
    wm = context.window_manager
    box = layout.box()
    head = box.row(align=True)
    head.label(text="With Krea", icon='BRUSHES_ALL')
    head.menu(STUDIO_MT_krea_presets.bl_idname, text=wm.studio_krea_preset or "", icon='PRESET')
    row = box.row(align=True)
    row.scale_y = 1.3
    try:
        row.prop(wm, "studio_krea_prompt", text="", placeholder="What should Krea change?")
    except TypeError:
        row.prop(wm, "studio_krea_prompt", text="")
    row = box.row(align=True)
    for key, label, _text in STYLES:
        if key in FAVOURITE_STYLES:
            row.prop(wm, "studio_krea_style_" + key, text=label, toggle=True)
    on = [label for key, label, _t in STYLES if key not in FAVOURITE_STYLES and getattr(wm, "studio_krea_style_" + key)]
    on += [label for key, label, _t, _p in krea.SCENE if getattr(wm, "studio_krea_" + key)]
    row.menu(STUDIO_MT_krea_more.bl_idname, text="+%d" % len(on) if on else "More", icon='DOWNARROW_HLT')
    items = references_with_roles(wm)
    if items:
        from . import render_review
        roles = dict((r[0], (r[1], r[2])) for r in ROLES)
        listing = box.column(align=True)
        for index, (role, ref) in enumerate(items):
            line = listing.row(align=True)
            thumb = render_review.thumb(ref)
            line.label(text="", icon_value=thumb) if thumb else line.label(text="", icon=roles[role][1])
            line.label(text="%s · %s" % (roles[role][0], os.path.basename(ref)), translate=False)
            line.operator(STUDIO_OT_krea_reference.bl_idname, text="", icon='X', emboss=False).remove = index
    row = box.row(align=True)
    row.prop(wm, "studio_krea_protect", text="Keep Product", toggle=True, icon='MOD_MASK')
    row.prop(wm, "studio_krea_scale", text="True Size", toggle=True,
             icon='LOCKED' if wm.studio_krea_scale else 'UNLOCKED')
    row.operator(STUDIO_OT_krea_product.bl_idname, text="", icon='RESTRICT_SELECT_OFF',
                 depress=krea_scene.is_marked(context.scene))
    if subsection(box, "krea_advanced", "Advanced", default_open=False):
        col = box.column(align=True)
        _labeled(col, "Model").prop(wm, "studio_krea_model", expand=True)
        row = _labeled(col, "Variants")
        for n in (1, 2, 4):
            row.prop_enum(wm, "studio_krea_count_enum", str(n))
        _product_info(col, context, path)
    used, cost = estimate(context)
    names = dict((k, n) for k, _p, n, _d in krea.MODELS)
    line = box.row()
    line.active = False
    what = {"REGION": "only in your marks", "NANO": "whole picture, your product put back",
            "PRECISE": "in place, product masked"}.get(used, "")
    balance = krea.last_balance()
    line.label(text="%s · ~%d credits%s%s" % (names.get(used, used), cost, " · %s" % what if what else "",
                                              " · %g left" % round(balance) if balance is not None else ""),
               icon='ERROR' if cost > krea.ASK_ABOVE else 'NONE', translate=False)
    row = box.row(align=True)                       # what goes on: in the status bar at the bottom
    row.scale_y = 1.5
    row.enabled = bool(path)
    row.operator(STUDIO_OT_krea_claude.bl_idname, text="Make it with Claude", icon='EVENT_RETURN')
    if krea.token():
        row.operator(STUDIO_OT_krea_refine.bl_idname, text="", icon='PLAY')


def _draw_adjust(layout, context, path):
    """Light (exposure, contrast, highlights, shadows) and color (temperature, tint, vibrance, saturation)
    of the finished image; [Save Image] writes …_edit_01.png."""
    rs = context.scene.studio
    box, head, is_open = section(layout, "adjust", "Adjust Image", 'IMAGE_DATA')
    head.menu(STUDIO_MT_edit_presets.bl_idname, text="", icon='PRESET')
    head.operator(STUDIO_OT_edit_save_preset.bl_idname, text="", icon='ADD')
    head.operator(STUDIO_OT_edit_reset.bl_idname, text="", icon='LOOP_BACK')
    if not is_open:
        return
    grid = box.grid_flow(row_major=True, columns=4, even_columns=True, align=True)   # looks: a starting point
    current = image_edit.values(context.scene)
    for name, vals in image_edit.LOOKS:
        same = all(abs(current[k] - float(vals.get(k, 0.0))) < 1e-4 for k in image_edit.KEYS)
        grid.operator(STUDIO_OT_edit_look.bl_idname, text=name, depress=same, translate=False).name = name
    for key, title, keys in (("adjust_light", "Light", image_edit.LIGHT), ("adjust_color", "Color", image_edit.COLOR),
                             ("adjust_effects", "Effects", image_edit.EFFECTS)):
        if not subsection(box, key, title, default_open=key != "adjust_effects"):
            continue
        col = box.column(align=True)
        for prop in keys:
            col.prop(rs, prop, slider=True)
    row = box.row(align=True)
    row.scale_y = 1.2
    row.enabled = bool(path) and not image_edit.is_neutral(image_edit.values(context.scene))
    row.operator(STUDIO_OT_edit_export.bl_idname, icon='FILE_TICK')


class STUDIO_PT_finish_settings(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Finish Settings"
    bl_ui_units_x = 14

    def draw(self, context):
        layout = self.layout
        p = runner.prefs(context)
        box = layout.box()
        box.label(text="Krea", icon='BRUSHES_ALL')
        box.row(align=True).prop(context.window_manager, "studio_krea_model", expand=True)
        col = box.column(align=True)
        col.active = False
        col.label(text="Refine with Claude: your Krea plan's credits")
        col.label(text="(connect Krea in Claude › Settings › Connectors)")
        box.separator()
        box.label(text="Without Claude: an API token (own balance, from $5)")
        if p is not None:
            box.prop(p, "krea_token", text="API Token")
        row = box.row(align=True)
        row.operator(STUDIO_OT_krea_token.bl_idname, icon='URL')
        row.operator(STUDIO_OT_krea_usage.bl_idname, icon='FUND')


class STUDIO_PT_finish(Panel):
    """Refine: in the Compositing workspace's node editor sidebar (the look is the compositor)."""
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Studio"
    bl_label = "Refine"
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return space is None or getattr(space, "tree_type", 'CompositorNodeTree') == 'CompositorNodeTree'

    def draw(self, context):
        layout = self.layout
        tab_head(layout, STUDIO_MT_edit_presets.bl_idname, "Adjustments", STUDIO_OT_edit_save_preset.bl_idname,
                 STUDIO_PT_finish_settings.__name__, icon='IMAGE_DATA')
        path = _draw_image(layout, context)
        from . import mockup
        box, _head, is_open = section(layout, "mockup", "Mockup: Product in a Photo", 'IMAGE_REFERENCE',
                                      default_open=mockup.is_mockup(context.scene))
        if is_open:
            mockup.draw(box, context)
        draw_look(layout, context)            # 1. the look (and every effect in the compositor)
        _draw_adjust(layout, context, path)   # 2. adjustments of the finished image
        _draw_krea(layout, context, path)     # 3. Krea


def draw_look(layout, context):
    """The look (compositor: next renders and, with the eye, the viewport): its saved looks and the switch."""
    shading = _shading(context)
    live = shading is not None and getattr(shading, "use_compositor", 'DISABLED') != 'DISABLED'
    look.draw(layout, context.scene, live=live, menu=STUDIO_MT_look_presets.bl_idname)


classes = (STUDIO_OT_look_saved, STUDIO_OT_look_save, STUDIO_MT_look_presets, STUDIO_OT_look_live, STUDIO_OT_edit_reset,
           STUDIO_OT_edit_saved, STUDIO_OT_edit_look, STUDIO_OT_edit_save_preset, STUDIO_MT_edit_presets, STUDIO_OT_edit_export, STUDIO_OT_krea_product,
           STUDIO_OT_krea_frame, STUDIO_OT_krea_reference,
           STUDIO_OT_krea_refine, STUDIO_OT_krea_claude,  STUDIO_OT_krea_usage, STUDIO_OT_krea_token,
           STUDIO_OT_krea_preset, STUDIO_MT_krea_presets, STUDIO_MT_krea_more,
           STUDIO_PT_finish_settings, STUDIO_PT_finish)


def _scene_toggled(protect):
    """A hand or a person touches the product: "Untouched" goes off (fingers go over it)."""
    def update(wm, _context):
        if not protect and any(getattr(wm, "studio_krea_" + k) for k, _l, _t, p in krea.SCENE if not p):
            wm.studio_krea_protect = False
    return update


def register():
    wm = bpy.types.WindowManager
    wm.studio_krea_prompt = StringProperty(
        name="Change", description="What Krea should change in the image (one or two sentences)", options={'SKIP_SAVE'})
    wm.studio_krea_model = EnumProperty(
        name="Model", items=[(k, label, text) for k, _path, label, text in krea.MODELS], default='AUTO',
        options={'SKIP_SAVE'})
    wm.studio_krea_refs = StringProperty(
        name="Pictures", description="Pictures for Krea, one path per line", options={'SKIP_SAVE'})
    wm.studio_krea_scale = BoolProperty(
        name="Lock Size", default=True, options={'SKIP_SAVE'},
        description="Tell Krea the product's true size from Blender, so added hands and things are to scale")
    for key, label, text, protect in krea.SCENE:
        setattr(wm, "studio_krea_" + key, BoolProperty(
            name=label, description=text, default=False, options={'SKIP_SAVE'},
            update=_scene_toggled(protect)))
    wm.studio_krea_zoom = EnumProperty(name="Framing", items=krea.ZOOMS, default='KEEP', options={'SKIP_SAVE'})
    wm.studio_krea_view = EnumProperty(name="Height", items=krea.VIEWS, default='KEEP',
                                              options={'SKIP_SAVE'})
    wm.studio_krea_protect = BoolProperty(
        name="Keep Product Untouched", default=True, options={'SKIP_SAVE'},
        description=("Precise only: a mask from Blender keeps the product exactly as rendered; Krea changes only "
                     "the rest. Off for a hand whose fingers go over the product"))
    for key, label, text in STYLES:
        setattr(wm, "studio_krea_style_" + key, BoolProperty(name=label, description=text, options={'SKIP_SAVE'}))
    wm.studio_krea_count = IntProperty(name="Variants", min=1, max=4, default=1, options={'SKIP_SAVE'})
    wm.studio_krea_count_enum = EnumProperty(
        name="Variants", items=[(str(n), str(n), "%d variant%s, one after another" % (n, "" if n == 1 else "s"))
                                for n in (1, 2, 4)],
        get=lambda self: {1: 0, 2: 1, 4: 2}.get(self.studio_krea_count, 0),
        set=lambda self, value: setattr(self, "studio_krea_count", (1, 2, 4)[value]), options={'SKIP_SAVE'})
    wm.studio_krea_preset = StringProperty(options={'SKIP_SAVE'})
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    if bpy.app.timers.is_registered(krea._finish):
        bpy.app.timers.unregister(krea._finish)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.studio_krea_prompt
    del bpy.types.WindowManager.studio_krea_model
    del bpy.types.WindowManager.studio_krea_refs
    del bpy.types.WindowManager.studio_krea_scale
    for key, _label, _text, _protect in krea.SCENE:
        delattr(bpy.types.WindowManager, "studio_krea_" + key)
    del bpy.types.WindowManager.studio_krea_zoom
    del bpy.types.WindowManager.studio_krea_view
    del bpy.types.WindowManager.studio_krea_protect
    for key, _label, _text in STYLES:
        delattr(bpy.types.WindowManager, "studio_krea_style_" + key)
    del bpy.types.WindowManager.studio_krea_count_enum
    del bpy.types.WindowManager.studio_krea_count
    del bpy.types.WindowManager.studio_krea_preset

# SPDX-License-Identifier: GPL-3.0-or-later
"""The Finish tab: the finished render. Its preview, refining it with Krea, then adjusting it like a raw
photo (image_edit; saved as a new file next to it). The look of the live camera (exposure, glow …) is in the
Camera tab (look.draw); its saved looks and the viewport switch are defined here."""

import json
import os

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Menu, Operator, Panel

from . import image_edit, krea, krea_scene, look, runner, tab_queue
from .ui_common import _SidebarPanel, _unpin, section, tab_head

FINISH_TAB_ORDER = 5
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
    rq, view = scene.render_studio, scene.view_settings
    data = {key: getattr(rq, key) for key in LOOK_KEYS}
    data["exposure"] = view.exposure
    if hasattr(view, "use_white_balance"):
        data["white_balance"] = view.white_balance_temperature if view.use_white_balance else 0.0
        data["tint"] = view.white_balance_tint if view.use_white_balance else 0.0
    return data


def apply_look(scene, data):
    rq, view = scene.render_studio, scene.view_settings
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


class RS_OT_look_saved(Operator):
    bl_idname = "render_studio.look_saved"
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


class RS_OT_look_save(Operator):
    bl_idname = "render_studio.look_save"
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


class RS_MT_look_presets(Menu):
    bl_idname = "RS_MT_look_presets"
    bl_label = "Looks"

    def draw(self, context):
        layout = self.layout
        for name in look.PRESETS:
            layout.operator(look.RS_OT_look_preset.bl_idname, text=name).name = name
        saved = sorted(saved_looks())
        if saved:
            layout.separator()
            for name in saved:
                layout.operator(RS_OT_look_saved.bl_idname, text=name, translate=False, icon='USER').name = name
            layout.separator()
            for name in saved:
                op = layout.operator(RS_OT_look_saved.bl_idname, text="Delete %s" % name, icon='TRASH')
                op.name, op.delete = name, True
        layout.separator()
        layout.operator(RS_OT_look_save.bl_idname, text="Save Look…", icon='ADD')
        layout.operator(look.RS_OT_look_reset.bl_idname, icon='LOOP_BACK')


# ------------------------------------------------------------------ live preview in the viewport

def _shading(context):
    space = context.space_data
    return space.shading if space is not None and space.type == 'VIEW_3D' else None


class RS_OT_look_live(Operator):
    bl_idname = "render_studio.look_live"
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


class RS_OT_edit_reset(Operator):
    bl_idname = "render_studio.edit_reset"
    bl_label = "Reset Adjustments"
    bl_description = "All adjustments back to 0: the preview shows the render as it is"
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        rs = context.scene.render_studio
        for key in image_edit.KEYS:
            rs.property_unset(key)
        image_edit.changed()
        return {'FINISHED'}


class RS_OT_edit_saved(Operator):
    bl_idname = "render_studio.edit_saved"
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
        rs = context.scene.render_studio
        for key in image_edit.KEYS:
            setattr(rs, key, float(edits[self.name].get(key, 0.0)))
        return {'FINISHED'}


class RS_OT_edit_save_preset(Operator):
    bl_idname = "render_studio.edit_save_preset"
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


class RS_MT_edit_presets(Menu):
    bl_idname = "RS_MT_edit_presets"
    bl_label = "Adjustments"

    def draw(self, context):
        layout = self.layout
        saved = sorted(saved_edits())
        for name in saved:
            layout.operator(RS_OT_edit_saved.bl_idname, text=name, translate=False, icon='USER').name = name
        if saved:
            layout.separator()
            for name in saved:
                op = layout.operator(RS_OT_edit_saved.bl_idname, text="Delete %s" % name, icon='TRASH')
                op.name, op.delete = name, True
            layout.separator()
        else:
            sub = layout.row()
            sub.active = False
            sub.label(text="No saved adjustments yet (+)")
        layout.operator(RS_OT_edit_reset.bl_idname, icon='LOOP_BACK')


class RS_OT_edit_export(Operator):
    bl_idname = "render_studio.edit_export"
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

class RS_OT_krea_product(Operator):
    bl_idname = "render_studio.krea_product"
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


class RS_OT_krea_idea(Operator):
    bl_idname = "render_studio.krea_idea"
    bl_label = "Idea"
    bl_description = "Put this idea into the text field (change it as you like)"
    bl_options = {'INTERNAL'}

    text: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    protect: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        context.window_manager.render_studio_krea_prompt = self.text
        context.window_manager.render_studio_krea_protect = self.protect
        return {'FINISHED'}


def _choices(prompt, model, environment, zoom, view, protect):
    """The tab's choices where an argument is None, the model Auto picks, the render and its size."""
    wm = bpy.context.window_manager
    environment = wm.render_studio_krea_env if environment is None else environment
    environment = bpy.path.abspath(environment) if environment else ""
    zoom = krea.zoom_key(zoom or wm.render_studio_krea_zoom)
    view = krea.view_key(view or wm.render_studio_krea_view)
    protect = wm.render_studio_krea_protect if protect is None else bool(protect)
    used = krea.pick_model((model or wm.render_studio_krea_model).upper(), environment, zoom, view)
    source = current_image(bpy.context.scene)
    if not source or not os.path.isfile(source):
        raise ValueError("Render an image first")
    if environment and not os.path.isfile(environment):
        raise ValueError("The environment picture is missing: %s" % environment)
    width, height = krea.image_size(source)
    return prompt, used, environment, zoom, view, protect, source, width, height


def _scale_and_mask(scene, source, width, height, used, zoom, view, protect):
    """The sentence about the product's true size and, for Precise with "keep the product", the mask."""
    info = krea_scene.product_scale(scene, krea_scene.render_camera(scene, source), width, height)
    scale = krea_scene.scale_text(info, width, height, framing_kept=zoom == 'KEEP' and view == 'KEEP')
    mask = ""
    if used == 'PRECISE' and protect and info:
        mask = krea_scene.render_mask(scene, source)
    return scale, mask


def refine(scene, prompt, model=None, environment=None, zoom=None, view=None, protect=None):
    """Refines the render shown in the Finish tab with the API token; the choices of the tab where an
    argument is None. Returns (result path, model used, width, height)."""
    prompt, used, environment, zoom, view, protect, source, width, height = _choices(
        prompt, model, environment, zoom, view, protect)
    if not krea.token():
        raise ValueError("Add your Krea API token in Preferences › Studio (or refine with Claude: Krea plan)")
    if krea.busy():
        raise ValueError("Krea is still working on the last image")
    scale, mask = _scale_and_mask(scene, source, width, height, used, zoom, view, protect)
    target, used = krea.start(used, prompt, source, width, height, environment, zoom, view, scale, mask)
    return target, used, width, height


_plan = {}     # the run being prepared with Claude: source, model, size


def prepare(scene, prompt, upload_url, model=None, environment=None, zoom=None, view=None, protect=None):
    """For Claude with the Krea connector: the full prompt (with the product's true scale), the render, the
    mask and the environment picture uploaded to `upload_url` (from the connector's get_upload_url).
    Returns {"model": Krea model id, "input": for generate_image, "credits_about", "scale", "size"}."""
    prompt, used, environment, zoom, view, protect, source, width, height = _choices(
        prompt, model, environment, zoom, view, protect)
    if not prompt.strip() and not environment and zoom == 'KEEP' and view == 'KEEP':
        raise ValueError("Write what Krea should change")
    scale, mask = _scale_and_mask(scene, source, width, height, used, zoom, view, protect)
    image_url = krea.upload_to(upload_url, source)
    environment_url = krea.upload_to(upload_url, environment) if environment else ""
    mask_url = krea.upload_to(upload_url, mask) if mask else ""
    body = krea.request_body(used, prompt, image_url, width, height, environment_url, zoom, view, scale, mask_url)
    _plan.clear()
    _plan.update(source=source, model=used, size=[width, height])
    krea.job.update(state="", text="", quote=None)
    return {"model": krea.model_path(used), "key": used, "input": body, "credits_about": krea.UNITS.get(used),
            "scale": scale, "size": [width, height], "keeps_product": bool(mask)}


class RS_OT_krea_refine(Operator):
    bl_idname = "render_studio.krea_refine"
    bl_label = "Refine with Krea"
    bl_description = ("Send the image shown above to Krea as it is written (without Claude). The result is "
                      "saved next to it (…_krea_01.png) at exactly its size; the render stays as it is")

    @classmethod
    def poll(cls, context):
        return not krea.busy()

    def execute(self, context):
        try:
            target, used, width, height = refine(context.scene, context.window_manager.render_studio_krea_prompt)
        except (ValueError, OSError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, "Krea (%s): %s, %d × %d" % (dict((k, n) for k, _p, n, _d in krea.MODELS)[used],
                                                         os.path.basename(target), width, height))
        return {'FINISHED'}


class RS_OT_krea_claude(Operator):
    bl_idname = "render_studio.krea_claude"
    bl_label = "Refine with Claude"
    bl_description = ("Claude looks at the render (and the environment picture), writes the prompt for Krea from "
                      "your sentence with the product's true size, picks the model, shows the price and makes it "
                      "with your Krea plan (the Krea connector of your Claude account). You see it in the chat")

    @classmethod
    def poll(cls, context):
        return not krea.busy()

    def execute(self, context):
        wm = context.window_manager
        source = current_image(context.scene)
        if not source:
            self.report({'ERROR'}, "Render an image first")
            return {'CANCELLED'}
        from .. import core
        if core.is_local():
            self.report({'ERROR'}, "Krea through Claude needs Claude (not a local model)")
            return {'CANCELLED'}
        lines = ["Refine this render with Krea: %s" % (wm.render_studio_krea_prompt.strip() or "(no wish, see below)"),
                 "Render: %s" % source]
        try:
            width, height = krea.image_size(source)
            info = krea_scene.product_scale(context.scene, krea_scene.render_camera(context.scene, source),
                                            width, height)
            if info:
                lines.append("Product: %s cm (true size), %d × %d px render" % (
                    " × ".join("%g" % v for v in info["size_cm"]), width, height))
        except (OSError, ValueError):
            pass
        lines.append("Keep the product untouched (mask): %s" % ("yes" if wm.render_studio_krea_protect else "no"))
        if wm.render_studio_krea_env:
            lines.append("Environment picture: %s" % bpy.path.abspath(wm.render_studio_krea_env))
        if wm.render_studio_krea_zoom != 'KEEP' or wm.render_studio_krea_view != 'KEEP':
            lines.append("Framing: zoom %s, perspective %s" % (wm.render_studio_krea_zoom, wm.render_studio_krea_view))
        if wm.render_studio_krea_model != 'AUTO':
            lines.append("Model: %s" % wm.render_studio_krea_model)
        try:
            core.add_attachment(source)
            if wm.render_studio_krea_env:
                core.add_attachment(bpy.path.abspath(wm.render_studio_krea_env))
        except Exception as exc:
            print("ID Toolkit: Krea attachment:", exc)
        text = "\n".join(lines)
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


class RS_OT_krea_environment(Operator):
    bl_idname = "render_studio.krea_environment"
    bl_label = "Environment Picture"
    bl_description = "A photo of a place or a mood: Krea puts the product into it (a new environment needs the Free model)"
    bl_options = {'INTERNAL'}

    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_image: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    filter_folder: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    clear: BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        if self.clear:
            return self.execute(context)
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        context.window_manager.render_studio_krea_env = "" if self.clear else self.filepath
        return {'FINISHED'}


class RS_OT_krea_usage(Operator):
    bl_idname = "render_studio.krea_usage"
    bl_label = "Krea Balance"
    bl_description = "Open your Krea API balance and usage (Krea shows it only on its website)"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        bpy.ops.wm.url_open(url=krea.USAGE_URL)
        return {'FINISHED'}


class RS_OT_krea_token(Operator):
    bl_idname = "render_studio.krea_token"
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
    older.operator(tab_queue.RS_OT_preview_step.bl_idname, text="", icon='TRIA_LEFT').direction = 1
    name = head.row(align=True)
    name.alignment = 'CENTER'
    name.label(text=os.path.basename(path), translate=False)
    newer = head.row(align=True)
    newer.enabled = index > 0
    newer.operator(tab_queue.RS_OT_preview_step.bl_idname, text="", icon='TRIA_RIGHT').direction = -1
    head.operator(tab_queue.RS_OT_show_render.bl_idname, text="", icon='ZOOM_IN')
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


def _step(layout, number, text):
    """A small heading: 1  What · 2  Where · 3  Camera."""
    row = layout.row()
    row.active = False
    row.label(text="%d   %s" % (number, text))


def _product_line(layout, context, path):
    """Product: Pod, PCB · 3.2 × 3.8 × 3.2 cm · 34 % of the width [⇥ selection]: what Krea is told."""
    scene = context.scene
    row = layout.row(align=True)
    sub = row.row(align=True)
    sub.active = False
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
    sub.label(text=text, icon='EMPTY_ARROWS', translate=False)
    row.operator(RS_OT_krea_product.bl_idname, text="", icon='RESTRICT_SELECT_OFF')


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
    count, known, _unknown, credits = krea.month_summary()
    if count:
        sub = layout.row()
        sub.active = False
        month = "Month: %d pictures" % count + (" · %g credits" % round(credits) if credits else "")
        if known:
            month += " · $%.2f" % known
        sub.label(text=month, translate=False)


def _draw_krea(layout, context, path):
    """1 What: ideas and the sentence · 2 Where: an environment picture · 3 Camera: framing and height ·
    the product and keeping it, the model · [Refine with Claude] · what it costs."""
    wm = context.window_manager
    box, head, is_open = section(layout, "krea", "Refine with Krea", 'BRUSHES_ALL')
    if not is_open:
        return
    _step(box, 1, "What")
    col = box.column(align=True)
    grid = col.grid_flow(row_major=True, columns=4, even_columns=True, align=True)
    for label, text, protect in krea.IDEAS:
        op = grid.operator(RS_OT_krea_idea.bl_idname, text=label)
        op.text, op.protect = text, protect
    col.prop(wm, "render_studio_krea_prompt", text="", placeholder="What should change? e.g. a hand holds it")
    _step(box, 2, "Where")
    row = box.row(align=True)
    env = wm.render_studio_krea_env
    if env:
        row.label(text=os.path.basename(env), icon='IMAGE_DATA', translate=False)
        row.operator(RS_OT_krea_environment.bl_idname, text="", icon='X').clear = True
    else:
        row.operator(RS_OT_krea_environment.bl_idname, text="Environment Picture…", icon='IMAGE_DATA')
    _step(box, 3, "Camera")
    col = box.column(align=True)
    split = col.split(factor=0.3, align=True)
    split.label(text="Framing")
    split.row(align=True).prop(wm, "render_studio_krea_zoom", expand=True)
    split = col.split(factor=0.3, align=True)
    split.label(text="Height")
    split.row(align=True).prop(wm, "render_studio_krea_view", expand=True)
    # the product (what is measured and kept) and the model
    box.separator(factor=0.5)
    _product_line(box, context, path)
    used = krea.pick_model(wm.render_studio_krea_model, env, wm.render_studio_krea_zoom, wm.render_studio_krea_view)
    names = dict((k, n) for k, _p, n, _d in krea.MODELS)
    row = box.row(align=True)
    sub = row.row(align=True)
    sub.active = used == 'PRECISE'
    sub.prop(wm, "render_studio_krea_protect", text="Keep Product",
             icon='LOCKED' if wm.render_studio_krea_protect else 'UNLOCKED')
    row.prop(wm, "render_studio_krea_model", text="")
    row = box.row(align=True)
    row.scale_y = 1.4
    row.enabled = bool(path)
    row.operator(RS_OT_krea_claude.bl_idname, icon='OUTLINER_DATA_GP_LAYER')
    if krea.token():
        row.operator(RS_OT_krea_refine.bl_idname, text="", icon='PLAY')
    _cost_lines(box, used, names)
    state = krea.job["state"]
    if state:
        line = box.row()
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
        sub = box.row()
        sub.active = False
        sub.label(text=reply[:90], icon='TIME' if status == "working" else 'OUTLINER_DATA_GP_LAYER', translate=False)


def _draw_adjust(layout, context, path):
    """Light (exposure, contrast, highlights, shadows) and color (temperature, tint, vibrance, saturation)
    of the finished image; [Save Image] writes …_edit_01.png."""
    rs = context.scene.render_studio
    box, head, is_open = section(layout, "adjust", "Adjust Image", 'IMAGE_DATA')
    head.operator(RS_OT_edit_reset.bl_idname, text="", icon='LOOP_BACK')
    if not is_open:
        return
    for title, keys in (("Light", image_edit.LIGHT), ("Color", image_edit.COLOR)):
        sub = box.row()
        sub.active = False
        sub.label(text=title)
        col = box.column(align=True)
        for key in keys:
            col.prop(rs, key, slider=True)
    row = box.row(align=True)
    row.scale_y = 1.2
    row.enabled = bool(path) and not image_edit.is_neutral(image_edit.values(context.scene))
    row.operator(RS_OT_edit_export.bl_idname, icon='FILE_TICK')


class RS_PT_finish_settings(Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Finish Settings"
    bl_ui_units_x = 14

    def draw(self, context):
        layout = self.layout
        p = runner.prefs(context)
        box = layout.box()
        box.label(text="Krea", icon='BRUSHES_ALL')
        col = box.column(align=True)
        col.active = False
        col.label(text="Refine with Claude: your Krea plan's credits")
        col.label(text="(connect Krea in Claude › Settings › Connectors)")
        box.separator()
        box.label(text="Without Claude: an API token (own balance, from $5)")
        if p is not None:
            box.prop(p, "krea_token", text="API Token")
        row = box.row(align=True)
        row.operator(RS_OT_krea_token.bl_idname, icon='URL')
        row.operator(RS_OT_krea_usage.bl_idname, icon='FUND')


class RS_PT_finish(_SidebarPanel, Panel):
    studio_tab = 'FINISH'
    bl_label = "Finish"
    bl_options = {'HIDE_HEADER'}
    bl_order = FINISH_TAB_ORDER

    def draw(self, context):
        _unpin(self)
        layout = self.layout
        tab_head(layout, RS_MT_edit_presets.bl_idname, "Adjustments", RS_OT_edit_save_preset.bl_idname,
                 RS_PT_finish_settings.__name__, icon='IMAGE_DATA')
        path = _draw_image(layout, context)
        _draw_krea(layout, context, path)
        _draw_adjust(layout, context, path)


def draw_look(layout, context):
    """The look of the live camera (compositor), in the Camera tab: its saved looks and the viewport switch."""
    shading = _shading(context)
    live = shading is not None and getattr(shading, "use_compositor", 'DISABLED') != 'DISABLED'
    look.draw(layout, context.scene, live=live, menu=RS_MT_look_presets.bl_idname)


classes = (RS_OT_look_saved, RS_OT_look_save, RS_MT_look_presets, RS_OT_look_live, RS_OT_edit_reset,
           RS_OT_edit_saved, RS_OT_edit_save_preset, RS_MT_edit_presets, RS_OT_edit_export, RS_OT_krea_product,
           RS_OT_krea_idea,
           RS_OT_krea_refine, RS_OT_krea_claude, RS_OT_krea_environment, RS_OT_krea_usage, RS_OT_krea_token,
           RS_PT_finish_settings, RS_PT_finish)


def register():
    wm = bpy.types.WindowManager
    wm.render_studio_krea_prompt = StringProperty(
        name="Change", description="What Krea should change in the image (one or two sentences)", options={'SKIP_SAVE'})
    wm.render_studio_krea_model = EnumProperty(
        name="Model", items=[(k, label, text) for k, _path, label, text in krea.MODELS], default='AUTO',
        options={'SKIP_SAVE'})
    wm.render_studio_krea_env = StringProperty(
        name="Environment", description="A photo of a place or mood the product goes into", subtype='FILE_PATH',
        options={'SKIP_SAVE'})
    wm.render_studio_krea_zoom = EnumProperty(name="Framing", items=krea.ZOOMS, default='KEEP', options={'SKIP_SAVE'})
    wm.render_studio_krea_view = EnumProperty(name="Height", items=krea.VIEWS, default='KEEP',
                                              options={'SKIP_SAVE'})
    wm.render_studio_krea_protect = BoolProperty(
        name="Keep Product Untouched", default=True, options={'SKIP_SAVE'},
        description=("Precise only: a mask from Blender keeps the product exactly as rendered; Krea changes only "
                     "the rest. Off for a hand whose fingers go over the product"))
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    if bpy.app.timers.is_registered(krea._finish):
        bpy.app.timers.unregister(krea._finish)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    del bpy.types.WindowManager.render_studio_krea_prompt
    del bpy.types.WindowManager.render_studio_krea_model
    del bpy.types.WindowManager.render_studio_krea_env
    del bpy.types.WindowManager.render_studio_krea_zoom
    del bpy.types.WindowManager.render_studio_krea_view
    del bpy.types.WindowManager.render_studio_krea_protect

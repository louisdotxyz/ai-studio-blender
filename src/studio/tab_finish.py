# SPDX-License-Identifier: GPL-3.0-or-later
"""The Finish tab: the image after rendering. The look (exposure, white balance, saturation, glow,
contrast, vignette) with a live preview in the viewport, and refining a finished render with Krea."""

import json
import os

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Menu, Operator, Panel

from . import krea, look, runner, tab_queue
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
    delete: bpy.props.BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

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


class RS_MT_finish_presets(Menu):
    bl_idname = "RS_MT_finish_presets"
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


# ------------------------------------------------------------------ Krea

class RS_OT_krea_idea(Operator):
    bl_idname = "render_studio.krea_idea"
    bl_label = "Idea"
    bl_description = "Put this idea into the text field (change it as you like)"
    bl_options = {'INTERNAL'}

    text: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        context.window_manager.render_studio_krea_prompt = self.text
        return {'FINISHED'}


def refine(scene, prompt, model=None, environment=None, zoom=None, view=None):
    """Refines the render shown in the Finish tab; the choices of the tab where an argument is None.
    Returns (result path, model used, width, height)."""
    wm = bpy.context.window_manager
    source = current_image(scene)
    width, height = krea.image_size(source) if source and os.path.isfile(source) else (0, 0)
    target, used = krea.start(model or wm.render_studio_krea_model, prompt, source, width, height,
                              wm.render_studio_krea_env if environment is None else environment,
                              zoom or wm.render_studio_krea_zoom, view or wm.render_studio_krea_view)
    return target, used, width, height


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
        except (ValueError, OSError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, "Krea (%s): %s, %d × %d" % (dict((k, n) for k, _p, n, _d in krea.MODELS)[used],
                                                         os.path.basename(target), width, height))
        return {'FINISHED'}


class RS_OT_krea_claude(Operator):
    bl_idname = "render_studio.krea_claude"
    bl_label = "Refine with Claude"
    bl_description = ("Claude looks at the render (and the environment picture), writes the prompt for Krea from "
                      "your sentence, picks the model and starts it. You see it in the chat")

    @classmethod
    def poll(cls, context):
        return not krea.busy()

    def execute(self, context):
        wm = context.window_manager
        source = current_image(context.scene)
        if not source:
            self.report({'ERROR'}, "Render an image first")
            return {'CANCELLED'}
        if not krea.token():
            self.report({'ERROR'}, "Add your Krea API token in Preferences › Studio")
            return {'CANCELLED'}
        lines = ["Refine this render with Krea: %s" % (wm.render_studio_krea_prompt.strip() or "(no wish, see below)"),
                 "Render: %s" % source]
        if wm.render_studio_krea_env:
            lines.append("Environment picture: %s" % bpy.path.abspath(wm.render_studio_krea_env))
        if wm.render_studio_krea_zoom != 'KEEP' or wm.render_studio_krea_view != 'KEEP':
            lines.append("Framing: zoom %s, perspective %s" % (wm.render_studio_krea_zoom, wm.render_studio_krea_view))
        if wm.render_studio_krea_model != 'AUTO':
            lines.append("Model: %s" % wm.render_studio_krea_model)
        from .. import core
        if not core.studio_prompt('FINISH', "\n".join(lines)):
            return {'CANCELLED'}
        try:
            core.add_attachment(source)
            if wm.render_studio_krea_env:
                core.add_attachment(bpy.path.abspath(wm.render_studio_krea_env))
        except Exception as exc:
            print("ID Toolkit: Krea attachment:", exc)
        return {'FINISHED'}


class RS_OT_krea_environment(Operator):
    bl_idname = "render_studio.krea_environment"
    bl_label = "Environment Picture"
    bl_description = "A photo of a place or a mood: Krea puts the product into it (a new environment needs the Free model)"
    bl_options = {'INTERNAL'}

    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    filter_image: bpy.props.BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    filter_folder: bpy.props.BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    clear: bpy.props.BoolProperty(default=False, options={'HIDDEN', 'SKIP_SAVE'})

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
        sub.label(text="Render an image in the Render tab first", icon='INFO')
        return ""
    image = bpy.data.images.get(runner.PREVIEW_IMAGE)
    if image is None or os.path.normpath(bpy.path.abspath(image.filepath)) != os.path.normpath(path):
        tab_queue._schedule_preview(path)
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


def _draw_krea(layout, context, path):
    """[Hand] [In Use] [Setting] [Props]; the sentence; environment picture with zoom and perspective;
    model; [Refine with Claude] [as written]; what it costs."""
    wm = context.window_manager
    box, head, is_open = section(layout, "krea", "Refine with Krea", 'BRUSHES_ALL')
    if not is_open:
        return
    if not krea.token():
        col = box.column(align=True)
        sub = col.row()
        sub.active = False
        sub.label(text="Needs a Krea API token (Preferences › Studio)", icon='INFO')
        col.operator(RS_OT_krea_token.bl_idname, icon='URL')
        p = runner.prefs(context)
        if p is not None:
            col.prop(p, "krea_token", text="", placeholder="Paste the token here")
        return
    grid = box.grid_flow(row_major=True, columns=4, even_columns=True, align=True)
    for label, text in krea.IDEAS:
        grid.operator(RS_OT_krea_idea.bl_idname, text=label).text = text
    box.prop(wm, "render_studio_krea_prompt", text="", placeholder="What should change? e.g. a hand holds it")
    # environment picture: the product goes into it; zoom out and another perspective need the Free model
    col = box.column(align=True)
    row = col.row(align=True)
    env = wm.render_studio_krea_env
    if env:
        row.label(text=os.path.basename(env), icon='IMAGE_DATA', translate=False)
        row.operator(RS_OT_krea_environment.bl_idname, text="", icon='X').clear = True
    else:
        row.operator(RS_OT_krea_environment.bl_idname, text="Environment Picture…", icon='IMAGE_DATA')
    row = col.row(align=True)
    row.prop(wm, "render_studio_krea_zoom", text="")
    row.prop(wm, "render_studio_krea_view", text="")
    row = box.row(align=True)
    row.prop(wm, "render_studio_krea_model", expand=True)
    used = krea.pick_model(wm.render_studio_krea_model, env, wm.render_studio_krea_zoom, wm.render_studio_krea_view)
    names = dict((k, n) for k, _p, n, _d in krea.MODELS)
    row = box.row(align=True)
    row.scale_y = 1.3
    row.enabled = bool(path)
    row.operator(RS_OT_krea_claude.bl_idname, icon='OUTLINER_DATA_GP_LAYER')
    row.operator(RS_OT_krea_refine.bl_idname, text="", icon='PLAY')
    # cost: the model and its price, what this month cost (Krea has no balance API: the button opens it)
    count, known, unknown = krea.month_summary()
    line = box.row(align=True)
    sub = line.row(align=True)
    sub.active = False
    text = "%s · %s" % (names[used], krea.price_text(used))
    sub.label(text=text, icon='FUND', translate=False)
    line.operator(RS_OT_krea_usage.bl_idname, text="", icon='URL', emboss=False)
    if count:
        sub = box.row()
        sub.active = False
        month = "This month: %d pictures" % count + (", about $%.2f" % known if known else "")
        if unknown:
            month += " + %d without a published price" % unknown
        sub.label(text=month, translate=False)
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
        if p is not None:
            box.prop(p, "krea_token", text="API Token")
        row = box.row(align=True)
        row.operator(RS_OT_krea_token.bl_idname, icon='URL')
        row.operator(RS_OT_krea_usage.bl_idname, icon='FUND')
        sub = box.row()
        sub.active = False
        sub.label(text="The API has its own balance (not the plan's credits)")


class RS_PT_finish(_SidebarPanel, Panel):
    studio_tab = 'FINISH'
    bl_label = "Finish"
    bl_options = {'HIDE_HEADER'}
    bl_order = FINISH_TAB_ORDER

    def draw(self, context):
        _unpin(self)
        layout = self.layout
        scene = context.scene
        tab_head(layout, RS_MT_finish_presets.bl_idname, "Look", RS_OT_look_save.bl_idname,
                 RS_PT_finish_settings.__name__, icon='NODE_COMPOSITING')
        path = _draw_image(layout, context)
        shading = _shading(context)
        live = shading is not None and getattr(shading, "use_compositor", 'DISABLED') != 'DISABLED'
        look.draw(layout, scene, live=live)
        _draw_krea(layout, context, path)


classes = (RS_OT_look_saved, RS_OT_look_save, RS_MT_finish_presets, RS_OT_look_live, RS_OT_krea_idea,
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
    wm.render_studio_krea_zoom = EnumProperty(name="Zoom", items=krea.ZOOMS, default='KEEP', options={'SKIP_SAVE'})
    wm.render_studio_krea_view = EnumProperty(name="Perspective", items=krea.VIEWS, default='KEEP',
                                              options={'SKIP_SAVE'})
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

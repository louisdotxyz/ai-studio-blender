# SPDX-License-Identifier: GPL-3.0-or-later
"""Render Studio (the "Studio" tab): collect cameras and render them in Low or High Quality.

Part of the combined extension: this sub-package registers after the chat; its
preferences live in the extension's preferences (``prefs.studio``) and its files
in <extension user dir>/studio. Settings in .blend files (Scene.render_studio)
and operator names (render_studio.*) are unchanged from the stand-alone add-on.

Renders go to the output folder from the preferences (Renders): a folder per file with
subfolders for Low, High, Animation and an archive for sorting by hand. The queue
renders in a second Blender without a window.
"""

import bpy
from bpy.props import (EnumProperty, PointerProperty, StringProperty)
from bpy.types import Operator, Panel

from . import background, cameras, claude_link, core, foreign, hdri, lights, runner, sync
# Loaded for their side effects: the chat finds claude_tools (Claude's studio_* tools) in
# sys.modules; api and variants are the helpers in execute_python.
from . import api, claude_tools, variants  # noqa: F401
from .props import RS_CameraSettings, RS_Item, RS_LightSettings, RS_Preferences, RS_Settings, _apply_clipping
from .ui_common import STUDIO_TABS, _queue_progress_text, _unpin
from .keymaps import register_keymaps, unregister_keymaps
from .tab_camera import (RS_OT_camera_back, RS_OT_camera_from_view, RS_OT_dof_focus, RS_OT_duplicate_camera,
                         RS_OT_fit_frame, RS_OT_reset_frame, RS_OT_set_camera, RS_OT_set_fstop,
                         RS_OT_set_lens, RS_OT_set_resolution, RS_OT_set_scale, RS_PT_camera, RS_PT_frame)
from .tab_light import (RS_MT_hdris, RS_MT_light_presets, RS_OT_add_light, RS_OT_bounces_preset,
                        RS_OT_duplicate_light, RS_OT_hdri_apply, RS_OT_hdri_favorite, RS_OT_hdri_refresh,
                        RS_OT_hdri_step, RS_OT_light_focus_selection, RS_OT_light_preset,
                        RS_OT_light_preset_delete, RS_OT_light_preset_save, RS_OT_light_setup,
                        RS_OT_lights_adjust, RS_OT_select_light, RS_OT_show_world, RS_OT_toggle_light,
                        RS_OT_world_restore, RS_PT_light_settings, RS_PT_lighting)
from .tab_material import (RS_MT_material_new, RS_MT_material_presets_delete, RS_OT_material_assign,
                           RS_OT_material_duplicate, RS_OT_material_group_toggle, RS_OT_material_new,
                           RS_OT_material_preset, RS_OT_material_preset_delete, RS_OT_material_preset_save,
                           RS_OT_material_presets_io, RS_OT_material_slot, RS_OT_material_unlink,
                           RS_OT_material_viewport, RS_OT_open_shading, RS_OT_pick_from_object,
                           RS_PT_material, RS_PT_material_settings)
from .tab_queue import (RS_OT_add, RS_OT_cancel, RS_OT_clear, RS_OT_low_scale, RS_OT_move,
                        RS_OT_preview_step, RS_OT_remove, RS_OT_render_queue, RS_OT_show_render,
                        RS_OT_view_camera, RS_PT_output, RS_PT_render_queue, draw_statusbar)
from .runner import RUN


def _tab_skills(tab):
    """Skills bound to a Studio tab (front matter studio_tab)."""
    try:
        from .. import core, skills
        return [s for s in skills.list_skills(core.workdir()) if s.get("studio_tab") == tab]
    except Exception:
        return []


class RS_OT_open_chat(Operator):
    bl_idname = "render_studio.open_chat"
    bl_label = "Open Claude"
    bl_description = "Switch to the Claude tab: your messages there use the skills of this Studio tab"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        claude_link.last_tab["dismissed"] = False
        claude_link.show_chat(context.region)
        return {'FINISHED'}


class RS_PT_tabs(Panel):
    """Switcher Render | Light | Material | Queue at the top of the Studio tab."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Studio"
    bl_label = "Studio"
    bl_options = {'HIDE_HEADER'}
    bl_order = 0

    def draw(self, context):
        _unpin(self)
        row = self.layout.row(align=True)
        row.scale_y = 1.3
        tab = context.window_manager.render_studio_tab
        row.prop(context.window_manager, "render_studio_tab", expand=True)
        # The chat uses this tab's skills from now on (shown there, ✕ turns them off).
        claude_link.note_tab(tab)
        bound = _tab_skills(tab)
        if bound:
            row = self.layout.row()
            row.alignment = 'RIGHT'
            row.operator(RS_OT_open_chat.bl_idname, text="Claude: " + ", ".join(s["title"] for s in bound),
                         icon='SHADERFX', emboss=False, translate=False)
        if RUN.busy:   # rendering: compact, in every sub-tab – ring for the queue, ✕ cancels
            row = self.layout.row(align=True)
            row.progress(factor=RUN.list_progress, type='BAR', text=_queue_progress_text())
            row.operator('render_studio.cancel', text="", icon='X', emboss=False)


# ------------------------------------------------------------------ Register

classes = (
    RS_LightSettings,
    RS_PT_tabs,
    RS_OT_low_scale,
    RS_PT_output,
    RS_OT_set_fstop,
    RS_OT_dof_focus,
    RS_OT_lights_adjust,
    RS_OT_bounces_preset,
    RS_OT_material_viewport,
    RS_OT_material_duplicate,
    RS_OT_material_unlink,
    RS_OT_open_shading,
    RS_OT_material_presets_io,
    RS_PT_light_settings,
    RS_PT_material_settings,
    RS_OT_fit_frame,
    RS_OT_reset_frame,
    RS_PT_frame,
    RS_CameraSettings,
    RS_Item,
    RS_Settings,
    RS_OT_render_queue,
    RS_OT_cancel,
    RS_OT_add,
    RS_OT_clear,
    RS_OT_remove,
    RS_OT_move,
    RS_OT_view_camera,
    RS_OT_set_camera,
    RS_OT_duplicate_camera,
    RS_OT_camera_back,
    RS_OT_camera_from_view,
    RS_OT_show_render,
    RS_OT_set_lens,
    RS_OT_set_resolution,
    RS_OT_set_scale,
    RS_OT_select_light,
    RS_OT_add_light,
    RS_OT_duplicate_light,
    RS_OT_light_preset,
    RS_OT_light_preset_save,
    RS_OT_light_preset_delete,
    RS_OT_light_setup,
    RS_OT_hdri_apply,
    RS_OT_hdri_favorite,
    RS_OT_hdri_refresh,
    RS_OT_world_restore,
    RS_OT_light_focus_selection,
    RS_OT_show_world,
    RS_OT_toggle_light,
    RS_OT_material_slot,
    RS_OT_material_preset,
    RS_OT_material_preset_save,
    RS_OT_material_preset_delete,
    RS_OT_material_assign,
    RS_OT_open_chat,
    RS_OT_preview_step,
    RS_OT_material_new,
    RS_OT_pick_from_object,
    RS_OT_material_group_toggle,
    RS_MT_material_new,
    RS_MT_material_presets_delete,
    RS_OT_hdri_step,
    RS_PT_camera,
    RS_MT_light_presets,
    RS_MT_hdris,
    RS_PT_lighting,
    RS_PT_material,
    RS_PT_render_queue,
)


_registered = False


def register_prefs():
    """Before the extension's preferences, which hold these settings."""
    bpy.utils.register_class(RS_Preferences)


def unregister_prefs():
    bpy.utils.unregister_class(RS_Preferences)


def register():
    global _registered
    if _registered:
        return
    _registered = True
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.render_studio = PointerProperty(type=RS_Settings)
    bpy.types.WindowManager.render_studio_tab = EnumProperty(
        name="Studio Tab", items=STUDIO_TABS, default='RENDER', options={'SKIP_SAVE'})
    # Material library groups toggled from their default (one name per line)
    bpy.types.WindowManager.render_studio_mat_open = StringProperty(options={'SKIP_SAVE'})
    bpy.types.Object.render_studio_camera = PointerProperty(type=RS_CameraSettings)
    bpy.types.Object.render_studio_light = PointerProperty(type=RS_LightSettings)
    bpy.types.STATUSBAR_HT_header.append(draw_statusbar)
    lights.register()
    runner.register()
    hdri.register()
    sync.register()
    cameras.register()
    cameras.register_history()
    background.register()
    foreign.register()

    register_keymaps()
    # Cameras of the file that is already open (load_post covers files opened later).
    bpy.app.timers.register(lambda: _apply_clipping() or None, first_interval=1.0)


def unregister():
    global _registered
    if not _registered:
        return
    _registered = False
    unregister_keymaps()
    if draw_statusbar in bpy.types.STATUSBAR_HT_header._dyn_ui_initialize():
        bpy.types.STATUSBAR_HT_header.remove(draw_statusbar)

    foreign.unregister()
    background.unregister()
    cameras.unregister_history()
    cameras.unregister()
    sync.unregister()
    hdri.unregister()
    runner.unregister()
    lights.unregister()
    del bpy.types.Object.render_studio_light
    del bpy.types.Object.render_studio_camera
    del bpy.types.Scene.render_studio
    del bpy.types.WindowManager.render_studio_tab
    del bpy.types.WindowManager.render_studio_mat_open
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

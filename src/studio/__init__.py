# SPDX-License-Identifier: GPL-3.0-or-later
"""Render Studio (the "Studio" tab): collect cameras and render them in Low or High Quality.

Part of the combined extension: this sub-package registers after the chat; its
preferences live in the extension's preferences (``prefs.studio``) and its files
in <extension user dir>/studio. Settings in .blend files (Scene.render_studio)
and operator names (render_studio.*) are unchanged from the stand-alone add-on.

Renders go to the output folder from the preferences (Renders): a folder per file with
subfolders for Low, High and Animation. The queue
renders in a second Blender without a window.
"""

import bpy
from bpy.props import (EnumProperty, PointerProperty, StringProperty)
from bpy.types import Operator, Panel

from . import (addons, background, cameras, claude_link, core, env_ui, environment, foreign, hdri,
               light_paint, light_ui, lights, presets_io, runner, shots, sync, tab_queue)
# Loaded for their side effects: the chat finds claude_tools (Claude's studio_* tools) in
# sys.modules; api and variants are the helpers in execute_python.
from . import api, baselib, claude_tools, light_items, looks, variants  # noqa: F401
from .props import (RS_CameraSettings, RS_HdriPin, RS_Item, RS_LightSettings, RS_Look, RS_Shot, RS_Preferences,
                    RS_Settings, _apply_clipping)
from .ui_common import STUDIO_TABS, _queue_progress_text, _unpin
from .keymaps import register_keymaps, unregister_keymaps
from .tab_camera import (RS_MT_camera_presets, RS_OT_camera_back, RS_OT_camera_from_view, RS_OT_camera_preset,
                         RS_OT_dof_focus, RS_OT_duplicate_camera,
                         RS_OT_fit_frame, RS_OT_reset_frame, RS_OT_set_camera, RS_OT_set_fstop,
                         RS_OT_set_lens, RS_OT_toggle_ortho, RS_OT_set_resolution, RS_OT_set_scale, RS_PT_camera, RS_PT_frame)
from .tab_light import (RS_MT_hdris, RS_MT_light_presets, RS_OT_add_light, RS_OT_bounces_preset,
                        RS_OT_duplicate_light, RS_OT_hdri_apply, RS_OT_hdri_favorite, RS_OT_hdri_refresh,
                        RS_OT_hdri_step, RS_OT_light_focus_selection, RS_OT_light_preset,
                        RS_OT_light_preset_delete, RS_OT_light_preset_save, RS_OT_light_setup,
                        RS_OT_lights_adjust, RS_OT_select_light, RS_OT_show_world, RS_OT_toggle_light,
                        RS_OT_world_restore, RS_PT_light_settings, RS_PT_lighting)
from .tab_material import (RS_MT_material_more, RS_MT_material_new, RS_MT_material_presets_delete, RS_OT_material_assign,
                           RS_OT_material_clean_unused, RS_OT_material_delete, RS_OT_material_favorite,
                           RS_OT_material_rename, RS_OT_material_select, RS_OT_material_variant,
                           draw_tile_menu, RS_OT_material_duplicate, RS_OT_material_group_toggle, RS_OT_material_new,
                           RS_OT_material_kind, RS_OT_material_kind_filter, RS_OT_material_structure, RS_OT_material_to_base,
                           RS_OT_material_wear,
                           RS_OT_material_preset, RS_OT_material_preset_delete, RS_OT_material_preset_save,
                           RS_OT_material_presets_io, RS_OT_material_slot, RS_OT_material_unlink,
                           RS_OT_material_viewport, RS_OT_open_shading, RS_OT_pick_from_object,
                           RS_PT_material, RS_PT_material_settings)
from .tab_queue import (RS_MT_render_presets, RS_OT_add, RS_OT_cancel, RS_OT_clear, RS_OT_low_scale, RS_OT_move,
                        RS_OT_preview_step, RS_OT_remove, RS_OT_render_queue, RS_OT_show_render,
                        RS_OT_view_camera, RS_PT_output, RS_PT_render_queue, draw_statusbar)
from .runner import RUN
from . import cad_link, render_setup
from .look import RS_OT_look_preset, RS_OT_look_reset


class RS_OT_open_chat(Operator):
    bl_idname = "render_studio.open_chat"
    bl_label = "Open Claude"
    bl_description = "Switch to the Claude tab: the whole answer, and talk about it there"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        claude_link.show_chat(context.region)
        return {'FINISHED'}


class RS_OT_section_toggle(bpy.types.Operator):
    bl_idname = "render_studio.section_toggle"
    bl_label = "Show / Hide"
    bl_description = "Open or close this section"
    bl_options = {'INTERNAL'}

    key: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        wm = context.window_manager
        keys = set(filter(None, wm.render_studio_sections.split("\n")))
        keys.symmetric_difference_update({self.key})
        wm.render_studio_sections = "\n".join(sorted(keys))
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
        row.prop(context.window_manager, "render_studio_tab", expand=True)
        if RUN.busy:   # rendering: compact, in every sub-tab – ring for the queue, ✕ cancels
            row = self.layout.row(align=True)
            row.progress(factor=RUN.list_progress, type='BAR', text=_queue_progress_text())
            row.operator('render_studio.cancel', text="", icon='X', emboss=False)


# ------------------------------------------------------------------ Register

classes = render_setup.classes + (
    RS_LightSettings,
    RS_OT_section_toggle,
    RS_PT_tabs,
    RS_OT_low_scale,
    RS_PT_output,
    RS_MT_render_presets,
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
    RS_Shot,
    RS_HdriPin,
    RS_Look,
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
    RS_OT_camera_preset,
    RS_MT_camera_presets,
    RS_OT_show_render,
    RS_OT_set_lens,
    RS_OT_toggle_ortho,
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
    RS_OT_look_preset,
    RS_OT_look_reset,
    RS_OT_material_assign,
    RS_OT_material_variant,
    RS_OT_material_select,
    RS_OT_material_favorite,
    RS_OT_material_structure,
    RS_OT_material_wear,
    RS_OT_material_kind,
    RS_OT_material_kind_filter,
    RS_OT_material_rename,
    RS_OT_material_delete,
    RS_OT_material_clean_unused,
    RS_OT_open_chat,
    RS_OT_preview_step,
    RS_OT_material_new,
    RS_OT_material_to_base,
    RS_OT_pick_from_object,
    RS_OT_material_group_toggle,
    RS_MT_material_more,
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


OLD_HDRI_DEFAULT = "~/Desktop/projects/+/HDRI"   # the default up to 0.23


def _keep_old_hdri_folder():
    """Who used the old default keeps it (it is no longer the default since 0.24)."""
    import os
    p = runner.prefs()
    if p is not None and not p.is_property_set("hdri_folder") \
            and os.path.isdir(os.path.expanduser(OLD_HDRI_DEFAULT)):
        p.hdri_folder = OLD_HDRI_DEFAULT
        bpy.context.preferences.is_dirty = True
    return None


def _tab_changed(wm, context):
    """Environment tab: clicks on the product place the open light; elsewhere the tool before is back."""
    try:
        if wm.render_studio_tab != 'LIGHT':
            light_paint.restore_tool(context)
        elif light_items.active_item(context.scene) is not None and not wm.render_studio_place_off:
            light_paint.activate_tool(context)
    except Exception as exc:        # a tab switch must never fail
        print("ID Toolkit: tab:", exc)


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
        name="Studio Tab", items=STUDIO_TABS, default='RENDER', options={'SKIP_SAVE'}, update=_tab_changed)
    # Material library groups toggled from their default (one name per line)
    bpy.types.WindowManager.render_studio_mat_open = StringProperty(options={'SKIP_SAVE'})
    # Sections of the Studio tabs toggled from their default (one key per line)
    bpy.types.WindowManager.render_studio_sections = StringProperty(options={'SKIP_SAVE'})
    bpy.types.WindowManager.render_studio_mat_source = EnumProperty(
        name="Library", options={'SKIP_SAVE'}, default='PROJECT',
        items=[('PROJECT', "Project", "The materials of this file", 'NONE', 1),
               ('LIBRARY', "Library", "Your materials for every project (the base material library)", 'NONE', 2),
               ('FAVORITES', "", "Your favorites (the heart on a tile)", 'FUND', 3)])
    bpy.types.WindowManager.render_studio_mat_kind = StringProperty(
        name="Kind", description="Show only materials of this kind", options={'SKIP_SAVE'})
    bpy.types.WindowManager.render_studio_mat_search = StringProperty(
        name="Search", description="Show only materials whose name contains this", options={'SKIP_SAVE'})
    bpy.types.UI_MT_button_context_menu.append(draw_tile_menu)
    bpy.types.Object.render_studio_camera = PointerProperty(type=RS_CameraSettings)
    bpy.types.Object.render_studio_light = PointerProperty(type=RS_LightSettings)
    bpy.types.STATUSBAR_HT_header.append(draw_statusbar)
    if tab_queue.on_live_image not in runner.on_live_image:
        runner.on_live_image.append(tab_queue.on_live_image)
    bpy.app.timers.register(_keep_old_hdri_folder, first_interval=0.2)
    lights.register()
    runner.register()
    hdri.register()
    sync.register()
    cameras.register()
    cameras.register_history()
    background.register()
    foreign.register()
    addons.register()
    env_ui.register()
    light_paint.register()
    light_ui.register()
    presets_io.register()
    shots.register()
    cad_link.register()

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
    if tab_queue.on_live_image in runner.on_live_image:
        runner.on_live_image.remove(tab_queue.on_live_image)

    cad_link.unregister()
    shots.unregister()
    presets_io.unregister()
    light_ui.unregister()
    light_paint.unregister()
    env_ui.unregister()
    addons.unregister()
    foreign.unregister()
    background.unregister()
    cameras.unregister_history()
    cameras.unregister()
    sync.unregister()
    hdri.unregister()
    if bpy.app.timers.is_registered(_keep_old_hdri_folder):
        bpy.app.timers.unregister(_keep_old_hdri_folder)
    runner.unregister()
    lights.unregister()
    del bpy.types.Object.render_studio_light
    del bpy.types.Object.render_studio_camera
    del bpy.types.Scene.render_studio
    del bpy.types.WindowManager.render_studio_tab
    del bpy.types.WindowManager.render_studio_mat_open
    del bpy.types.WindowManager.render_studio_sections
    del bpy.types.WindowManager.render_studio_mat_search
    del bpy.types.WindowManager.render_studio_mat_kind
    del bpy.types.WindowManager.render_studio_mat_source
    bpy.types.UI_MT_button_context_menu.remove(draw_tile_menu)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

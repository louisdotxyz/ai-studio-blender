# SPDX-License-Identifier: GPL-3.0-or-later
"""AI Studio (the "Studio" tab): collect cameras and render them in Low or High Quality.

Part of the combined extension: this sub-package registers after the chat; its
preferences live in the extension's preferences (``prefs.studio``) and its files
in <extension user dir>/studio. Settings in .blend files (Scene.studio)
and operator names (studio.*) are unchanged from the stand-alone add-on.

Renders go to the output folder from the preferences (Renders): a folder per file with
subfolders for Low, High and Animation. The queue
renders in a second Blender without a window.
"""

import bpy
from bpy.props import (EnumProperty, PointerProperty, StringProperty)
from bpy.types import Operator, Panel

from . import (addons, background, cameras, claude_link, core, env_ui, environment, foreign, hdri,
               light_paint, light_ui, lights, presets_io, render_size, runner, shots, sync, tab_finish, tab_queue,
               variants_ab, chat_render, render_review, film, migrate, keys, motion, cut, explode, autoquality, project)
# Loaded for their side effects: the chat finds claude_tools (Claude's studio_* tools) in
# sys.modules; api and variants are the helpers in execute_python.
from . import api, baselib, claude_tools, light_items, looks, quality, variants  # noqa: F401
from .props import (STUDIO_CameraSettings, STUDIO_HdriPin, STUDIO_Item, STUDIO_LightSettings, STUDIO_Look, STUDIO_MotionStep, STUDIO_CutItem, STUDIO_Shot, STUDIO_Preferences,
                    STUDIO_Settings, _apply_clipping)
from .ui_common import STUDIO_TABS, _unpin
from .keymaps import register_keymaps, unregister_keymaps
from .tab_camera import (STUDIO_MT_aspect, STUDIO_MT_formats, STUDIO_OT_set_aspect, STUDIO_MT_camera_presets, STUDIO_OT_camera_back, STUDIO_OT_camera_from_view, STUDIO_OT_camera_preset,
                         STUDIO_OT_dof_focus, STUDIO_OT_duplicate_camera,
                         STUDIO_OT_fit_frame, STUDIO_OT_reset_frame, STUDIO_OT_set_camera, STUDIO_OT_set_fstop,
                         STUDIO_OT_set_lens, STUDIO_OT_toggle_ortho, STUDIO_OT_set_resolution, STUDIO_OT_set_scale, STUDIO_PT_camera, STUDIO_PT_frame)
from .tab_light import (STUDIO_MT_hdris, STUDIO_MT_light_presets, STUDIO_OT_add_light, STUDIO_OT_bounces_preset,
                        STUDIO_OT_duplicate_light, STUDIO_OT_hdri_apply, STUDIO_OT_hdri_favorite, STUDIO_OT_hdri_refresh,
                        STUDIO_OT_hdri_step, STUDIO_OT_light_focus_selection, STUDIO_OT_light_preset,
                        STUDIO_OT_light_preset_delete, STUDIO_OT_light_preset_save, STUDIO_OT_light_setup,
                        STUDIO_OT_lights_adjust, STUDIO_OT_select_light, STUDIO_OT_show_world, STUDIO_OT_toggle_light,
                        STUDIO_OT_world_restore, STUDIO_PT_light_settings, STUDIO_PT_lighting)
from .tab_material import (STUDIO_MT_material_more, STUDIO_MT_material_new, STUDIO_MT_material_presets_delete, STUDIO_OT_material_assign,
                           STUDIO_OT_material_open,
                           STUDIO_OT_material_clean_unused, STUDIO_OT_material_delete, STUDIO_OT_material_favorite,
                           STUDIO_OT_material_rename, STUDIO_OT_material_select, STUDIO_OT_material_variant,
                           draw_tile_menu, STUDIO_OT_material_duplicate, STUDIO_OT_material_group_toggle, STUDIO_OT_material_new,
                           STUDIO_OT_material_kind, STUDIO_OT_material_kind_filter, STUDIO_MT_material_kind_filter, STUDIO_OT_material_structure, STUDIO_OT_material_to_base,
                           STUDIO_OT_material_wear,
                           STUDIO_OT_material_preset, STUDIO_OT_material_preset_delete, STUDIO_OT_material_preset_save,
                           STUDIO_OT_material_presets_io, STUDIO_OT_material_slot, STUDIO_OT_material_unlink,
                           STUDIO_OT_material_viewport, STUDIO_OT_open_shading, STUDIO_OT_pick_from_object,
                           STUDIO_PT_material, STUDIO_PT_material_settings)
from .tab_queue import (STUDIO_MT_render_presets, STUDIO_OT_add, STUDIO_OT_cancel, STUDIO_OT_clear, STUDIO_OT_low_scale, STUDIO_OT_move,
                        STUDIO_OT_preview_step, STUDIO_OT_remove, STUDIO_OT_render_queue, STUDIO_OT_show_render,
                        STUDIO_OT_render_overview, STUDIO_OT_render_pick, STUDIO_OT_render_reveal,
                        STUDIO_OT_render_ab, STUDIO_OT_render_rate, STUDIO_OT_render_rename, STUDIO_PT_photo_export, draw_photo_header, STUDIO_OT_photo_step, STUDIO_OT_goto_workspace, STUDIO_RenderEntry, STUDIO_UL_renders, _on_render_index,
                        STUDIO_OT_render_arrange, STUDIO_MT_render_arrange,
                        STUDIO_OT_render_refine, STUDIO_OT_open_review, STUDIO_PT_review,
                        STUDIO_OT_view_camera, STUDIO_PT_output, STUDIO_PT_render_queue, draw_statusbar)
from . import cad_link, render_setup
from .look import STUDIO_OT_look_preset, STUDIO_OT_look_reset, STUDIO_OT_open_compositor


class STUDIO_OT_open_chat(Operator):
    bl_idname = "studio.open_chat"
    bl_label = "Open Claude"
    bl_description = "Switch to the Claude tab: the whole answer, and talk about it there"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        claude_link.show_chat(context.region)
        return {'FINISHED'}


class STUDIO_OT_section_toggle(bpy.types.Operator):
    bl_idname = "studio.section_toggle"
    bl_label = "Show / Hide"
    bl_description = "Open or close this section"
    bl_options = {'INTERNAL'}

    key: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        wm = context.window_manager
        toggled = set(filter(None, wm.studio_sections.split("\n")))
        toggled.symmetric_difference_update({self.key})
        wm.studio_sections = "\n".join(sorted(toggled))
        return {'FINISHED'}


class STUDIO_OT_tab(bpy.types.Operator):
    bl_idname = "studio.tab"
    bl_label = "Tab"
    bl_options = {'INTERNAL'}

    tab: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return next((f"{name}: {text}" for key, name, text, _i, _n in STUDIO_TABS if key == props.tab), "")

    def execute(self, context):
        context.window_manager.studio_tab = self.tab
        return {'FINISHED'}


class STUDIO_PT_tabs(Panel):
    """Switcher Render | Light | Material | Queue at the top of the Studio tab."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Studio"
    bl_label = "Studio"
    bl_options = {'HIDE_HEADER'}
    bl_order = 0

    def draw(self, context):
        _unpin(self)
        # the five tabs as equal icons (names in the tooltips)
        from .ui_common import studio_tab, visible_tabs
        row = self.layout.row(align=True)
        row.scale_y = 1.3
        shown = visible_tabs(context.scene)
        current = studio_tab(context)
        grid = row.grid_flow(row_major=True, columns=len(shown), even_columns=True, align=True)
        for key, _name, _text, icon, _n in STUDIO_TABS:
            if key in shown:     # the tab a hidden one falls back to looks pressed
                grid.operator("studio.tab", text="", icon=icon, depress=key == current).tab = key
        row.popover("STUDIO_PT_project", text="", icon='FILE_BLEND')   # scenes, archive, CAD rules


# ------------------------------------------------------------------ Register

from . import renderfarm, tab_anim

classes = render_setup.classes + tab_anim.classes + (
    STUDIO_LightSettings,
    STUDIO_OT_section_toggle,
    STUDIO_OT_tab,
    STUDIO_PT_tabs,
    STUDIO_OT_low_scale,
    STUDIO_PT_output,
    STUDIO_MT_render_presets,
    STUDIO_OT_set_fstop,
    STUDIO_OT_dof_focus,
    STUDIO_OT_lights_adjust,
    STUDIO_OT_bounces_preset,
    STUDIO_OT_material_viewport,
    STUDIO_OT_material_duplicate,
    STUDIO_OT_material_unlink,
    STUDIO_OT_open_shading,
    STUDIO_OT_material_presets_io,
    STUDIO_PT_light_settings,
    STUDIO_PT_material_settings,
    STUDIO_OT_fit_frame,
    STUDIO_OT_reset_frame,
    STUDIO_PT_frame,
    STUDIO_CameraSettings,
    STUDIO_Item,
    STUDIO_MotionStep,
    STUDIO_CutItem,
    STUDIO_Shot,
    STUDIO_HdriPin,
    STUDIO_Look,
    quality.STUDIO_Quality,
    STUDIO_Settings,
    STUDIO_OT_render_queue,
    STUDIO_OT_cancel,
    STUDIO_OT_add,
    STUDIO_OT_clear,
    STUDIO_OT_remove,
    STUDIO_OT_move,
    STUDIO_OT_view_camera,
    STUDIO_OT_set_camera,
    STUDIO_OT_duplicate_camera,
    STUDIO_OT_camera_back,
    STUDIO_OT_camera_from_view,
    STUDIO_OT_camera_preset,
    STUDIO_MT_camera_presets,
    STUDIO_OT_set_aspect,
    STUDIO_MT_aspect,
    STUDIO_MT_formats,
    STUDIO_OT_show_render,
    STUDIO_OT_set_lens,
    STUDIO_OT_toggle_ortho,
    STUDIO_OT_set_resolution,
    STUDIO_OT_set_scale,
    STUDIO_OT_select_light,
    STUDIO_OT_add_light,
    STUDIO_OT_duplicate_light,
    STUDIO_OT_light_preset,
    STUDIO_OT_light_preset_save,
    STUDIO_OT_light_preset_delete,
    STUDIO_OT_light_setup,
    STUDIO_OT_hdri_apply,
    STUDIO_OT_hdri_favorite,
    STUDIO_OT_hdri_refresh,
    STUDIO_OT_world_restore,
    STUDIO_OT_light_focus_selection,
    STUDIO_OT_show_world,
    STUDIO_OT_toggle_light,
    STUDIO_OT_material_slot,
    STUDIO_OT_material_preset,
    STUDIO_OT_material_preset_save,
    STUDIO_OT_material_preset_delete,
    STUDIO_OT_look_preset,
    STUDIO_OT_open_compositor,
    STUDIO_OT_look_reset,
    STUDIO_OT_material_assign,
    STUDIO_OT_material_open,
    STUDIO_OT_material_variant,
    STUDIO_OT_material_select,
    STUDIO_OT_material_favorite,
    STUDIO_OT_material_structure,
    STUDIO_OT_material_wear,
    STUDIO_OT_material_kind,
    STUDIO_OT_material_kind_filter,
    STUDIO_MT_material_kind_filter,
    STUDIO_OT_material_rename,
    STUDIO_OT_material_delete,
    STUDIO_OT_material_clean_unused,
    STUDIO_OT_open_chat,
    STUDIO_OT_preview_step,
    STUDIO_OT_render_pick,
    STUDIO_OT_render_reveal,
    STUDIO_OT_render_overview,
    STUDIO_OT_render_ab,
    STUDIO_OT_render_rate,
    STUDIO_OT_render_rename,
    STUDIO_PT_photo_export,
    STUDIO_OT_photo_step,
    STUDIO_OT_goto_workspace,
    STUDIO_RenderEntry,
    STUDIO_UL_renders,
    STUDIO_OT_render_arrange,
    STUDIO_MT_render_arrange,
    STUDIO_OT_render_refine,
    STUDIO_OT_material_new,
    STUDIO_OT_material_to_base,
    STUDIO_OT_pick_from_object,
    STUDIO_OT_material_group_toggle,
    STUDIO_MT_material_more,
    STUDIO_MT_material_new,
    STUDIO_MT_material_presets_delete,
    STUDIO_OT_hdri_step,
    STUDIO_PT_camera,
    STUDIO_MT_light_presets,
    STUDIO_MT_hdris,
    STUDIO_PT_lighting,
    STUDIO_PT_material,
    STUDIO_PT_render_queue,
    STUDIO_OT_open_review,
    STUDIO_PT_review,
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
        if wm.studio_tab != 'LIGHT':
            light_paint.restore_tool(context)
        elif light_items.active_item(context.scene) is not None and not wm.studio_place_off:
            light_paint.activate_tool(context)
    except Exception as exc:        # a tab switch must never fail
        print("ID Toolkit: tab:", exc)


def register_prefs():
    """Before the extension's preferences, which hold these settings."""
    bpy.utils.register_class(STUDIO_Preferences)


def unregister_prefs():
    bpy.utils.unregister_class(STUDIO_Preferences)


def register():
    global _registered
    if _registered:
        return
    _registered = True
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.studio = PointerProperty(type=STUDIO_Settings)
    bpy.types.WindowManager.studio_tab = EnumProperty(
        name="Studio Tab", items=STUDIO_TABS, default='RENDER', options={'SKIP_SAVE'}, update=_tab_changed)
    # Material library groups toggled from their default (one name per line)
    bpy.types.WindowManager.studio_mat_open = StringProperty(options={'SKIP_SAVE'})
    # Sections of the Studio tabs toggled from their default (one key per line)
    bpy.types.WindowManager.studio_sections = StringProperty(options={'SKIP_SAVE'})
    bpy.types.WindowManager.studio_mat_source = EnumProperty(
        name="Library", options={'SKIP_SAVE'}, default='PROJECT',
        items=[('PROJECT', "Project", "The materials of this file", 'NONE', 1),
               ('LIBRARY', "Library", "Your materials for every project (the base material library)", 'NONE', 2),
               ('FAVORITES', "", "Your favorites (the heart on a tile)", 'FUND', 3)])
    bpy.types.WindowManager.studio_mat_kind = StringProperty(
        name="Kind", description="Show only materials of this kind", options={'SKIP_SAVE'})
    bpy.types.WindowManager.studio_mat_search = StringProperty(
        name="Search", description="Show only materials whose name contains this", options={'SKIP_SAVE'})
    bpy.types.UI_MT_button_context_menu.append(draw_tile_menu)
    bpy.types.Object.studio_camera = PointerProperty(type=STUDIO_CameraSettings)
    bpy.types.Object.studio_light = PointerProperty(type=STUDIO_LightSettings)
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
    tab_finish.register()
    from . import mockup, photo_tools, photo_view
    photo_view.register()
    photo_tools.register()
    mockup.register()
    variants_ab.register()
    film.register()
    renderfarm.register()
    keys.register()
    motion.register()
    cut.register()
    explode.register()
    autoquality.register()
    project.register()
    chat_render.register()
    render_size.register()
    quality.register()
    bpy.types.IMAGE_HT_header.append(draw_photo_header)
    bpy.types.WindowManager.studio_render_layout = EnumProperty(
        name="Show", options={'SKIP_SAVE'}, default='GRID',
        items=[('GRID', "Grid", "Pictures in a grid", 'IMGDISPLAY', 0),
               ('LIST', "List", "One line each", 'LONGDISPLAY', 1)])
    bpy.types.WindowManager.studio_render_items = bpy.props.CollectionProperty(type=STUDIO_RenderEntry,
                                                                            options={'SKIP_SAVE'})
    bpy.types.WindowManager.studio_render_index = bpy.props.IntProperty(
        default=-1, options={'SKIP_SAVE'}, update=_on_render_index)
    bpy.types.WindowManager.studio_render_columns = bpy.props.IntProperty(
        name="Columns", description="How many pictures side by side (fewer = bigger)", min=1, max=6, default=2,
        options={'SKIP_SAVE'})
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

    render_review.unregister()
    renderfarm.unregister()
    film.unregister()
    keys.unregister()
    explode.unregister()
    autoquality.unregister()
    cut.unregister()
    motion.unregister()
    project.unregister()
    cad_link.unregister()
    shots.unregister()
    presets_io.unregister()
    quality.unregister()
    bpy.types.IMAGE_HT_header.remove(draw_photo_header)
    render_size.unregister()
    chat_render.unregister()
    variants_ab.unregister()
    from . import mockup, photo_tools, photo_view
    mockup.unregister()
    photo_tools.unregister()
    photo_view.unregister()
    tab_finish.unregister()
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
    del bpy.types.Object.studio_light
    del bpy.types.Object.studio_camera
    del bpy.types.Scene.studio
    del bpy.types.WindowManager.studio_tab
    del bpy.types.WindowManager.studio_mat_open
    del bpy.types.WindowManager.studio_sections
    del bpy.types.WindowManager.studio_mat_search
    del bpy.types.WindowManager.studio_mat_kind
    del bpy.types.WindowManager.studio_mat_source
    bpy.types.UI_MT_button_context_menu.remove(draw_tile_menu)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

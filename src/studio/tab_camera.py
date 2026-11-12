# SPDX-License-Identifier: GPL-3.0-or-later
"""The Render tab: cameras, lens, format, framing, depth of field."""

import bpy
from bpy.props import (BoolProperty, FloatProperty, IntProperty, StringProperty)
from bpy.types import Operator, Panel

from . import background, cameras, look, runner, sync
from .props import FORMAT_PRESETS, PPI
from .ui_common import (RENDER_TAB_ORDER, _SidebarPanel, _look_through, _unpin,
                        active_camera, draw_claude_prompt, studio_tab)


class RS_OT_set_camera(Operator):
    bl_idname = "render_studio.set_camera"
    bl_label = "Switch Camera"
    bl_description = "Makes this camera the active camera and looks through it"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        cam = bpy.data.objects.get(self.name)
        if cam is None or cam.type != 'CAMERA':
            return {'CANCELLED'}
        _look_through(context, cam)
        return {'FINISHED'}


class RS_OT_camera_back(Operator):
    bl_idname = "render_studio.camera_back"
    bl_label = "Camera Back"
    bl_description = ("Put the camera back where it was before it was last moved, also after moving it "
                      "with Lock Camera to View, which Undo does not catch (up to 20 steps)")
    bl_options = {'UNDO', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        cam = active_camera(context.scene)
        return cam is not None and bool(cameras.history(cam))

    def execute(self, context):
        cam = active_camera(context.scene)
        if not cameras.go_back(cam):
            return {'CANCELLED'}
        sync.schedule_fit()
        return {'FINISHED'}


class RS_OT_duplicate_camera(Operator):
    bl_idname = "render_studio.duplicate_camera"
    bl_label = "Duplicate Camera"
    bl_description = "Duplicates the active camera into the camera collection (Camera) and makes the copy active"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        cam = active_camera(context.scene)
        return cam is not None and cam.type == 'CAMERA'

    def execute(self, context):
        scene = context.scene
        src = active_camera(scene)   # the user's camera, also while the queue renders a copy
        name = cameras.scheme_name('CAMERA', src.data.type, src.name)   # "P_side_02", never ".001"
        new = src.copy()
        new.data = src.data.copy()
        new.name = new.data.name = name
        for key in (runner.TEMP_KEY, cameras.HISTORY_KEY):
            if key in new:
                del new[key]
        if src.animation_data and src.animation_data.action:
            new.animation_data.action = src.animation_data.action.copy()
        view_layer = context.view_layer
        visible = cameras.cameras_visible(scene, view_layer)
        cameras.camera_collection(scene).objects.link(new)
        if not visible:
            new.hide_set(True)
        elif src.name in view_layer.objects:
            src.select_set(False)
            new.select_set(True)
            view_layer.objects.active = new
        _look_through(context, new)
        self.report({'INFO'}, "%s created" % new.name)
        return {'FINISHED'}


class RS_OT_camera_from_view(Operator):
    bl_idname = "render_studio.camera_from_view"
    bl_label = "Camera From View"
    bl_description = ("Saves the current viewport view as a new camera and looks through it. "
                      "Shift+click also adds it to the render queue")
    bl_options = {'UNDO'}

    add_to_queue: BoolProperty(default=False, options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        space = context.space_data
        return space is not None and space.type == 'VIEW_3D' and space.region_3d is not None

    def invoke(self, context, event):
        self.add_to_queue = event.shift
        return self.execute(context)

    def execute(self, context):
        scene = context.scene
        cam = cameras.camera_from_view(scene, context.space_data, context.view_layer)
        _look_through(context, cam)
        if self.add_to_queue:
            bpy.ops.render_studio.add()
        self.report({'INFO'}, "%s created" % cam.name)
        return {'FINISHED'}


LENS_PRESETS = (24, 35, 50, 85, 135)


class RS_OT_set_lens(Operator):
    bl_idname = "render_studio.set_lens"
    bl_label = "Set Focal Length"
    bl_description = "Sets the focal length of the active camera"
    bl_options = {'UNDO', 'INTERNAL'}

    lens: FloatProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=50.0, min=1.0)

    @classmethod
    def poll(cls, context):
        cam = context.scene.camera
        return cam is not None and cam.type == 'CAMERA'

    def execute(self, context):
        context.scene.camera.data.lens = self.lens
        return {'FINISHED'}


class RS_OT_set_resolution(Operator):
    bl_idname = "render_studio.set_resolution"
    bl_label = "Format"
    bl_description = "Sets the resolution"
    bl_options = {'UNDO', 'INTERNAL'}

    width: IntProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=1920, min=4)
    height: IntProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=1080, min=4)

    @classmethod
    def description(cls, _context, props):
        return "%d × %d pixels" % (props.width, props.height)

    def execute(self, context):
        render = context.scene.render
        render.resolution_x, render.resolution_y = self.width, self.height
        sync.schedule_fit()
        return {'FINISHED'}


class RS_OT_set_scale(Operator):
    bl_idname = "render_studio.set_scale"
    bl_label = "Scale"
    bl_description = "Renders at 0.5x, 1x, 2x or 4x the resolution"
    bl_options = {'UNDO', 'INTERNAL'}

    factor: FloatProperty(options={'HIDDEN', 'SKIP_SAVE'}, default=1.0, min=0.1)

    def execute(self, context):
        context.scene.render.resolution_percentage = round(100 * self.factor)
        return {'FINISHED'}


class RS_PT_camera(_SidebarPanel, Panel):
    """Everything about the camera and the image, in the Render tab. Without a
    header so the tab stays in front (see above). The boxes give the controls a
    solid background over the viewport (Region Overlap)."""
    bl_label = "Camera"
    bl_options = {'HIDE_HEADER'}
    bl_order = RENDER_TAB_ORDER
    studio_tab = 'RENDER'

    def draw(self, context):
        _unpin(self)
        layout = self.layout
        draw_claude_prompt(layout.box(), context, 'RENDER')
        scene = context.scene
        # Set Up for Product Renders is in the chat's setup card, the CAD box in the chat (Claude tab)
        rq = scene.render_studio
        render = scene.render
        cam = active_camera(scene)

        # Camera bar, orbit object and lock
        box = layout.box()
        row = box.row(align=True)
        if cam is not None and cam.type == 'CAMERA':
            row.prop(cam.render_studio_camera, "short_name", text="", icon='CAMERA_DATA')   # rename right here
        else:
            row.label(text="Camera", icon='CAMERA_DATA')
        # Moved the camera by accident (e.g. with "Lock Camera to View", which ⌘Z does not undo)?
        steps = len(cameras.history(cam)) if cam is not None else 0
        back = row.row(align=True)
        back.enabled = steps > 0 and not rq.camera_locked
        back.operator(RS_OT_camera_back.bl_idname, text="", icon='LOOP_BACK')
        row.popover(RS_PT_frame.__name__, text="", icon='PREFERENCES')
        grid = box.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
        for obj in cameras.scene_cameras(scene):
            grid.operator(RS_OT_set_camera.bl_idname, text=obj.name, depress=(obj == cam)).name = obj.name
        grid.operator(RS_OT_duplicate_camera.bl_idname, text="", icon='ADD')
        grid.operator(RS_OT_camera_from_view.bl_idname, text="", icon='VIEW_CAMERA')
        if cam is None or cam.type != 'CAMERA':
            box.label(text="No scene camera")
            return
        locked = rq.camera_locked
        row = box.row(align=True)
        sub = row.row(align=True)
        sub.enabled = not locked
        sub.prop(rq, "orbit_target", text="", icon='PIVOT_CURSOR', placeholder="Orbit Around: 3D Cursor")
        row.prop(rq, "camera_locked", text="", icon='LOCKED' if locked else 'UNLOCKED')

        # Lens, format, scale
        data = cam.data
        space = context.space_data
        box = layout.box()
        col = box.column(align=True)
        row = col.row(align=True)
        if data.type == 'ORTHO':
            row.prop(rq, "cam_ortho_scale", text="Ortho Scale")
        else:
            row.prop(data, "lens", text="Focal Length")
        if space is not None and space.type == 'VIEW_3D':
            sub = row.row(align=True)
            sub.active = not locked
            sub.prop(space, "lock_camera", text="", icon='VIEW_CAMERA')
        if data.type == 'PERSP':
            row = col.row(align=True)
            for mm in LENS_PRESETS:
                row.operator(RS_OT_set_lens.bl_idname, text=str(mm),
                             depress=abs(data.lens - mm) < 0.01).lens = mm
        draw_dof(box, cam, space)

        col = box.column(align=True)
        row = col.row(align=True)
        for label, _long, w, h in FORMAT_PRESETS:
            op = row.operator(RS_OT_set_resolution.bl_idname, text=label,
                              depress=(render.resolution_x, render.resolution_y) == (w, h))
            op.width, op.height = w, h
        # "4 : 5" with a narrow colon, the pin keeps the format for this camera
        col.separator(factor=0.5)
        row = col.row(align=True)
        row.prop(rq, "aspect_x", text="")
        sub = row.row(align=True)
        sub.ui_units_x = 0.7
        sub.alignment = 'CENTER'
        sub.label(text=":")
        row.prop(rq, "aspect_y", text="")
        own = cam.render_studio_camera
        sub = row.row(align=True)
        sub.prop(own, "use_resolution", text="", icon='PINNED' if own.use_resolution else 'UNPINNED')
        pct = render.resolution_percentage / 100
        sub = col.row()
        sub.alignment = 'CENTER'
        sub.active = False
        sub.label(text="%d × %d px · %d ppi" % (round(render.resolution_x * pct), round(render.resolution_y * pct), PPI))

        # Orbit around the picked object (or the 3D cursor)
        box = layout.box()
        col = box.column(align=True)
        col.enabled = not locked
        col.prop(rq, "orbit_horizontal")
        col.prop(rq, "orbit_vertical")
        col.prop(rq, "orbit_distance")
        col.prop(rq, "orbit_height")
        col.prop(rq, "orbit_side_offset")
        col.prop(rq, "orbit_up_offset")

        # Background
        box = layout.box()
        col = box.column(align=True)
        col.row(align=True).prop(rq, "background_mode", expand=True)
        space = context.space_data
        if rq.background_mode == 'COLOR':
            if rq.use_gradient:
                col.prop(rq, "background_color_top", text="")
            col.prop(rq, "background_color", text="")
            col = box.column(align=True)
            col.prop(rq, "use_gradient", toggle=True)
            row = col.row(align=True)
            row.prop(rq, "noise_intensity", text="Noise")
            if rq.noise_intensity > 0:
                row.prop(rq, "noise_size", text="Size")
            shading = getattr(space, "shading", None)
            if shading is not None and shading.type in {'SOLID', 'WIREFRAME'}:
                box.label(text="Shown in Material Preview and Rendered", icon='INFO')
        elif rq.background_mode == 'NORMAL':
            # The World itself (HDRI, strength, rotation) is set in the Light tab.
            problems = background.world_problems(scene, space)
            if problems:
                sub = box.column(align=True)
                for text in problems:
                    sub.label(text=text, icon='ERROR')
                box.operator('render_studio.show_world', icon='WORLD')

        look.draw(layout, scene)


class RS_OT_fit_frame(Operator):
    bl_idname = "render_studio.fit_frame"
    bl_label = "Fit Now"
    bl_description = "Center and size the camera frame in every viewport that looks through the camera"

    def execute(self, context):
        sync.fit_camera_views(force=True)
        return {'FINISHED'}


class RS_OT_reset_frame(Operator):
    bl_idname = "render_studio.reset_frame"
    bl_label = "Reset"
    bl_description = "Back to the default size and position"

    def execute(self, context):
        p = runner.prefs(context)
        if p is not None:
            for name in ("frame_auto", "frame_size", "frame_shift_x", "frame_shift_y"):
                p.property_unset(name)
        sync.fit_camera_views()
        return {'FINISHED'}


class RS_PT_frame(Panel):
    """Gear menu in the Render tab: composition guides, clipping, camera frame."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Camera Settings"
    bl_ui_units_x = 14

    def draw(self, context):
        layout = self.layout
        p = runner.prefs(context)
        if p is None:
            layout.label(text="Preferences not available")
            return
        cam = active_camera(context.scene)
        if cam is not None and cam.type == 'CAMERA':
            box = layout.box()
            box.label(text="Composition Guides", icon='MESH_GRID')
            data = cam.data
            grid = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
            grid.prop(data, "show_composition_thirds", text="Thirds", toggle=True)
            grid.prop(data, "show_composition_golden", text="Golden Ratio", toggle=True)
            grid.prop(data, "show_composition_center", text="Center", toggle=True)
            grid.prop(data, "show_composition_center_diagonal", text="Diagonal", toggle=True)
            box.prop(data, "passepartout_alpha", text="Passepartout", slider=True)

        box = layout.box()
        box.label(text="Clipping", icon='VIEW_PERSPECTIVE')
        box.prop(p, "min_clipping")
        if cam is not None and cam.type == 'CAMERA' and not p.min_clipping:
            row = box.row(align=True)
            row.prop(context.scene.render_studio, "cam_clip_start", text="Start")
            row.prop(context.scene.render_studio, "cam_clip_end", text="End")

        layout.label(text="Camera Frame", icon='SELECT_SET')
        layout.prop(p, "frame_auto")
        if p.frame_auto:
            col = layout.column(align=True)
            col.prop(p, "frame_size", slider=True)
            col.prop(p, "frame_shift_x", slider=True)
            col.prop(p, "frame_shift_y", slider=True)
        row = layout.row(align=True)
        row.operator(RS_OT_fit_frame.bl_idname, icon='VIEW_CAMERA')
        row.operator(RS_OT_reset_frame.bl_idname, icon='LOOP_BACK')


FSTOPS = (1.4, 2.8, 4, 5.6, 8, 16)


def dof_hint(space):
    """Why the viewport does not show the depth of field right now, or ""."""
    if space is None or space.type != 'VIEW_3D':
        return ""
    if space.shading.type in {'SOLID', 'WIREFRAME'}:
        return "Shown in Material Preview and Rendered"
    region = space.region_3d
    if region is not None and region.view_perspective != 'CAMERA':
        return "Shown in the camera view (Numpad 0)"
    return ""


def draw_dof(layout, cam, space):
    """Depth of field: one toggle; aperture and focus only while it is on."""
    dof = cam.data.dof
    col = layout.column(align=True)
    col.prop(dof, "use_dof", text="Depth of Field", toggle=True, icon='CAMERA_DATA')
    if not dof.use_dof:
        return
    row = col.row(align=True)
    for fstop in FSTOPS:
        row.operator(RS_OT_set_fstop.bl_idname, text="f/%g" % fstop,
                     depress=abs(dof.aperture_fstop - fstop) < 0.01).fstop = fstop
    col.prop(dof, "aperture_fstop", text="Aperture")
    row = col.row(align=True)
    row.prop(dof, "focus_object", text="", placeholder="Focus: Distance")
    row.operator(RS_OT_dof_focus.bl_idname, text="", icon='PIVOT_CURSOR').target = "ORBIT"
    row.operator(RS_OT_dof_focus.bl_idname, text="", icon='RESTRICT_SELECT_OFF').target = "ACTIVE"
    if dof.focus_object is None:
        col.prop(bpy.context.scene.render_studio, "cam_focus_distance", text="Distance")   # 1 mm steps
    hint = dof_hint(space)
    if hint:
        row = layout.row()
        row.active = False
        row.label(text=hint, icon='INFO')


class RS_OT_set_fstop(Operator):
    bl_idname = "render_studio.set_fstop"
    bl_label = "Aperture"
    bl_description = "Depth of field with this aperture (small number = shallow focus)"
    bl_options = {'UNDO', 'INTERNAL'}

    fstop: FloatProperty()

    def execute(self, context):
        cam = active_camera(context.scene)
        if cam is None or cam.type != 'CAMERA':
            return {'CANCELLED'}
        cam.data.dof.use_dof = True
        cam.data.dof.aperture_fstop = self.fstop
        return {'FINISHED'}


class RS_OT_dof_focus(Operator):
    bl_idname = "render_studio.dof_focus"
    bl_label = "Focus On"
    bl_description = "Focus on the orbit object or the active object"
    bl_options = {'UNDO', 'INTERNAL'}

    target: StringProperty(default="ORBIT")   # ORBIT / ACTIVE

    def execute(self, context):
        scene = context.scene
        cam = active_camera(scene)
        obj = scene.render_studio.orbit_target if self.target == "ORBIT" else context.active_object
        if cam is None or cam.type != 'CAMERA' or obj is None or obj == cam:
            self.report({'WARNING'}, "Pick an object first")
            return {'CANCELLED'}
        cam.data.dof.use_dof = True
        cam.data.dof.focus_object = obj
        return {'FINISHED'}

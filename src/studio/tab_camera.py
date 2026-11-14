# SPDX-License-Identifier: GPL-3.0-or-later
"""The Render tab: cameras, lens, format, framing, depth of field."""

import bpy
from bpy.props import (BoolProperty, FloatProperty, IntProperty, StringProperty)
from bpy.types import Menu, Operator, Panel

from . import cameras, env_ui, runner, sync
from .props import FORMAT_PRESETS, PPI
from .ui_common import (RENDER_TAB_ORDER, _SidebarPanel, _look_through, _unpin, section, tab_head,
                        active_camera, studio_tab)


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


RATIOS = ((1, 1), (4, 5), (3, 4), (2, 3), (9, 16), (5, 4), (4, 3), (3, 2), (16, 9), (21, 9))


class RS_OT_set_aspect(Operator):
    bl_idname = "render_studio.set_aspect"
    bl_label = "Aspect Ratio"
    bl_description = "Set the aspect ratio (the short side stays 1080 px at 1x)"
    bl_options = {'UNDO', 'INTERNAL'}

    x: IntProperty(default=3, min=1, options={'HIDDEN', 'SKIP_SAVE'})
    y: IntProperty(default=4, min=1, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return "%d : %d (%s)" % (props.x, props.y, "portrait" if props.y > props.x else
                                 "landscape" if props.x > props.y else "square")

    def execute(self, context):
        rq = context.scene.render_studio
        rq.aspect_x = self.x      # the setter sizes the image
        rq.aspect_y = self.y
        return {'FINISHED'}


class RS_MT_aspect(Menu):
    """Left in the format row: the aspect ratio, and two fields for any other ratio."""
    bl_idname = "RS_MT_aspect"
    bl_label = "Aspect Ratio"

    def draw(self, context):
        layout = self.layout
        rq = context.scene.render_studio
        current = (rq.aspect_x, rq.aspect_y)
        for x, y in RATIOS:
            op = layout.operator(RS_OT_set_aspect.bl_idname, text="%d : %d" % (x, y),
                                 icon='CHECKMARK' if (x, y) == current else 'BLANK1')
            op.x, op.y = x, y
        layout.separator()
        row = layout.row(align=True)
        row.prop(rq, "aspect_x", text="")
        row.label(text=":")
        row.prop(rq, "aspect_y", text="")


class RS_MT_formats(Menu):
    """Right in the format row: formats with a fixed size (Instagram, HD …)."""
    bl_idname = "RS_MT_formats"
    bl_label = "Formats"

    def draw(self, context):
        layout = self.layout
        render = context.scene.render
        for _short, long, w, h in FORMAT_PRESETS:
            op = layout.operator(RS_OT_set_resolution.bl_idname, text="%s (%d × %d)" % (long, w, h),
                                 icon='CHECKMARK' if (render.resolution_x, render.resolution_y) == (w, h) else 'BLANK1')
            op.width, op.height = w, h


def format_name(render):
    return next((short for short, _l, w, h in FORMAT_PRESETS if (render.resolution_x, render.resolution_y) == (w, h)),
                "Format")


class RS_OT_camera_preset(Operator):
    bl_idname = "render_studio.camera_preset"
    bl_label = "Camera Preset"
    bl_description = "A new camera for this view of the product, and look through it"
    bl_options = {'UNDO', 'INTERNAL'}

    view: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        preset = next((p for p in cameras.VIEW_PRESETS if p[0] == props.view), None)
        if preset is None:
            return cls.bl_description
        kind = "orthographic (no perspective, true proportions)" if preset[4] else "%d mm" % preset[5]
        return "A new camera: %s, %s. Around the camera's orbit object (otherwise the 3D cursor)" % (preset[1], kind)

    def execute(self, context):
        try:
            cam = cameras.camera_preset(context.scene, context.view_layer, self.view)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _look_through(context, cam)
        self.report({'INFO'}, "%s created" % cam.name)
        return {'FINISHED'}


class RS_MT_camera_presets(Menu):
    """The preset menu of the Camera tab: standard views as new cameras, the formats."""
    bl_idname = "RS_MT_camera_presets"
    bl_label = "Camera Presets"

    def draw(self, context):
        layout = self.layout
        layout.label(text="Orthographic")
        for key, name, _a, _h, ortho, _lens, _room in cameras.VIEW_PRESETS:
            if ortho:
                layout.operator(RS_OT_camera_preset.bl_idname, text=name, icon='VIEW_ORTHO').view = key
        layout.separator()
        layout.label(text="Perspective")
        for key, name, _a, _h, ortho, _lens, _room in cameras.VIEW_PRESETS:
            if not ortho:
                layout.operator(RS_OT_camera_preset.bl_idname, text=name, icon='VIEW_PERSPECTIVE').view = key
        layout.operator(RS_OT_camera_from_view.bl_idname, text="From the Viewport", icon='VIEW_CAMERA')


class RS_OT_toggle_ortho(Operator):
    bl_idname = "render_studio.toggle_ortho"
    bl_label = "Orthographic / Perspective"
    bl_description = ("Switch the camera between perspective and orthographic; the framing stays about the "
                      "same (ortho scale from the distance, the distance from the ortho scale)")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        cam = context.scene.camera
        return cam is not None and cam.type == 'CAMERA'

    def execute(self, context):
        from . import orbit
        scene = context.scene
        cam, rq = scene.camera, scene.render_studio
        data = cam.data
        sensor = data.sensor_height if data.sensor_fit == 'VERTICAL' else data.sensor_width
        distance = orbit.camera_orbit(rq, cam)[2]
        if data.type == 'ORTHO':
            data.type = 'PERSP'
            rq.orbit_distance = max(orbit.MIN_DISTANCE, data.ortho_scale * data.lens / sensor)
        else:
            data.ortho_scale = max(0.001, distance * sensor / data.lens)
            data.type = 'ORTHO'
        return {'FINISHED'}


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
        scene = context.scene
        # Set Up for Product Renders is in the chat's setup card, the CAD box in the chat (Claude tab)
        rq = scene.render_studio
        render = scene.render
        cam = active_camera(scene)

        # The same head as every tab: [presets ▾] [+ a copy of this camera] [settings]
        tab_head(layout, RS_MT_camera_presets.bl_idname, "Preset", RS_OT_duplicate_camera.bl_idname,
                 RS_PT_frame.__name__)

        # Camera: name, back, the cameras of the scene, orbit object and lock
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
        grid = box.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
        for obj in cameras.scene_cameras(scene):
            grid.operator(RS_OT_set_camera.bl_idname, text=obj.name, depress=(obj == cam)).name = obj.name
        if cam is None or cam.type != 'CAMERA':
            box.label(text="No scene camera")
            return
        locked = rq.camera_locked
        row = box.row(align=True)
        sub = row.row(align=True)
        sub.enabled = not locked
        sub.prop(rq, "orbit_target", text="", icon='PIVOT_CURSOR', placeholder="Orbit Around: 3D Cursor")
        row.prop(rq, "camera_locked", text="", icon='LOCKED' if locked else 'UNLOCKED')

        # Lens, depth of field and format: one line each
        data = cam.data
        space = context.space_data
        box = layout.box()
        col = box.column(align=True)
        row = col.row(align=True)
        if data.type == 'ORTHO':
            row.prop(rq, "cam_ortho_scale", text="Ortho Scale")
        else:
            row.prop(data, "lens", text="Focal Length")
        row.operator(RS_OT_toggle_ortho.bl_idname, text="",
                     icon='VIEW_PERSPECTIVE' if data.type == 'ORTHO' else 'VIEW_ORTHO')
        if space is not None and space.type == 'VIEW_3D':
            sub = row.row(align=True)
            sub.active = not locked
            sub.prop(space, "lock_camera", text="", icon='VIEW_CAMERA')
        if data.type == 'PERSP':    # a grid: five equal buttons also in a narrow sidebar
            grid = col.grid_flow(row_major=True, columns=len(LENS_PRESETS), even_columns=True, align=True)
            for mm in LENS_PRESETS:
                grid.operator(RS_OT_set_lens.bl_idname, text=str(mm),
                              depress=abs(data.lens - mm) < 0.01).lens = mm
        draw_dof(box, cam, space)
        # Format in one row: [3 : 4 ▾] [IG Portrait ▾] [📌 1500 × 2000] (pin: this camera keeps its format)
        own = cam.render_studio_camera
        pct = render.resolution_percentage / 100
        row = box.row(align=True)
        row.menu(RS_MT_aspect.bl_idname, text="%d : %d" % (rq.aspect_x, rq.aspect_y), icon='FULLSCREEN_ENTER')
        row.menu(RS_MT_formats.bl_idname, text=format_name(render))
        row.prop(own, "use_resolution", icon='PINNED' if own.use_resolution else 'UNPINNED', translate=False,
                 text="%d × %d" % (round(render.resolution_x * pct), round(render.resolution_y * pct)))

        # The look of the live camera: exposure, white balance, glow … (compositor), saved looks
        from . import tab_finish
        tab_finish.draw_look(layout, context)

        # Orbit around the picked object (or the 3D cursor)
        box, _head, is_open = section(layout, "orbit", "Orbit", 'ORIENTATION_GIMBAL')
        if is_open:
            col = box.column(align=True)
            col.enabled = not locked
            col.prop(rq, "orbit_horizontal")
            col.prop(rq, "orbit_vertical")
            if cam.data.type != 'ORTHO':   # orthographic: the distance changes nothing in the picture
                col.prop(rq, "orbit_distance")
            col.prop(rq, "orbit_height")
            col.prop(rq, "orbit_side_offset")
            col.prop(rq, "orbit_up_offset")

        # Backdrop: what the camera sees behind the product, and the floor it stands on
        box, _head, is_open = section(layout, "backdrop", "Backdrop", 'IMAGE_BACKGROUND')
        if is_open:
            env_ui.draw_backdrop(box, context)


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
    """Depth of field in one line: [on] [aperture] [focus on …] [orbit object] [selected object].
    A second line only for the focus distance (without a focus object) or a hint."""
    dof = cam.data.dof
    row = layout.row(align=True)
    if not dof.use_dof:
        row.prop(dof, "use_dof", text="Depth of Field", toggle=True, icon='CAMERA_DATA')
        return
    row.prop(dof, "use_dof", text="", toggle=True, icon='CAMERA_DATA')
    row.prop(dof, "aperture_fstop", text="f/")
    sub = row.row(align=True)
    sub.ui_units_x = 6
    sub.prop(dof, "focus_object", text="", placeholder="Focus")
    row.operator(RS_OT_dof_focus.bl_idname, text="", icon='PIVOT_CURSOR').target = "ORBIT"
    row.operator(RS_OT_dof_focus.bl_idname, text="", icon='RESTRICT_SELECT_OFF').target = "ACTIVE"
    if dof.focus_object is None:
        layout.prop(bpy.context.scene.render_studio, "cam_focus_distance", text="Focus Distance")   # 1 mm steps
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

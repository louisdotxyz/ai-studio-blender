# SPDX-License-Identifier: GPL-3.0-or-later
"""Shots, the render check and looking at the results (Render tab).

A shot = camera + scene preset + material variant + quality. "Render Shots" goes through them one
after another: it applies the shot's scene preset (lights, World, background, floor …) to your scene,
adds the camera with its variant to the queue, waits until it is rendered and goes on. The scene keeps
the environment of the last shot.

The render check lists what would spoil a render before it starts (no camera, missing textures, no
light at all, problems with the World).
"""

import os
import subprocess

import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import Menu, Operator

from . import background, environment, runner


# ------------------------------------------------------------------ render check

def render_problems(scene):
    """[(text, icon)] that would spoil a render, most important first."""
    problems = []
    rq = scene.render_studio
    if not any(it.camera for it in rq.items) and scene.camera is None:
        problems.append(("No camera to render", 'CAMERA_DATA'))
    missing = sorted({img.name for img in bpy.data.images
                      if img.source in {'FILE', 'SEQUENCE', 'TILED'} and img.filepath and img.packed_file is None
                      and img.users and not os.path.exists(bpy.path.abspath(img.filepath))})
    if missing:
        problems.append(("Missing textures: %s" % ", ".join(missing[:3]) + (" …" if len(missing) > 3 else ""),
                         'IMAGE_DATA'))
    lit = any(o.type == 'LIGHT' and not o.hide_render and o.data.energy > 0 for o in scene.objects)
    emissive = any(o.type == 'MESH' and not o.hide_render for o in scene.objects
                   if any(s.material and "emiss" in s.material.name.lower() for s in o.material_slots))
    world_strength = background.get_world_strength(rq) if scene.world else 0.0
    if not lit and not emissive and (scene.world is None or world_strength <= 0):
        problems.append(("No light: no lamp, and the World is off", 'LIGHT'))
    if rq.background_mode == 'NORMAL':
        for text in background.world_problems(scene, None):
            problems.append((text, 'WORLD'))
    render = scene.render
    if render.resolution_x * render.resolution_y * (render.resolution_percentage / 100) ** 2 > 60e6:
        problems.append(("Very large image (over 60 megapixels)", 'IMAGE_PLANE'))
    return problems


def draw_check(layout, scene):
    problems = render_problems(scene)
    if not problems:
        return
    box = layout.box()
    box.label(text="Before rendering", icon='ERROR')
    col = box.column(align=True)
    for text, icon in problems:
        col.label(text=text, icon=icon, translate=False)


# ------------------------------------------------------------------ shots

class RS_OT_shot_add(Operator):
    bl_idname = "render_studio.shot_add"
    bl_label = "Add Shot"
    bl_description = "A new shot from the active camera and the current scene preset"
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        scene = context.scene
        rq = scene.render_studio
        from .ui_common import active_camera
        cam = active_camera(scene)
        shot = rq.shots.add()
        shot.camera = cam
        shot.scene_preset = rq.scene_preset
        shot.name = cam.name if cam else "Shot %d" % len(rq.shots)
        return {'FINISHED'}


class RS_OT_shot_remove(Operator):
    bl_idname = "render_studio.shot_remove"
    bl_label = "Remove Shot"
    bl_options = {'UNDO', 'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        rq = context.scene.render_studio
        if 0 <= self.index < len(rq.shots):
            rq.shots.remove(self.index)
        return {'FINISHED'}


class RS_OT_shot_preset(Operator):
    bl_idname = "render_studio.shot_preset"
    bl_label = "Scene Preset of the Shot"
    bl_options = {'UNDO', 'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})
    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        rq = context.scene.render_studio
        if 0 <= self.index < len(rq.shots):
            rq.shots[self.index].scene_preset = self.name
        return {'FINISHED'}


class RS_MT_shot_presets(Menu):
    bl_idname = "RS_MT_shot_presets"
    bl_label = "Scene Preset"

    def draw(self, context):
        layout = self.layout
        index = context.window_manager.render_studio_shot_menu
        op = layout.operator(RS_OT_shot_preset.bl_idname, text="As it is now", icon='BLANK1')
        op.index, op.name = index, ""
        for name in list(environment.BUILTIN_SCENES) + sorted(environment.saved_scenes()):
            op = layout.operator(RS_OT_shot_preset.bl_idname, text=name, icon='SCENE_DATA')
            op.index, op.name = index, name


class RS_OT_shot_menu(Operator):
    """Opens the preset menu for one shot (menus cannot carry the shot's index themselves)."""
    bl_idname = "render_studio.shot_menu"
    bl_label = "Scene Preset"
    bl_description = "Pick the scene preset of this shot (empty: the scene as it is)"
    bl_options = {'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, _event):
        context.window_manager.render_studio_shot_menu = self.index
        bpy.ops.wm.call_menu(name=RS_MT_shot_presets.bl_idname)
        return {'FINISHED'}


_run = {"todo": [], "busy": False, "quality": 'HIGH', "waiting": False}


def _next_shot():
    """Timer: the next shot once the queue is idle."""
    from .runner import RUN
    scene = bpy.context.scene
    if RUN.busy:
        return 1.0
    if not _run["todo"]:
        _run["busy"] = False
        runner.tag_redraw()
        return None
    index = _run["todo"].pop(0)
    rq = scene.render_studio
    if not (0 <= index < len(rq.shots)):
        return 0.1
    shot = rq.shots[index]
    if shot.camera is None:
        return 0.1
    try:
        if shot.scene_preset:
            environment.apply_scene(scene, shot.scene_preset)
        runner.RUN.set_scene_camera(scene, shot.camera)
        from .ui_common import ensure_uids
        ensure_uids(rq)
        bpy.ops.render_studio.add(quality=shot.quality)
        if shot.variant:
            rq.items[-1].variant = shot.variant
        if not RUN.busy:
            RUN.start(scene)
    except Exception as exc:   # one broken shot must not stop the others
        runner.log("shot %s failed: %s" % (shot.name, exc))
    runner.tag_redraw()
    return 2.0


def render_shots(scene, indices=None):
    rq = scene.render_studio
    _run["todo"] = list(indices if indices is not None else range(len(rq.shots)))
    if not _run["todo"]:
        return False
    _run["busy"] = True
    if not bpy.app.timers.is_registered(_next_shot):
        bpy.app.timers.register(_next_shot, first_interval=0.1)
    return True


def shots_running():
    return _run["busy"]


class RS_OT_render_shots(Operator):
    bl_idname = "render_studio.render_shots"
    bl_label = "Render Shots"
    bl_description = ("Render every shot one after another: its scene preset is applied to the scene, then its "
                      "camera with its variant is rendered")

    @classmethod
    def poll(cls, context):
        return len(context.scene.render_studio.shots) > 0 and not _run["busy"]

    def execute(self, context):
        render_shots(context.scene)
        return {'FINISHED'}


class RS_OT_shots_stop(Operator):
    bl_idname = "render_studio.shots_stop"
    bl_label = "Stop Shots"
    bl_description = "No further shots (the one rendering now finishes)"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        _run["todo"].clear()
        return {'FINISHED'}


def draw_shots(layout, scene):
    rq = scene.render_studio
    box = layout.box()
    row = box.row()
    row.label(text="Shots", icon='SEQUENCE')
    row.operator(RS_OT_shot_add.bl_idname, text="", icon='ADD', emboss=False)
    if not rq.shots:
        sub = box.row()
        sub.active = False
        sub.label(text="Camera + scene + variant, rendered together")
        return
    col = box.column(align=True)
    for i, shot in enumerate(rq.shots):
        row = col.row(align=True)
        row.prop(shot, "name", text="")
        row.prop(shot, "camera", text="", icon='CAMERA_DATA')
        row.operator(RS_OT_shot_menu.bl_idname, text=shot.scene_preset or "Scene", icon='SCENE_DATA').index = i
        row.operator(RS_OT_shot_remove.bl_idname, text="", icon='X').index = i
        row = col.row(align=True)
        row.prop(shot, "variant", text="", icon='COLOR', placeholder="Variant")
        row.prop(shot, "quality", expand=True)
        col.separator()
    row = box.row(align=True)
    if _run["busy"]:
        row.label(text="%d shots to go" % len(_run["todo"]), icon='TIME')
        row.operator(RS_OT_shots_stop.bl_idname, text="", icon='X')
    else:
        row.operator(RS_OT_render_shots.bl_idname, icon='RENDER_ANIMATION')


# ------------------------------------------------------------------ results: contact sheet, compare

def recent_images(scene, count):
    return [path for path in runner.queue_images(scene, limit=count) if path and os.path.isfile(path)
            and not os.path.basename(path).startswith("Contact Sheet")][:count]


def _open(paths):
    if not paths:
        return
    try:
        subprocess.Popen(["open"] + list(paths))
    except OSError:
        bpy.ops.wm.path_open(filepath=paths[0])


class RS_OT_contact_sheet(Operator):
    bl_idname = "render_studio.contact_sheet"
    bl_label = "Contact Sheet"
    bl_description = "The last renders on one sheet (PNG next to them), opened right away"
    bl_options = {'INTERNAL'}

    count: IntProperty(default=12, min=2, max=60)

    def execute(self, context):
        paths = recent_images(context.scene, self.count)
        if len(paths) < 2:
            self.report({'WARNING'}, "Render at least two images first")
            return {'CANCELLED'}
        from .variants import V
        sheet = V.sheet(list(reversed(paths)), name="contact_sheet.png", cols=4, width=480)
        # next to the preview / HQ folders, so it never counts as a render itself
        target = os.path.join(os.path.dirname(os.path.dirname(paths[0])), "Contact Sheet.png")
        try:
            import shutil
            shutil.copy2(sheet, target)
        except OSError:
            target = sheet
        _open([target])
        return {'FINISHED'}


def compare_pair(scene):
    """The render shown in the preview and the one before it (else the newest two)."""
    from . import tab_queue
    images = [p for p in runner.queue_images(scene) if p and os.path.isfile(p)
              and not os.path.basename(p).startswith("Contact Sheet")]
    shown = tab_queue._selected_image(images) or (images[0] if images else "")
    if shown in images and images.index(shown) + 1 < len(images):
        return [shown, images[images.index(shown) + 1]]
    return images[:2]


def _split_compare(names, tries=[0]):
    """In the render window: two image editors side by side, left the newer render, right the older."""
    images = [bpy.data.images.get(n) for n in names]
    if None in images:
        return None
    for window in bpy.context.window_manager.windows:
        if not window.screen.is_temporary:
            continue
        areas = [a for a in window.screen.areas if a.type == 'IMAGE_EDITOR']
        if not areas:
            continue
        if len(areas) == 1:
            area = areas[0]
            region = next(r for r in area.regions if r.type == 'WINDOW')
            with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region):
                bpy.ops.screen.area_split(direction='VERTICAL', factor=0.5)
            areas = [a for a in window.screen.areas if a.type == 'IMAGE_EDITOR']
        areas.sort(key=lambda a: a.x)
        for area, image in zip(areas, images):
            area.spaces.active.image = image
            region = next(r for r in area.regions if r.type == 'WINDOW')
            try:
                with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region):
                    bpy.ops.image.view_all(fit_view=True)
            except RuntimeError:
                pass
            area.tag_redraw()
        tries[0] = 0
        return None
    tries[0] += 1               # the window comes a moment later
    if tries[0] > 20:
        tries[0] = 0
        return None
    return 0.1


class RS_OT_compare_renders(Operator):
    bl_idname = "render_studio.compare_renders"
    bl_label = "Compare"
    bl_description = ("This render and the one before it side by side in a Blender window (zoom and move each "
                      "side on its own)")
    bl_options = {'INTERNAL'}

    def execute(self, context):
        paths = compare_pair(context.scene)
        if len(paths) < 2:
            self.report({'WARNING'}, "Render at least two images first")
            return {'CANCELLED'}
        names = []
        for path in paths:
            image = bpy.data.images.load(path, check_existing=True)
            image.reload()
            names.append(image.name)
        if any(w.screen.is_temporary for w in context.window_manager.windows):
            bpy.app.timers.register(lambda: _split_compare(names), first_interval=0.01)   # already open
            return {'FINISHED'}
        view = context.preferences.view
        old = view.render_display_type
        view.render_display_type = 'WINDOW'       # always its own window, whatever the preference says
        try:
            bpy.ops.render.view_show('INVOKE_DEFAULT')
        finally:
            view.render_display_type = old
        bpy.app.timers.register(lambda: _split_compare(names), first_interval=0.15)
        return {'FINISHED'}


def draw_results(row):
    row.operator(RS_OT_compare_renders.bl_idname, text="", icon='IMAGE_REFERENCE')
    row.operator(RS_OT_contact_sheet.bl_idname, text="", icon='IMGDISPLAY')


classes = (RS_OT_shot_add, RS_OT_shot_remove, RS_OT_shot_preset, RS_MT_shot_presets, RS_OT_shot_menu,
           RS_OT_render_shots, RS_OT_shots_stop, RS_OT_contact_sheet, RS_OT_compare_renders)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.render_studio_shot_menu = IntProperty(options={'SKIP_SAVE'})


def unregister():
    if bpy.app.timers.is_registered(_next_shot):
        bpy.app.timers.unregister(_next_shot)
    del bpy.types.WindowManager.render_studio_shot_menu
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


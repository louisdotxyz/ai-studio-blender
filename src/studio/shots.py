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

import bpy
from bpy.props import IntProperty, StringProperty
from bpy.types import Menu, Operator

from . import background, environment, runner


# ------------------------------------------------------------------ render check

def _node_images(tree, found, seen):
    if tree is None or tree.as_pointer() in seen:     # not by name: every material's tree has the same one
        return
    seen.add(tree.as_pointer())
    for node in tree.nodes:
        image = getattr(node, "image", None)
        if image is not None:
            found.add(image.name)
        if node.type == 'GROUP':
            _node_images(node.node_tree, found, seen)


def used_images(scene):
    """The images a render of this scene really uses: materials of the objects that render, and the World."""
    found, seen = set(), set()
    for obj in scene.objects:
        if obj.hide_render or obj.type not in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'CURVES', 'VOLUME'}:
            continue
        for slot in obj.material_slots:
            if slot.material is not None:
                _node_images(slot.material.node_tree, found, seen)
    if scene.world is not None:
        _node_images(scene.world.node_tree, found, seen)
    return found


def missing_images(scene):
    used = used_images(scene)
    return sorted(img.name for img in bpy.data.images
                  if img.name in used and img.source in {'FILE', 'SEQUENCE', 'TILED'} and img.filepath
                  and img.packed_file is None and not os.path.exists(bpy.path.abspath(img.filepath)))


_searched = set()      # (file, missing) already looked for: once per session


def _search_missing():
    """Looks for missing textures by name in the project folder (and one folder up), like
    File › External Data › Find Missing Files. Runs by itself, before anything is shown."""
    folder = os.path.dirname(bpy.data.filepath) if bpy.data.filepath else ""
    for path in [p for p in (folder, os.path.dirname(folder)) if p and os.path.isdir(p)]:
        try:
            bpy.ops.file.find_missing_files(directory=path, find_all=False)
        except Exception as ex:
            print("ID Toolkit: find missing files:", ex)
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()
    return None


def render_problems(scene, search=True):
    """[(text, icon)] that would spoil a render, most important first. Missing textures are looked for
    in the project folder first; only those really used by the render count."""
    problems = []
    rq = scene.studio
    if not any(it.camera for it in rq.items) and scene.camera is None:
        problems.append(("No camera to render", 'CAMERA_DATA'))
    missing = missing_images(scene)
    if missing and search:
        key = (bpy.data.filepath, tuple(missing))
        if key not in _searched:
            _searched.add(key)
            if not bpy.app.timers.is_registered(_search_missing):
                bpy.app.timers.register(_search_missing, first_interval=0.0)
            missing = []                  # looked for first, shown only if still missing
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


class STUDIO_OT_fix_render_problems(Operator):
    bl_idname = "studio.fix_render_problems"
    bl_label = "Let Claude Fix It"
    bl_description = "Claude gets the list in the chat and fixes what would spoil the render"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        problems = render_problems(context.scene, search=False)
        if not problems:
            return {'CANCELLED'}
        missing = missing_images(context.scene)
        text = ("Before rendering, please fix these problems in the scene \"%s\" by yourself (look for missing "
                "files in the project and asset folders, relink or replace them; ask only if there is no way):\n- %s"
                % (context.scene.name, "\n- ".join(t for t, _i in problems)))
        if missing:
            text += "\nMissing textures (all): " + ", ".join(missing)
        try:
            from .. import ui_menus
            wm = context.window_manager
            wm.claude_prompt = text
            ui_menus._send_prompt(wm)
        except Exception as ex:
            self.report({'ERROR'}, "Could not reach the chat: %s" % ex)
            return {'CANCELLED'}
        self.report({'INFO'}, "Claude is on it (see the Claude tab)")
        return {'FINISHED'}


def draw_check(layout, scene):
    problems = render_problems(scene)
    if not problems:
        return
    box = layout.box()
    row = box.row(align=True)
    row.label(text="Before rendering", icon='ERROR')
    row.operator(STUDIO_OT_fix_render_problems.bl_idname, text="Fix", icon='TOOL_SETTINGS')
    col = box.column(align=True)
    for text, icon in problems:
        col.label(text=text, icon=icon, translate=False)


# ------------------------------------------------------------------ shots

class STUDIO_OT_shot_add(Operator):
    bl_idname = "studio.shot_add"
    bl_label = "Add Shot"
    bl_description = "A new shot from the active camera and the current scene preset"
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        scene = context.scene
        rq = scene.studio
        from .ui_common import active_camera
        cam = active_camera(scene)
        shot = rq.shots.add()
        shot.camera = cam
        shot.scene_preset = rq.scene_preset
        shot.name = cam.name if cam else "Shot %d" % len(rq.shots)
        return {'FINISHED'}


class STUDIO_OT_shot_remove(Operator):
    bl_idname = "studio.shot_remove"
    bl_label = "Remove Shot"
    bl_options = {'UNDO', 'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        rq = context.scene.studio
        if 0 <= self.index < len(rq.shots):
            rq.shots.remove(self.index)
        return {'FINISHED'}


class STUDIO_OT_shot_preset(Operator):
    bl_idname = "studio.shot_preset"
    bl_label = "Scene Preset of the Shot"
    bl_options = {'UNDO', 'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})
    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        rq = context.scene.studio
        if 0 <= self.index < len(rq.shots):
            rq.shots[self.index].scene_preset = self.name
        return {'FINISHED'}


class STUDIO_MT_shot_presets(Menu):
    bl_idname = "STUDIO_MT_shot_presets"
    bl_label = "Scene Preset"

    def draw(self, context):
        layout = self.layout
        index = context.window_manager.studio_shot_menu
        op = layout.operator(STUDIO_OT_shot_preset.bl_idname, text="As it is now", icon='BLANK1')
        op.index, op.name = index, ""
        for name in list(environment.BUILTIN_SCENES) + sorted(environment.saved_scenes()):
            op = layout.operator(STUDIO_OT_shot_preset.bl_idname, text=name, icon='SCENE_DATA')
            op.index, op.name = index, name


class STUDIO_OT_shot_menu(Operator):
    """Opens the preset menu for one shot (menus cannot carry the shot's index themselves)."""
    bl_idname = "studio.shot_menu"
    bl_label = "Scene Preset"
    bl_description = "Pick the scene preset of this shot (empty: the scene as it is)"
    bl_options = {'INTERNAL'}

    index: IntProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, _event):
        context.window_manager.studio_shot_menu = self.index
        bpy.ops.wm.call_menu(name=STUDIO_MT_shot_presets.bl_idname)
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
    rq = scene.studio
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
        bpy.ops.studio.add(quality=shot.quality)
        if shot.variant:
            rq.items[-1].variant = shot.variant
        if not RUN.busy:
            RUN.start(scene)
    except Exception as exc:   # one broken shot must not stop the others
        runner.log("shot %s failed: %s" % (shot.name, exc))
    runner.tag_redraw()
    return 2.0


def render_shots(scene, indices=None):
    rq = scene.studio
    _run["todo"] = list(indices if indices is not None else range(len(rq.shots)))
    if not _run["todo"]:
        return False
    _run["busy"] = True
    if not bpy.app.timers.is_registered(_next_shot):
        bpy.app.timers.register(_next_shot, first_interval=0.1)
    return True


def shots_running():
    return _run["busy"]


class STUDIO_OT_render_shots(Operator):
    bl_idname = "studio.render_shots"
    bl_label = "Render Shots"
    bl_description = ("Render every shot one after another: its scene preset is applied to the scene, then its "
                      "camera with its variant is rendered")

    @classmethod
    def poll(cls, context):
        return len(context.scene.studio.shots) > 0 and not _run["busy"]

    def execute(self, context):
        render_shots(context.scene)
        return {'FINISHED'}


class STUDIO_OT_shots_stop(Operator):
    bl_idname = "studio.shots_stop"
    bl_label = "Stop Shots"
    bl_description = "No further shots (the one rendering now finishes)"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        _run["todo"].clear()
        return {'FINISHED'}


def draw_shots(layout, scene):
    rq = scene.studio
    box = layout.box()
    row = box.row()
    row.label(text="Shots", icon='SEQUENCE')
    row.operator(STUDIO_OT_shot_add.bl_idname, text="", icon='ADD', emboss=False)
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
        row.operator(STUDIO_OT_shot_menu.bl_idname, text=shot.scene_preset or "Scene", icon='SCENE_DATA').index = i
        row.operator(STUDIO_OT_shot_remove.bl_idname, text="", icon='X').index = i
        row = col.row(align=True)
        row.prop(shot, "variant", text="", icon='COLOR', placeholder="Variant")
        row.prop(shot, "quality", expand=True)
        col.separator()
    row = box.row(align=True)
    if _run["busy"]:
        row.label(text="%d shots to go" % len(_run["todo"]), icon='TIME')
        row.operator(STUDIO_OT_shots_stop.bl_idname, text="", icon='X')
    else:
        row.operator(STUDIO_OT_render_shots.bl_idname, icon='RENDER_ANIMATION')


# ------------------------------------------------------------------ results: compare

def compare_pair(scene):
    """A and B as picked in the Render tab; the one missing is the shown render, else the one before it."""
    from . import tab_queue
    images = [p for p in runner.queue_images(scene) if p and os.path.isfile(p)]
    shown = tab_queue._selected_image(images) or (images[0] if images else "")
    picked = [tab_queue._ab[k] for k in ("A", "B") if tab_queue._ab[k] and os.path.isfile(tab_queue._ab[k])]
    if len(picked) == 2:
        return picked
    if len(picked) == 1:
        other = shown if shown != picked[0] else next((p for p in images if p != picked[0]), "")
        return [picked[0], other] if other else picked
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


class STUDIO_OT_compare_renders(Operator):
    bl_idname = "studio.compare_renders"
    bl_label = "Compare"
    bl_description = ("A and B (else this render and the one before it) side by side in a Blender window; zoom and "
                      "move each side on its own")
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


def draw_results(layout):
    """[Compare A · B]: what is picked as A and B (else this render and the one before) side by side."""
    from . import tab_queue
    a, b = tab_queue._ab["A"], tab_queue._ab["B"]
    text = "Compare A · B" if a and b else "Compare with A" if a or b else "Compare with the One Before"
    row = layout.row(align=True)
    row.scale_y = 1.2
    row.operator(STUDIO_OT_compare_renders.bl_idname, text=text, icon='IMAGE_REFERENCE')


classes = (STUDIO_OT_shot_add, STUDIO_OT_shot_remove, STUDIO_OT_shot_preset, STUDIO_MT_shot_presets, STUDIO_OT_shot_menu,
           STUDIO_OT_render_shots, STUDIO_OT_shots_stop, STUDIO_OT_compare_renders, STUDIO_OT_fix_render_problems)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.studio_shot_menu = IntProperty(options={'SKIP_SAVE'})


def unregister():
    if bpy.app.timers.is_registered(_next_shot):
        bpy.app.timers.unregister(_next_shot)
    del bpy.types.WindowManager.studio_shot_menu
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


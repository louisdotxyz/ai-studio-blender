# SPDX-License-Identifier: GPL-3.0-or-later
"""Camera collection, hiding cameras in the viewport and the movement lock.

- New cameras (the "+" in the camera bar) go into the camera collection, new lights
  into the light collection (names in Preferences › Studio, default AS_camera and
  AS_lights). Collections with older names (RS_Cameras, RS_Lights, "Render Studio
  Cameras/Lights") are renamed when a file is opened.
- Cameras are hidden in the viewport by default: once per scene (when a file
  is opened or the extension is enabled) all cameras are hidden. Looking
  through a hidden camera works as usual; the eye in the Render tab shows them
  again.
- The lock fixes position and rotation of the active camera (the transform
  locks of the object). Blender respects them everywhere, also with
  "Lock Camera to View" and in the orbit fields of the Render tab.
"""

import bpy

# Names of earlier versions, renamed to the collection names set in the preferences.
OLD_CAMERAS = ("Render Studio Cameras", "RS_Cameras")
OLD_LIGHTS = ("Render Studio Lights", "RS_Lights")


def _pref(key, default):
    from . import runner
    p = runner.prefs()
    return (getattr(p, key, "") or "").strip() or default if p is not None else default


def camera_collection_name():
    return _pref("camera_collection", "AS_camera")


def light_collection_name():
    return _pref("light_collection", "AS_lights")


def archive_collection_name():
    return _pref("archive_collection", "AS_archiv")


def temp_collection_name():
    return _pref("temp_collection", "temp")


def scene_cameras(scene):
    """The cameras of the scene, without the temporary render copies of the queue."""
    return sorted((o for o in scene.objects if o.type == 'CAMERA' and "render_studio_temp_camera" not in o),
                  key=lambda o: o.name.lower())


def rename_old_collections():
    """Renames the collections of older versions (RS_Cameras, RS_Lights …) to the names set in
    the preferences, unless one of that name exists already."""
    for olds, new in ((OLD_CAMERAS, camera_collection_name()), (OLD_LIGHTS, light_collection_name())):
        for old in olds:
            coll = bpy.data.collections.get(old)
            if coll is not None and old != new and coll.library is None and bpy.data.collections.get(new) is None:
                coll.name = new


def named_collection(scene, name):
    """The collection with this name, linked into the scene (created when missing)."""
    rename_old_collections()
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if coll not in scene.collection.children_recursive:
        scene.collection.children.link(coll)
    return coll


def camera_collection(scene):
    return named_collection(scene, camera_collection_name())


def _layer_collection(layer, coll):
    if layer is None:
        return None
    if layer.collection == coll:
        return layer
    for child in layer.children:
        found = _layer_collection(child, coll)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------- Visibility

def cameras_visible(scene, view_layer):
    """True if at least one camera of the scene is shown in the viewport."""
    return any(cam.visible_get(view_layer=view_layer) for cam in scene_cameras(scene)
               if cam.name in view_layer.objects)


def set_cameras_visible(scene, visible):
    """Shows or hides all cameras of the scene in the viewport (all view layers)."""
    for view_layer in scene.view_layers:
        if view_layer.layer_collection is None:
            view_layer.update()   # a new scene: its view layer is not filled yet
        for cam in scene_cameras(scene):
            if cam.name in view_layer.objects:
                cam.hide_set(not visible, view_layer=view_layer)
        coll = bpy.data.collections.get(camera_collection_name())
        layer = _layer_collection(view_layer.layer_collection, coll) if coll is not None else None
        if layer is not None and visible and layer.hide_viewport:
            layer.hide_viewport = False


def hide_by_default(scene):
    """Once per scene: hide all cameras."""
    rs = scene.render_studio
    if rs.cameras_hidden_once:
        return
    rs.cameras_hidden_once = True
    set_cameras_visible(scene, False)


BLENDER_SAMPLES = 4096   # Cycles' own default: far more than a denoised product shot needs


def default_samples(scene):
    """Once per scene: Blender's 4096 Cycles samples become the add-on's default (512)."""
    rs = scene.render_studio
    if rs.samples_set_once:
        return
    rs.samples_set_once = True
    from . import runner
    cycles = getattr(scene, "cycles", None)
    p = runner.prefs()
    if cycles is not None and cycles.samples == BLENDER_SAMPLES:
        cycles.samples = p.default_samples if p is not None else 512


@bpy.app.handlers.persistent
def _on_load_post(*_args):
    from . import runner
    try:
        rename_old_collections()
    except Exception as ex:
        print("Render Studio: collections:", ex)
    try:
        runner.remove_temp_cameras()
    except Exception as ex:
        print("Render Studio: temporary cameras:", ex)
    try:
        renamed = tidy_names()
        if renamed:
            print("AI Studio: short names:", ", ".join("%s → %s" % kv for kv in renamed.items()))
    except Exception as ex:
        print("AI Studio: names:", ex)
    for scene in bpy.data.scenes:
        try:
            hide_by_default(scene)
        except Exception as ex:
            print("Render Studio: could not hide the cameras:", ex)
        try:
            default_samples(scene)
        except Exception as ex:
            print("Render Studio: samples:", ex)
    try:
        apply_min_clipping()
    except Exception as ex:
        print("Render Studio: clipping:", ex)


def tidy_names():
    """Cameras and lights with names of older versions or Blender's ".001" get short ones:
    "AS_camera View.001" → "View_02", "AS_1" → "Camera_01". Returns {old: new}."""
    from ..naming import tidy
    prefixes = (camera_collection_name(), light_collection_name(), "AS", "RS") + OLD_CAMERAS + OLD_LIGHTS
    done = {}
    for kind, fallback in (('CAMERA', "Camera"), ('LIGHT', "Light")):
        objects = [o for o in bpy.data.objects if o.type == kind and o.library is None
                   and "render_studio_temp_camera" not in o]
        names = tidy([o.name for o in objects], prefixes, fallback)
        for obj in objects:
            new = names.get(obj.name)
            if not new:
                continue
            old = obj.name
            obj.name = new
            if obj.data is not None and obj.data.users == 1 and obj.data.library is None:
                obj.data.name = new
            done[old] = obj.name
    return done


def _hide_later():
    _on_load_post()
    return None


# ------------------------------------------------------------------- Locking

def is_locked(camera):
    return camera is not None and all(camera.lock_location) and all(camera.lock_rotation)


def set_locked(camera, locked):
    camera.lock_location = (locked,) * 3
    camera.lock_rotation = (locked,) * 3
    camera.lock_rotation_w = locked


# Getters/setters for the scene properties (RS_Settings) ---------------------

def get_show(settings):
    scene = settings.id_data
    view_layer = bpy.context.view_layer
    if view_layer is None or view_layer not in scene.view_layers[:]:
        view_layer = scene.view_layers[0]
    return cameras_visible(scene, view_layer)


def set_show(settings, value):
    set_cameras_visible(settings.id_data, value)


def get_lock(settings):
    return is_locked(settings.id_data.camera)


def set_lock(settings, value):
    camera = settings.id_data.camera
    if camera is not None and camera.type == 'CAMERA':
        set_locked(camera, value)


# ------------------------------------------------------------------ Register

def register():
    handlers = bpy.app.handlers.load_post
    if _on_load_post not in handlers:
        handlers.append(_on_load_post)
    # The file that is open right now (not during registration itself).
    bpy.app.timers.register(_hide_later, first_interval=0.5)


def unregister():
    if bpy.app.timers.is_registered(_hide_later):
        bpy.app.timers.unregister(_hide_later)
    handlers = bpy.app.handlers.load_post
    if _on_load_post in handlers:
        handlers.remove(_on_load_post)


CLIP_START = 0.001   # 1 mm: nothing close to the camera gets cut off
CLIP_END = 1000.0


def apply_min_clipping(scene=None, force=False):
    """All cameras of the scene (or every scene): clipping start at the minimum.
    Only when the Studio setting "Minimal Clipping" is on (or force)."""
    from .runner import prefs
    p = prefs()
    if not force and (p is None or not p.min_clipping):
        return
    scenes = [scene] if scene is not None else list(bpy.data.scenes)
    for sc in scenes:
        for cam in scene_cameras(sc):
            data = cam.data
            if data.clip_start != CLIP_START:
                data.clip_start = CLIP_START
            if data.clip_end < CLIP_END:
                data.clip_end = CLIP_END


def _new_camera(scene, view_layer, name):
    from ..naming import numbered
    name = numbered(name, bpy.data.objects.keys())   # "Ortho Top_01", never "….001"
    data = bpy.data.cameras.new(name)
    cam = bpy.data.objects.new(name, data)
    from .runner import prefs
    p = prefs()
    if p is not None and p.min_clipping:
        data.clip_start, data.clip_end = CLIP_START, CLIP_END
    visible = cameras_visible(scene, view_layer)
    camera_collection(scene).objects.link(cam)
    if not visible:
        cam.hide_set(True)
    return cam


def camera_from_view(scene, space, view_layer, name="View"):
    """A new camera that shows what the 3D viewport shows right now."""
    rv3d = space.region_3d
    from .runner import RUN
    user_cam = RUN.original("camera", scene.camera)
    if user_cam is not None and user_cam.get("render_studio_temp_camera"):
        user_cam = bpy.data.objects.get(user_cam["render_studio_temp_camera"])
    if rv3d.view_perspective == 'CAMERA' and user_cam is not None and user_cam.type == 'CAMERA':
        src = user_cam   # already looking through a camera: copy it (never the render's copy)
        cam = _new_camera(scene, view_layer, name)
        cam.data.type, cam.data.lens, cam.data.ortho_scale = src.data.type, src.data.lens, src.data.ortho_scale
        cam.data.sensor_width = src.data.sensor_width
        cam.matrix_world = src.matrix_world.copy()
        return cam
    cam = _new_camera(scene, view_layer, name)
    cam.matrix_world = rv3d.view_matrix.inverted()
    if rv3d.view_perspective == 'ORTHO':
        cam.data.type = 'ORTHO'
        cam.data.ortho_scale = rv3d.view_distance * 2.0
    else:
        # The viewport's "focal length" uses a 72 mm sensor; camera default is 36 mm.
        cam.data.lens = space.lens / 2.0
    return cam


def camera_around(scene, view_layer, name, horizontal, vertical, distance, focal_length=50.0):
    """A new camera placed like the light setups: angles around the subject (degrees, 0 = where
    the active camera is), distance in subject radii, looking at the subject."""
    import math
    from . import lights
    center, radius = lights.subject(scene, scene.render_studio.light_orbit_target)
    front = lights.front_angle(scene, center)
    cam = _new_camera(scene, view_layer, name)
    cam.data.lens = max(1.0, float(focal_length))
    lights.place(cam, center, front + math.radians(horizontal), math.radians(vertical),
                 max(0.3, float(distance)) * radius)
    return cam


# ---------------------------------------------------------------------------
# Camera history: "back" after the camera was moved by accident.
# Before a camera starts moving, its position (and focal length) is pushed onto a
# small history stored in the camera itself; a move ends after MOVE_PAUSE seconds
# without change. Only the scene camera is watched.

HISTORY_KEY = "_rs_history"
HISTORY_MAX = 20
MOVE_PAUSE = 0.8
_last = {}            # camera name -> (matrix as tuple, lens)
_moving = set()
_restoring = [False]


def _state(cam):
    return tuple(round(v, 6) for row in cam.matrix_world for v in row), round(cam.data.lens, 4)


def history(cam):
    return list(cam.get(HISTORY_KEY, [])) if cam is not None else []


def _push(cam, state):
    items = history(cam)
    items.append(list(state[0]) + [state[1]])
    cam[HISTORY_KEY] = items[-HISTORY_MAX:]


def _end_moves():
    _moving.clear()
    return None


def watch(scene):
    """Called after depsgraph updates: notice the scene camera starting to move."""
    cam = scene.camera
    if cam is None or cam.type != 'CAMERA' or _restoring[0] or cam.get("render_studio_temp_camera"):
        return
    from .runner import RUN, render_job_running
    if RUN.preparing or render_job_running():
        return   # no data changes while a render reads the scene (queue renders run in their own Blender)
    state = _state(cam)
    before = _last.get(cam.name)
    _last[cam.name] = state
    if before is None:   # camera just became the scene camera: start from here
        return
    if before == state:
        return
    if cam.name not in _moving:
        _moving.add(cam.name)
        _push(cam, before)
    if bpy.app.timers.is_registered(_end_moves):
        bpy.app.timers.unregister(_end_moves)
    bpy.app.timers.register(_end_moves, first_interval=MOVE_PAUSE)


def go_back(cam):
    """Restore the camera position from before the last move. Returns False if there is none."""
    items = history(cam)
    if not items:
        return False
    *matrix, lens = items.pop()
    cam[HISTORY_KEY] = items
    from mathutils import Matrix
    _restoring[0] = True
    try:
        cam.matrix_world = Matrix([matrix[0:4], matrix[4:8], matrix[8:12], matrix[12:16]])
        cam.data.lens = lens
    finally:
        _restoring[0] = False
    _last[cam.name] = _state(cam)
    return True


def seed(scene=None):
    """Remember the current position of the scene camera(s) as the starting point."""
    for sc in [scene] if scene is not None else list(bpy.data.scenes):
        cam = sc.camera
        if cam is not None and cam.type == 'CAMERA':
            _last[cam.name] = _state(cam)


def _seed_later():
    try:
        seed()
    except Exception:
        pass
    return None


@bpy.app.handlers.persistent
def _on_load_history(*_args):
    _last.clear()
    bpy.app.timers.register(_seed_later, first_interval=0.2)


@bpy.app.handlers.persistent
def _on_depsgraph(scene, _depsgraph):
    try:
        watch(scene)
    except Exception as ex:
        print("Render Studio: camera history:", ex)


def register_history():
    if _on_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_on_depsgraph)
    if _on_load_history not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_history)
    bpy.app.timers.register(_seed_later, first_interval=0.2)


def unregister_history():
    if _on_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_on_depsgraph)
    if _on_load_history in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_history)
    _last.clear()

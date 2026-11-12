# SPDX-License-Identifier: GPL-3.0-or-later
"""Camera collection, hiding cameras in the viewport and the movement lock.

- New cameras (the "+" in the camera bar) go into the camera collection, new lights
  into the light collection (names in Preferences › Studio, default Camera and
  Light). Collections with older names (AS_camera, AS_lights, AS_archiv, AS_set, RS_Cameras,
  RS_Lights, also "AS_camera | Packshot") are renamed when a file is opened.
- Names (Preferences › Studio › Name Scheme): cameras P_/O_ (perspective/orthographic) and
  lights A_/P_/S_/D_ (area/point/spot/sun), then what they show or do, then the number:
  "P_side_01", "O_front_01", "A_key_01".
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
OLD_CAMERAS = ("Render Studio Cameras", "RS_Cameras", "AS_camera")
OLD_LIGHTS = ("Render Studio Lights", "RS_Lights", "AS_lights")
OLD_ARCHIVES = ("AS_archiv",)
OLD_SETS = ("AS_set",)
SET_COLLECTION = "Set"


def _pref(key, default):
    from . import runner
    p = runner.prefs()
    return (getattr(p, key, "") or "").strip() or default if p is not None else default


def camera_collection_name():
    return _pref("camera_collection", "Camera")


def light_collection_name():
    return _pref("light_collection", "Light")


def archive_collection_name():
    return _pref("archive_collection", "Archive")


def temp_collection_name():
    return _pref("temp_collection", "temp")


def active_camera(scene):
    """The user's active camera, also while the queue renders a temporary copy."""
    from .runner import RUN, TEMP_KEY
    cam = RUN.original("camera", scene.camera)
    if cam is not None and TEMP_KEY in cam:
        cam = bpy.data.objects.get(cam[TEMP_KEY])
    return cam if cam is not None and cam.type == 'CAMERA' else None


def scene_cameras(scene):
    """The cameras of the scene, without the temporary render copies of the queue."""
    return sorted((o for o in scene.objects if o.type == 'CAMERA' and "render_studio_temp_camera" not in o),
                  key=lambda o: o.name.lower())


def rename_old_collections():
    """Renames the collections of older versions (AS_camera, RS_Cameras …, also "AS_camera |
    Packshot") to the names set in the preferences, unless one of that name exists already."""
    pairs = [(old, new) for olds, new in ((OLD_CAMERAS, camera_collection_name()),
                                          (OLD_LIGHTS, light_collection_name()),
                                          (OLD_ARCHIVES, archive_collection_name()),
                                          (OLD_SETS, SET_COLLECTION)) for old in olds if old != new]
    for coll in list(bpy.data.collections):
        if coll.library is not None:
            continue
        for old, new in pairs:
            if coll.name == old or coll.name.startswith(old + " "):
                target = new + coll.name[len(old):]
                if bpy.data.collections.get(target) is None:
                    coll.name = target
                break


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


def scheme_on():
    from . import runner
    p = runner.prefs()
    return p is None or getattr(p, "name_scheme", True)


def _prefixes():
    return ((camera_collection_name(), light_collection_name(), "AS", "RS")
            + OLD_CAMERAS + OLD_LIGHTS)


def letter_of(obj, data_type=None):
    """P/O for a camera, A/P/S/D for a light."""
    from ..naming import CAMERA_LETTERS, LIGHT_LETTERS
    kind = data_type or (obj.data.type if obj is not None and obj.data is not None else "")
    if obj is None or obj.type == 'CAMERA':
        return CAMERA_LETTERS.get(kind, "P")
    return LIGHT_LETTERS.get(kind, "A")


def fallback_role(obj_type, data_type=""):
    if obj_type == 'CAMERA':
        return "view"
    return "sun" if data_type == 'SUN' else "light"


def scheme_name(obj_type, data_type, name, exclude=None):
    """The next free name for a new camera or light ("P_side_01"); without the scheme the
    short name with a number ("Side_01")."""
    from ..naming import numbered, role_of, scheme_base, CAMERA_LETTERS, LIGHT_LETTERS
    taken = [n for n in bpy.data.objects.keys() if n != exclude]
    if not scheme_on():
        return numbered(name, taken)
    letters = CAMERA_LETTERS if obj_type == 'CAMERA' else LIGHT_LETTERS
    role = role_of(name, _prefixes(), fallback_role(obj_type, data_type))
    return numbered(scheme_base(letters.get(data_type, "P" if obj_type == 'CAMERA' else "A"), role), taken)


def rename(obj, name):
    """Rename a camera or light (Render tab, Claude): in the scheme when it is on – "side"
    becomes "P_side_01" – and its data with it."""
    from ..naming import in_scheme, role_of
    name = (name or "").strip()
    if not name or obj is None:
        return obj.name if obj is not None else ""
    if scheme_on() and obj.type in {'CAMERA', 'LIGHT'}:
        letter = letter_of(obj)
        role = role_of(name, _prefixes(), fallback_role(obj.type, obj.data.type if obj.data else ""))
        free = name not in bpy.data.objects or bpy.data.objects[name] == obj
        if not (in_scheme(name, letter, role) and free):
            name = obj.name if in_scheme(obj.name, letter, role) else \
                scheme_name(obj.type, obj.data.type if obj.data else "", name, exclude=obj.name)
    obj.name = name
    if obj.data is not None and obj.data.users == 1 and obj.data.library is None:
        obj.data.name = obj.name
    return obj.name


def view_role(matrix):
    """Where a camera looks from: front, back, side, top, bottom or iso (world axes,
    Blender's front looks along +Y)."""
    import math
    back = matrix.to_3x3().col[2].normalized()   # the camera looks along −Z: this points to it
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, back.z))))
    if elevation > 60.0:
        return "top"
    if elevation < -60.0:
        return "bottom"
    azimuth = math.degrees(math.atan2(back.x, -back.y)) % 360.0   # 0 = in front of it (−Y)
    for angle, role in ((0.0, "front"), (90.0, "side"), (180.0, "back"), (270.0, "side"), (360.0, "front")):
        if abs(azimuth - angle) <= 22.5:
            return role
    return "iso"


def tidy_names():
    """Every camera and light in the scheme ("AS_camera Ortho Oben.001" → "O_top_01", a camera
    switched to orthographic "P_front_01" → "O_front_01"); with the scheme off only the bad
    names of older versions and Blender's ".001" are fixed. Returns {old: new}."""
    from ..naming import scheme_names, tidy
    objects = [o for o in bpy.data.objects if o.type in {'CAMERA', 'LIGHT'} and o.library is None
               and "render_studio_temp_camera" not in o and o.data is not None]
    if scheme_on():
        # a camera whose name says nothing ("Camera", "View") is named after where it looks from
        names = scheme_names([(o.name, letter_of(o), view_role(o.matrix_world) if o.type == 'CAMERA'
                               else fallback_role(o.type, o.data.type)) for o in objects], _prefixes())
    else:
        names = {}
        for kind, fallback in (('CAMERA', "Camera"), ('LIGHT', "Light")):
            names.update(tidy([o.name for o in objects if o.type == kind], _prefixes(), fallback))
    plan = [(o, o.name, names[o.name]) for o in objects if o.name in names]
    for i, (obj, _old, _new) in enumerate(plan):   # out of the way first: no "….001" on the way
        obj.name = "ai_studio_rename_%d" % i
    done = {}
    for obj, old, new in plan:
        obj.name = new
        if obj.data.users == 1 and obj.data.library is None:
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
    name = scheme_name('CAMERA', 'PERSP', name)   # "P_top_01" (or "Top_01"), never "….001"
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


def _named(cam, name):
    """After placing a new camera: its letter (P/O) and, when the name says nothing, where it
    looks from ("P_front_01")."""
    from ..naming import role_of
    if not scheme_on():
        return cam
    role = role_of(name, _prefixes(), "")
    rename(cam, role if role and role not in {"view"} else view_role(cam.matrix_world))
    return cam


def camera_from_view(scene, space, view_layer, name=""):
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
        return _named(cam, name)
    cam = _new_camera(scene, view_layer, name)
    cam.matrix_world = rv3d.view_matrix.inverted()
    if rv3d.view_perspective == 'ORTHO':
        cam.data.type = 'ORTHO'
        cam.data.ortho_scale = rv3d.view_distance * 2.0
    else:
        # The viewport's "focal length" uses a 72 mm sensor; camera default is 36 mm.
        cam.data.lens = space.lens / 2.0
    return _named(cam, name)


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
    return _named(cam, name)


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

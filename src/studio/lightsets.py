# SPDX-License-Identifier: GPL-3.0-or-later
"""Light sets: every camera has its own lights (Light tab).

A light set is a collection under the light collection ("Light · P_Pack_01") that points to its camera
(collection["studio_light_camera"] = the camera object, so renaming the camera keeps it). Only the set of
the camera you look through is shown and rendered; "Light · All Cameras" (collection["studio_light_shared"])
always is. The World, background and floor stay the same for all cameras.

A file from before (lights without sets) works as it always did until the lights are given to the cameras
once (setup(), the dialog in the Light tab). From then on (scene["studio_light_sets"]) a camera that has no
set yet gets a copy of the set of the camera before it, so a new camera is never dark.

Each set keeps its own variants A / B … (looks.py), in collection["studio_looks"].
The render workers run without the add-on: they switch the sets themselves with the same keys
(render_worker.py, film_job.py, renderfarm.py: _light_sets).
"""

import json

import bpy

from . import cameras

CAMERA_KEY = "studio_light_camera"
SHARED_KEY = "studio_light_shared"
ON_KEY = "studio_light_sets"            # on the scene: the lights belong to cameras
LAST_KEY = "studio_light_last"          # on the scene: the camera the sets followed last
LOOKS_KEY = "studio_looks"              # on a set: its variants as JSON
LOOK_INDEX_KEY = "studio_look_index"


# ------------------------------------------------------------------ the sets of a scene

def enabled(scene):
    return bool(scene.get(ON_KEY))


def is_set(coll):
    return coll.get(CAMERA_KEY) is not None or bool(coll.get(SHARED_KEY))


def is_shared(coll):
    return bool(coll.get(SHARED_KEY))


def camera_of(coll):
    owner = coll.get(CAMERA_KEY)
    return owner if isinstance(owner, bpy.types.Object) else None


def sets(scene):
    return [c for c in scene.collection.children_recursive if is_set(c)]


def set_of(scene, cam):
    if cam is None:
        return None
    return next((c for c in sets(scene) if camera_of(c) == cam), None)


def shared_set(scene, create=False):
    found = next((c for c in sets(scene) if is_shared(c)), None)
    if found is None and create:
        found = bpy.data.collections.new("%s · All Cameras" % cameras.light_collection_name())
        found[SHARED_KEY] = True
        _parent(scene).children.link(found)
    return found


def _parent(scene):
    return cameras.named_collection(scene, cameras.light_collection_name())


def set_name(cam):
    return "%s · %s" % (cameras.light_collection_name(), cam.name)


def new_set(scene, cam):
    coll = bpy.data.collections.new(set_name(cam))
    coll[CAMERA_KEY] = cam
    _parent(scene).children.link(coll)
    return coll


def set_lights(coll):
    if coll is None:
        return []
    return sorted((o for o in coll.all_objects if o.type == 'LIGHT'), key=lambda o: o.name.lower())


def owner_of(scene, obj):
    """The set a light belongs to (None: no set, it lights every camera)."""
    return next((c for c in sets(scene) if obj.name in c.all_objects), None)


def _archived(obj):
    archive = cameras.archive_collection_name()
    return any(c.name == archive for c in obj.users_collection)


def _visible(scene, coll, cam=None):
    cam = _set_camera(scene) if cam is None else cam
    return is_shared(coll) or (cam is not None and camera_of(coll) == cam)


def current_lights(scene):
    """The lights the camera you look through has: its own set, the shared set and lights without a set.
    Before the setup: every light of the scene, as always."""
    found = [o for o in scene.objects if o.type == 'LIGHT']
    if not enabled(scene):
        return found
    shown, owned = set(), set()
    for coll in sets(scene):
        names = {o.name for o in coll.all_objects if o.type == 'LIGHT'}
        owned |= names
        if _visible(scene, coll):
            shown |= names
    return [o for o in found if (o.name in shown or o.name not in owned) and not (o.name not in shown and _archived(o))]


def groups(scene):
    """(camera set, shared set, lights without a set) for the list in the Light tab."""
    own = set_of(scene, scene.camera)
    shared = shared_set(scene)
    owned = set()
    for coll in sets(scene):
        owned |= {o.name for o in coll.all_objects}
    loose = [o for o in scene.objects if o.type == 'LIGHT' and o.name not in owned and not _archived(o)]
    return own, shared, sorted(loose, key=lambda o: o.name.lower())


def active_collection(scene):
    """Where a new light goes: the set of the camera you look through (made when missing)."""
    if not enabled(scene) and not any(o.type == 'LIGHT' for o in scene.objects):
        scene[ON_KEY] = True              # a scene without lights starts with sets right away
    if enabled(scene) and scene.camera is not None:
        return set_of(scene, scene.camera) or new_set(scene, scene.camera)
    return _parent(scene)


# ------------------------------------------------------------------ copy and move

def _copy_light(scene, src, coll):
    from . import lights
    new = lights.duplicate_light(scene, src)
    for c in list(new.users_collection):
        c.objects.unlink(new)
    coll.objects.link(new)
    return new


def copy_into(scene, src_coll, coll, only_on=False):
    """Copies the lights of one set into another (with the variants, names remapped)."""
    names = {}
    for obj in set_lights(src_coll):
        if only_on and obj.hide_render:
            continue
        names[obj.name] = _copy_light(scene, obj, coll).name
    raw = src_coll.get(LOOKS_KEY) if src_coll is not None else None
    if raw:
        try:
            looks = json.loads(raw)
            for look in looks:
                data = look.get("data") or {}
                data["lights"] = {names[k]: v for k, v in (data.get("lights") or {}).items() if k in names}
                look["data"] = data
            coll[LOOKS_KEY] = json.dumps(looks)
            coll[LOOK_INDEX_KEY] = int(src_coll.get(LOOK_INDEX_KEY, 0))
        except (ValueError, TypeError, AttributeError):
            pass
    return names


def clear_set(scene, coll):
    """Removes the lights of a set (they go to the archive, switched off)."""
    for obj in set_lights(coll):
        archive(scene, obj)
    for key in (LOOKS_KEY, LOOK_INDEX_KEY):
        if key in coll:
            del coll[key]


def archive(scene, obj):
    target = cameras.named_collection(scene, cameras.archive_collection_name())
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    target.objects.link(obj)
    obj.hide_render = obj.hide_viewport = True


def take_from(scene, cam, other):
    """The camera's set becomes a copy of the set of camera `other`."""
    coll = set_of(scene, cam) or new_set(scene, cam)
    src = set_of(scene, other)
    if src is None or src == coll:
        raise ValueError("%s has no lights of its own" % (other.name if other else "?"))
    clear_set(scene, coll)
    copy_into(scene, src, coll)
    return coll


def move_light(scene, obj, coll):
    """A light goes into this set (out of the others)."""
    for c in list(obj.users_collection):
        if is_set(c) or c == _parent(scene) or c == scene.collection:
            c.objects.unlink(obj)
    if obj.name not in coll.objects:
        coll.objects.link(obj)


def make_shared(scene, obj, shared=True):
    if shared:
        move_light(scene, obj, shared_set(scene, create=True))
    elif scene.camera is not None:
        move_light(scene, obj, set_of(scene, scene.camera) or new_set(scene, scene.camera))


# ------------------------------------------------------------------ other add-ons

WRANGLER_KEY = "last_light_type"


def guard_lights(scene):
    """Light Wrangler gives every light that gets active without its mark its own "Default" (new data
    "Area.Default": name, size and look gone). Our lights carry the mark, so it leaves them as they are."""
    obj = getattr(bpy.context, "active_object", None)
    if obj is not None and obj.type == 'LIGHT' and obj.get(WRANGLER_KEY) != obj.data.type:
        obj[WRANGLER_KEY] = obj.data.type
    for obj in [o for o in scene.objects if o.type == 'LIGHT' and o.get(WRANGLER_KEY) is None][:50]:
        obj[WRANGLER_KEY] = obj.data.type


# ------------------------------------------------------------------ follow the camera

def _set_camera(scene, cam=None):
    """The camera whose set shows: the scene camera, but only a real camera. Looking through a light
    ("Set Active Object as Camera") keeps the set of the camera before."""
    cam = scene.camera if cam is None else cam
    if cam is not None and cam.type == 'CAMERA':
        return cam
    last = bpy.data.objects.get(scene.get(LAST_KEY) or "")
    return last if last is not None and last.type == 'CAMERA' else None


def _layer_collections(layer, coll):
    """The view layer's entries of this collection (where its eye is)."""
    found, todo = [], [layer.layer_collection]
    while todo:
        lc = todo.pop()
        if lc.collection == coll:
            found.append(lc)
        todo.extend(lc.children)
    return found


def follow(scene, cam=None, viewport=True):
    """Shows and renders only the sets of this camera (default: the scene camera) and the shared one.
    In the viewport only the eye of the view layer changes: the lights stay evaluated, so nothing you look
    through (a light as camera) is ever taken out of the scene – that crashed Blender. A set that holds the
    object you look through always stays."""
    if not enabled(scene):
        return
    view = scene.camera
    cam = _set_camera(scene, cam)
    for coll in sets(scene):
        hide = not _visible(scene, coll, cam) if cam is not None else False
        if hide and view is not None and view.name in coll.all_objects:
            hide = False
        if coll.hide_render != hide:
            coll.hide_render = hide
        if coll.hide_viewport:                 # from 4.30.0: never disable the collection itself
            coll.hide_viewport = False
        if viewport:
            for layer in scene.view_layers:
                for lc in _layer_collections(layer, coll):
                    if lc.hide_viewport != hide:
                        lc.hide_viewport = hide
    current = cam
    if current is not None and scene.get(LAST_KEY) != current.name:
        last = bpy.data.objects.get(scene.get(LAST_KEY) or "")
        scene[LAST_KEY] = current.name
        if set_of(scene, current) is None:
            _pending.append((scene.name, current.name, last.name if last is not None else ""))
            if not bpy.app.timers.is_registered(_make_pending):
                bpy.app.timers.register(_make_pending, first_interval=0.0)


_follow_scenes = set()


def _follow_later():
    for name in list(_follow_scenes):
        scene = bpy.data.scenes.get(name)
        if scene is not None:
            try:
                follow(scene)
            except Exception as ex:
                print("ID Toolkit: light sets:", ex)
    _follow_scenes.clear()
    return None


def schedule_follow(scene):
    """From the depsgraph handler: the sets switch right after the update, not inside it."""
    if not enabled(scene):
        return
    cam = _set_camera(scene)
    if cam is not None and scene.get(LAST_KEY) == cam.name and not _mismatch(scene, cam):
        return
    _follow_scenes.add(scene.name)
    if not bpy.app.timers.is_registered(_follow_later):
        bpy.app.timers.register(_follow_later, first_interval=0.0)


def _mismatch(scene, cam):
    view = scene.camera
    for coll in sets(scene):
        hide = not _visible(scene, coll, cam)
        if hide and view is not None and view.name in coll.all_objects:
            hide = False
        if coll.hide_render != hide or coll.hide_viewport:
            return True
    return False


_pending = []


def _make_pending():
    """A camera without a set got active: it starts with a copy of the set of the camera before
    (not inside the depsgraph handler: new objects are off limits there)."""
    while _pending:
        scene_name, cam_name, last_name = _pending.pop(0)
        scene = bpy.data.scenes.get(scene_name)
        cam = bpy.data.objects.get(cam_name)
        if scene is None or cam is None or set_of(scene, cam) is not None:
            continue
        coll = new_set(scene, cam)
        src = set_of(scene, bpy.data.objects.get(last_name)) if last_name else None
        if src is not None:
            copy_into(scene, src, coll)
        follow(scene)
    return None


@bpy.app.handlers.persistent
def _on_frame(scene, *_args):
    try:
        follow(scene, viewport=False)
    except Exception as ex:          # never block playback or rendering
        print("ID Toolkit: light sets:", ex)


# ------------------------------------------------------------------ the setup (files from before)

def needs_setup(scene):
    return not enabled(scene) and any(o.type == 'LIGHT' for o in scene.objects)


def light_collections(scene):
    """Collections that hold lights directly (the user's own sets from before), not the archive."""
    archive = cameras.archive_collection_name()
    found = []
    for coll in scene.collection.children_recursive:
        if coll.name == archive or is_set(coll):
            continue
        if any(o.type == 'LIGHT' for o in coll.objects):
            found.append(coll)
    return found


def sources(scene):
    """[(key, label, description)] what a camera's lights can come from."""
    items = [('EMPTY', "Empty", "No lights: add them later"),
             ('CURRENT', "Lights as Now", "A copy of the lights that are on now")]
    for coll in light_collections(scene):
        n = sum(1 for o in coll.objects if o.type == 'LIGHT')
        items.append(('COLL:' + coll.name, "%s (%d)" % (coll.name, n), "The lights of this collection"))
    for index, look in enumerate(scene.studio.light_looks):
        items.append(('LOOK:%d' % index, "Look %s" % look.name, "The lights that are on in this look"))
    return items


def guess(scene, cam):
    """The source a camera most likely means: a light collection with a word of the camera's name."""
    from ..naming import role_of
    words = set(role_of(cam.name, cameras._prefixes(), "").split("-")) - {""}
    best = None
    for coll in light_collections(scene):
        theirs = set(coll.name.lower().replace("_", " ").replace("-", " ").split())
        if words & theirs:
            best = 'COLL:' + coll.name
            break
    return best or 'CURRENT'


def setup(scene, mapping):
    """Gives every camera its lights once: mapping {camera name: source key}. A collection given to one
    camera becomes its set; given to more, the others get copies. Lights nobody took go to the archive,
    switched off. The looks so far go into the sets that came from them, as A / B … are now per camera."""
    from . import looks
    used, reused = set(), set()
    old_looks = [(look.name, look.data) for look in scene.studio.light_looks]
    for cam_name, key in mapping.items():
        cam = bpy.data.objects.get(cam_name)
        if cam is None:
            continue
        coll = set_of(scene, cam)
        if key.startswith('COLL:') and coll is None:
            src = bpy.data.collections.get(key[5:])
            if src is not None and src.name not in reused:          # this collection becomes the set
                reused.add(src.name)
                src[CAMERA_KEY] = cam
                src["studio_old_name"] = src.name
                src.name = set_name(cam)
                parent = _parent(scene)
                for p in [p for p in scene.collection.children_recursive if src.name in p.children] + \
                         ([scene.collection] if src.name in scene.collection.children else []):
                    if p != parent:
                        p.children.unlink(src)
                if src.name not in parent.children:
                    parent.children.link(src)
                src.hide_render = src.hide_viewport = False
                used |= {o.name for o in src.objects}
                continue
        coll = coll or new_set(scene, cam)
        if key == 'CURRENT':
            for obj in [o for o in scene.objects if o.type == 'LIGHT' and not o.hide_render and not _archived(o)]:
                used.add(obj.name)
                _copy_light(scene, obj, coll)
        elif key.startswith('COLL:'):
            src = bpy.data.collections.get(key[5:]) or next(
                (c for c in bpy.data.collections if c.get("studio_old_name") == key[5:]), None)
            for obj in [o for o in (src.objects if src else []) if o.type == 'LIGHT']:
                used.add(obj.name)
                _copy_light(scene, obj, coll)
        elif key.startswith('LOOK:'):
            index = int(key[5:])
            if 0 <= index < len(old_looks):
                try:
                    states = (json.loads(old_looks[index][1] or "{}").get("lights") or {})
                except ValueError:
                    states = {}
                for name, state in states.items():
                    obj = bpy.data.objects.get(name)
                    if obj is None or obj.type != 'LIGHT' or not state.get("on", True):
                        continue
                    used.add(name)
                    new = _copy_light(scene, obj, coll)
                    looks._restore_light(scene, new, state)
    owned = set()
    for coll in sets(scene):
        owned |= {o.name for o in coll.all_objects}
    for obj in [o for o in scene.objects if o.type == 'LIGHT' and o.name not in owned]:
        archive(scene, obj)
    if old_looks:
        scene["studio_light_looks_before"] = json.dumps(old_looks)
        scene.studio.light_looks.clear()
        scene.studio.light_look_index = -1
    scene[ON_KEY] = True
    scene[LAST_KEY] = scene.camera.name if scene.camera else ""
    follow(scene)


def register():
    for handlers in (bpy.app.handlers.frame_change_pre, bpy.app.handlers.render_pre):
        if _on_frame not in handlers:
            handlers.append(_on_frame)


def unregister():
    for handlers in (bpy.app.handlers.frame_change_pre, bpy.app.handlers.render_pre):
        if _on_frame in handlers:
            handlers.remove(_on_frame)
    for timer in (_make_pending, _follow_later):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)

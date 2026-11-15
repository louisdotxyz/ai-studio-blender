# SPDX-License-Identifier: GPL-3.0-or-later
"""Low or High Quality by itself: a render (a camera's still or a section of the film) is Low Quality
first; once it has a Low Quality render and nothing in the scene changed since (objects moved,
geometry, materials, lights, World, camera), the next one is High Quality. Shift-click: the other one.

Changes are counted in a depsgraph handler (frame changes and the scene's own settings do not count).
What was clean when the file was saved is clean again after opening it."""

import bpy
from bpy.app.handlers import persistent

CLEAN_KEY = "studio_lq_clean"      # on the scene, written when saving: the keys that were clean
_edits = [0]
_lq = {}                           # (scene name, key) → the edit count when its Low Quality was queued
_frame = {}


def _counts(update):
    data = update.id
    if isinstance(data, bpy.types.Object):
        return update.is_updated_transform or update.is_updated_geometry
    if isinstance(data, (bpy.types.Mesh, bpy.types.Curve)):
        return update.is_updated_geometry
    if isinstance(data, (bpy.types.Material, bpy.types.World, bpy.types.Light, bpy.types.Camera,
                         bpy.types.NodeTree, bpy.types.Image)):
        return True
    return False


@persistent
def _changed(scene, depsgraph):
    if _frame.get(scene.name) != scene.frame_current:     # scrubbing, playing: not a change
        _frame[scene.name] = scene.frame_current
        return
    from .runner import RUN
    if getattr(RUN, "preparing", False):
        return
    try:
        if any(_counts(u) for u in depsgraph.updates):
            _edits[0] += 1
    except ReferenceError:
        pass


def still_key(camera_name):
    return "cam:" + camera_name


def section_key(section):
    return "sec:" + section


def record(scene, key):
    """A Low Quality render of `key` was queued now."""
    _lq[(scene.name, key)] = _edits[0]


def clean(scene, key):
    return _lq.get((scene.name, key)) == _edits[0]


def next_quality(scene, key, done=True):
    """'HIGH' when `key` had a Low Quality render (done) and nothing changed since, else 'LOW'."""
    return 'HIGH' if done and clean(scene, key) else 'LOW'


def describe(quality):
    return ("High Quality (a Low Quality is there and nothing changed since)" if quality == 'HIGH'
            else "Low Quality to check it (Render again without changes: High Quality)")


@persistent
def _save(_dummy=None):
    for scene in bpy.data.scenes:
        keys = [k for (s, k), n in _lq.items() if s == scene.name and n == _edits[0]]
        try:
            if keys:
                scene[CLEAN_KEY] = keys
            elif CLEAN_KEY in scene:
                del scene[CLEAN_KEY]
        except (TypeError, AttributeError, RuntimeError):
            pass


def _restore():
    """A little after opening: the first evaluation of the file has run (it is not a change)."""
    _lq.clear()
    for scene in bpy.data.scenes:
        for key in scene.get(CLEAN_KEY, []) or []:
            _lq[(scene.name, str(key))] = _edits[0]
    return None


@persistent
def _load(_dummy=None):
    _frame.clear()
    if bpy.app.timers.is_registered(_restore):
        bpy.app.timers.unregister(_restore)
    bpy.app.timers.register(_restore, first_interval=1.5)


_HANDLERS = ((bpy.app.handlers.depsgraph_update_post, _changed), (bpy.app.handlers.save_pre, _save),
             (bpy.app.handlers.load_post, _load))


def register():
    for handlers, fn in _HANDLERS:
        if fn not in handlers:
            handlers.append(fn)


def unregister():
    for handlers, fn in _HANDLERS:
        if fn in handlers:
            handlers.remove(fn)

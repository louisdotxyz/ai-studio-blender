# SPDX-License-Identifier: GPL-3.0-or-later
"""The render farm: Flamenco (Render tab · Render Farm).

- The Manager: its address comes from the Flamenco add-on's preferences (else http://localhost:8080/);
  whether it answers and how many workers are awake is asked in the background every 15 s while the
  Render tab is open. A Manager on this Mac (~/Flamenco/flamenco-manager) can be started from here.
- High Quality films on the farm (scene.studio.farm_films): a section's High Quality render goes to the
  farm instead of this Mac. farm_job.py prepares a copy in the shared storage (camera, full size, PNG,
  images packed into it), sends a "simple-blender-render" job and copies the finished frames into the
  section's frames folder; its bar is the same as a film's (film.py), the Video Editor shows the frames.
"""

import json
import os
import re
import subprocess
import threading
import time
import urllib.request

import bpy

from . import cut, film

FARM_DIR = ".farm"
DEFAULT_URL = "http://localhost:8080/"
MANAGER_DIRS = ("~/Flamenco", "/Applications/Flamenco")
_state = {"checked": 0.0, "busy": False, "online": None, "version": "", "awake": 0, "workers": 0,
          "storage": "", "error": ""}


def addon_prefs():
    addon = bpy.context.preferences.addons.get("flamenco")
    return addon.preferences if addon is not None else None


def manager_url():
    prefs = addon_prefs()
    url = getattr(prefs, "manager_url", "") if prefs is not None else ""
    return url or DEFAULT_URL


def is_local(url=None):
    return re.match(r"https?://(localhost|127\.0\.0\.1|\[::1\])(:|/|$)", url or manager_url()) is not None


def _get(url, path, timeout=3):
    with urllib.request.urlopen(url.rstrip("/") + "/" + path, timeout=timeout) as response:
        return json.loads(response.read() or b"{}")


def _check(url):
    out = {"online": False, "version": "", "awake": 0, "workers": 0, "storage": "", "error": ""}
    try:
        out["version"] = _get(url, "api/v3/version").get("shortversion", "")
        out["online"] = True
        workers = _get(url, "api/v3/worker-mgt/workers").get("workers", [])
        out["workers"] = len(workers)
        out["awake"] = sum(1 for w in workers if w.get("status") == "awake")
        try:
            out["storage"] = _get(url, "api/v3/configuration/shared-storage/users/darwin").get("location", "")
        except Exception:
            out["storage"] = ""
    except Exception as ex:
        out["error"] = str(getattr(ex, "reason", ex))[:80]
    out["checked"] = time.time()
    _state.update(out, busy=False)
    try:
        from . import runner
        bpy.app.timers.register(lambda: (runner.tag_redraw(), None)[1], first_interval=0.1)
    except Exception:
        pass


def status(force=False):
    """What is known of the Manager now; asks again in the background (every 15 s, or now when forced)."""
    if not _state["busy"] and (force or time.time() - _state["checked"] > 15.0):
        _state["busy"] = True
        threading.Thread(target=_check, args=(manager_url(),), daemon=True).start()
    return _state


def manager_binary():
    for folder in MANAGER_DIRS:
        path = os.path.join(os.path.expanduser(folder), "flamenco-manager")
        if os.path.isfile(path):
            return path
    return ""


def start_manager():
    """The Manager on this Mac, in its own process (its folder holds its settings and database)."""
    path = manager_binary()
    if not path:
        raise ValueError("No Flamenco Manager in ~/Flamenco")
    folder = os.path.dirname(path)
    log = open(os.path.join(folder, "manager-from-ai-studio.log"), "a", encoding="utf-8")
    try:
        subprocess.Popen([path], cwd=folder, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    finally:
        log.close()
    _state["checked"] = 0.0
    return path


def _safe(text):
    return re.sub(r"[^\w\-]+", "_", text).strip("_") or "film"


def start_section(scene, name, start, end, cam):
    """A section's High Quality frames on the farm (see the module). Returns the film entry."""
    from .ui_common import _user_resolution
    from . import core
    st = status()
    if not st["online"]:
        raise ValueError("The Flamenco Manager does not answer (%s)" % manager_url())
    storage = st["storage"]
    if not storage:
        raise ValueError("The Manager did not say where its shared storage is")
    if not os.path.isdir(storage):
        raise ValueError("The shared storage is not reachable on this Mac: %s (network drive mounted?)" % storage)
    folder = cut.frames_folder(name)
    stem = cut.frame_stem(name)
    work = os.path.join(folder, FARM_DIR)
    os.makedirs(work, exist_ok=True)
    moved = film._clear_old_frames(folder, stem, cut.archive_folder())
    for old in ("status.json",):
        try:
            os.remove(os.path.join(work, old))
        except OSError:
            pass
    project = _safe(core.project_name(None, scene.studio.output_name))
    stamp = time.strftime("%Y-%m-%d_%H%M%S")
    remote = os.path.join(storage, "ai-studio", project, "%s_%s" % (_safe(name), stamp))
    rx, ry = _user_resolution(scene, cam)
    overrides = [["render.resolution_x", rx], ["render.resolution_y", ry], ["render.resolution_percentage", 100],
                 ["render.image_settings.media_type", "IMAGE"], ["render.image_settings.file_format", "PNG"],
                 ["render.image_settings.color_mode", "RGB"], ["render.image_settings.color_depth", "8"],
                 ["render.use_file_extension", True]]
    if scene.render.engine == 'CYCLES':
        overrides.append(["cycles.device", "GPU"])
    job = {"scene": scene.name, "section": name, "frames": [start, end], "camera": cam.name,
           "overrides": overrides, "manager": manager_url(), "name": "%s %s HQ %s" % (project, _safe(name), stamp),
           "project": project, "farm_blend": os.path.join(remote, "scene.blend"),
           "farm_output": os.path.join(remote, "frames", stem + "_"), "folder": folder,
           "chunk": scene.studio.farm_chunk, "fps": scene.render.fps / scene.render.fps_base,
           "quality": "HIGH", "overrides_for_status": [["render.resolution_x", rx],
                                                       ["render.resolution_percentage", 100]]}
    with open(os.path.join(work, "job.json"), "w", encoding="utf-8") as f:
        json.dump(job, f, indent=1)
    film_job = os.path.join(folder, film.FILM_DIR)             # what the Video Editor reads: quality, size
    os.makedirs(film_job, exist_ok=True)
    with open(os.path.join(film_job, "job.json"), "w", encoding="utf-8") as f:
        json.dump({"scene": scene.name, "quality": "HIGH", "section": name, "frames": [start, end],
                   "overrides": job["overrides_for_status"], "farm": True}, f, indent=1)
    from . import runner
    runner.RUN.preparing = True
    try:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(work, "scene.blend"), copy=True, compress=False,
                                    relative_remap=True, check_existing=False)
    finally:
        runner.RUN.preparing = False
    log = open(os.path.join(work, "log.txt"), "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(film._command(work), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)
    finally:
        log.close()
    entry = film.register_process(work, folder, "%s · HQ · Farm" % name, proc.pid,
                                  extra={"scene": scene.name, "quality": "HIGH", "section": name, "farm": True})
    if moved:
        runner.log("farm %s: %d older frames moved to archiv" % (name, moved))
    runner.log("farm %s: job prepared (%d frames) → %s" % (name, end - start + 1, remote))
    return entry


def draw(layout, scene):
    """Render tab · Render Farm: the Manager, its workers, High Quality films on the farm."""
    rq = scene.studio
    st = status()
    prefs = addon_prefs()
    col = layout.column(align=True)
    row = col.row(align=True)
    if st["online"]:
        row.label(text="Flamenco %s · %d of %d workers awake" % (st["version"], st["awake"], st["workers"]),
                  icon='CHECKMARK', translate=False)
    elif st["online"] is None:
        row.label(text="Asking the Manager …", icon='TIME')
    else:
        row.label(text="Manager offline", icon='ERROR')
    row.operator("studio.farm", text="", icon='FILE_REFRESH', emboss=False).action = 'CHECK'
    row.operator("studio.farm", text="", icon='URL', emboss=False).action = 'OPEN'
    if prefs is not None:
        col.prop(prefs, "manager_url", text="")
    else:
        hint = col.row()
        hint.active = False
        hint.label(text="Flamenco add-on not installed (Manager: %s)" % DEFAULT_URL, translate=False)
    if not st["online"] and is_local() and manager_binary():
        col.operator("studio.farm", text="Start the Manager on this Mac", icon='PLAY').action = 'START'
    col = layout.column(align=True)
    row = col.row(align=True)
    row.prop(rq, "farm_films", text="High Quality Films on the Farm", toggle=True, icon='NETWORK_DRIVE')
    sub = row.row(align=True)
    sub.enabled = rq.farm_films
    sub.ui_units_x = 4
    sub.prop(rq, "farm_chunk", text="Chunk")
    if st["storage"]:
        info = layout.row()
        info.active = False
        info.label(text=st["storage"], icon='FILE_FOLDER', translate=False)


class STUDIO_OT_farm(bpy.types.Operator):
    bl_idname = "studio.farm"
    bl_label = "Render Farm"
    bl_options = {'INTERNAL'}

    action: bpy.props.StringProperty(default="CHECK", options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return {"CHECK": "Ask the Flamenco Manager again (online, workers)",
                "OPEN": "The Manager's web page (jobs, workers, settings) in the browser",
                "START": "Start the Flamenco Manager on this Mac (in the background)"}.get(props.action, "")

    def execute(self, context):
        try:
            if self.action == 'START':
                start_manager()
                self.report({'INFO'}, "Flamenco Manager starting")
            elif self.action == 'OPEN':
                bpy.ops.wm.url_open(url=manager_url())
            status(force=True)
        except (ValueError, OSError) as ex:
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


classes = (STUDIO_OT_farm,)

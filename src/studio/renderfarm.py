# SPDX-License-Identifier: GPL-3.0-or-later
"""The render farm over a server folder (Render tab · Render Farm): jobs go to the ZHdK Mac Studios
through files on the DDE share only. There is no network connection to the render machines and no port
is used here; the workers (<share>/_worker/Mac_Render_Worker.command) read the same folder.

Folder protocol (fixed, the workers rely on it):
    <share>/queue/<job>.blend        the packed copy of the scene (written as .blend.part, renamed when complete)
    <share>/queue/<job>.json         written last (as .json.part, renamed): only then the job is ready
                                     {"scene", "start", "end", "format", "sent_by", "note"}
    <share>/renders/<job>/<job>_####.<ext>   the workers' frames; 0 bytes = a frame being rendered
    <share>/queue/<job>.done         written by a worker when all frames are there
    <share>/_worker/status/<name>-render.txt   "Name | rendert JOB / wartet | time"

Sending never changes the working file: it is saved as a copy into a local folder; a Blender process in
the background makes linked data local and packs all resources into that copy (drivers stay as they are);
a thread copies it to the share with progress (files of 3 GB and more never freeze the interface). Half
files stay as .part and are never a job. Nothing in queue/ is deleted here.
"""

import getpass
import glob
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time

import bpy
from bpy.props import EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator

DEFAULT_SHARE = "/Volumes/DDE/BDE_VID/BA/02_Studierende/Louis Widmer/Flamenco"
CONNECT_HINT = "Finder: Cmd+K smb://fileredu.ad.zhdk.ch/DDE"
FORMATS = (('PNG', "PNG", "8-bit PNG frames", 'IMAGE_DATA', 0),
           ('JPEG', "JPEG", "Small JPEG frames (previews)", 'IMAGE_RGB', 1),
           ('OPEN_EXR', "OpenEXR", "32-bit float frames for grading", 'IMAGE_RGB_ALPHA', 2))
EXTENSIONS = {'PNG': "png", 'JPEG': "jpg", 'OPEN_EXR': "exr"}
REFRESH = 15.0                 # seconds between looks at the share while the panel is open
CHUNK = 8 * 1024 * 1024        # bytes per step of the copy to the share

PACK_SCRIPT = '''
import bpy, sys
out, scene_name, fmt = sys.argv[sys.argv.index("--") + 1:]
local = 0
for idb in list(bpy.data.user_map().keys()):      # linked library data becomes local (drivers untouched)
    if getattr(idb, "library", None) is not None:
        try:
            idb.make_local()
            local += 1
        except Exception as ex:
            print("RF make_local", idb.name, ex)
try:
    bpy.ops.file.make_paths_absolute()
except Exception as ex:
    print("RF absolute", ex)
try:
    bpy.ops.file.pack_all()
except Exception as ex:
    print("RF pack", ex)
scene = bpy.data.scenes.get(scene_name)
if scene is not None:                              # the job's format, so the worker can set it
    images = scene.render.image_settings
    try:
        images.media_type = 'IMAGE'
    except Exception:
        pass
    try:
        images.file_format = fmt
    except Exception as ex:
        print("RF format", ex)
bpy.ops.wm.save_as_mainfile(filepath=out, compress=True, relative_remap=False)
print("RF packed, %d made local" % local)
'''


# ------------------------------------------------------------------ the share

def share_root():
    from . import runner
    p = runner.prefs()
    root = getattr(p, "farm_share", "") if p is not None else ""
    return bpy.path.abspath(root or DEFAULT_SHARE).rstrip("/\\")


def connected(root=None):
    return os.path.isdir(root or share_root())


def job_name(blend_path, scene_name, now=None):
    """<file>_<scene>_<YYYYmmdd-HHMMSS>, only letters, digits, - and _."""
    stem = os.path.splitext(os.path.basename(blend_path or "untitled"))[0] or "untitled"
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    name = "%s_%s_%s" % (stem, scene_name, stamp)
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", name).strip("_")


def _done_files(folder, job):
    """(frames with content, placeholders) in renders/<job>."""
    done = empty = 0
    try:
        names = os.listdir(folder)
    except OSError:
        return 0, 0
    for name in names:
        if not name.startswith(job + "_") or name.endswith(".part"):
            continue
        try:
            size = os.path.getsize(os.path.join(folder, name))
        except OSError:
            continue
        if size > 0:
            done += 1
        else:
            empty += 1
    return done, empty


def scan(root):
    """What the share says: {"ok", "error", "jobs": [{"job", "scene", "start", "end", "total", "done",
    "state" ('waiting' / 'rendering' / 'done'), "note", "sent_by", "time"}], "halves": [names],
    "workers": [(name, what, when)]} (newest job first)."""
    out = {"ok": False, "error": "", "jobs": [], "halves": [], "workers": [], "time": time.time()}
    if not os.path.isdir(root):
        out["error"] = "Share not connected (%s)" % CONNECT_HINT
        return out
    queue = os.path.join(root, "queue")
    try:
        names = os.listdir(queue)
    except OSError as ex:
        names = []
        if os.path.exists(queue):
            out["error"] = "queue/ not readable: %s" % ex
    workers = []
    for path in sorted(glob.glob(os.path.join(root, "_worker", "status", "*-render.txt"))):
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                line = f.readline().strip()
        except OSError:
            continue
        parts = [p.strip() for p in line.split("|")]
        if parts and parts[0]:
            workers.append((parts[0], parts[1] if len(parts) > 1 else "", parts[2] if len(parts) > 2 else ""))
    out["workers"] = workers
    busy = {w[1].split(" ", 1)[1].strip() for w in workers if w[1].startswith("rendert ")}
    for name in names:
        if name.endswith(".part"):
            out["halves"].append(name)
            continue
        if not name.endswith(".json"):
            continue
        job = name[:-5]
        try:
            with open(os.path.join(queue, name), encoding="utf-8") as f:
                cfg = json.load(f)
            start, end = int(cfg.get("start", 0)), int(cfg.get("end", 0))
        except (OSError, ValueError, TypeError):
            continue
        done, empty = _done_files(os.path.join(root, "renders", job), job)
        total = max(0, end - start + 1)
        if os.path.exists(os.path.join(queue, job + ".done")):
            state = 'done'
        elif empty or done or job in busy:
            state = 'rendering'
        else:
            state = 'waiting'
        try:
            when = os.path.getmtime(os.path.join(queue, name))
        except OSError:
            when = 0.0
        out["jobs"].append({"job": job, "scene": cfg.get("scene", ""), "start": start, "end": end, "total": total,
                            "done": done, "state": state, "note": cfg.get("note", ""),
                            "sent_by": cfg.get("sent_by", ""), "time": when,
                            "blend": os.path.exists(os.path.join(queue, job + ".blend"))})
    out["jobs"].sort(key=lambda j: j["time"], reverse=True)
    out["ok"] = True
    return out


_view = {"scan": None, "busy": False, "drawn": 0.0}


def _scan_now(root):
    try:
        _view["scan"] = scan(root)
    except Exception as ex:                       # the share away in the middle of it
        _view["scan"] = {"ok": False, "error": str(ex), "jobs": [], "halves": [], "workers": [],
                         "time": time.time()}
    _view["busy"] = False


def _refresh(force=False):
    if _view["busy"]:
        return
    last = (_view["scan"] or {}).get("time", 0.0)
    if force or time.time() - last > REFRESH:
        _view["busy"] = True
        threading.Thread(target=_scan_now, args=(share_root(),), daemon=True).start()


def _tick():
    """Looks at the share every 15 s while the panel is drawn; stops when it has not been for a while."""
    if time.monotonic() - _view["drawn"] > 2 * REFRESH:
        return None
    _refresh()
    from . import runner
    runner.tag_redraw()
    return REFRESH


# ------------------------------------------------------------------ sending

_send = {"phase": "", "job": "", "progress": 0.0, "error": "", "proc": None, "local": "", "cfg": None,
         "root": "", "size": 0, "log": ""}


def sending():
    return _send["phase"] in {"saving", "packing", "copying"}


def _fail(message):
    _send.update(phase="error", error=message)
    print("AI Studio farm:", message)


def send(scene, start, end, fmt, note):
    """Start sending (see the module). Returns the job's name; the rest runs in the background."""
    if sending():
        raise ValueError("A job is still being sent")
    root = share_root()
    if not connected(root):
        raise ValueError("Share not connected: %s" % CONNECT_HINT)
    if end < start:
        raise ValueError("The last frame is before the first")
    job = job_name(bpy.data.filepath, scene.name)
    local = os.path.join(os.path.expanduser("~/.cache/ai_studio_farm"), job)
    os.makedirs(local, exist_ok=True)
    need = os.path.getsize(bpy.data.filepath) * 3 if bpy.data.filepath and os.path.exists(bpy.data.filepath) else 0
    free = shutil.disk_usage(local).free
    if need and free < need:
        raise ValueError("Not enough space on this Mac (%.1f GB free, about %.1f GB needed)"
                         % (free / 1e9, need / 1e9))
    raw = os.path.join(local, "copy.blend")
    _send.update(phase="saving", job=job, progress=0.0, error="", root=root, local=local, size=0,
                 log=os.path.join(local, "pack.log"),
                 cfg={"scene": scene.name, "start": int(start), "end": int(end), "format": fmt,
                      "sent_by": "%s@%s" % (getpass.getuser(), socket.gethostname().split(".")[0]),
                      "note": note})
    try:
        bpy.ops.wm.save_as_mainfile(filepath=raw, copy=True, compress=False, relative_remap=True,
                                    check_existing=False)
    except RuntimeError as ex:
        _fail("Copy not saved: %s" % ex)
        raise ValueError(_send["error"])
    script = os.path.join(local, "pack.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(PACK_SCRIPT)
    with open(_send["log"], "w", encoding="utf-8") as log:
        _send["proc"] = subprocess.Popen(
            [bpy.app.binary_path, "-b", raw, "--factory-startup", "-noaudio", "--python", script, "--",
             os.path.join(local, job + ".blend"), scene.name, fmt],
            stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    _send["phase"] = "packing"
    if not bpy.app.timers.is_registered(_send_tick):
        bpy.app.timers.register(_send_tick, first_interval=1.0)
    return job


def _send_tick():
    from . import runner
    runner.tag_redraw()
    if _send["phase"] == "packing":
        proc = _send["proc"]
        if proc is None or proc.poll() is None:
            return 1.0
        packed = os.path.join(_send["local"], _send["job"] + ".blend")
        if proc.returncode != 0 or not os.path.isfile(packed):
            tail = ""
            try:
                with open(_send["log"], encoding="utf-8", errors="replace") as f:
                    tail = [line.strip() for line in f if line.strip()][-1]
            except (OSError, IndexError):
                pass
            _fail("Packing failed: %s" % (tail or "code %s" % proc.returncode))
            return None
        _send.update(phase="copying", size=os.path.getsize(packed), progress=0.0)
        threading.Thread(target=_copy, args=(packed,), daemon=True).start()
        return 1.0
    return 1.0 if _send["phase"] == "copying" else None


def _copy(packed):
    """The packed copy onto the share: <job>.blend.part → <job>.blend, then <job>.json (last)."""
    root, job, size = _send["root"], _send["job"], _send["size"]
    queue = os.path.join(root, "queue")
    part = os.path.join(queue, job + ".blend.part")
    try:
        if not os.path.isdir(root):
            raise OSError("Share not connected: %s" % CONNECT_HINT)
        os.makedirs(queue, exist_ok=True)
        free = shutil.disk_usage(queue).free
        if free < size * 1.05:
            raise OSError("The share is full (%.1f GB free, %.1f GB needed)" % (free / 1e9, size / 1e9))
        copied = 0
        with open(packed, "rb") as src, open(part, "wb") as dst:
            while True:
                block = src.read(CHUNK)
                if not block:
                    break
                dst.write(block)
                copied += len(block)
                _send["progress"] = copied / size if size else 1.0
            dst.flush()
            os.fsync(dst.fileno())
        if os.path.getsize(part) != size:
            raise OSError("The copy on the share is incomplete")
        os.replace(part, os.path.join(queue, job + ".blend"))
        meta = os.path.join(queue, job + ".json.part")
        with open(meta, "w", encoding="utf-8") as f:
            json.dump(_send["cfg"], f, indent=1)
        os.replace(meta, os.path.join(queue, job + ".json"))      # now the job is ready
        _send.update(phase="sent", progress=1.0)
        shutil.rmtree(_send["local"], ignore_errors=True)            # the local copies (ours, not the queue)
        _view["scan"] = None
    except OSError as ex:
        reason = "the share is full" if getattr(ex, "errno", 0) == 28 else str(ex)
        _fail("Copy to the share stopped: %s (%s stays, it is never a job)" % (reason, os.path.basename(part)))


# ------------------------------------------------------------------ into the Video Editor

def job_files(root, job):
    folder = os.path.join(root, "renders", job)
    try:
        names = sorted(n for n in os.listdir(folder) if n.startswith(job + "_") and not n.endswith(".part")
                       and os.path.getsize(os.path.join(folder, n)) > 0)
    except OSError:
        return []
    return [os.path.join(folder, n) for n in names]


def load_into_editor(context, job):
    """The finished frames of a job as one image strip in the Video Editor (at the current frame)."""
    from . import cut, film_edit
    files = job_files(share_root(), job)
    if not files:
        raise ValueError("No finished frames of %s yet" % job)
    scene = cut._scene(context)
    sed = scene.sequence_editor or scene.sequence_editor_create()
    start = scene.frame_current
    channel = film_edit._free_channel(scene, start, start + len(files), lowest=2)
    strip = cut._image(cut._strips(sed), job, files, channel, start, fit='FIT')
    return strip, len(files)


# ------------------------------------------------------------------ panel

def _range(context):
    wm = context.window_manager
    scene = wm.studio_farm_scene or context.scene
    if wm.studio_farm_range == 'SCENE':
        return scene, scene.frame_start, scene.frame_end
    return scene, wm.studio_farm_start, wm.studio_farm_end


STATE_ICONS = {'waiting': 'TIME', 'rendering': 'RENDER_ANIMATION', 'done': 'CHECKMARK'}


def draw(layout, context):
    """Render tab · Render Farm."""
    _view["drawn"] = time.monotonic()
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.1)
    wm = context.window_manager
    root = share_root()
    online = connected(root)
    col = layout.column(align=True)
    col.prop(wm, "studio_farm_scene", text="", icon='SCENE_DATA')
    row = col.row(align=True)
    row.prop(wm, "studio_farm_range", expand=True)
    scene, start, end = _range(context)
    if wm.studio_farm_range == 'CUSTOM':
        row = col.row(align=True)
        row.prop(wm, "studio_farm_start", text="Start")
        row.prop(wm, "studio_farm_end", text="End")
    row = col.row(align=True)
    row.label(text="%d–%d · %d frames" % (start, end, max(0, end - start + 1)), translate=False)
    row.operator(STUDIO_OT_farm_frame.bl_idname, text="Current Frame", icon='TIME')
    col.prop(wm, "studio_farm_format", text="")
    col.prop(wm, "studio_farm_note", text="", icon='TEXT')
    render = scene.render
    pct = render.resolution_percentage
    hint = layout.row()
    hint.active = False
    hint.label(text="%d × %d (%d %%) · %s" % (render.resolution_x * pct // 100, render.resolution_y * pct // 100,
                                              pct, render.engine.replace("BLENDER_", "").title()), translate=False)
    row = layout.row()
    row.scale_y = 1.4
    row.enabled = online and not sending()
    row.operator(STUDIO_OT_farm_send.bl_idname, text="Send to Render Farm", icon='EXPORT')
    if not online:
        warn = layout.column(align=True)
        warn.alert = True
        warn.label(text="Share not connected", icon='ERROR')
        warn.label(text=CONNECT_HINT, translate=False)
    phase = _send["phase"]
    if phase in {"saving", "packing"}:
        layout.label(text="%s: %s" % (_send["job"], "saving a copy" if phase == "saving" else
                                      "packing resources (background)"), icon='TIME', translate=False)
    elif phase == "copying":
        layout.progress(factor=_send["progress"], type='BAR', text="To the share · %.1f of %.1f GB" % (
            _send["progress"] * _send["size"] / 1e9, _send["size"] / 1e9))
    elif phase == "sent":
        layout.label(text="Sent: %s" % _send["job"], icon='CHECKMARK', translate=False)
    elif phase == "error":
        box = layout.box()
        box.alert = True
        box.label(text=_send["error"], icon='ERROR', translate=False)
    if not online:
        return
    _refresh()
    view = _view["scan"]
    box = layout.box()
    head = box.row(align=True)
    head.label(text="Jobs", icon='SORTTIME')
    head.operator(STUDIO_OT_farm_folder.bl_idname, text="", icon='FILE_FOLDER', emboss=False).job = ""
    head.operator(STUDIO_OT_farm_refresh.bl_idname, text="", icon='FILE_REFRESH', emboss=False)
    if view is None:
        box.label(text="Looking at the share …", icon='TIME')
        return
    if view.get("error"):
        warn = box.row()
        warn.alert = True
        warn.label(text=view["error"], icon='ERROR', translate=False)
    for job in view["jobs"][:12]:
        col = box.column(align=True)
        row = col.row(align=True)
        row.label(text=job["job"], icon=STATE_ICONS[job["state"]], translate=False)
        sub = row.row(align=True)
        sub.alignment = 'RIGHT'
        sub.label(text="%d/%d · %s" % (job["done"], job["total"], job["state"]), translate=False)
        row = col.row(align=True)
        if job["total"]:
            row.progress(factor=min(1.0, job["done"] / job["total"]), type='BAR',
                         text=job["note"] or job["scene"])
        row.operator(STUDIO_OT_farm_folder.bl_idname, text="", icon='FILE_FOLDER').job = job["job"]
        load = row.row(align=True)
        load.enabled = job["done"] > 0
        load.operator(STUDIO_OT_farm_load.bl_idname, text="", icon='SEQUENCE').job = job["job"]
    if not view["jobs"]:
        hint = box.row()
        hint.active = False
        hint.label(text="No job in queue/")
    for half in view["halves"]:
        hint = box.row()
        hint.active = False
        hint.label(text="%s: incomplete, never a job" % half, icon='INFO', translate=False)
    box = layout.box()
    box.label(text="Workers", icon='DESKTOP')
    for name, what, when in view["workers"]:
        row = box.row(align=True)
        row.label(text=name, icon='RENDER_ANIMATION' if what.startswith("rendert") else 'TIME', translate=False)
        sub = row.row()
        sub.alignment = 'RIGHT'
        sub.active = False
        sub.label(text="%s · %s" % (what, when[-5:]), translate=False)
    if not view["workers"]:
        hint = box.row()
        hint.active = False
        hint.label(text="No worker has reported yet")
    gear = layout.row()
    gear.active = False
    gear.label(text=root, icon='NETWORK_DRIVE', translate=False)


# ------------------------------------------------------------------ operators

class STUDIO_OT_farm_send(Operator):
    bl_idname = "studio.farm_send"
    bl_label = "Send to Render Farm"
    bl_description = ("A packed copy of the scene (the working file stays as it is) into the share's queue: the "
                      "Mac Studios render it into renders/<job>/")
    bl_options = {'INTERNAL'}

    def execute(self, context):
        wm = context.window_manager
        scene, start, end = _range(context)
        try:
            job = send(scene, start, end, wm.studio_farm_format, wm.studio_farm_note)
        except (ValueError, OSError) as ex:
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
        self.report({'INFO'}, "Render farm: sending %s" % job)
        return {'FINISHED'}


class STUDIO_OT_farm_frame(Operator):
    bl_idname = "studio.farm_frame"
    bl_label = "Current Frame"
    bl_description = "Only the current frame goes to the render farm"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        wm = context.window_manager
        scene = wm.studio_farm_scene or context.scene
        wm.studio_farm_range = 'CUSTOM'
        wm.studio_farm_start = wm.studio_farm_end = scene.frame_current
        return {'FINISHED'}


class STUDIO_OT_farm_refresh(Operator):
    bl_idname = "studio.farm_refresh"
    bl_label = "Look Again"
    bl_description = "Read the share's jobs and workers now"
    bl_options = {'INTERNAL'}

    def execute(self, _context):
        _refresh(force=True)
        return {'FINISHED'}


class STUDIO_OT_farm_folder(Operator):
    bl_idname = "studio.farm_folder"
    bl_label = "Open Render Folder"
    bl_description = "The job's render folder on the share in the Finder (no job: renders/)"
    bl_options = {'INTERNAL'}

    job: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, _context):
        folder = os.path.join(share_root(), "renders", self.job) if self.job else os.path.join(share_root(), "renders")
        if not os.path.isdir(folder):
            folder = os.path.dirname(folder) if os.path.isdir(os.path.dirname(folder)) else ""
        if not folder:
            self.report({'WARNING'}, "Share not connected: %s" % CONNECT_HINT)
            return {'CANCELLED'}
        bpy.ops.wm.path_open(filepath=folder)
        return {'FINISHED'}


class STUDIO_OT_farm_load(Operator):
    bl_idname = "studio.farm_load"
    bl_label = "Load into the Video Editor"
    bl_description = "The job's finished frames as one image sequence in the Video Editor, at the current frame"
    bl_options = {'UNDO', 'INTERNAL'}

    job: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        try:
            strip, count = load_into_editor(context, self.job)
        except (ValueError, OSError, RuntimeError) as ex:
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
        self.report({'INFO'}, "%s: %d frames in channel %d" % (self.job, count, strip.channel))
        return {'FINISHED'}


classes = (STUDIO_OT_farm_send, STUDIO_OT_farm_frame, STUDIO_OT_farm_refresh, STUDIO_OT_farm_folder,
           STUDIO_OT_farm_load)


def register():
    wm = bpy.types.WindowManager
    wm.studio_farm_scene = PointerProperty(type=bpy.types.Scene, name="Scene",
                                           description="The scene that renders on the farm (empty: this one)")
    wm.studio_farm_range = EnumProperty(name="Frames", options={'SKIP_SAVE'}, default='SCENE',
                                        items=(('SCENE', "Scene Range", "The scene's start to end frame"),
                                               ('CUSTOM', "Custom", "Frames of your choice")))
    wm.studio_farm_start = IntProperty(name="Start", default=1, options={'SKIP_SAVE'})
    wm.studio_farm_end = IntProperty(name="End", default=250, options={'SKIP_SAVE'})
    wm.studio_farm_format = EnumProperty(name="Format", items=FORMATS, default='PNG', options={'SKIP_SAVE'})
    wm.studio_farm_note = StringProperty(name="Note", description="A note for the job (shown in the list)",
                                         default="", options={'SKIP_SAVE'})
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for timer in (_tick, _send_tick):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    for prop in ("studio_farm_scene", "studio_farm_range", "studio_farm_start", "studio_farm_end",
                 "studio_farm_format", "studio_farm_note"):
        if hasattr(bpy.types.WindowManager, prop):
            delattr(bpy.types.WindowManager, prop)

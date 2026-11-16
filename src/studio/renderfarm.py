# SPDX-License-Identifier: GPL-3.0-or-later
"""The render farm over a server folder (Render tab · Render Farm): jobs go to the ZHdK Mac Studios
through files on the DDE share only. With the switch on (scene.studio.farm_on) every render of the queue
goes there: a still comes back into the renders (preview), a section's frames into animation/Frames/
(the Video Editor shows them frame by frame). There is no network connection to the render machines and no port
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
from bpy.props import StringProperty
from bpy.types import Operator

DEFAULT_SHARE = "/Volumes/DDE/BDE_VID/BA/02_Studierende/Louis Widmer/Flamenco"
CONNECT_HINT = "Finder: Cmd+K smb://fileredu.ad.zhdk.ch/DDE"
FORMATS = (('PNG', "PNG", "8-bit PNG frames", 'IMAGE_DATA', 0),
           ('JPEG', "JPEG", "Small JPEG frames (previews)", 'IMAGE_RGB', 1),
           ('OPEN_EXR', "OpenEXR", "32-bit float frames for grading", 'IMAGE_RGB_ALPHA', 2))
EXTENSIONS = {'PNG': "png", 'JPEG': "jpg", 'OPEN_EXR': "exr"}
REFRESH = 15.0                 # seconds between looks at the share while the panel is open
ONLINE = 180.0                 # a waiting worker writes its status every 20 s: older than this = gone
CHUNK = 8 * 1024 * 1024        # bytes per step of the copy to the share

PACK_SCRIPT = '''
import bpy, json, sys
cfg = json.load(open(sys.argv[sys.argv.index("--") + 1]))
local = 0
for idb in list(bpy.data.user_map().keys()):      # linked library data becomes local (drivers untouched)
    if getattr(idb, "library", None) is not None:
        try:
            idb.make_local()
            local += 1
        except Exception as ex:
            print("RF make_local", idb.name, ex)
for op in (bpy.ops.file.make_paths_absolute, bpy.ops.file.pack_all):
    try:
        op()
    except Exception as ex:
        print("RF", op, ex)
scene = bpy.data.scenes.get(cfg["scene"]) or bpy.context.scene
spec = cfg.get("camera_spec")
overrides = cfg.get("overrides") or []
if spec and bpy.data.objects.get(spec.get("source", "")) is not None:      # a still from a stored place
    from mathutils import Matrix
    source = bpy.data.objects[spec["source"]]
    data = source.data.copy()
    data.lens = spec.get("lens", data.lens)
    cam = bpy.data.objects.new("farm_camera", data)
    scene.collection.objects.link(cam)
    m = spec["matrix"]
    cam.matrix_world = Matrix([m[0:4], m[4:8], m[8:12], m[12:16]])
    overrides = [[p, {"object": cam.name}] if p == "camera" else [p, v] for p, v in overrides]
def value(v):
    return bpy.data.objects.get(v["object"]) if isinstance(v, dict) and "object" in v else v
pending = list(overrides)
for _attempt in range(3):                          # some values fit only after others
    failed = []
    for path, v in pending:
        head, _, attr = path.rpartition(".")
        try:
            setattr(scene.path_resolve(head) if head else scene, attr, value(v))
        except Exception as ex:
            failed.append((path, v, ex))
    pending = [(p, v) for p, v, _ex in failed]
    if not pending:
        break
for path, _v, ex in failed:
    print("RF could not set", path, ex)
if any(p == "camera" for p, _v in overrides):      # one camera for the job: no marker switches it
    scene.timeline_markers.clear()
images = scene.render.image_settings
for attr, v in (("media_type", "IMAGE"), ("file_format", cfg["format"])):
    try:
        setattr(images, attr, v)
    except Exception as ex:
        print("RF format", ex)
scene.render.use_sequencer = False
bpy.ops.wm.save_as_mainfile(filepath=cfg["out"], compress=True, relative_remap=False)
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
    out = {"ok": False, "error": "", "jobs": [], "halves": [], "workers": [], "online": 0, "time": time.time()}
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
    workers, online = [], 0
    for path in sorted(glob.glob(os.path.join(root, "_worker", "status", "*-render.txt"))):
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                line = f.readline().strip()
            fresh = time.time() - os.path.getmtime(path) < ONLINE
        except OSError:
            continue
        parts = [p.strip() for p in line.split("|")]
        if parts and parts[0]:
            what = parts[1] if len(parts) > 1 else ""
            workers.append((parts[0], what, parts[2] if len(parts) > 2 else ""))
            online += fresh or what.startswith("rendert")
    out["workers"] = workers
    out["online"] = online
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


# ------------------------------------------------------------------ sending and collecting

LOCAL = os.path.expanduser("~/.cache/ai_studio_farm")
REGISTRY = os.path.join(LOCAL, "jobs.json")
EXT = {'PNG': "png", 'JPEG': "jpg", 'OPEN_EXR': "exr", 'TIFF': "tif"}
SETTLE = 5.0              # seconds a frame on the share stays unchanged before it is taken
_jobs = []                # the jobs of this Blender: queued → packing → copying → sent (collected) → done / error
_raw = {}                 # (file, scene state) → the saved copy several jobs pack from


def enabled(scene):
    return bool(getattr(scene.studio, "farm_on", False))


def takes(scene, item):
    """The render farm renders this queue item (on, share there, no material variant)."""
    return enabled(scene) and connected() and not getattr(item, "variant", "")


def active():
    return [j for j in _jobs if j["phase"] in {"queued", "packing", "copying", "sent"}]


def sending():
    return any(j["phase"] in {"queued", "packing", "copying"} for j in _jobs)


def renders_section(folder):
    folder = os.path.normcase(folder)
    return any(j.get("kind") == 'ANIM' and os.path.normcase(j.get("folder", "")) == folder for j in active())


def _save_registry():
    keep = [j for j in _jobs if j["phase"] == "sent"]
    try:
        os.makedirs(LOCAL, exist_ok=True)
        with open(REGISTRY + ".tmp", "w", encoding="utf-8") as f:
            json.dump(keep, f, indent=1)
        os.replace(REGISTRY + ".tmp", REGISTRY)
    except OSError as ex:
        print("AI Studio farm: jobs not kept:", ex)


def _load_registry():
    try:
        with open(REGISTRY, encoding="utf-8") as f:
            for job in json.load(f):
                if not any(j["job"] == job["job"] for j in _jobs):
                    _jobs.append(job)
    except (OSError, ValueError):
        pass


def _raw_copy(scene):
    """The working file saved as a copy once per scene state (the working file stays as it is)."""
    from . import runner
    key = (bpy.data.filepath, getattr(runner, "_generation", [0])[0], scene.name)
    path = _raw.get(key)
    if path and os.path.isfile(path):
        return path
    os.makedirs(LOCAL, exist_ok=True)
    need = os.path.getsize(bpy.data.filepath) * 3 if bpy.data.filepath and os.path.exists(bpy.data.filepath) else 0
    free = shutil.disk_usage(LOCAL).free
    if need and free < need:
        raise ValueError("Not enough space on this Mac (%.1f GB free, about %.1f GB needed)" % (free / 1e9, need / 1e9))
    path = os.path.join(LOCAL, "copy_%s.blend" % time.strftime("%Y%m%d-%H%M%S"))
    runner.RUN.preparing = True                  # saving is no scene change
    try:
        bpy.ops.wm.save_as_mainfile(filepath=path, copy=True, compress=False, relative_remap=True,
                                    check_existing=False)
    finally:
        runner.RUN.preparing = False
    _raw[key] = path
    return path


def _format_of(overrides, fallback='PNG'):
    fmt = next((v for p, v in overrides if p == "render.image_settings.file_format"), fallback)
    return fmt if fmt in EXT else 'PNG'


def submit(scene, start, end, fmt=None, note="", kind='MANUAL', overrides=None, camera_spec=None, **extra):
    """A job into the send queue (packed and copied one after another in the background). Returns it."""
    root = share_root()
    if not connected(root):
        raise ValueError("Share not connected: %s" % CONNECT_HINT)
    if end < start:
        raise ValueError("The last frame is before the first")
    overrides = [[p, v] for p, v in (overrides or []) if p != "render.filepath"]
    fmt = fmt or _format_of(overrides)
    name = job_name(bpy.data.filepath, extra.get("section") or scene.name)
    while any(j["job"] == name for j in _jobs):
        name += "x"
    job = {"job": name, "scene": scene.name, "kind": kind, "start": int(start), "end": int(end), "format": fmt,
           "ext": EXT.get(fmt, "png"), "overrides": overrides, "camera_spec": camera_spec, "note": note,
           "root": root, "raw": _raw_copy(scene), "phase": "queued", "progress": 0.0, "error": "", "size": 0,
           "have": 0, "blend": bpy.data.filepath, "added": time.time(),
           "sent_by": "%s@%s" % (getpass.getuser(), socket.gethostname().split(".")[0])}
    job.update(extra)
    _jobs.append(job)
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=0.5, persistent=True)
    return job


def submit_item(scene, item, job, animation, path, camera, quality):
    """A queue item of the runner on the farm: a still into its output file, a section's frames into
    animation/Frames/<section>/ (the Video Editor shows them frame by frame)."""
    from . import cut, film
    label = item.variant or camera.name
    overrides = job["overrides"]
    if animation:
        own = item.frame_end > 0 and item.frame_end >= item.frame_start
        a, b = (item.frame_start, item.frame_end) if own else (scene.frame_start, scene.frame_end)
        section = item.section or cut.WHOLE
        stem = cut.frame_stem(section)
        overrides = [["camera", {"object": camera.name}]] + [o for o in overrides if o[0] != "camera"]
        fmt = _format_of(overrides)
        if not item.keep_frames:                     # a new render: older frames go to archiv first
            film._clear_old_frames(path, stem, cut.archive_folder())
        meta = os.path.join(path, film.FILM_DIR)     # what the Video Editor reads: quality and size
        os.makedirs(meta, exist_ok=True)
        keep = [o for o in overrides if o[0] in {"render.resolution_x", "render.resolution_y",
                                                  "render.resolution_percentage", "render.image_settings.file_format"}]
        with open(os.path.join(meta, "job.json"), "w", encoding="utf-8") as f:
            json.dump({"scene": scene.name, "quality": quality, "section": section, "frames": [a, b],
                       "overrides": keep, "farm": True}, f, indent=1)
        return submit(scene, a, b, fmt, "%s · %s" % (section, "HQ" if quality == 'HIGH' else "LQ"), kind='ANIM',
                      overrides=overrides, folder=path, stem=stem, section=section, quality=quality)
    frame = scene.frame_current
    return submit(scene, frame, frame, None, "%s · %s" % (label, "HQ" if quality == 'HIGH' else "LQ"),
                  kind='STILL', overrides=overrides, camera_spec=job.get("camera"), target=path, quality=quality)


def _pump():
    """One job at a time: pack (own Blender process) → copy (thread) → sent; collects the frames of sent
    jobs every 10 s."""
    from . import runner
    try:
        busy = next((j for j in _jobs if j["phase"] in {"packing", "copying"}), None)
        if busy is not None and busy["phase"] == "packing":
            _packed(busy)
        elif busy is None:
            nxt = next((j for j in _jobs if j["phase"] == "queued"), None)
            if nxt is not None:
                _pack(nxt)
        _collect_all()
        runner.tag_redraw()
    except Exception as ex:                      # never stop the timer
        print("AI Studio farm:", ex)
    if sending():
        return 1.0
    return 10.0 if active() else None


def _pack(job):
    local = os.path.join(LOCAL, job["job"])
    os.makedirs(local, exist_ok=True)
    cfg = os.path.join(local, "pack.json")
    with open(cfg, "w", encoding="utf-8") as f:
        json.dump({"out": os.path.join(local, job["job"] + ".blend"), "scene": job["scene"], "format": job["format"],
                   "overrides": job["overrides"], "camera_spec": job.get("camera_spec")}, f)
    script = os.path.join(local, "pack.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(PACK_SCRIPT)
    job["log"] = os.path.join(local, "pack.log")
    with open(job["log"], "w", encoding="utf-8") as log:
        proc = subprocess.Popen([bpy.app.binary_path, "-b", job["raw"], "--factory-startup", "-noaudio", "--python",
                                 script, "--", cfg], stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)
    _procs[job["job"]] = proc
    job["phase"] = "packing"


_procs = {}


def _packed(job):
    proc = _procs.get(job["job"])
    if proc is not None and proc.poll() is None:
        return
    _procs.pop(job["job"], None)
    packed = os.path.join(LOCAL, job["job"], job["job"] + ".blend")
    if proc is None or proc.returncode != 0 or not os.path.isfile(packed):
        tail = ""
        try:
            with open(job.get("log", ""), encoding="utf-8", errors="replace") as f:
                tail = [line.strip() for line in f if line.strip()][-1]
        except (OSError, IndexError):
            pass
        job.update(phase="error", error="Packing failed: %s" % (tail or "process ended"))
    else:
        job.update(phase="copying", size=os.path.getsize(packed), progress=0.0)
        threading.Thread(target=_copy, args=(job, packed), daemon=True).start()
    if not any(j["raw"] == job["raw"] and j["phase"] in {"queued", "packing"} for j in _jobs):
        try:                                     # our own local copy, no longer needed
            os.remove(job["raw"])
        except OSError:
            pass


def _copy(job, packed):
    """The packed copy onto the share: <job>.blend.part → <job>.blend, then <job>.json (last)."""
    root, name, size = job["root"], job["job"], job["size"]
    queue = os.path.join(root, "queue")
    part = os.path.join(queue, name + ".blend.part")
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
                job["progress"] = copied / size if size else 1.0
            dst.flush()
            os.fsync(dst.fileno())
        if os.path.getsize(part) != size:
            raise OSError("The copy on the share is incomplete")
        os.replace(part, os.path.join(queue, name + ".blend"))
        meta = os.path.join(queue, name + ".json.part")
        with open(meta, "w", encoding="utf-8") as f:
            json.dump({"scene": job["scene"], "start": job["start"], "end": job["end"], "format": job["format"],
                       "sent_by": job["sent_by"], "note": job["note"]}, f, indent=1)
        os.replace(meta, os.path.join(queue, name + ".json"))      # now the job is ready
        job.update(phase="sent" if job["kind"] in {'STILL', 'ANIM'} else "done", progress=1.0)
        shutil.rmtree(os.path.join(LOCAL, name), ignore_errors=True)
        _view["scan"] = None
        _save_registry()
    except OSError as ex:
        reason = "the share is full" if getattr(ex, "errno", 0) == 28 else str(ex)
        job.update(phase="error", error="Copy to the share stopped: %s (%s stays, it is never a job)"
                   % (reason, os.path.basename(part)))


def _collect_all():
    now = time.time()
    for job in [j for j in _jobs if j["phase"] == "sent"]:
        if now - job.get("looked", 0.0) < 10.0:
            continue
        job["looked"] = now
        try:
            _collect(job)
        except OSError as ex:
            job["error"] = "Frames not fetched: %s" % ex


def _collect(job):
    """The finished frames of a job into AI Studio's folders."""
    from . import cut, runner
    folder = os.path.join(job["root"], "renders", job["job"])
    if not os.path.isdir(folder):
        return
    now, new = time.time(), 0
    if job["kind"] == 'STILL':
        source = os.path.join(folder, "%s_%04d.%s" % (job["job"], job["start"], job["ext"]))
        try:
            st = os.stat(source)
        except OSError:
            return
        if st.st_size > 0 and now - st.st_mtime > SETTLE:
            target = os.path.splitext(job["target"])[0] + "." + job["ext"]
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copyfile(source, target + ".part")
            os.replace(target + ".part", target)
            job.update(phase="done", have=1)
            runner.RUN.last_path = target
            try:
                runner.show_in_preview(target)
            except Exception:
                pass
            runner.notify("%s ready (render farm)" % job["note"])
            _save_registry()
        return
    have = 0
    for frame in range(job["start"], job["end"] + 1):
        name = "%s_%04d.%s" % (job["stem"], frame, job["ext"])
        local = os.path.join(job["folder"], name)
        if os.path.exists(local) and os.path.getsize(local) > 0:
            have += 1
            continue
        source = os.path.join(folder, "%s_%04d.%s" % (job["job"], frame, job["ext"]))
        try:
            st = os.stat(source)
        except OSError:
            continue
        if st.st_size > 0 and now - st.st_mtime > SETTLE:
            shutil.copyfile(source, local + ".part")
            os.replace(local + ".part", local)
            have += 1
            new += 1
    job["have"] = have
    if new:
        cut.on_progress(job["scene"])
    if have >= job["end"] - job["start"] + 1:
        job["phase"] = "done"
        cut.on_film_done(job["scene"])
        runner.notify("%s ready (render farm)" % job["note"])
        _save_registry()


def draw_progress(layout, sent=True):
    """The farm's jobs of this Blender as bars (Render tab, Video Editor); sent=False: only those not on
    the share yet (and errors)."""
    phases = {"queued", "packing", "copying", "sent", "error"} if sent else {"queued", "packing", "copying", "error"}
    for job in [j for j in _jobs if j["phase"] in phases]:
        row = layout.row(align=True)
        total = job["end"] - job["start"] + 1
        if job["phase"] == "error":
            row.alert = True
            row.label(text="%s: %s" % (job["note"] or job["job"], job["error"]), icon='ERROR', translate=False)
            row.operator(STUDIO_OT_farm_dismiss.bl_idname, text="", icon='X', emboss=False).job = job["job"]
            continue
        text = {"queued": "waiting to be sent", "packing": "packing (background)",
                "copying": "to the share %.1f/%.1f GB" % (job["progress"] * job["size"] / 1e9, job["size"] / 1e9),
                "sent": "farm %d/%d" % (job["have"], total)}[job["phase"]]
        factor = job["progress"] if job["phase"] == "copying" else (job["have"] / total if job["phase"] == "sent" else 0.0)
        row.progress(factor=factor, type='BAR', text="%s · %s" % (job["note"] or job["job"], text))


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

STATE_TEXT = {'waiting': "waiting", 'rendering': "rendering", 'done': "done"}


def draw(layout, context):
    """Render tab · Render Farm: on / off, how many workers are there, the queue with its bars."""
    _view["drawn"] = time.monotonic()
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.1)
    rq = context.scene.studio
    online = connected()
    row = layout.row(align=True)                 # on / off
    row.scale_y = 1.4
    row.prop_enum(rq, "farm_mode", 'OFF')
    row.prop_enum(rq, "farm_mode", 'ON')
    if not online:
        warn = layout.column(align=True)
        warn.alert = True
        warn.label(text="Share not connected" + (": renders stay on this Mac" if rq.farm_on else ""), icon='ERROR')
        warn.label(text=CONNECT_HINT, translate=False)
        draw_progress(layout.column(align=True), sent=False)
        return
    _refresh()
    view = _view["scan"] or {}
    workers = view.get("online", 0)
    rendering = sum(1 for w in view.get("workers", []) if w[1].startswith("rendert"))
    row = layout.row()
    row.label(text="%d worker%s online%s" % (workers, "" if workers == 1 else "s",
                                             " · %d rendering" % rendering if rendering else ""),
              icon='DESKTOP' if workers else 'ERROR', translate=False)
    col = layout.column(align=True)              # the queue
    draw_progress(col, sent=False)
    jobs = view.get("jobs", [])
    shown = [j for j in jobs if j["state"] != 'done'] + [j for j in jobs if j["state"] == 'done'][:3]
    for job in shown[:10]:
        total = max(1, job["total"])
        col.progress(factor=min(1.0, job["done"] / total), type='BAR', text="%s · %d/%d · %s" % (
            job["note"] or job["job"], job["done"], job["total"], STATE_TEXT[job["state"]]))
    if not shown and not active():
        hint = layout.row()
        hint.active = False
        hint.label(text="Queue empty")


# ------------------------------------------------------------------ operators

class STUDIO_OT_farm_dismiss(Operator):
    bl_idname = "studio.farm_dismiss"
    bl_label = "Dismiss"
    bl_description = "Hide this message (half files on the share stay as .part and are never a job)"
    bl_options = {'INTERNAL'}

    job: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, _context):
        _jobs[:] = [j for j in _jobs if j["job"] != self.job]
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


classes = (STUDIO_OT_farm_dismiss, STUDIO_OT_farm_refresh, STUDIO_OT_farm_folder,
           STUDIO_OT_farm_load)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    _load_registry()                             # jobs sent before a restart: their frames still come
    if active() and not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=5.0, persistent=True)


def unregister():
    for timer in (_tick, _pump):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

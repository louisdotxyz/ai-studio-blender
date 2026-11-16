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

The queue (Render tab, Photo, Video Editor, chat) shows the farm's jobs like the renders of this Mac: a
bar with frames and time left, ▲ to render a job earlier, ✕ to take it out (asks first: files leave
queue/). Sending never changes the working file: it is saved as a copy into a local folder; a Blender process in
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
overrides = [o for o in overrides if o[0] != "camera"] + [o for o in overrides if o[0] == "camera"]
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
expected = next((value(v) for p, v in overrides if p == "camera"), None)
if expected is not None:                           # one camera for the job: no marker switches it
    scene.timeline_markers.clear()
    scene.camera = expected
print("RF camera", scene.camera.name if scene.camera else None)
images = scene.render.image_settings
for attr, v in (("media_type", "IMAGE"), ("file_format", cfg["format"])):
    try:
        setattr(images, attr, v)
    except Exception as ex:
        print("RF format", ex)
scene.render.use_sequencer = False
keep = set()                                       # the job's scene and the scenes it needs
s = scene
while s is not None and s.name not in keep:
    keep.add(s.name)
    s = s.background_set
for tree in (getattr(scene, "compositing_node_group", None), getattr(scene, "node_tree", None)):
    for node in (getattr(tree, "nodes", None) or ()):
        if getattr(node, "scene", None) is not None:
            keep.add(node.scene.name)
for other in [x for x in bpy.data.scenes if x.name not in keep]:
    try:                                           # other scenes only make the upload bigger
        bpy.data.scenes.remove(other)
    except Exception as ex:
        print("RF scene", other.name, ex)
if bpy.context.window is not None:
    bpy.context.window.scene = scene
try:
    removed = bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
    print("RF purged", removed, "unused data blocks")
except Exception as ex:
    print("RF purge", ex)
for stamp in ("use_stamp_camera", "use_stamp_render_time", "use_stamp_hostname", "use_stamp_frame"):
    setattr(scene.render, stamp, True)             # into each frame's file: camera, time and which Mac
bpy.ops.wm.save_as_mainfile(filepath=cfg["out"], compress=True, relative_remap=False)
print("RF packed, %d made local" % local)
'''


# ------------------------------------------------------------------ the share

def share_root():
    from . import runner
    p = runner.prefs()
    root = getattr(p, "farm_share", "") if p is not None else ""
    return bpy.path.abspath(root or DEFAULT_SHARE).rstrip("/\\")


_conn = {"root": None, "ok": False, "time": -1e9, "busy": False}
CONN_AGE = 10.0           # seconds a look at the share counts (a network folder can take seconds to answer)


def _look(root):
    ok = os.path.isdir(root)
    _conn.update(root=root, ok=ok, time=time.monotonic(), busy=False)
    return ok


def connected(root=None):
    """The share is mounted. Looked at in a thread at most every 10 s (a slow or gone network folder must not
    freeze Blender's window); only the very first look waits for the answer."""
    root = root or share_root()
    if _conn["root"] != root:
        return _look(root)
    if time.monotonic() - _conn["time"] > CONN_AGE and not _conn["busy"]:
        _conn["busy"] = True
        threading.Thread(target=_look, args=(root,), daemon=True).start()
    return _conn["ok"]


def job_name(blend_path, scene_name, now=None):
    """<file>_<YYYYmmdd-HHMMSS>_<scene>, only letters, digits, - and _. The time comes before the scene:
    the workers take the job that sorts first, so jobs render in the order they were sent."""
    stem = os.path.splitext(os.path.basename(blend_path or "untitled"))[0] or "untitled"
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    name = "%s_%s_%s" % (stem, stamp, scene_name)
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
    workers, rows, online = [], [], 0
    for path in sorted(glob.glob(os.path.join(root, "_worker", "status", "*-render.txt"))):
        try:
            with open(path, encoding="utf-8-sig", errors="replace") as f:
                line = f.readline().strip()
            age = time.time() - os.path.getmtime(path)
            fresh = age < ONLINE
        except OSError:
            continue
        parts = [p.strip() for p in line.split("|")]
        if parts and parts[0]:
            what = parts[1] if len(parts) > 1 else ""
            when = parts[2] if len(parts) > 2 else ""
            workers.append((parts[0], what, when))
            there = fresh or (what.startswith("rendert") and age < 12 * 3600)   # no new line while it renders
            rows.append((parts[0], what, when, there))
            online += there
    out["workers"] = workers
    out["worker_rows"] = rows
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
                            "format": cfg.get("format", "PNG"),
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
_jobs = []                # the farm's queue of this Mac, in order: queued → packing → copying → sent → done / error
TRANSIENT = {"fetching", "new", "arrived", "looked", "t0", "h0", "per", "cancel", "seeded"}
_rate = [0.0]             # seconds per frame the farm made lately (for jobs that wait)
_hosts = {}               # worker → [frames, render seconds] of the frames fetched since Blender started
_events = []              # (finished, worker, render seconds) of every frame fetched: the graph and the speed
_stamps = {}              # worker → [(its own clock's time of the frame, when we saw it or None)]
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
    keep = [{k: v for k, v in j.items() if k not in TRANSIENT} for j in _jobs if j["phase"] == "sent"]
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
        overrides = [o for o in overrides if o[0] != "camera"] + [["camera", {"object": camera.name}]]
        fmt = _format_of(overrides)
        if not item.keep_frames:                     # a new render: older frames go to archiv first
            film._clear_old_frames(path, stem, cut.archive_folder())
        meta = os.path.join(path, film.FILM_DIR)     # what the Video Editor reads: quality and size
        os.makedirs(meta, exist_ok=True)
        keep = [o for o in overrides if o[0] in {"render.resolution_x", "render.resolution_y",
                                                  "render.resolution_percentage", "render.image_settings.file_format"}]
        with open(os.path.join(meta, "job.json"), "w", encoding="utf-8") as f:
            json.dump({"scene": scene.name, "quality": quality, "section": section, "frames": [a, b],
                       "overrides": keep, "farm": True, "camera_name": camera.name}, f, indent=1)
        return submit(scene, a, b, fmt, "%s · %s" % (section, "HQ" if quality == 'HIGH' else "LQ"), kind='ANIM',
                      overrides=overrides, folder=path, stem=stem, section=section, quality=quality,
                      camera_name=camera.name)
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
    return 3.0 if active() else None


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
                if job.get("cancel"):
                    raise InterruptedError
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
        _reconcile()
        shutil.rmtree(os.path.join(LOCAL, name), ignore_errors=True)
        _view["scan"] = None
        _save_registry()
    except InterruptedError:                     # removed by the user (confirmed): its half file goes too
        try:
            os.remove(part)
        except OSError:
            pass
    except OSError as ex:
        reason = "the share is full" if getattr(ex, "errno", 0) == 28 else str(ex)
        job.update(phase="error", error="Copy to the share stopped: %s (%s stays, it is never a job)"
                   % (reason, os.path.basename(part)))


def total_of(job):
    return job["end"] - job["start"] + 1


def _note_progress(job, have):
    """Frames there now; from the second look on also how fast they come."""
    now = time.time()
    if have and "t0" not in job:
        job["t0"], job["h0"] = now, have
    if "t0" in job and have > job["h0"] and now > job["t0"]:
        job["per"] = _rate[0] = (now - job["t0"]) / (have - job["h0"])
    job["have"] = have


def _per_frame(job=None):
    """Seconds per frame of the farm now (all workers together)."""
    rate = farm_rate()
    if rate:
        return 60.0 / rate
    return (job or {}).get("per") or _rate[0] or None


def left(job):
    """Seconds until the job is done, or None (not known yet)."""
    per = _per_frame(job)
    if not per or job["kind"] != 'ANIM':
        return None
    return per * max(0, total_of(job) - job.get("have", 0))


def starts_in(job):
    """Seconds until the farm gets to a job that waits (the frames of the jobs before it), or None."""
    per = _per_frame()
    if not per:
        return None
    before = 0
    for other in _jobs:
        if other is job:
            return per * before
        if other["phase"] in {"sent", "copying", "packing", "queued"} and other["kind"] == 'ANIM':
            before += max(0, total_of(other) - other.get("have", 0))
    return None


def summary():
    """All of ours on the farm in one: (frames there, frames in all, seconds left or None, jobs sending)."""
    jobs = [j for j in _jobs if j["phase"] in {"queued", "packing", "copying", "sent"} and j["kind"] == 'ANIM']
    have = sum(j.get("have", 0) for j in jobs)
    total = sum(total_of(j) for j in jobs)
    per = _per_frame()
    return have, total, (per * max(0, total - have) if per else None), \
        sum(1 for j in jobs if j["phase"] in {"queued", "packing", "copying"})


def _note_frame(job, path, live=True):
    """(thread) What a fetched frame says: which Mac rendered it, how long, from which camera. live: seen
    just now (its time is ours); else fetched earlier (after a restart: its time from the Mac's own clock,
    shifted by how far that clock is off)."""
    if job["ext"] != "png":
        return
    from . import cut
    meta = cut.png_text(path)
    host = meta.get("Hostname", "").split(".")[0]
    if host:
        key = host.lower()
        secs = cut.render_seconds(meta.get("RenderTime", ""))
        entry = _hosts.setdefault(key, [0, 0.0, host])
        entry[0] += 1
        entry[1] += secs
        try:
            stamped = time.mktime(time.strptime(meta.get("Date", ""), "%Y/%m/%d %H:%M:%S"))
        except (ValueError, OverflowError):
            stamped = None
        seen = time.time() if live else None
        if stamped is not None:
            _stamps.setdefault(key, []).append((stamped, seen))
            del _stamps[key][:-400]
        finished = seen if seen is not None else (stamped + _clock_offset(key) if stamped is not None
                                                  else time.time())
        _events.append((finished, key, secs))
        del _events[:-5000]
    wanted = job.get("camera_name", "")
    if wanted and meta.get("Camera") and meta["Camera"] != wanted:
        job["wrong_camera"] = meta["Camera"]


def _clock_offset(host):
    """How far behind our clock a worker's clock is (from the frames seen as they came), 0 if unknown."""
    diffs = sorted(seen - stamped for stamped, seen in _stamps.get(host, ()) if seen is not None)
    return diffs[len(diffs) // 2] if diffs else 0.0


def seconds_per_frame(host):
    """A worker's speed: the usual time between two of its frames (its own clock, so rendering, saving and
    loading count, as on the farm); the render time in the frames when there are too few."""
    times = sorted(stamped for stamped, _seen in _stamps.get(host, ()))
    gaps = [b - a for a, b in zip(times, times[1:]) if 0 < b - a < 900][-12:]
    if len(gaps) >= 2:
        gaps.sort()
        return gaps[len(gaps) // 2]
    stats = _hosts.get(host)
    return stats[1] / stats[0] if stats and stats[0] and stats[1] else None


def farm_rate(now=None):
    """Frames per minute of the whole farm lately (last 15 minutes, or the last hour when few), None if
    nothing came."""
    now = now or time.time()
    for window, least in ((900.0, 4), (3600.0, 2)):
        times = [finished for finished, _w, _s in _events if now - window <= finished <= now + 60]
        if len(times) >= least:
            span = max(120.0, now - min(times))          # the farm may have started less than a window ago
            return len(times) / (span / 60.0)
    return None


def _collect_all():
    """Every 5 s per job: a thread fetches its finished frames; here (Blender's thread) they are shown."""
    from . import cut, runner
    now = time.time()
    for job in [j for j in _jobs if j["phase"] == "sent"]:
        if job.pop("new", 0):
            cut.on_film_done(job["scene"])        # the strip comes with the first frames and fills in
        if job["kind"] == 'STILL' and job.get("arrived"):
            job.update(phase="done", have=1)
            runner.RUN.last_path = job["arrived"]
            try:
                runner.show_in_preview(job["arrived"])
            except Exception:
                pass
            runner.notify("%s ready (render farm)" % job["note"])
            _save_registry()
        elif job["kind"] == 'ANIM' and job.get("have", 0) >= total_of(job):
            job["phase"] = "done"
            cut.on_film_done(job["scene"])
            runner.notify("%s ready (render farm)" % job["note"])
            _save_registry()
            _reconcile()
        elif not job.get("fetching") and now - job.get("looked", 0.0) >= 5.0:
            job["looked"], job["fetching"] = now, True
            threading.Thread(target=_fetch, args=(job,), daemon=True).start()


def _fetch(job):
    """(thread, no bpy) The finished frames of a job from the share into AI Studio's folders."""
    try:
        folder = os.path.join(job["root"], "renders", job["job"])
        if not os.path.isdir(folder):
            return
        now = time.time()
        if job["kind"] == 'STILL':
            source = os.path.join(folder, "%s_%04d.%s" % (job["job"], job["start"], job["ext"]))
            st = os.stat(source)
            if st.st_size > 0 and now - st.st_mtime > SETTLE:
                target = os.path.splitext(job["target"])[0] + "." + job["ext"]
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copyfile(source, target + ".part")
                os.replace(target + ".part", target)
                job["arrived"] = target
            return
        have = new = 0
        for frame in range(job["start"], job["end"] + 1):
            local = os.path.join(job["folder"], "%s_%04d.%s" % (job["stem"], frame, job["ext"]))
            if os.path.exists(local) and os.path.getsize(local) > 0:
                have += 1
                if not job.get("seeded"):              # after a restart: the graph and speeds again
                    _note_frame(job, local, live=False)
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
                _note_frame(job, local)
                if new % 8 == 0:                  # many at once: show them as they come
                    job["new"] = job.get("new", 0) + 8
                    _note_progress(job, have)
        job["seeded"] = True
        if new:
            job["new"] = job.get("new", 0) + new
        _note_progress(job, have)
        job["error"] = ""
    except OSError as ex:
        if job["kind"] != 'STILL' or not isinstance(ex, FileNotFoundError):
            job["error"] = "Frames not fetched: %s" % ex
    finally:
        job["fetching"] = False


# ------------------------------------------------------------------ the order, removing

def _reconcile():
    """The queue's order on the share. The workers take the ready job that sorts first by name, so a job
    that is to render after one that sorts later waits: its .json is <job>.json.hold until then (never
    seen by a worker; made ready again here, also after a restart of Blender)."""
    sent = [j for j in _jobs if j["phase"] == "sent"]
    for n, job in enumerate(sent):
        hold = any(other["job"] > job["job"] for other in sent[:n])
        queue = os.path.join(job["root"], "queue")
        ready, held = os.path.join(queue, job["job"] + ".json"), os.path.join(queue, job["job"] + ".json.hold")
        try:
            if hold and os.path.exists(ready):
                os.replace(ready, held)
            elif not hold and os.path.exists(held):
                os.replace(held, ready)
            job["held"] = hold
        except OSError as ex:
            job["error"] = "Order not set on the share: %s" % ex


def move_up(name):
    """One place up among the jobs that are not done."""
    live = [j for j in _jobs if j["phase"] not in {"done"}]
    job = next((j for j in live if j["job"] == name), None)
    if job is None or live.index(job) == 0:
        return False
    other = live[live.index(job) - 1]
    a, b = _jobs.index(other), _jobs.index(job)
    _jobs[a], _jobs[b] = _jobs[b], _jobs[a]
    _reconcile()
    _save_registry()
    return True


def remove_from_share(root, name, start, end, ext):
    """A job off the farm (the user confirmed it): its .json and .blend leave queue/, so no worker starts
    it; workers that render it now skip the frames left (an empty file per frame, like their own
    placeholders) and go on with the next job. Rendered frames stay in renders/<job>/."""
    queue = os.path.join(root, "queue")
    for ending in (".json", ".json.hold", ".blend", ".blend.part", ".json.part"):
        try:
            os.remove(os.path.join(queue, name + ending))
        except FileNotFoundError:
            pass
    folder = os.path.join(root, "renders", name)
    if os.path.isdir(folder):
        for frame in range(start, end + 1):
            path = os.path.join(folder, "%s_%04d.%s" % (name, frame, ext))
            if not os.path.exists(path):
                open(path, "wb").close()


def remove(name):
    """Take a job out of the queue, wherever it is (waiting here, being sent, on the farm)."""
    job = next((j for j in _jobs if j["job"] == name), None)
    if job is None:                                # a job of the share that is not from this Blender
        seen = next((j for j in (_view["scan"] or {}).get("jobs", []) if j["job"] == name), None)
        if seen is not None:
            remove_from_share(share_root(), name, seen["start"], seen["end"], EXT.get(seen.get("format"), "png"))
            _view["scan"] = None
        return
    phase = job["phase"]
    if phase == "packing":
        proc = _procs.pop(name, None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
    elif phase == "copying":
        job["cancel"] = True
    elif phase == "sent":
        remove_from_share(job["root"], name, job["start"], job["end"], job["ext"])
    _jobs.remove(job)
    shutil.rmtree(os.path.join(LOCAL, name), ignore_errors=True)
    _reconcile()
    _save_registry()
    _view["scan"] = None


# ------------------------------------------------------------------ the queue's rows

def _row_text(job):
    from .runner import format_time
    total = total_of(job)
    phase = job["phase"]
    name = job["note"] or job["job"]
    if phase == "queued":
        what = "waits to be sent"
    elif phase == "packing":
        what = "packing"
    elif phase == "copying":
        what = "upload %.1f/%.1f GB" % (job["progress"] * job["size"] / 1e9, job["size"] / 1e9)
    else:
        have = job.get("have", 0)
        if job["kind"] != 'ANIM':
            what = "on the farm" if not have else "done"
        elif have or _renders(job["job"]):
            what = "%d/%d" % (have, total)
            secs = left(job)
            if secs:
                what += " · ~%s left" % format_time(secs)
        else:
            secs = starts_in(job)
            what = "on the farm ✓ · starts %s" % ("in ~%s (%s)" % (format_time(secs), time.strftime(
                "%H:%M", time.localtime(time.time() + secs))) if secs else "after the job before")
        if job.get("wrong_camera"):
            what += " · wrong camera (%s)" % job["wrong_camera"]
        if job.get("error"):
            what += " · " + job["error"]
    return "%s · %s" % (name, what)


def _renders(name):
    return any(w[1] == "rendert " + name for w in (_view["scan"] or {}).get("workers", []))


def rows():
    """The jobs to show: ours that are not done (in order), then unfinished jobs of the share from elsewhere."""
    mine = [j for j in _jobs if j["phase"] != "done"]
    known = {j["job"] for j in _jobs}
    other = [j for j in (_view["scan"] or {}).get("jobs", []) if j["state"] != 'done' and j["job"] not in known]
    return mine, other


def total_left():
    """Seconds until everything of ours on the farm is done, or None."""
    secs, known = 0.0, False
    for job in [j for j in _jobs if j["phase"] in {"queued", "packing", "copying", "sent"} and j["kind"] == 'ANIM']:
        one = left(job)
        if one is not None:
            secs += one
            known = True
    return secs if known else None


SENDING = {"queued", "packing", "copying"}


def _send_text(job):
    if job["phase"] == "queued":
        return "%s · waits to be sent" % (job["note"] or job["job"])
    if job["phase"] == "packing":
        return "%s · packing the file" % (job["note"] or job["job"])
    return "%s · upload %d %% of %.1f GB" % (job["note"] or job["job"], round(job["progress"] * 100),
                                            job["size"] / 1e9)


def draw_rows(layout, buttons=True):
    """The farm's part of the queue: what is being sent as a line each (no bar: it is not the render),
    then a bar per job on the farm (frames, time left); ▲ up, ✕ remove."""
    mine, other = rows()
    sending = [j for j in mine if j["phase"] in SENDING]
    farm = [j for j in mine if j["phase"] not in SENDING]
    for job in sending:
        row = layout.row(align=True)
        sub = row.row(align=True)
        sub.active = False
        sub.label(text=_send_text(job), icon='EXPORT' if job["phase"] == "copying" else 'TIME', translate=False)
        if buttons:
            row.operator(STUDIO_OT_farm_remove.bl_idname, text="", icon='X', emboss=False).job = job["job"]
    for n, job in enumerate(farm):
        row = layout.row(align=True)
        if job["phase"] == "error":
            row.alert = True
            row.label(text="%s: %s" % (job["note"] or job["job"], job["error"]), icon='ERROR', translate=False)
            row.operator(STUDIO_OT_farm_remove.bl_idname, text="", icon='X', emboss=False).job = job["job"]
            continue
        total = total_of(job)
        row.progress(factor=min(1.0, job.get("have", 0) / total if total else 0.0), type='BAR', text=_row_text(job))
        if buttons:
            up = row.row(align=True)
            up.enabled = n > 0
            up.operator(STUDIO_OT_farm_move.bl_idname, text="", icon='TRIA_UP', emboss=False).job = job["job"]
            row.operator(STUDIO_OT_farm_remove.bl_idname, text="", icon='X', emboss=False).job = job["job"]
    for job in other[:6]:
        row = layout.row(align=True)
        total = max(1, job["total"])
        row.progress(factor=min(1.0, job["done"] / total), type='BAR', text="%s · Farm %d/%d" % (
            job["note"] or job["job"], job["done"], job["total"]))
        if buttons:
            row.label(text="", icon='BLANK1')
            row.operator(STUDIO_OT_farm_remove.bl_idname, text="", icon='X', emboss=False).job = job["job"]
    return bool(mine or other)


BLOCKS = "▁▂▃▄▅▆▇█"
BUCKET = 300.0            # seconds per bar of the graph
BARS = 12                 # the last hour


def frames_per_bucket(host=None, now=None):
    """Frames finished per 5 minutes over the last hour (oldest first), of one worker or all."""
    now = now or time.time()
    counts = [0] * BARS
    for finished, who, _secs in _events:
        if host is not None and who != host:
            continue
        index = BARS - 1 - int((now - finished) // BUCKET)
        if 0 <= index < BARS:
            counts[index] += 1
    return counts


def sparkline(values, top=None):
    """A small bar graph in text: ▁▂▃▅▇ (a dot where nothing was done)."""
    top = top or max(values) or 1
    return "".join(BLOCKS[min(7, max(0, round(v / top * 7)))] if v else "·" for v in values)


def draw_progress(layout, sent=True):
    """Kept for older calls: the farm's rows without buttons."""
    draw_rows(layout, buttons=False)


# ------------------------------------------------------------------ panel

def watch():
    """Called while the queue is drawn: the share is read every 15 s (workers, jobs from elsewhere)."""
    _view["drawn"] = time.monotonic()
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.1)


def draw_switch(row, context):
    """Render tab, at the top: where renders go, two buttons (the one that is on is highlighted)."""
    rq = context.scene.studio
    row.prop_enum(rq, "farm_mode", 'OFF')
    row.prop_enum(rq, "farm_mode", 'ON')


def _job_note(name):
    job = next((j for j in _jobs if j["job"] == name), None)
    return (job["note"] or name) if job else name


def draw_state(layout, context):
    """Under the switch while the farm is on (or still works for us): how fast the farm is, what each Mac
    does and how fast it is, or what is missing."""
    rq = context.scene.studio
    if not rq.farm_on and not active():
        return
    watch()
    if not connected():
        warn = layout.column(align=True)
        warn.alert = True
        warn.label(text="Share not connected" + (": renders stay on this Mac" if rq.farm_on else ""), icon='ERROR')
        warn.label(text=CONNECT_HINT, translate=False)
        return
    view = _view["scan"]
    if view is None:
        return
    from .runner import format_time
    workers = view.get("online", 0)
    have, total, secs, _sending = summary()
    rate = farm_rate()
    box = layout.box()
    col = box.column(align=True)
    head = col.row()
    head.label(text="%d Mac%s online" % (workers, "" if workers == 1 else "s"),
               icon='DESKTOP' if workers else 'ERROR', translate=False)
    if total:
        right = head.row()
        right.alignment = 'RIGHT'
        right.label(text="%d/%d frames%s" % (have, total, " · ~%s left" % format_time(secs) if secs else ""),
                    translate=False)
    row = col.row()                                  # the farm's speed in plain words
    row.label(text="Speed", icon='SORTTIME')
    right = row.row()
    right.alignment = 'RIGHT'
    if rate:
        right.label(text="%.1f frames/min · one every %.0f s" % (rate, 60.0 / rate), translate=False)
    else:
        right.active = False
        right.label(text="no frames yet", translate=False)
    every = frames_per_bucket()
    if any(every):
        row = col.row()
        row.label(text="Last hour", icon='GRAPH')
        right = row.row()
        right.alignment = 'RIGHT'
        right.label(text="%s  frames per 5 min" % sparkline(every), translate=False)
    col.separator(factor=0.5)
    for name, what, _when, fresh in view.get("worker_rows", []):
        if not fresh:
            continue
        key = name.lower()
        busy = what.startswith("rendert ")
        job = what.split(" ", 1)[1].strip() if busy else ""
        mine = [t for t, _s in _stamps.get(key, ())]
        working = busy and mine and time.time() - (max(mine) + _clock_offset(key)) < 600
        row = col.row()
        row.label(text=name, icon='RENDER_ANIMATION' if working else 'TIME' if not busy else 'FILE_REFRESH',
                  translate=False)
        doing = row.row()
        doing.active = bool(working)
        doing.label(text=("renders " if working else "loads ") + _job_note(job) if busy else "waits for a job",
                    translate=False)
        right = row.row()
        right.alignment = 'RIGHT'
        per = seconds_per_frame(key)
        stats = _hosts.get(key)
        right.label(text="%.0f s/frame · %d" % (per, stats[0]) if per and stats else "—", translate=False)


# ------------------------------------------------------------------ operators

class STUDIO_OT_farm_move(Operator):
    bl_idname = "studio.farm_move"
    bl_label = "Move Up"
    bl_description = "Render this job earlier (jobs that are to come after it wait on the share until then)"
    bl_options = {'INTERNAL'}

    job: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, _context):
        move_up(self.job)
        return {'FINISHED'}


class STUDIO_OT_farm_remove(Operator):
    bl_idname = "studio.farm_remove"
    bl_label = "Remove from the Render Farm"
    bl_description = ("Take this job out of the queue. On the farm its files leave queue/ and the workers go on "
                      "with the next job; frames already rendered stay")
    bl_options = {'INTERNAL'}

    job: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, event):
        job = next((j for j in _jobs if j["job"] == self.job), None)
        if job is not None and job["phase"] in {"queued", "error"}:
            return self.execute(context)           # nothing on the share yet
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, _context):
        try:
            remove(self.job)
        except OSError as ex:
            self.report({'WARNING'}, "Not removed: %s" % ex)
            return {'CANCELLED'}
        return {'FINISHED'}


classes = (STUDIO_OT_farm_move, STUDIO_OT_farm_remove)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    _load_registry()                             # jobs sent before a restart: their frames still come
    try:
        _reconcile()
    except OSError:
        pass
    if active() and not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=5.0, persistent=True)


def unregister():
    for timer in (_tick, _pump):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

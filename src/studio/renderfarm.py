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
    """The farm's folder on the server, where the server is mounted now: macOS mounts it as /Volumes/DDE-1
    (-2 …) when an old /Volumes/DDE is left over, so the folder of the setting is moved to that mount."""
    from . import runner
    p = runner.prefs()
    setting = (getattr(p, "farm_share", "") if p is not None else "") or DEFAULT_SHARE
    root = bpy.path.abspath(setting).rstrip("/\\") or DEFAULT_SHARE
    point = _mount["point"]
    parts = root.split("/")
    if point and len(parts) > 2 and parts[1] == "Volumes" and "/Volumes/" + parts[2] != point:
        return point + "/" + "/".join(parts[3:])
    return root


SERVER = "fileredu.ad.zhdk.ch"
_mount = {"point": None, "url": None, "time": 0.0}


def _read_mounts():
    """(Thread, no network) Where the server's DDE share is mounted, from macOS's list of mounts; the
    address with the user name is kept for mounting it again (the password comes from the keychain)."""
    try:
        out = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return
    point = None
    for line in out.splitlines():
        hit = re.match(r"//(?:([^@]+)@)?([^/]+)/(\S+) on (/Volumes/[^(]+?) \(smbfs", line)
        if hit and hit.group(2).lower().startswith(SERVER.split(".")[0]) and hit.group(3).upper() == "DDE":
            point = hit.group(4)
            url = "smb://%s%s/%s" % (hit.group(1) + "@" if hit.group(1) else "", hit.group(2), hit.group(3))
            if url != _mount["url"]:
                _mount["url"] = url
                try:
                    os.makedirs(LOCAL, exist_ok=True)
                    with open(os.path.join(LOCAL, "server.json"), "w", encoding="utf-8") as f:
                        json.dump({"url": url}, f)
                except OSError:
                    pass
            break
    _mount.update(point=point, time=time.monotonic())


def _server_url():
    if _mount["url"]:
        return _mount["url"]
    try:
        with open(os.path.join(LOCAL, "server.json"), encoding="utf-8") as f:
            _mount["url"] = json.load(f).get("url")
    except (OSError, ValueError):
        pass
    return _mount["url"] or "smb://%s/DDE" % SERVER


_conn = {"root": None, "ok": False, "time": -1e9, "busy": False}
CONN_AGE = 10.0           # seconds a look at the share counts (a network folder can take seconds to answer)


def _look(root):
    if root.startswith("/Volumes/"):
        _read_mounts()                       # the share may be mounted somewhere else now (DDE-1)
        root = share_root()
    ok = os.path.isdir(root)
    _conn.update(root=root, ok=ok, time=time.monotonic(), busy=False)
    return ok


def connected(root=None):
    """The share is mounted. Looked at in a thread at most every 10 s (a slow or gone network folder must not
    freeze Blender's window); only the very first look waits for the answer."""
    root = root or share_root()
    if _conn["root"] != root:
        if not root.startswith("/Volumes/"):            # a local folder answers at once
            return _look(root)
        _conn.update(root=root, ok=False, time=-1e9, busy=False)   # a network one: in a thread, from the start
    if time.monotonic() - _conn["time"] > CONN_AGE:
        _look_later(root)
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
            rows.append((parts[0], what, when, there, age))
            online += there
    out["workers"] = workers
    out["worker_rows"] = rows
    out["online"] = online
    busy = {w[1].split(" ", 1)[1].strip() for w in workers if w[1].startswith("rendert ")}
    out["last_frame"] = _last_frames(root, busy)
    present = set(names)
    for name in names:
        if name.endswith(".part"):
            out["halves"].append(name)
            continue
        if not name.endswith(".json"):
            continue
        job = name[:-5]
        known = _job_files.get((root, job))
        if known is None:                          # a job's .json never changes: read once (over VPN every
            try:                                   # file costs a round trip)
                with open(os.path.join(queue, name), encoding="utf-8") as f:
                    cfg = json.load(f)
                start, end = int(cfg.get("start", 0)), int(cfg.get("end", 0))
                when = os.path.getmtime(os.path.join(queue, name))
            except (OSError, ValueError, TypeError):
                continue
            known = _job_files[(root, job)] = {"cfg": cfg, "start": start, "end": end, "when": when}
        cfg, start, end, when = known["cfg"], known["start"], known["end"], known["when"]
        total = max(0, end - start + 1)
        if job + ".done" in present:
            state = 'done'
            if "done" not in known:                # a finished job does not change any more either
                known["done"] = _done_files(os.path.join(root, "renders", job), job)[0]
            done = known["done"]
        else:
            done, empty = _done_files(os.path.join(root, "renders", job), job)
            state = 'rendering' if empty or done or job in busy else 'waiting'
        out["jobs"].append({"job": job, "scene": cfg.get("scene", ""), "start": start, "end": end, "total": total,
                            "format": cfg.get("format", "PNG"),
                            "done": done, "state": state, "note": cfg.get("note", ""),
                            "sent_by": cfg.get("sent_by", ""), "time": when,
                            "blend": job + ".blend" in present})
    out["jobs"].sort(key=lambda j: j["time"], reverse=True)
    out["ok"] = True
    return out


def _last_frames(root, jobs):
    """(thread) worker → time of its newest frame on the share, read from the newest frames of the jobs
    that render (also jobs not sent from this Blender): a worker writes no line while it renders, its
    frames show it is alive."""
    from . import cut
    out = {}
    for job in jobs:
        folder = os.path.join(root, "renders", job)
        try:
            entries = [e for e in os.scandir(folder) if e.is_file() and e.name.endswith(".png")]
            full = sorted(((e.stat().st_mtime, e.path) for e in entries if e.stat().st_size > 0), reverse=True)
        except OSError:
            continue
        for mtime, path in full[:12]:
            host = cut.png_text(path).get("Hostname", "").split(".")[0].lower()
            if host and mtime > out.get(host, 0.0):
                out[host] = mtime
    return out


_job_files = {}           # (share, job) → its .json (and the frame count once it is done): read once
_view = {"scan": None, "busy": False, "drawn": 0.0, "gen": 0, "since": 0.0}
STUCK = 30.0              # a look at the share that takes longer: the share hangs (VPN gone, Mac slept)
STUCK_SENDING = 180.0     # while a file uploads the share answers slowly (the line is full): longer


def _scan_now(root, gen):
    try:
        view = scan(root)
        _retire_strays(root, view)
        if gen == _view["gen"]:
            _view["scan"] = view
    except Exception as ex:                       # the share away in the middle of it
        if _view["scan"] is None and gen == _view["gen"]:
            _view["scan"] = {"ok": False, "error": str(ex), "jobs": [], "halves": [], "workers": [],
                             "time": time.time()}
    if gen == _view["gen"]:
        _view["busy"] = False


def stuck():
    """The look at the share has hung for a while (a stale network folder). While an upload runs the
    share is slow, not gone: it then takes much longer to count as hung."""
    now = time.monotonic()
    limit = STUCK_SENDING if any(j["phase"] == "copying" for j in _jobs) else STUCK
    return (_view["busy"] and now - _view["since"] > limit) or (_conn["busy"] and now - _conn.get("since", now) > limit)


def _retire_strays(root, view):
    """(thread) An older job of ours on the share that this Blender lost track of (sent while it closed),
    for a section that has a newer job: the workers skip its frames (empty files) and it gets a .done.
    Nothing is deleted."""
    me = "%s@%s" % (getpass.getuser(), socket.gethostname().split(".")[0])
    newest = {}
    for job in list(_jobs):
        if job["phase"] in {"queued", "packing", "packed", "copying", "retry", "sent"} and job.get("note"):
            newest[job["note"]] = max(newest.get(job["note"], ""), job["job"])
    known = {j["job"] for j in _jobs}
    for seen in view.get("jobs", []):
        if seen["job"] in known or seen["state"] == 'done' or seen.get("sent_by") != me:
            continue
        if seen.get("note") in newest and seen["job"] < newest[seen["note"]]:
            ext = EXTENSIONS.get(seen.get("format"), "png")
            renders = os.path.join(root, "renders", seen["job"])
            try:
                os.makedirs(renders, exist_ok=True)
                for frame in range(seen["start"], seen["end"] + 1):
                    path = os.path.join(renders, "%s_%04d.%s" % (seen["job"], frame, ext))
                    if not os.path.exists(path):
                        open(path, "wb").close()
                with open(os.path.join(root, "queue", seen["job"] + ".done"), "w", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " replaced by a newer job\n")
                seen["state"] = 'done'
            except OSError:
                pass


def _refresh(force=False):
    if _view["busy"]:
        return
    last = (_view["scan"] or {}).get("time", 0.0)
    if force or time.time() - last > REFRESH:
        _view.update(busy=True, since=time.monotonic())
        threading.Thread(target=_scan_now, args=(share_root(), _view["gen"]), daemon=True).start()


def _look_later(root=None):
    """The share looked at in a thread (a stale network folder can block for minutes)."""
    if not _conn["busy"]:
        _conn.update(busy=True, since=time.monotonic())
        threading.Thread(target=_look, args=(root or share_root(),), daemon=True).start()


def reconnect(mount=True):
    """Everything about the farm starts fresh: a hung look is given up, the share is mounted again
    (the login from the keychain, else the Mac asks), the jobs' frames are fetched again."""
    _view["gen"] += 1
    _view["busy"] = False
    _conn.update(busy=False, time=-1e9)
    now = time.time()
    for job in _jobs:
        job["looked"] = 0.0
        job["quiet"] = 0
        if job.get("fetching") and now - job.get("fetch_since", now) > STUCK:
            job["fetching"] = False                  # that thread hangs on the old mount: a new one goes
    if mount:
        _reconnect(force=True)                       # mounted again if it is not (in a thread)
    _look_later()
    _refresh(force=True)


CLEAN_EVERY = 1800        # the share is tidied every half hour
CLEAN_AFTER = 86400       # a finished job of this Mac goes from the share a day after it was done


def clean_share(root, host, keep=(), now=None):
    """(Thread) Jobs of this Mac on the share, done for more than a day: their .blend (≈0.5 GB), job
    files and frames go; half uploads older than a day too. Other people's jobs and jobs this Blender
    still waits for are never touched. Returns the jobs removed."""
    now = now or time.time()
    queue = os.path.join(root, "queue")
    try:
        names = os.listdir(queue)
    except OSError:
        return []
    present = set(names)
    gone = []
    for name in names:
        if name.endswith(".blend.part"):
            job = name[:-len(".blend.part")]
            path = os.path.join(queue, name)
            try:
                if job not in keep and now - os.path.getmtime(path) > CLEAN_AFTER:
                    os.remove(path)
                    gone.append(job)
            except OSError:
                pass
            continue
        if not name.endswith(".done"):
            continue
        job = name[:-5]
        if job in keep:
            continue
        try:
            if now - os.path.getmtime(os.path.join(queue, name)) <= CLEAN_AFTER:
                continue
            with open(os.path.join(queue, job + ".json"), encoding="utf-8") as f:
                if host.lower() not in str(json.load(f).get("sent_by", "")).lower():
                    continue                       # someone else's job
        except (OSError, ValueError):
            continue
        for ext in (".blend", ".json.hold", ".json", ".done"):        # the job file (.json) before .done
            if job + ext in present:
                try:
                    os.remove(os.path.join(queue, job + ext))
                except OSError:
                    pass
        shutil.rmtree(os.path.join(root, "renders", job), ignore_errors=True)
        gone.append(job)
    if gone:
        print("AI Studio farm: %d old jobs removed from the share" % len(gone))
    return gone


def _tick():
    """Looks at the share every 15 s while the panel is drawn; stops when it has not been for a while."""
    if time.monotonic() - _view["drawn"] > 2 * REFRESH:
        return None
    from . import runner
    now = time.monotonic()
    if not _conn["ok"]:                     # not connected: look every 2 s, so it shows as soon as it is
        _view["waiting"] = True
        if _conn["busy"] and now - _conn.get("since", now) > 20:
            _conn["busy"] = False           # that look hangs (VPN came back, old mount): a new one goes
        _look_later()
        _reconnect()                        # mounted again by itself (VPN on, login in the keychain)
        runner.tag_redraw()
        return 2.0
    if stuck() and now - _alarm.get("healed", 0.0) > 60:
        _alarm["healed"] = now              # a look hangs on a stale mount: given up by itself
        reconnect(mount=False)
    if time.time() - _alarm.get("cleaned", 0.0) > CLEAN_EVERY:
        _alarm["cleaned"] = time.time()
        threading.Thread(target=clean_share, args=(_conn["root"], socket.gethostname().split(".")[0],
                                                   {j["job"] for j in _jobs if j["phase"] != "done"}),
                         daemon=True).start()
    if _view.pop("waiting", False):
        _view["force"] = True
        if _restart["state"] == "offline" and not bpy.app.timers.is_registered(_after_load):
            bpy.app.timers.register(_after_load, first_interval=1.0)   # compared now that the share is there
    _refresh(force=_view.pop("force", False))
    runner.tag_redraw()
    return REFRESH


# ------------------------------------------------------------------ sending and collecting

LOCAL = os.path.expanduser("~/.cache/ai_studio_farm")
REGISTRY = os.path.join(LOCAL, "jobs.json")
EXT = {'PNG': "png", 'JPEG': "jpg", 'OPEN_EXR': "exr", 'TIFF': "tif"}
FETCH_COPIES = 3          # frames copied at once from the share
SETTLE = 5.0              # seconds a frame on the share stays unchanged before it is taken
_jobs = []                # the farm's queue of this Mac, in order: queued → packing → copying → sent → done / error
TRANSIENT = {"fetching", "new", "arrived", "looked", "t0", "h0", "per", "cancel", "seeded"}
_rate = [0.0]             # seconds per frame the farm made lately (for jobs that wait)
_hosts = {}               # worker → [frames, render seconds] of the frames fetched since Blender started
_events = []              # (finished, worker, render seconds) of every frame fetched: the graph and the speed
newest_frame = {"path": "", "time": 0.0, "title": ""}   # the frame fetched last (the Render tab's preview)
_stamps = {}              # worker → [(its own clock's time of the frame, when we saw it or None)]
_raw = {}                 # (file, scene state) → the saved copy several jobs pack from


def enabled(scene):
    return bool(getattr(scene.studio, "farm_on", False))


def takes(scene, item):
    """The render farm renders this queue item (on, share there, no material variant)."""
    return enabled(scene) and connected() and not getattr(item, "variant", "")


def active():
    return [j for j in _jobs if j["phase"] in {"queued", "packing", "packed", "copying", "retry", "sent"}]


def sending():
    return any(j["phase"] in {"queued", "packing", "packed", "copying", "retry"} for j in _jobs)


def renders_section(folder):
    folder = os.path.normcase(folder)
    return any(j.get("kind") == 'ANIM' and os.path.normcase(j.get("folder", "")) == folder for j in active())


KEEP = {"queued", "packing", "packed", "copying", "retry", "sent"}   # phases that survive a restart
_loaded = {"blend": None, "seen": set()}
_lock = threading.Lock()


def _registry_path(blend):
    """~/.cache/ai_studio_farm/jobs/<file>-<hash>.json: each .blend its own list of farm jobs."""
    import hashlib
    stem = re.sub(r"[^A-Za-z0-9_\-]+", "_", os.path.splitext(os.path.basename(blend))[0]) or "untitled"
    return os.path.join(LOCAL, "jobs", "%s-%s.json" % (stem, hashlib.sha1(blend.encode()).hexdigest()[:8]))


def _save_registry():
    """The jobs on their way or on the farm, per .blend (after every change of phase; from any thread)."""
    groups = {b: [] for b in _loaded["seen"]}
    for job in list(_jobs):
        blend = job.get("blend") or _loaded["blend"]
        if blend and job["phase"] in KEEP:
            groups.setdefault(blend, []).append({k: v for k, v in job.items() if k not in TRANSIENT})
    if _loaded["blend"]:
        groups.setdefault(_loaded["blend"], [])
    with _lock:
        for blend, jobs in groups.items():
            _loaded["seen"].add(blend)
            path = _registry_path(blend)
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(jobs, f, indent=1)
                os.replace(path + ".tmp", path)
            except OSError:
                pass


def _migrate_registry():
    """The one list of older versions (all files in one) into one list per file."""
    if not os.path.exists(REGISTRY):
        return
    try:
        with open(REGISTRY, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = []
    groups = {}
    for job in old:
        blend = job.get("blend") or job.get("sent_file")
        if blend:
            groups.setdefault(blend, []).append(job)
    for blend, jobs in groups.items():
        path = _registry_path(blend)
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(jobs, f, indent=1)
    os.replace(REGISTRY, REGISTRY + ".old")


def _load_registry(blend=None):
    """The jobs of this .blend from before a restart: on the farm they are fetched on, an upload that broke
    off goes on, one not packed yet is packed again (if its copy of the file is still there)."""
    blend = blend if blend is not None else bpy.data.filepath
    if not blend:
        return
    try:
        _migrate_registry()
        with open(_registry_path(blend), encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return
    _loaded["seen"].add(blend)
    for job in saved:
        if any(j["job"] == job["job"] for j in _jobs):
            continue
        job.setdefault("blend", blend)
        phase = job.get("phase")
        packed = os.path.join(LOCAL, job["job"], job["job"] + ".blend")
        if phase in {"copying", "retry", "packed"} and os.path.isfile(packed):
            job.update(phase="retry" if phase != "packed" else "packed", retry_at=0.0, packed=packed,
                       size=os.path.getsize(packed))
        elif phase in {"queued", "packing", "packed"}:
            if job.get("raw") and os.path.isfile(job["raw"]):
                job["phase"] = "queued"
            else:
                job.update(phase="error", error="Its copy of the file is gone after the restart: send it again")
        _jobs.append(job)


_restart = {"state": "", "report": [], "halves": [], "adopt": None, "sent": [], "shown": False}


@bpy.app.handlers.persistent
def _on_load(_dummy=None):
    """A .blend opened (also after a restart): its farm jobs come back and the share is compared in a thread."""
    _save_registry()                              # the file before: its list is saved as it is
    blend = bpy.data.filepath
    _jobs[:] = [j for j in _jobs if j.get("blend") == blend or j["phase"] in {"packing", "copying"}]
    _loaded["blend"] = blend
    _restart.update(state="", report=[], halves=[], adopt=None, sent=[], shown=False)
    if not bpy.app.background:                    # on every start: the server mounted if it is not
        _reconnect(force=True)
        _look_later()
    if not blend:
        return
    _load_registry(blend)
    if _jobs and not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=2.0, persistent=True)
    if not bpy.app.timers.is_registered(_after_load):
        bpy.app.timers.register(_after_load, first_interval=4.0)


def _file_prefix(blend):
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", os.path.splitext(os.path.basename(blend))[0]).strip("_") + "_"


def _after_load():
    """(Blender's thread, a few seconds after loading) What the share is compared with: the film's sections
    and where their frames go. The comparing itself runs in a thread."""
    from . import cut
    try:
        scene = cut.film_scene(bpy.context.scene)
        sections = {name: (cut.frames_folder(name), cut.frame_stem(name))
                    for name, _s, _e, _m in cut.sections_of(scene)}
        archive = cut.archive_folder()
    except Exception as ex:                        # no sections, no folder: nothing to compare
        print("AI Studio farm restart:", ex)
        return None
    root = share_root()
    _restart["state"] = "looking"
    known = {j["job"] for j in _jobs}
    host = socket.gethostname().split(".")[0]
    threading.Thread(target=_adopt, args=(root, _file_prefix(bpy.data.filepath), host, sections, known, archive),
                     daemon=True).start()
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=2.0, persistent=True)
    return None


ADOPT_DAYS = 3


def _adopt(root, prefix, host, sections, known, archive):
    """(Thread) Jobs of this file and this Mac on the share that the list does not know (it was lost):
    the newest job of each section, still rendering or done, comes back and its frames are fetched.
    Half uploads (.blend.part) are found: one with its packed file here goes on, the others are listed."""
    queue = os.path.join(root, "queue")
    try:
        names = os.listdir(queue)
    except OSError:
        _restart["state"] = "offline"             # tried again when the share is there (_tick)
        return
    present = set(names)
    stamp = re.compile(re.escape(prefix) + r"(\d{8}-\d{6})_")
    newest, halves = {}, []
    for name in names:
        hit = stamp.match(name)
        if hit is None:
            continue
        if name.endswith(".blend.part"):
            job = name[:-len(".blend.part")]
            if job not in known:
                try:
                    halves.append((job, os.path.getsize(os.path.join(queue, name))))
                except OSError:
                    pass
            continue
        if not name.endswith(".json") or name[:-5] in known:
            continue
        try:
            when = time.mktime(time.strptime(hit.group(1), "%Y%m%d-%H%M%S"))
        except ValueError:
            continue
        if time.time() - when > ADOPT_DAYS * 86400:
            continue
        job = name[:-5]
        try:
            with open(os.path.join(queue, name), encoding="utf-8") as f:
                cfg = json.load(f)
            done = ""
            if job + ".done" in present:
                with open(os.path.join(queue, job + ".done"), encoding="utf-8", errors="replace") as f:
                    done = f.read()
        except (OSError, ValueError):
            continue
        if host.lower() not in str(cfg.get("sent_by", "")).lower() or "replaced" in done:
            continue
        note = cfg.get("note", "")
        section = note.split(" · ")[0]
        if section in sections and (note not in newest or job > newest[note][0]):
            newest[note] = (job, cfg)
    found = []
    for note, (job, cfg) in newest.items():
        section = note.split(" · ")[0]
        folder, stem = sections[section]
        fmt = cfg.get("format", "PNG")
        found.append({"job": job, "scene": cfg.get("scene", ""), "kind": 'ANIM', "start": int(cfg.get("start", 0)),
                      "end": int(cfg.get("end", 0)), "format": fmt, "ext": EXT.get(fmt, "png"), "note": note,
                      "root": root, "phase": "sent", "progress": 1.0, "error": "", "size": 0, "have": 0,
                      "folder": folder, "stem": stem, "section": section,
                      "quality": 'HIGH' if note.endswith("HQ") else 'LOW', "skip": [], "added": time.time(),
                      "sent_by": cfg.get("sent_by", ""), "adopted": True, "raw": "",
                      "archive": os.path.join(archive, "%s %s" % (os.path.basename(folder),
                                                                  time.strftime("%Y-%m-%d %H%M%S")))})
    for job, _size in halves:                      # its packed file and job are still here: it goes on
        meta = os.path.join(LOCAL, job, "job.json")
        packed = os.path.join(LOCAL, job, job + ".blend")
        if os.path.isfile(meta) and os.path.isfile(packed):
            try:
                with open(meta, encoding="utf-8") as f:
                    back = json.load(f)
                back.update(phase="retry", retry_at=0.0, packed=packed, tries=0, error="",
                            size=os.path.getsize(packed))
                found.append(back)
            except (OSError, ValueError):
                pass
    resumed = {j["job"] for j in found}
    _restart.update(adopt=found, halves=[h for h in halves if h[0] not in resumed], state="adopted")


def _ranges(frames):
    out = []
    for f in sorted(frames):
        if out and f == out[-1][1] + 1:
            out[-1][1] = f
        else:
            out.append([f, f])
    return [tuple(r) for r in out]


def restart_report(scene):
    """(Blender's thread) Per section: frames here, on the farm (on their way), missing (neither here nor on
    the farm, as ranges) and whether the farm frames are older than the scene (OUTDATED_FARM). Only sections
    with frames (a section never rendered is not missing anything)."""
    from . import cut, film_tools
    outdated = {p[0] for p in film_tools.problems(scene) if p[1] == 'OUTDATED_FARM'}
    rows = []
    for name, start, end, _m in cut.sections_of(scene):
        st = cut.frame_status(name, start, end, fresh=True)
        here = {f for f, code in zip(range(start, end + 1), st["codes"]) if code in {'HIGH', 'LOW'}}
        if not here and not any(j.get("section") == name for j in _jobs):
            continue
        coming = set()
        for job in _jobs:
            if job.get("section") == name and job["phase"] in KEEP:
                coming.update(range(job["start"], job["end"] + 1))
        coming -= here
        missing = set(range(start, end + 1)) - here - coming
        rows.append({"section": name, "done": len(here), "total": end - start + 1, "farm": len(coming),
                     "missing": _ranges(missing), "outdated": name in outdated, "quality": st.get("quality")})
    return rows


def resend_missing(scene, rows=None):
    """Only the missing frames of each section again (frames a–b), from the film's scene, without stopping
    what renders of it. Returns ["01 Hero 120–180", …]."""
    from . import cut
    scene = cut.film_scene(scene)
    sent = []
    for row in rows if rows is not None else restart_report(scene):
        for a, b in row["missing"]:
            result = bpy.ops.studio.add(mode='ANIM', section=row["section"], frames="%d-%d" % (a, b),
                                        keep_running=True)
            if 'FINISHED' in result:
                sent.append("%s %d–%d" % (row["section"], a, b))
    _restart["sent"] += sent
    return sent


def _took_over():
    """(Blender's thread, from _pump) The jobs found on the share join the list; then the report and,
    if wanted, the missing frames go again."""
    found = _restart.get("adopt")
    if found is None:
        return
    _restart["adopt"] = None
    for job in found:
        if not any(j["job"] == job["job"] for j in _jobs):
            job.setdefault("blend", bpy.data.filepath)
            _jobs.append(job)
    if found:
        _save_registry()
    from . import cut, runner
    try:
        scene = cut.film_scene(bpy.context.scene)
        rows = restart_report(scene)
    except Exception as ex:
        print("AI Studio farm restart:", ex)
        return
    _restart["report"] = rows
    _restart["state"] = "done"
    wanted = [r for r in rows if r["missing"]]
    mode = getattr(scene.studio, "farm_resend", 'ASK')
    if wanted and mode == 'AUTO' and scene.studio.farm_on:
        sent = resend_missing(scene, wanted)
        if sent:
            runner.notify("Farm after the restart: sent again %s" % ", ".join(sent))
    taken = sum(1 for j in found if j.get("adopted"))
    if taken:
        runner.log("farm: %d jobs from the share taken over after the restart" % taken)


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
    from . import film
    film._scene_copy(path)                       # nothing changed since the last copy: a hard link, no save
    _raw[key] = path
    return path


_SESSION = "%x" % int(time.time() * 1000)   # this Blender: an edit count is only comparable within it


def _edits():
    """Real edits of the scene (geometry, materials, lights, moves – not frame changes or AI Studio's lists)."""
    from . import autoquality
    return autoquality._edits[0]


def outdated_jobs():
    """Jobs of ours still rendering on the farm although the scene changed after they were sent (in this
    Blender: real edits since; after a restart it is not known)."""
    out = []
    for job in _jobs:
        if job.get("kind") != 'ANIM' or job["phase"] not in {"packing", "packed", "copying", "sent"}:
            continue
        if job.get("have", 0) >= total_of(job):
            continue
        if job.get("sent_session") == _SESSION and _edits() > job["sent_changes"]:
            out.append(job)
    return out


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
           "sent_changes": _edits(), "sent_session": _SESSION, "sent_file": bpy.data.filepath,
           "sent_at": time.time(),
           "have": 0, "blend": bpy.data.filepath, "added": time.time(),
           "sent_by": "%s@%s" % (getpass.getuser(), socket.gethostname().split(".")[0])}
    job.update(extra)
    _jobs.append(job)
    try:                                          # found again after a restart (with its half upload)
        os.makedirs(os.path.join(LOCAL, name), exist_ok=True)
        with open(os.path.join(LOCAL, name, "job.json"), "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in job.items() if k not in TRANSIENT}, f, indent=1)
    except OSError:
        pass
    _save_registry()
    if _control["farm"] != "an":                  # after "Log Out All Macs": the next job switches the farm on
        set_control("an", root=root)
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
        supersede(path, a, b)                        # sent again: the older job of these frames stops
        skip = []
        if not item.keep_frames and not getattr(item, "redo", False) and not getattr(item, "job_folder", ""):
            # Frames there in this quality (or better) stay: a longer intro renders only its new frames.
            st = cut.frame_status(section, a, b, fresh=True)
            wanted = {'HIGH'} if quality == 'HIGH' else {'HIGH', 'LOW'}
            skip = [f for f, code in zip(range(a, b + 1), st["codes"]) if code in wanted]
            if len(skip) == b - a + 1:
                return None                          # nothing to render
        # Older frames stay in the Video Editor until each new one comes (then the old one goes to archiv).
        meta = os.path.join(path, film.FILM_DIR)     # what the Video Editor reads: quality and size
        os.makedirs(meta, exist_ok=True)
        keep = [o for o in overrides if o[0] in {"render.resolution_x", "render.resolution_y",
                                                  "render.resolution_percentage", "render.image_settings.file_format"}]
        with open(os.path.join(meta, "job.json"), "w", encoding="utf-8") as f:
            json.dump({"scene": scene.name, "quality": quality, "section": section, "frames": [a, b],
                       "overrides": keep, "farm": True, "camera_name": camera.name}, f, indent=1)
        version = getattr(item, "job_folder", "")
        return submit(scene, a, b, fmt, "%s%s · %s" % (section, " " + version if version else "",
                                                      "HQ" if quality == 'HIGH' else "LQ"), kind='ANIM',
                      overrides=overrides, folder=path, stem=stem, section=section, quality=quality,
                      camera_name=camera.name, skip=skip,
                      archive=os.path.join(cut.archive_folder(), "%s %s" % (os.path.basename(path),
                                                                         time.strftime("%Y-%m-%d %H%M%S"))))
    frame = scene.frame_current
    return submit(scene, frame, frame, None, "%s · %s" % (label, "HQ" if quality == 'HIGH' else "LQ"),
                  kind='STILL', overrides=overrides, camera_spec=job.get("camera"), target=path, quality=quality)


def _remap_roots():
    """Jobs sent while the server was mounted elsewhere (/Volumes/DDE ↔ DDE-1) use where it is now."""
    root = share_root()
    if not root.startswith("/Volumes/"):
        return
    tail = "/".join(root.split("/")[3:])
    for job in _jobs:
        old = job.get("root", "")
        if old != root and old.startswith("/Volumes/") and "/".join(old.split("/")[3:]) == tail:
            job["root"] = root


def _pump():
    """Sending, with the next job prepared ahead: while one job uploads, the next one is packed already
    (own Blender process), so the farm gets it right after; collects the frames of sent jobs every 5 s."""
    from . import runner
    try:
        _remap_roots()
        packing = next((j for j in _jobs if j["phase"] == "packing"), None)
        if packing is not None:
            _packed(packing)
        if not any(j["phase"] == "copying" for j in _jobs):
            ready = next((j for j in _jobs if j["phase"] == "packed"), None)
            if ready is not None:
                _start_copy(ready)
            else:
                _retry_due()
        if not any(j["phase"] in {"packing", "packed"} for j in _jobs):
            nxt = next((j for j in _jobs if j["phase"] == "queued"), None)
            if nxt is not None:
                _pack(nxt)
        _took_over()
        _collect_all()
        _watch_workers()
        runner.tag_redraw()
    except Exception as ex:                      # never stop the timer
        print("AI Studio farm:", ex)
    if sending():
        return 1.0
    return 3.0 if active() or _restart["state"] == "looking" else None


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
        job.update(phase="packed", size=os.path.getsize(packed), progress=0.0, packed=packed)
        if not any(j["phase"] == "copying" for j in _jobs):
            _start_copy(job)
    if not any(j["raw"] == job["raw"] and j["phase"] in {"queued", "packing"} for j in _jobs):
        try:                                     # our own local copy, no longer needed
            os.remove(job["raw"])
        except OSError:
            pass


_alarm = {"offline": set(), "looked": 0.0}


_awake = {"proc": None}


def _keep_awake(on):
    """While jobs of ours are on the way or on the farm this Mac does not sleep (it fetches the frames;
    a night without it brought nothing from 3 to 10 o'clock)."""
    proc = _awake["proc"]
    if on and not bpy.app.background and (proc is None or proc.poll() is not None):
        try:
            _awake["proc"] = subprocess.Popen(["caffeinate", "-is", "-w", str(os.getpid())],
                                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL)
        except OSError:
            _awake["proc"] = None
    elif not on and proc is not None:
        if proc.poll() is None:
            proc.terminate()
        _awake["proc"] = None


def _reconnect(force=False):
    """The share is not there (sleep, VPN dropped, Blender just started): mounted again by itself, at most
    every minute, only when the server answers (VPN on) and the share is not mounted. The login comes from
    the keychain (macOS asks once if it is not there): no window or error while away from the VPN."""
    if (not force and time.time() - _alarm.get("mounted", 0.0) < 60) or _conn.get("busy_mount"):
        return
    _alarm["mounted"] = time.time()

    def mount():
        _conn["busy_mount"] = True
        try:
            _read_mounts()
            if _mount["point"]:
                return                             # mounted: the look decides
            try:
                socket.create_connection((SERVER, 445), timeout=3).close()
            except OSError:
                return                             # no VPN / network: nothing to mount
            subprocess.run(["osascript", "-e", 'mount volume "%s"' % _server_url()], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=90)
            _read_mounts()
            _conn["time"] = -1e9                   # looked at again right away
        except (OSError, subprocess.SubprocessError):
            pass
        finally:
            _conn["busy_mount"] = False
    threading.Thread(target=mount, daemon=True).start()


def _watch_workers():
    """While jobs of ours are on the farm: the share's worker lines are read every minute (also with the
    panel closed), a Mac going offline or no new frame for 10 minutes is reported once, the share is
    mounted again when it went away, and this Mac stays awake."""
    from . import runner
    busy = any(j["phase"] in {"queued", "packing", "packed", "copying", "retry", "sent"} for j in _jobs)
    _keep_awake(busy)
    if not any(j["phase"] == "sent" for j in _jobs):
        return
    if not connected():
        if _alarm.get("had_share"):
            _reconnect()
        return
    _alarm["had_share"] = True
    last = last_frame_time()
    if last and time.time() - last > STALL and not _alarm.get("stalled"):
        _alarm["stalled"] = True
        runner.notify("Render farm: no new frame for %d min" % ((time.time() - last) // 60))
    elif last and time.time() - last <= STALL:
        _alarm["stalled"] = False
    if time.time() - _alarm["looked"] > 60:
        _alarm["looked"] = time.time()
        _refresh(force=True)
    states = worker_states()
    gone = {n for n, st, _j, _s in states if st == 'offline'}
    new = gone - _alarm["offline"]
    _alarm["offline"] = gone
    if new and _view["scan"] is not None:
        alive = sum(1 for _n, st, _j, _s in states if st != 'offline')
        runner.notify("Render farm: %s offline%s" % (", ".join(sorted(new)),
                                                    " · no Mac renders now!" if not alive else ""))


def _start_copy(job):
    job.update(phase="copying", progress=0.0)
    threading.Thread(target=_copy, args=(job, job["packed"]), daemon=True).start()


RETRIES = 6               # an upload that broke off (VPN, share gone, timeout) is tried again this often
RETRY_WAIT = (30, 60, 120, 240, 300, 300)   # seconds before each new try


def _sample_hash(path, size):
    """A fingerprint of a file from 16 blocks spread over it (a full hash would read 0.5 GB over VPN)."""
    import hashlib
    digest = hashlib.sha1(str(size).encode())
    block = 1 << 20
    with open(path, "rb") as f:
        for n in range(16):
            f.seek(max(0, (size - block) * n // 15))
            digest.update(f.read(block))
    return digest.hexdigest()


def _copy(job, packed):
    """The packed copy onto the share: <job>.blend.part → <job>.blend, then <job>.json (last). A copy that
    broke off goes on where it stopped (the .part stays for that); a full copy is checked (size and a
    fingerprint) before it counts."""
    root, name, size = job["root"], job["job"], job["size"]
    queue = os.path.join(root, "queue")
    part = os.path.join(queue, name + ".blend.part")
    final = os.path.join(queue, name + ".blend")
    try:
        if not os.path.isdir(root):
            raise OSError("Share not connected: %s" % CONNECT_HINT)
        os.makedirs(queue, exist_ok=True)
        there = os.path.getsize(part) if os.path.exists(part) else -1
        done_before = there < 0 and os.path.exists(final) and os.path.getsize(final) == size
        if not done_before:
            free = shutil.disk_usage(queue).free
            if free < (size - max(0, there)) * 1.05:
                raise OSError("The share is full (%.1f GB free, %.1f GB needed)" % (free / 1e9, size / 1e9))
            start = 0
            if 0 < there <= size:                  # go on where it broke off (the last block again)
                start = max(0, (there // CHUNK - 1) * CHUNK)
            copied = start
            with open(packed, "rb") as src, open(part, "r+b" if start else "wb") as dst:
                src.seek(start)
                dst.seek(start)
                dst.truncate(start)
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
            if os.path.getsize(part) != size or _sample_hash(part, size) != _sample_hash(packed, size):
                os.remove(part)                    # wrong somewhere: the next try copies it anew
                raise OSError("The copy on the share differs from this Mac's")
            os.replace(part, final)
        if job.get("skip"):                      # frames that are rendered already: the workers skip them
            folder = os.path.join(root, "renders", name)
            os.makedirs(folder, exist_ok=True)
            for frame in job["skip"]:
                path = os.path.join(folder, "%s_%04d.%s" % (name, frame, job["ext"]))
                if not os.path.exists(path):
                    open(path, "wb").close()
        meta = os.path.join(queue, name + ".json.part")
        with open(meta, "w", encoding="utf-8") as f:
            json.dump({"scene": job["scene"], "start": job["start"], "end": job["end"], "format": job["format"],
                       "sent_by": job["sent_by"], "note": job["note"]}, f, indent=1)
        os.replace(meta, os.path.join(queue, name + ".json"))      # now the job is ready
        job.update(phase="sent" if job["kind"] in {'STILL', 'ANIM'} else "done", progress=1.0, error="",
                   tries=0)
        _reconcile()
        shutil.rmtree(os.path.join(LOCAL, name), ignore_errors=True)
        _view["force"] = True                    # looked at again soon (the old view stays until then)
        _save_registry()
    except InterruptedError:                     # removed by the user (confirmed): its half file goes too
        try:
            os.remove(part)
        except OSError:
            pass
    except OSError as ex:
        full = getattr(ex, "errno", 0) == 28 or "is full" in str(ex)
        tries = job.get("tries", 0) + 1
        if full or tries > RETRIES:
            reason = "the share is full" if full else "%s (tried %d times)" % (ex, tries - 1)
            job.update(phase="error", error="Upload stopped: %s · Retry tries again" % reason)
        else:                                    # VPN dropped, timeout, share away: again in a while
            job.update(phase="retry", tries=tries, error=str(ex)[:80],
                       retry_at=time.time() + RETRY_WAIT[min(tries, len(RETRY_WAIT)) - 1])
        _save_registry()


def _retry_due():
    """(Blender's thread) An upload that broke off starts again when its time came and the share is there."""
    if any(j["phase"] == "copying" for j in _jobs):
        return
    job = next((j for j in _jobs if j["phase"] == "retry" and time.time() >= j.get("retry_at", 0.0)), None)
    if job is None or not connected():
        return
    if not os.path.isfile(job.get("packed", "")):
        job.update(phase="error", error="The packed file is gone: send it again")
        return
    _start_copy(job)


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
        if other["phase"] in {"sent", "copying", "packed", "packing", "queued"} and other["kind"] == 'ANIM':
            before += max(0, total_of(other) - other.get("have", 0))
    return None


def summary():
    """All of ours on the farm in one: (frames there, frames in all, seconds left or None, jobs sending)."""
    jobs = [j for j in _jobs if j["phase"] in {"queued", "packing", "packed", "copying", "retry", "sent"}
            and j["kind"] == 'ANIM']
    have = sum(j.get("have", 0) for j in jobs)
    total = sum(total_of(j) for j in jobs)
    per = _per_frame()
    return have, total, (per * max(0, total - have) if per else None), \
        sum(1 for j in jobs if j["phase"] in {"queued", "packing", "packed", "copying", "retry"})


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
    # the smallest gap: clock difference plus the shortest way to us (the median also held the waiting until a
    # frame was fetched, which put frames in the future: "-27 min ago")
    return diffs[0] if diffs else 0.0


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


def busy_share(host):
    """How much of its time a Mac renders: its render time per frame / the time between two of its frames
    (1.0 = it never waits; loading and saving are the rest). None if unknown."""
    per = seconds_per_frame(host)
    stats = _hosts.get(host)
    if not per or not stats or not stats[0] or not stats[1]:
        return None
    return min(1.0, (stats[1] / stats[0]) / per)


def farm_rate(now=None):
    """Frames per minute of the whole farm: the sum of the Macs that render now (each one's time between
    two frames); else counted from the frames of the last 15 minutes / hour. None if nothing came."""
    now = now or time.time()
    speeds = []
    for host, stamps in _stamps.items():
        last = max((t + _clock_offset(host) for t, _s in stamps), default=0.0)
        per = seconds_per_frame(host)
        if per and now - last < max(600.0, 4 * per):
            speeds.append(60.0 / per)
    if speeds and len(_stamps) == len(speeds) or len(speeds) >= 2:
        return sum(speeds)
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
        elif job["kind"] == 'ANIM' and job.get("farm_done") and job.get("have", 0) < total_of(job):
            job["phase"] = "done"                    # the farm closed it short: never waits for frames forever
            runner.log("farm job %s closed with %d frames missing" % (job["job"], job["farm_done"]))
            runner.notify("%s: the farm finished it, %d frames missing (render the section again)"
                          % (job["note"] or job["job"], job["farm_done"]))
            _save_registry()
            threading.Thread(target=_reconcile, daemon=True).start()
        elif job["kind"] == 'ANIM' and job.get("have", 0) >= total_of(job):
            job["phase"] = "done"
            cut.on_film_done(job["scene"])
            runner.notify("%s ready (render farm)" % job["note"])
            _save_registry()
            threading.Thread(target=_reconcile, daemon=True).start()
        elif not job.get("fetching") and now - job.get("looked", 0.0) >= min(60.0, 5.0 * (1 + job.get("quiet", 0))):
            job["looked"], job["fetching"], job["fetch_since"] = now, True, now
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
        skip = set(job.get("skip") or ())
        prefix = job["job"] + "_"
        listed = {}                                   # one look at the folder (over VPN: not one per frame)
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.name.startswith(prefix):
                    try:
                        st = entry.stat()
                    except OSError:
                        continue
                    listed[entry.name] = (st.st_size, st.st_mtime)
        on_share = have = settling = 0
        todo = []
        for frame in range(job["start"], job["end"] + 1):
            if frame in skip:                         # rendered before: kept, the workers skip it
                have += 1
                on_share += 1
                continue
            name = "%s_%04d.%s" % (job["job"], frame, job["ext"])
            size, mtime = listed.get(name, (0, 0.0))
            if size <= 0 or now - mtime <= SETTLE:
                settling += size > 0
                continue                              # not rendered yet, or still being written
            on_share += 1
            local = os.path.join(job["folder"], "%s_%04d.%s" % (job["stem"], frame, job["ext"]))
            try:
                mine = os.stat(local)
            except OSError:
                mine = None
            if mine is not None and mine.st_size > 0 and mine.st_mtime >= mtime:
                have += 1                             # fetched already (before a restart too)
                if not job.get("seeded"):
                    _note_frame(job, local, live=False)
                continue
            todo.append((frame, os.path.join(folder, name), local, mine))
        job["seeded"] = True
        job["on_share"] = on_share

        def fetch_one(item):
            frame, source, local, mine = item
            shutil.copyfile(source, local + ".part")
            if mine is not None and mine.st_size > 0 and job.get("archive"):
                os.makedirs(job["archive"], exist_ok=True)     # the older version of this frame (a copy:
                kept = os.path.join(job["archive"], os.path.basename(local))   # the strip shows it until …
                try:
                    os.link(local, kept)
                except OSError:
                    shutil.copyfile(local, kept)
            os.replace(local + ".part", local)       # … the new one takes its place in one step (never missing:
            #                                          Blender's Video Editor crashes on a frame file that is gone)
            newest_frame.update(path=local, time=time.time(), title="%s · frame %d" % (job["note"] or job["job"], frame))
            _note_frame(job, local)
            return frame

        new = 0
        if todo:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=FETCH_COPIES) as pool:   # a few copies at once (VPN latency)
                for _frame in pool.map(fetch_one, todo):
                    have += 1
                    new += 1
                    if new % 8 == 0:                  # many at once: show them as they come
                        job["new"] = job.get("new", 0) + 8
                        _note_progress(job, have)
            job["new"] = job.get("new", 0) + new % 8
        _note_progress(job, have)
        job["quiet"] = 0 if new or on_share > job.get("seen_share", -1) else job.get("quiet", 0) + 1
        job["seen_share"] = on_share
        if new:
            job["last_new"] = time.time()
        done = os.path.join(job["root"], "queue", job["job"] + ".done")
        if have >= total_of(job) and job.get("skip"):
            if not os.path.exists(done):              # the workers count only their own frames: we say it
                with open(done, "w", encoding="utf-8") as f:
                    f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " all frames there\n")
        elif not todo and not settling and have < total_of(job) and os.path.exists(done):
            job["farm_done"] = total_of(job) - have   # finished on the farm without these frames (a newer
            #                                           job of the section took them, or the worker failed)
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
        there = set(os.listdir(folder))               # one look (over VPN not one per frame)
        for frame in range(start, end + 1):
            file = "%s_%04d.%s" % (name, frame, ext)
            if file not in there:
                open(os.path.join(folder, file), "wb").close()


def _retire(root, name, start, end, ext):
    """(thread) A job of ours not wanted any more: an empty file for every frame not rendered (the workers
    skip it) and a .done (no worker starts it again). Nothing is deleted."""
    renders = os.path.join(root, "renders", name)
    try:
        os.makedirs(renders, exist_ok=True)
        there = set(os.listdir(renders))
        for frame in range(start, end + 1):
            file = "%s_%04d.%s" % (name, frame, ext)
            if file not in there:
                open(os.path.join(renders, file), "wb").close()
        with open(os.path.join(root, "queue", name + ".done"), "w", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + " replaced by a newer job\n")
    except OSError as ex:
        print("AI Studio farm: older job not stopped:", ex)


def supersede(folder, start=None, end=None):
    """A section is sent again: its older job is not wanted any more (with start/end: only a job whose
    frames overlap these). Not sent yet: it is dropped here. On the farm: every frame not rendered yet gets
    an empty file (the workers skip it, as their own placeholders) and the job a .done, so no worker starts
    it again. Nothing is deleted on the share."""
    folder = os.path.normcase(os.path.abspath(folder))
    for job in [j for j in _jobs if j.get("kind") == 'ANIM' and j["phase"] != "done"
                and os.path.normcase(os.path.abspath(j.get("folder", ""))) == folder
                and (start is None or (j["start"] <= end and start <= j["end"]))]:
        if job["phase"] == "sent":
            job["phase"] = "done"                     # the share is written in a thread (over VPN: slow)
            threading.Thread(target=_retire, args=(job["root"], job["job"], job["start"], job["end"], job["ext"]),
                             daemon=True).start()
        else:
            if job["phase"] == "packing":
                proc = _procs.pop(job["job"], None)
                if proc is not None and proc.poll() is None:
                    proc.terminate()
            elif job["phase"] == "copying":
                job["cancel"] = True
            _jobs.remove(job)
            shutil.rmtree(os.path.join(LOCAL, job["job"]), ignore_errors=True)
            if job.get("raw") and not any(j.get("raw") == job["raw"] for j in _jobs):
                try:
                    os.remove(job["raw"])
                except OSError:
                    pass
    _reconcile()
    _save_registry()


def remove(name):
    """Take a job out of the queue, wherever it is (waiting here, being sent, on the farm)."""
    job = next((j for j in _jobs if j["job"] == name), None)
    if job is None:                                # a job of the share that is not from this Blender
        seen = next((j for j in (_view["scan"] or {}).get("jobs", []) if j["job"] == name), None)
        if seen is not None:
            remove_from_share(share_root(), name, seen["start"], seen["end"], EXT.get(seen.get("format"), "png"))
            _refresh(force=True)
        return
    phase = job["phase"]
    if phase == "packing":
        proc = _procs.pop(name, None)
        if proc is not None and proc.poll() is None:
            proc.terminate()
    elif phase == "copying":
        job["cancel"] = True
    elif phase == "retry":                         # confirmed: its half upload goes too (in a thread)
        part = os.path.join(job["root"], "queue", name + ".blend.part")
        threading.Thread(target=lambda: os.path.exists(part) and os.remove(part), daemon=True).start()
    elif phase == "sent":
        remove_from_share(job["root"], name, job["start"], job["end"], job["ext"])
    _jobs.remove(job)
    if job.get("raw") and not any(j.get("raw") == job["raw"] for j in _jobs):
        try:
            os.remove(job["raw"])
        except OSError:
            pass
    shutil.rmtree(os.path.join(LOCAL, name), ignore_errors=True)
    _reconcile()
    _save_registry()
    _refresh(force=True)


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
    elif phase == "packed":
        what = "packed, uploads next"
    elif phase == "copying":
        what = "upload %.1f/%.1f GB" % (job["progress"] * job["size"] / 1e9, job["size"] / 1e9)
    else:
        have = job.get("have", 0)
        ready = job.get("on_share", have) - have
        if job["kind"] != 'ANIM':
            what = "on the farm" if not have else "done"
        elif have or _renders(job["job"]):
            what = "%d/%d" % (have, total) + (" · %d coming" % ready if ready > 0 else "")
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
    for job in [j for j in _jobs if j["phase"] in {"queued", "packing", "packed", "copying", "retry", "sent"}
                and j["kind"] == 'ANIM']:
        one = left(job)
        if one is not None:
            secs += one
            known = True
    return secs if known else None


SENDING = {"queued", "packing", "packed", "copying", "retry"}


def _send_text(job):
    if job["phase"] == "queued":
        return "%s · waits to be sent" % (job["note"] or job["job"])
    if job["phase"] == "packing":
        return "%s · packing the file" % (job["note"] or job["job"])
    if job["phase"] == "packed":
        return "%s · packed, uploads next" % (job["note"] or job["job"])
    if job["phase"] == "retry":
        wait = max(0, int(job.get("retry_at", 0.0) - time.time()))
        return "%s · upload broke off, goes on %s (try %d of %d)" % (
            job["note"] or job["job"], "in %d s" % wait if wait else "when the share is there",
            job.get("tries", 1) + 1, RETRIES + 1)
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
        sub.label(text=_send_text(job), icon={'copying': 'EXPORT', 'retry': 'FILE_REFRESH'}.get(job["phase"], 'TIME'),
                  translate=False)
        if buttons:
            row.operator(STUDIO_OT_farm_remove.bl_idname, text="", icon='X', emboss=False).job = job["job"]
    for n, job in enumerate(farm):
        row = layout.row(align=True)
        if job["phase"] == "error":
            row.alert = True
            row.label(text="%s: %s" % (job["note"] or job["job"], job["error"]), icon='ERROR', translate=False)
            if os.path.isfile(job.get("packed", "")):
                row.operator(STUDIO_OT_farm_retry.bl_idname, text="", icon='FILE_REFRESH').job = job["job"]
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
STALL = 600.0             # no new frame this long while workers are online: said in the Render tab


def _clock(secs):
    """1:05 (minutes:seconds) or 1 h 05."""
    secs = int(max(0, secs))
    return "%d h %02d" % (secs // 3600, secs // 60 % 60) if secs >= 3600 else "%d:%02d" % (secs // 60, secs % 60)


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


LOADING = 900.0           # a worker that started a job may take this long for its first frame (loading)
OFFLINE_SHOWN = 1200.0    # an offline worker stays in the list this long (to notice it), then it goes


def worker_states(view=None):
    """[(name, state, what, since)] of the workers seen in the last 12 h: state 'rendering' (frames come),
    'loading' (started a job, no frame yet), 'waiting' (writes its line every 20 s) or 'offline' (no line and
    no frame for too long: the Mac sleeps, its Terminal was closed or it lost the share). since: the time of
    its last sign of life."""
    view = view or _view["scan"] or {}
    out = []
    now = time.time()
    for row in view.get("worker_rows", []):
        name, what, _when, _there = row[:4]
        age = row[4] if len(row) > 4 else 0.0
        key = name.lower()
        status_time = now - age
        frames = [t + _clock_offset(key) for t, _s in _stamps.get(key, ())]
        last_frame = min(now, max(frames + [view.get("last_frame", {}).get(key, 0.0)]))
        per = seconds_per_frame(key) or 0.0
        since = max(status_time, last_frame)
        if what.startswith("rendert "):
            if last_frame and now - last_frame < max(600.0, 4 * per) and last_frame >= status_time - 60:
                state = 'rendering'
            elif age < LOADING:
                state = 'loading'
            else:
                state = 'offline'
        elif age < ONLINE:
            state = 'waiting'
        else:
            state = 'offline'
        if state == 'offline' and now - since > OFFLINE_SHOWN:
            continue                                  # gone for a while: not listed any more
        out.append((name, state, what.split(" ", 1)[1].strip() if what.startswith("rendert ") else "", since))
    return out


def online_count(view=None):
    return sum(1 for _n, state, _w, _s in worker_states(view) if state != 'offline')


def _job_note(name):
    """The job's name as the queue shows it ("01 Hero · HQ"); a job not of this Blender (or removed): its
    section from the job name ("01 Hero, older job")."""
    job = next((j for j in _jobs if j["job"] == name), None)
    if job:
        return job["note"] or name
    short = re.sub(r"^.*?_\d{8}-\d{6}_", "", name).replace("_", " ")
    return "%s (older job)" % short if short != name else name


def last_frame_time():
    """When the last frame of ours came (0 if none yet)."""
    return max((finished for finished, _w, _s in _events), default=0.0)


_mac_icons = {}


def _mac_icon(name, fallback):
    """A colour icon of this Blender (green: renders, blue: online and waiting)."""
    if not _mac_icons:
        _mac_icons.update(dict.fromkeys(bpy.types.UILayout.bl_rna.functions['prop'].parameters['icon']
                                        .enum_items.keys(), True))
    return name if name in _mac_icons else fallback


def _draw_reconnect(col):
    row = col.row(align=True)
    row.scale_y = 1.2
    row.operator(STUDIO_OT_farm_refresh.bl_idname, text="Connect Server", icon='LINKED')


def draw_state(layout, context):
    """The farm in short: Macs and speed, the frames of the last hour, each Mac in one line; when something
    is wrong (share, VPN, no Mac, no frame for a while) the way back in one click."""
    rq = context.scene.studio
    if not rq.farm_on and not active() and not _restart["report"]:
        return
    watch()
    _draw_restart(layout, context.scene)
    box = layout.box()
    col = box.column(align=True)
    if not connected() and _conn["time"] < 0 and not stuck():    # the first look is on its way
        row = col.row(align=True)
        row.active = False
        row.label(text="Looking at the render farm …", icon='TIME')
        row.operator(STUDIO_OT_farm_refresh.bl_idname, icon='FILE_REFRESH')
        return
    if not connected() and not stuck():
        row = col.row(align=True)
        row.alert = True
        row.label(text="Share not connected" + (": renders stay on this Mac" if rq.farm_on and not active() else
                                                ": frames wait on the farm"), icon='ERROR')
        _draw_reconnect(col)
        hint = col.row()
        hint.active = False
        hint.label(text="From home: VPN on first (it also connects by itself)", translate=False)
        return
    view = _view["scan"]
    if stuck():                                      # the share hangs: VPN gone, Mac slept
        row = col.row()
        row.alert = True
        row.label(text="The farm does not answer (VPN off? Mac slept?)", icon='ERROR', translate=False)
        _draw_reconnect(col)
        if view is None:
            return
    elif view is None:                               # the first look at the share is on its way
        row = col.row(align=True)
        row.active = False
        row.label(text="Looking at the render farm …", icon='TIME')
        row.operator(STUDIO_OT_farm_refresh.bl_idname, icon='FILE_REFRESH')
        return
    from .runner import format_time
    states = worker_states(view)
    workers = sum(1 for _n, st, _w, _s in states if st != 'offline')
    have, total, secs, sending = summary()
    rate = farm_rate()
    now = time.time()
    last = last_frame_time()
    waiting = total > have or sending
    rendering = [(n, j) for n, st, j, _s in states if st == 'rendering']
    head = col.row(align=True)                       # Macs at work · speed · what is left
    if not workers and waiting:
        head.alert = True
        head.label(text="No Mac renders · last frame %s" % (time.strftime("%H:%M", time.localtime(last))
                                                           if last else "–"), icon='ERROR', translate=False)
    else:
        head.label(text="%d of %d Macs render%s" % (len(rendering), len([x for x in states if x[1] != 'offline']),
                                                    " · %.1f frames/min" % rate if rate and rendering else ""),
                   icon='DESKTOP', translate=False)
    head.operator(STUDIO_OT_farm_refresh.bl_idname, text="", icon='FILE_REFRESH')
    if total:
        line = col.row()
        line.active = False
        line.label(text="%d of %d frames%s" % (have, total, " · ~%s left, done at %s" % (
            format_time(secs), time.strftime("%H:%M", time.localtime(now + secs))) if secs and workers else ""),
                   icon='RENDER_ANIMATION', translate=False)
    if workers and waiting and last and now - last > STALL and not any(st == 'loading' for _n, st, _j, _s in states):
        row = col.row()
        row.alert = True
        row.label(text="No new frame for %s" % format_time(now - last), icon='ERROR', translate=False)
    lines = col.column(align=True)                   # each Mac: green renders, blue is there and waits
    green, blue = _mac_icon('STRIP_COLOR_04', 'RENDER_ANIMATION'), _mac_icon('STRIP_COLOR_05', 'TIME')
    for name, state, job, since in states:
        if state == 'offline':                       # gone: not shown
            continue
        key = name.lower()
        per = seconds_per_frame(key)
        row = lines.row(align=True)
        if state == 'rendering':
            row.label(text="", icon=green)
            busy = busy_share(key)
            hour = sum(frames_per_bucket(key, now))
            row.progress(factor=busy if busy is not None else 1.0, type='BAR', text="%s · %s%s%s" % (
                name, "%.0f s per frame" % per if per else "renders",
                " · %d %% busy" % round(busy * 100) if busy is not None else "",
                " · %d in 1 h" % hour if hour else ""))
            continue
        row.label(text="%s · %s" % (name, {
            "loading": "loads the file · %s" % _clock(now - since),
            "waiting": "online, waits for a job"}.get(state, state)), icon=blue, translate=False)
    if rendering:
        doing = sorted({_job_note(j) for _n, j in rendering if j})
        every = frames_per_bucket(None, now)
        foot = col.row()
        foot.active = False
        foot.scale_y = 0.8
        foot.label(text="%s · last hour %d frames, best 5 min %d" % (", ".join(doing)[:48], sum(every), max(every)),
                   translate=False)
    if not workers and waiting:
        hint = col.row()
        hint.active = False
        hint.label(text="On the Macs: double-click \"Renderfarm Start (Mac).terminal\" in Flamenco", translate=False)
    if any(st != 'offline' for _n, st, _j, _s in states):
        row = col.row()
        row.alignment = 'RIGHT'
        row.operator(STUDIO_OT_farm_logout.bl_idname, text="Log Out All Macs", icon='QUIT', emboss=False)


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


class STUDIO_OT_farm_refresh(Operator):
    bl_idname = "studio.farm_refresh"
    bl_label = "Connect Server"
    bl_description = ("Connect to the render farm's server: mounts the share if it is gone (the login from "
                      "the keychain, else the Mac asks), gives up a hung look, reads the Macs, fetches the frames")
    bl_options = {'INTERNAL'}

    def execute(self, _context):
        reconnect()
        _alarm["looked"] = time.time()
        if active() and not bpy.app.timers.is_registered(_pump):
            bpy.app.timers.register(_pump, first_interval=0.1, persistent=True)
        watch()
        return {'FINISHED'}


def set_control(farm, end="standby", root=None):
    """(Thread) _worker/control.json, what every render Mac reads: farm "an"/"aus", end "standby" /
    "abmelden" (log out; the autostart brings the farm back at the next login)."""
    root = root or share_root()

    def write():
        try:
            path = os.path.join(root, "_worker", "control.json")
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"farm": farm, "ende": end, "seit": int(time.time()), "von": socket.gethostname()}, f)
            os.replace(path + ".tmp", path)
            _control["farm"] = farm
        except OSError as ex:
            print("AI Studio farm: control.json:", ex)
    threading.Thread(target=write, daemon=True).start()


_control = {"farm": None}


class STUDIO_OT_farm_logout(Operator):
    bl_idname = "studio.farm_logout"
    bl_label = "Log Out All Macs"
    bl_description = ("Every render Mac logs out now (after the 10 frames it is on); at their next login the "
                      "farm starts again by itself, and the next job switches it on")
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, _context):
        set_control("aus", "abmelden")
        self.report({'INFO'}, "The render Macs log out")
        return {'FINISHED'}


class STUDIO_OT_farm_restart(Operator):
    bl_idname = "studio.farm_restart"
    bl_label = "After the Restart"
    bl_options = {'INTERNAL'}

    action: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})      # SEND / HIDE / HALVES

    @classmethod
    def description(cls, _context, props):
        return {"SEND": "Send only the missing frames of each section again (from the film's scene; what "
                        "renders goes on)",
                "HIDE": "Hide this summary",
                "HALVES": "Remove the half uploads on the share (they never became jobs; nothing else is touched)"
                }.get(props.action, "")

    def invoke(self, context, event):
        if self.action == 'HALVES':
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        if self.action == 'SEND':
            sent = resend_missing(context.scene, [r for r in _restart["report"] if r["missing"]])
            for row in _restart["report"]:
                row["missing"] = []
            self.report({'INFO'}, "Sent again: %s" % (", ".join(sent) or "nothing"))
        elif self.action == 'HIDE':
            _restart["shown"] = True
        elif self.action == 'HALVES':
            root, halves = share_root(), list(_restart["halves"])
            _restart["halves"] = []

            def drop():
                for job, _size in halves:
                    try:
                        os.remove(os.path.join(root, "queue", job + ".blend.part"))
                    except OSError:
                        pass
            threading.Thread(target=drop, daemon=True).start()
        return {'FINISHED'}


def _draw_restart(col, scene):
    """After a restart (or opening the file): per section here · on the farm · missing (and what was sent)."""
    if _restart["shown"]:
        return
    rows = [r for r in _restart["report"] if r["missing"] or r["farm"] or r["outdated"]]
    if not rows and not _restart["halves"] and not _restart["sent"]:
        return
    box = col.box()
    head = box.row(align=True)
    head.label(text="Since this file was opened", icon='RECOVER_LAST')
    head.prop(scene.studio, "farm_resend", text="")
    head.operator(STUDIO_OT_farm_restart.bl_idname, text="", icon='X', emboss=False).action = 'HIDE'
    lines = box.column(align=True)
    for row in rows:
        bits = ["%d/%d here" % (row["done"], row["total"])]
        if row["farm"]:
            bits.append("%d on the farm" % row["farm"])
        if row["missing"]:
            bits.append("missing " + ", ".join("%d–%d" % r if r[0] != r[1] else "%d" % r[0]
                                               for r in row["missing"][:3]) + (" …" if len(row["missing"]) > 3 else ""))
        line = lines.row()
        line.alert = bool(row["missing"]) or row["outdated"]
        line.label(text="%s · %s%s" % (row["section"], " · ".join(bits), " · older than the scene"
                                         if row["outdated"] else ""),
                   icon='ERROR' if row["missing"] else ('CHECKMARK' if not row["farm"] else 'TIME'), translate=False)
    if _restart["sent"]:
        sent = box.row()
        sent.active = False
        sent.label(text="Sent again: " + ", ".join(_restart["sent"])[:90], icon='EXPORT', translate=False)
    if any(r["missing"] for r in rows):
        box.operator(STUDIO_OT_farm_restart.bl_idname, text="Send Missing Frames", icon='EXPORT').action = 'SEND'
    if _restart["halves"]:
        row = box.row(align=True)
        row.label(text="%d half uploads on the share (%.1f GB)" % (
            len(_restart["halves"]), sum(s for _j, s in _restart["halves"]) / 1e9), icon='ERROR', translate=False)
        row.operator(STUDIO_OT_farm_restart.bl_idname, text="Remove", icon='TRASH').action = 'HALVES'


class STUDIO_OT_farm_retry(Operator):
    bl_idname = "studio.farm_retry"
    bl_label = "Retry"
    bl_description = "Upload it again, from where it broke off"
    bl_options = {'INTERNAL'}

    job: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, _context):
        job = next((j for j in _jobs if j["job"] == self.job), None)
        if job is None:
            return {'CANCELLED'}
        job.update(phase="retry", tries=0, retry_at=0.0, error="")
        reconnect(mount=True)
        if not bpy.app.timers.is_registered(_pump):
            bpy.app.timers.register(_pump, first_interval=0.1, persistent=True)
        return {'FINISHED'}


classes = (STUDIO_OT_farm_move, STUDIO_OT_farm_remove, STUDIO_OT_farm_refresh, STUDIO_OT_farm_retry,
           STUDIO_OT_farm_restart, STUDIO_OT_farm_logout)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    if _on_load not in bpy.app.handlers.load_post:   # each .blend: its jobs back, the share compared
        bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_register_late, first_interval=1.0)   # the add-on (re)loaded with a file open


def _register_late():
    if bpy.data.filepath and _loaded["blend"] != bpy.data.filepath:
        _on_load()
    return None


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    _keep_awake(False)
    for timer in (_tick, _pump):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

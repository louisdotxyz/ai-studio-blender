# SPDX-License-Identifier: GPL-3.0-or-later
"""A section on the render farm (Flamenco), in its own Blender process (started by farm.py, never imported):

    Blender -b <frames>/.farm/scene.blend --factory-startup --python farm_job.py -- <frames>/.farm/job.json

1. The copy gets the render's settings (High Quality at full size, the section's camera, PNG frames; no
   timeline markers, so the camera stays), its images are packed into it, and it is saved into the
   Manager's shared storage (the workers read it from there).
2. A "simple-blender-render" job goes to the Manager over its API.
3. Until the job is done, the frames the workers write are copied into the section's frames folder; the
   Video Editor shows them like any render. Started again, it goes on with the job it sent.
Progress goes to status.json next to the job (read by film.py for the bar in Blender).
"""

import json
import os
import platform
import shutil
import sys
import time
import traceback
import urllib.request

import bpy

JOB = sys.argv[sys.argv.index("--") + 1]
FOLDER = os.path.dirname(JOB)
STATUS = os.path.join(FOLDER, "status.json")
SETTLE = 5.0          # seconds a frame on the share must be unchanged before it is copied
_state = {}


def status(**values):
    _state.update(values, updated=time.time(), pid=os.getpid())
    tmp = STATUS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_state, f)
    os.replace(tmp, STATUS)


def api(url, path, body=None, timeout=20):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url.rstrip("/") + "/" + path.lstrip("/"), data=data,
                                     headers={"Content-Type": "application/json", "Accept": "application/json"},
                                     method="POST" if body is not None else "GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}


def _apply(scene, overrides):
    for _attempt in range(3):              # some values only fit after others (color_mode after file_format)
        failed = []
        for path, value in overrides:
            head, _, attr = path.rpartition(".")
            try:
                owner = scene.path_resolve(head) if head else scene
                setattr(owner, attr, value)
            except Exception as ex:
                failed.append((path, value, ex))
        if not failed:
            return
        overrides = [(p, v) for p, v, _ex in failed]
    for path, _v, ex in failed:
        print("FARM could not set %s: %s" % (path, ex), flush=True)


def prepare(job):
    """The copy for the farm, saved into the shared storage. Returns its path."""
    scene = bpy.data.scenes[job["scene"]]
    _apply(scene, job.get("overrides", []))
    cam = bpy.data.objects.get(job.get("camera", ""))
    if cam is not None:
        scene.camera = cam
    scene.timeline_markers.clear()         # no marker switches the camera on the farm
    start, end = job["frames"]
    scene.frame_start, scene.frame_end = start, end
    render = scene.render
    render.use_sequencer = False
    render.compositor_device = 'CPU'
    try:
        bpy.ops.file.pack_all()            # textures, HDRIs: inside the file, the workers have no other path
    except Exception as ex:
        print("FARM pack:", ex, flush=True)
    out = job["farm_blend"]
    os.makedirs(os.path.dirname(out), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True, compress=True, relative_remap=False)
    return out


def submit(job, blend):
    url = job["manager"]
    types = api(url, "api/v3/jobs/types")
    found = next((t for t in types.get("job_types", []) if t.get("name") == "simple-blender-render"), None)
    if found is None:
        raise RuntimeError("The Manager has no job type simple-blender-render")
    start, end = job["frames"]
    output = job["farm_output"]
    settings = {"frames": "%d-%d" % (start, end), "chunk_size": int(job.get("chunk", 1)),
                "render_output_root": os.path.dirname(output), "add_path_components": 0,
                "render_output_path": output + "####", "blender_cmd": "{blender}", "blendfile": blend,
                "fps": float(job.get("fps", 24)), "format": "PNG", "image_file_extension": ".png",
                "has_previews": False}
    known = {s.get("key") for s in found.get("settings", [])}
    settings = {k: v for k, v in settings.items() if not known or k in known}
    body = {"name": job["name"], "type": "simple-blender-render", "priority": 50, "settings": settings,
            "metadata": {"project": job.get("project", ""), "section": job.get("section", "")},
            "submitter_platform": platform.system().lower()}
    if found.get("etag"):
        body["type_etag"] = found["etag"]
    sent = api(url, "api/v3/jobs", body)
    print("FARM job", sent.get("id"), flush=True)
    return sent.get("id")


def collect(job):
    """Copy the frames the workers finished into the section's folder. Returns how many are there."""
    source = os.path.dirname(job["farm_output"])
    stem = os.path.basename(job["farm_output"])
    target = job["folder"]
    start, end = job["frames"]
    done, now = 0, time.time()
    for frame in range(start, end + 1):
        name = "%s%04d.png" % (stem, frame)
        local = os.path.join(target, name)
        if os.path.exists(local) and os.path.getsize(local) > 0:
            done += 1
            continue
        remote = os.path.join(source, name)
        try:
            st = os.stat(remote)
        except OSError:
            continue
        if st.st_size > 0 and now - st.st_mtime > SETTLE:
            shutil.copyfile(remote, local + ".part")
            os.replace(local + ".part", local)
            done += 1
    return done


def main():
    with open(JOB, encoding="utf-8") as f:
        job = json.load(f)
    try:                                   # started again: the job that was sent
        with open(STATUS, encoding="utf-8") as f:
            _state.update({k: v for k, v in json.load(f).items() if k in ("job_id", "started", "blend")})
    except (OSError, ValueError):
        pass
    start, end = job["frames"]
    total = end - start + 1
    status(state="rendering", parts=1, part=1, total=total, done=0, started=_state.get("started", time.time()))
    if not _state.get("job_id"):
        status(stage="preparing the file for the farm")
        blend = prepare(job)
        status(stage="sending the job", blend=blend)
        _state["job_id"] = submit(job, blend)
        status(job_id=_state["job_id"], stage="on the farm")
    times = []
    while True:
        done = collect(job)
        if not times or done != times[-1][1]:
            times.append((time.time(), done))
        per = None
        if len(times) > 1 and times[-1][1] > times[0][1]:
            per = (times[-1][0] - times[0][0]) / (times[-1][1] - times[0][1])
        state = ""
        try:
            info = api(job["manager"], "api/v3/jobs/%s" % _state["job_id"], timeout=10)
            state = info.get("status", "")
        except Exception as ex:            # the Manager away for a moment: keep copying
            print("FARM status:", ex, flush=True)
        status(done=done, per_frame=per, farm_status=state)
        if done >= total:
            break
        if state in {"failed", "canceled"}:
            raise RuntimeError("Flamenco job %s" % state)
        time.sleep(10)
    status(state="done", done=total, finished=time.time())
    print("FARM done", flush=True)


try:
    main()
except Exception:
    print("FARM failed", traceback.format_exc(), flush=True)
    try:
        status(state="failed", error=traceback.format_exc(limit=2).strip().splitlines()[-1])
    except Exception:
        pass
    sys.exit(3)

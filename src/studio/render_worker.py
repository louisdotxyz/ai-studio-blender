# SPDX-License-Identifier: GPL-3.0-or-later
"""Render worker: a second Blender without a window that renders queue entries (started by runner.py).

    Blender -b --factory-startup <base.blend> --python render_worker.py -- <settings json>

<base.blend> is a plain copy of your file. The worker stays open and reads one job per
line from stdin (JSON); every job carries the entry's settings as data:

    {"uid": 12, "scene": "Scene", "animation": false,
     "overrides": [["render.filepath", "/…/x.png"], ["cycles.samples", 64], ["camera", {"object": "o_side"}], …],
     "camera": {"source": "o_side", "matrix": [16 floats], "lens": 50.0} or null,
     "variant": [["Holz TH", "Group", "Input_3", [r, g, b, a]], …]}

It applies them, renders, prints "RS done <uid>" (or "RS fail <uid> …") and puts the values back.
{"quit": true} or the end of stdin ends it. The runner starts a fresh worker when your scene
changed (or the camera), so between jobs only these values differ: persistent data keeps
geometry and textures loaded and saves most of the preparation time.
Not part of the add-on itself: never imported.
"""

import json
import os
import re
import sys
import traceback

import bpy
from mathutils import Matrix

_SAMPLE_RE = re.compile(r"(?:Sample|Rendering)\s+(\d+)\s*/\s*(\d+)")
_last = [None]
_last_status = [None]
TEMP_NAME = "Render Studio Worker Camera"


def _settings():
    argv = sys.argv
    return json.loads(argv[argv.index("--") + 1]) if "--" in argv else {}


def _devices(settings):
    kind = settings.get("compute_device_type")
    if not kind or kind == 'NONE':
        return
    try:
        cycles = bpy.context.preferences.addons["cycles"].preferences
        cycles.compute_device_type = kind
        cycles.get_devices()
        wanted = {tuple(d) for d in settings.get("devices", [])}
        for device in cycles.devices:
            device.use = (device.type, device.name) in wanted
        print("RS devices:", kind, [d.name for d in cycles.devices if d.use], flush=True)
    except Exception as ex:
        print("RS devices failed:", ex, flush=True)


@bpy.app.handlers.persistent
def _stats(stats, *_args):
    """One line per new sample count or new step ("Mem: 1M | Updating Scene | …")."""
    match = _SAMPLE_RE.search(stats or "")
    if match:
        if match.groups() != _last[0]:
            _last[0] = match.groups()
            print("RS Sample %s/%s" % match.groups(), flush=True)
        return
    parts = [p.strip() for p in (stats or "").split("|")]
    status = parts[1] if len(parts) > 1 and parts[0].startswith("Mem") else parts[0]
    if status and status != _last_status[0]:
        _last_status[0] = status
        print("RS Status %s" % status[:80], flush=True)


def _missing_files():
    missing = []
    for image in bpy.data.images:
        if image.source in {'FILE', 'SEQUENCE', 'TILED'} and not image.packed_file and image.filepath:
            if not os.path.exists(bpy.path.abspath(image.filepath)):
                missing.append(image.filepath)
    return missing


def _value(value):
    if isinstance(value, dict) and "object" in value:
        return bpy.data.objects.get(value["object"])
    return value


def _split(path):
    head, _, attr = path.rpartition(".")
    return head, attr


def _owner(scene, head):
    return scene.path_resolve(head) if head else scene


def _copy(value):
    return tuple(value) if hasattr(value, "__len__") and not isinstance(value, str) else value


def _apply(scene, overrides, saved):
    """Set [path, value] pairs on the scene, remembering the old values in `saved`. Some
    values depend on others (color_mode on file_format): what fails is tried again."""
    pending = list(overrides)
    failed = []
    for _attempt in range(3):
        failed = []
        for path, value in pending:
            head, attr = _split(path)
            try:
                owner = _owner(scene, head)
                old = _copy(getattr(owner, attr))
                setattr(owner, attr, _value(value))
                saved.append((owner, attr, old))
            except Exception as ex:
                failed.append((path, value, ex))
        if not failed:
            return
        pending = [(p, v) for p, v, _ex in failed]
    for path, _v, ex in failed:
        print("RS could not set %s: %s" % (path, ex), flush=True)


def _restore(saved):
    for owner, attr, value in reversed(saved):
        try:
            setattr(owner, attr, value)
        except Exception:
            pass
    saved.clear()


def _temp_camera(scene, spec):
    """One copy of the entry's camera at the stored position, reused between jobs (the
    runner starts a new worker when the camera changes)."""
    source = bpy.data.objects[spec["source"]]
    temp = bpy.data.objects.get(TEMP_NAME)
    if temp is None:
        temp = source.copy()
        temp.data = source.data.copy()
        temp.name = TEMP_NAME
        temp.animation_data_clear()
        temp.data.animation_data_clear()
        temp.constraints.clear()
        temp.parent = None
        scene.collection.objects.link(temp)
    m = spec["matrix"]
    temp.matrix_world = Matrix([m[0:4], m[4:8], m[8:12], m[12:16]])
    if temp.data.type != 'ORTHO' and spec.get("lens", 0) > 0:
        temp.data.lens = spec["lens"]
    return temp


def _variant(values, saved):
    for material, node_name, identifier, value in values:
        node = bpy.data.materials[material].node_tree.nodes[node_name]
        socket = next(i for i in node.inputs if i.identifier == identifier)
        saved.append((socket, "default_value", _copy(socket.default_value)))
        socket.default_value = value


def run(job):
    uid = job["uid"]
    print("RS job %s" % uid, flush=True)
    _last[0] = _last_status[0] = None
    scene = bpy.data.scenes[job.get("scene") or bpy.context.scene.name]
    saved = []
    try:
        overrides = job.get("overrides", [])
        if job.get("camera"):
            temp = _temp_camera(scene, job["camera"])
            overrides = [[p, {"object": temp.name}] if p == "camera" else [p, v] for p, v in overrides]
        _variant(job.get("variant") or [], saved)
        _apply(scene, overrides, saved)
        animation = bool(job.get("animation"))
        print("RS start", scene.camera.name if scene.camera else None, scene.render.filepath, flush=True)
        bpy.ops.render.render(animation=animation, write_still=not animation, scene=scene.name)
        print("RS done %s" % uid, flush=True)
    except Exception:
        print("RS fail %s %s" % (uid, traceback.format_exc(limit=3).replace("\n", " | ")), flush=True)
    finally:
        _restore(saved)


def main():
    _devices(_settings())
    for scene in bpy.data.scenes:
        # The GPU compositor gets a windowless Blender killed (SIGTERM at the end of the render,
        # 5.2 on macOS at High Quality size); the CPU compositor gives the same image.
        scene.render.compositor_device = 'CPU'
        if scene.render.engine == 'CYCLES':
            scene.render.use_persistent_data = True   # keep geometry/textures between jobs
    missing = _missing_files()
    if missing:
        print("RS missing files:", missing[:5], flush=True)
    bpy.app.handlers.render_stats.append(_stats)
    print("RS ready", flush=True)
    while True:
        line = sys.stdin.readline()   # blocks until the runner sends the next job
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        job = json.loads(line)
        if job.get("quit"):
            break
        run(job)
    print("RS bye", flush=True)


main()

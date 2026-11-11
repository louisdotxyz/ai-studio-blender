# SPDX-License-Identifier: GPL-3.0-or-later
"""Render worker: a second Blender without a window that renders queue entries (started by runner.py).

    Blender -b --factory-startup --python render_worker.py -- <settings json>

It starts without a file (the runner opens it ahead of time, the GPU kernels load meanwhile)
and reads one message per line from stdin (JSON):

    {"load": "/…/base.blend", "scripts": false}     open a plain copy of your file
    {"uid": 12, "scene": "Scene", "animation": false,
     "overrides": [["render.filepath", "/…/x.png"], ["cycles.samples", 64], ["camera", {"object": "o_side"}], …],
     "camera": {"source": "o_side", "matrix": [16 floats], "lens": 50.0} or null,
     "variant": [["Holz TH", "Group", "Input_3", [r, g, b, a]], …],
     "draft": {"path": "/…/draft_12", "stages": [{"max_size": 480, "samples": 8}, …]} or null}

A job applies the values, renders (first small drafts for the preview if asked), prints
"RS done <uid>" (or "RS fail <uid> …") and puts the values back. {"quit": true} or the end of
stdin ends it. The runner sends a fresh copy when your scene changed (or the camera), so
between jobs only these values differ: persistent data keeps geometry and textures loaded.
Not part of the add-on itself: never imported.
"""

import json
import os
import re
import sys
import time
import traceback

import bpy
from mathutils import Matrix

_SAMPLE_RE = re.compile(r"(?:Sample|Rendering)\s+(\d+)\s*/\s*(\d+)")
# Big images render in tiles: "Rendered 3/9 Tiles, Sample 200/512" – the samples count per tile.
_TILE_RE = re.compile(r"Rendered\s+(\d+)\s*/\s*(\d+)\s+Tiles")
_REMAINING_RE = re.compile(r"Remaining:\s*(?:(\d+):)?(\d+):(\d+(?:\.\d+)?)")
_T0 = time.monotonic()
_last = [None]
_last_status = [None]
TEMP_NAME = "Render Studio Worker Camera"


def _settings():
    argv = sys.argv
    return json.loads(argv[argv.index("--") + 1]) if "--" in argv else {}


_GPU = [False]   # the worker renders on the GPU: jobs set the scene's device to GPU


def _devices(settings):
    """Your Cycles devices. "AUTO" (Cycles was never set up in Blender's preferences, the
    default of a new Blender): the Mac's GPU over Metal when there is one."""
    kind = settings.get("compute_device_type")
    if not kind or kind == 'NONE':
        return
    try:
        cycles = bpy.context.preferences.addons["cycles"].preferences
        if kind == 'AUTO':
            try:
                cycles.compute_device_type = 'METAL'
            except TypeError:
                print("RS devices: no Metal, CPU", flush=True)
                return
            cycles.get_devices()
            for device in cycles.devices:
                device.use = device.type != 'CPU'
        else:
            cycles.compute_device_type = kind
            cycles.get_devices()
            wanted = {tuple(d) for d in settings.get("devices", [])}
            for device in cycles.devices:
                device.use = (device.type, device.name) in wanted
        _GPU[0] = any(d.use and d.type != 'CPU' for d in cycles.devices)
        print("RS devices:", cycles.compute_device_type, [d.name for d in cycles.devices if d.use], flush=True)
    except Exception as ex:
        print("RS devices failed:", ex, flush=True)


@bpy.app.handlers.persistent
def _stats(stats, *_args):
    """One line per new sample count or new step ("Mem: 1M | Updating Scene | …")."""
    match = _SAMPLE_RE.search(stats or "")
    if match:
        tiles = _TILE_RE.search(stats)
        key = match.groups() + (tiles.groups() if tiles else ())
        if key != _last[0]:
            _last[0] = key
            # Cycles' own estimate: in the background it reports only every 30-80 s, the runner
            # moves the bar on in between with it.
            left = _REMAINING_RE.search(stats)
            seconds = (int(left.group(1) or 0) * 3600 + int(left.group(2)) * 60 + float(left.group(3))) if left else None
            print("RS Sample %s/%s" % match.groups() + (" tile %s/%s" % tiles.groups() if tiles else "")
                  + (" left %.1f" % seconds if seconds is not None else ""), flush=True)
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
    # Several rounds: some values only fit once another is back (color_depth after file_format).
    for _attempt in range(3):
        failed = False
        for owner, attr, value in reversed(saved):
            try:
                if _copy(getattr(owner, attr)) != value:
                    setattr(owner, attr, value)
            except Exception:
                failed = True
        if not failed:
            break
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


def _draft(scene, spec, uid):
    """Quick images of the entry for the preview before the real render (Cycles stills): first a
    very small one, then a sharper one for long renders. With persistent data the real render
    reuses what the drafts loaded, so they cost little. Each stage writes <path>_<n>.png and
    prints "RS draft <uid> <n>"."""
    render, cycles = scene.render, scene.cycles
    long_side = max(render.resolution_x, render.resolution_y) * render.resolution_percentage / 100
    final = max(cycles.samples, 1)
    for n, stage in enumerate(spec.get("stages") or [spec]):
        samples = min(final, stage["samples"])
        scale = min(1.0, stage["max_size"] / max(long_side, 1))
        if scale * scale * samples / final > stage.get("max_share", 0.15):
            continue   # the render itself (or the next stage) is about as quick
        saved = []
        path = "%s_%d.png" % (spec["path"], n)
        _apply(scene, [["render.resolution_percentage", max(1, int(render.resolution_percentage * scale))],
                       ["cycles.samples", samples], ["cycles.use_denoising", True], ["cycles.time_limit", 0.0],
                       ["render.image_settings.file_format", 'PNG'],
                       ["render.image_settings.color_mode", 'RGBA' if render.film_transparent else 'RGB'],
                       ["render.image_settings.color_depth", '8'], ["render.use_file_extension", False],
                       ["render.filepath", path]], saved)
        print("RS draft-start %s %d" % (uid, n), flush=True)
        try:
            bpy.ops.render.render(write_still=True, scene=scene.name)
        except Exception as ex:
            print("RS draft failed:", ex, flush=True)
        finally:
            _restore(saved)
            _last[0] = _last_status[0] = None
            print("RS draft %s %d" % (uid, n), flush=True)   # also after a failure: the real render follows


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
        if _GPU[0] and scene.render.engine == 'CYCLES':
            # A new scene says CPU even when Blender is set to the GPU: the worker always uses it.
            overrides = list(overrides) + [["cycles.device", 'GPU']]
        _variant(job.get("variant") or [], saved)
        _apply(scene, overrides, saved)
        animation = bool(job.get("animation"))
        if job.get("draft") and not animation and scene.render.engine == 'CYCLES':
            _draft(scene, job["draft"], uid)
        print("RS start", scene.camera.name if scene.camera else None, scene.render.filepath, flush=True)
        bpy.ops.render.render(animation=animation, write_still=not animation, scene=scene.name)
        print("RS done %s" % uid, flush=True)
    except Exception:
        print("RS fail %s %s" % (uid, traceback.format_exc(limit=3).replace("\n", " | ")), flush=True)
    finally:
        _restore(saved)


def _prepare_file():
    for scene in bpy.data.scenes:
        # The GPU compositor gets a windowless Blender killed (SIGTERM at the end of the render,
        # 5.2 on macOS at High Quality size); the CPU compositor gives the same image.
        scene.render.compositor_device = 'CPU'
        if scene.render.engine == 'CYCLES':
            scene.render.use_persistent_data = True   # keep geometry/textures between jobs
    missing = _missing_files()
    if missing:
        print("RS missing files:", missing[:5], flush=True)


def _warm_up(settings):
    """Load the GPU render kernels while the runner still saves your file: a tiny render of
    the empty start scene."""
    if not _GPU[0]:
        return
    try:
        scene = bpy.context.scene
        scene.render.engine = 'CYCLES'
        scene.cycles.device = 'GPU'
        scene.cycles.samples = 1
        scene.cycles.use_denoising = False
        scene.render.resolution_x = scene.render.resolution_y = 16
        scene.render.use_compositing = False
        bpy.ops.render.render(write_still=False)
    except Exception as ex:
        print("RS warm-up failed:", ex, flush=True)


def _load(message):
    bpy.ops.wm.open_mainfile(filepath=message["load"], load_ui=False, use_scripts=bool(message.get("scripts")))
    try:
        os.remove(message["load"])   # read completely: the copy is not needed any more
    except OSError:
        pass
    _prepare_file()
    print("RS loaded %.1f s" % (time.monotonic() - _T0), flush=True)


def main():
    settings = _settings()
    _devices(settings)
    if bpy.data.filepath:
        _prepare_file()   # started with a file (older runner)
    else:
        _warm_up(settings)
    bpy.app.handlers.render_stats.append(_stats)
    print("RS ready %.1f s" % (time.monotonic() - _T0), flush=True)
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
        if job.get("load"):
            try:
                _load(job)
            except Exception:
                print("RS load failed %s" % traceback.format_exc(limit=3).replace("\n", " | "), flush=True)
            continue
        run(job)
    print("RS bye", flush=True)


main()

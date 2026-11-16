# SPDX-License-Identifier: GPL-3.0-or-later
"""A film in its own Blender process (started by film.py, not part of the add-on: never imported).

    Blender -b <folder>/.film/scene.blend --factory-startup --python film_job.py -- <folder>/.film/job.json

It runs on its own (its own process group), so it goes on when the Blender you work in crashes or is
closed. The frames are written one by one as images and existing ones are skipped: started again
(film.resume), it goes on where it stopped. The frame range renders in N parts one after the other;
with a video format each part becomes a video (<name>_part_01.mp4) and at the end the whole film
(<name>.mp4); without one (sections since 4.0) the frames are the result. Progress goes to status.json next to the job (read by film.py for the bar in Blender).
"""

import json
import os
import sys
import time
import traceback

import bpy
from mathutils import Matrix

JOB = sys.argv[sys.argv.index("--") + 1]
FOLDER = os.path.dirname(JOB)
STATUS = os.path.join(FOLDER, "status.json")
TEMP_NAME = "Studio Film Camera"
_state = {}


def status(**values):
    _state.update(values, updated=time.time(), pid=os.getpid())
    tmp = STATUS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_state, f)
    os.replace(tmp, STATUS)


def _devices(settings):
    kind = settings.get("compute_device_type")
    if not kind or kind == 'NONE':
        return False
    try:
        cycles = bpy.context.preferences.addons["cycles"].preferences
        if kind == 'AUTO':
            try:
                cycles.compute_device_type = 'METAL'
            except TypeError:
                return False
            cycles.get_devices()
            for device in cycles.devices:
                device.use = device.type != 'CPU'
        else:
            cycles.compute_device_type = kind
            cycles.get_devices()
            wanted = {tuple(d) for d in settings.get("devices", [])}
            for device in cycles.devices:
                device.use = (device.type, device.name) in wanted
        return any(d.use and d.type != 'CPU' for d in cycles.devices)
    except Exception as ex:
        print("FILM devices failed:", ex, flush=True)
        return False


def _value(value):
    if isinstance(value, dict) and "object" in value:
        return bpy.data.objects.get(value["object"])
    return value


def _apply(scene, overrides):
    pending = list(overrides)
    for _attempt in range(3):     # some values only fit after others (color_mode after file_format)
        failed = []
        for path, value in pending:
            head, _, attr = path.rpartition(".")
            try:
                owner = scene.path_resolve(head) if head else scene
                setattr(owner, attr, _value(value))
            except Exception as ex:
                failed.append((path, value, ex))
        if not failed:
            return
        pending = [(p, v) for p, v, _ex in failed]
    for path, _v, ex in failed:
        print("FILM could not set %s: %s" % (path, ex), flush=True)


def _pin_camera(scene):
    """The film's camera stays the camera on every frame. Unbinding the timeline markers' cameras is not
    enough: Blender still switched to a marker's camera on a frame change (the films of 4.0 rendered from
    the last section's camera). This copy only renders, so its markers go, and the camera is set again
    on every frame."""
    wanted = scene.camera
    scene.timeline_markers.clear()

    def keep(sc, *_args):
        if wanted is not None and sc.camera != wanted:
            sc.camera = wanted
    for handlers in (bpy.app.handlers.frame_change_pre, bpy.app.handlers.frame_change_post,
                     bpy.app.handlers.render_pre):
        handlers.append(keep)
    print("FILM camera", wanted.name if wanted else None, flush=True)


def _camera(scene, spec):
    source = bpy.data.objects[spec["source"]]
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


def _variant(values):
    for material, node_name, identifier, value in values:
        node = bpy.data.materials[material].node_tree.nodes[node_name]
        socket = next(i for i in node.inputs if i.identifier == identifier)
        socket.default_value = value


def split(frames, parts):
    """The frames in `parts` runs of about the same length, in order."""
    parts = max(1, min(parts, len(frames)))
    size, rest = divmod(len(frames), parts)
    runs, at = [], 0
    for n in range(parts):
        length = size + (1 if n < rest else 0)
        runs.append(frames[at:at + length])
        at += length
    return [r for r in runs if r]


def _done(path):
    try:
        return os.path.getsize(path) > 0
    except OSError:
        return False


def encode(scene, paths, out, video):
    """The frames as a video with Blender's own encoder (a sequencer scene)."""
    enc = bpy.data.scenes.new("Studio Encode")
    try:
        sed = enc.sequence_editor_create()
        strips = sed.strips if hasattr(sed, "strips") else sed.sequences   # empty collections are falsy
        strip = strips.new_image(name="frames", filepath=paths[0], channel=1, frame_start=1)
        for path in paths[1:]:
            strip.elements.append(os.path.basename(path))
        render = enc.render
        pct = scene.render.resolution_percentage / 100
        render.resolution_x = max(2, round(scene.render.resolution_x * pct) // 2 * 2)
        render.resolution_y = max(2, round(scene.render.resolution_y * pct) // 2 * 2)
        render.resolution_percentage = 100
        render.fps, render.fps_base = scene.render.fps, scene.render.fps_base
        enc.frame_start, enc.frame_end = 1, len(paths)
        images = render.image_settings
        if hasattr(images, "media_type"):
            images.media_type = 'VIDEO'
        images.file_format = 'FFMPEG'
        ff = render.ffmpeg
        if video == 'PRORES':
            ff.format, ff.codec = 'QUICKTIME', 'PRORES'
        else:
            ff.format, ff.codec = 'MPEG4', 'H264'
            ff.constant_rate_factor = 'HIGH'
        render.filepath = out
        render.use_file_extension = True
        bpy.ops.render.render(animation=True, scene=enc.name)
        named(out)
    finally:
        bpy.data.scenes.remove(enc)


def named(out):
    """Blender names a video <out>0001-0960.mp4: it becomes <out>.mp4 (what the add-on looks for)."""
    import glob
    import re
    made = [p for p in glob.glob(glob.escape(out) + "*") if re.fullmatch(r"\d+-\d+\.(mp4|mov)", p[len(out):])]
    if made:
        newest = max(made, key=os.path.getmtime)
        os.replace(newest, out + os.path.splitext(newest)[1])


def main():
    with open(JOB, encoding="utf-8") as f:
        job = json.load(f)
    try:                                   # started again: keep when it began and how fast it was
        with open(STATUS, encoding="utf-8") as f:
            _state.update({k: v for k, v in json.load(f).items() if k in ("started", "per_frame")})
    except (OSError, ValueError):
        pass
    gpu = _devices(job.get("devices") or {})
    scene = bpy.data.scenes[job["scene"]]
    overrides = job.get("overrides", [])
    if job.get("camera"):
        temp = _camera(scene, job["camera"])
        overrides = [[p, {"object": temp.name}] if p == "camera" else [p, v] for p, v in overrides]
    if gpu and scene.render.engine == 'CYCLES':
        overrides = list(overrides) + [["cycles.device", 'GPU']]
    _variant(job.get("variant") or [])
    _apply(scene, overrides)
    _pin_camera(scene)
    render = scene.render
    render.compositor_device = 'CPU'       # the GPU compositor gets a windowless Blender killed
    render.use_sequencer = False           # the film's strips are in this scene's Video Editor: render 3D
    images = render.image_settings
    if job.get("video") or images.file_format == 'FFMPEG':   # frames (resumable; the video after, if any)
        if hasattr(images, "media_type"):
            images.media_type = 'IMAGE'
        images.file_format = 'PNG'
        images.color_mode = 'RGB'
        images.color_depth = '8'
    render.filepath = job["pattern"]
    render.use_file_extension = True
    render.use_overwrite = False           # frames that are there stay: resume where it stopped
    render.use_placeholder = True
    start, end = job["frames"]
    frames = list(range(start, end + 1, max(1, scene.frame_step)))
    paths = {f: render.frame_path(frame=f) for f in frames}
    for f, path in paths.items():         # a frame that was cut off (empty placeholder): render again
        if os.path.exists(path) and not _done(path):
            os.remove(path)
    names = []
    if job.get("runs"):                    # the timeline's sections: one part (and video) each
        runs = [[f for f in frames if a <= f <= b] for a, b, _n in job["runs"]]
        names = [n for (_a, _b, n), run in zip(job["runs"], runs) if run]
        runs = [r for r in runs if r]
    else:
        runs = split(frames, job.get("parts", 1))
    times = []
    status(state="rendering", parts=len(runs), part=1, total=len(frames),
           done=sum(1 for p in paths.values() if _done(p)), started=_state.get("started", time.time()))

    def after_frame(*_args):
        now = time.time()
        times.append(now)
        per = (times[-1] - times[0]) / (len(times) - 1) if len(times) > 1 else None
        status(done=sum(1 for p in paths.values() if _done(p)), frame=bpy.context.scene.frame_current,
               per_frame=per if per else _state.get("per_frame"))

    bpy.app.handlers.render_write.append(after_frame)
    base = job["out"]
    for n, run in enumerate(runs, 1):
        status(part=n)
        if not all(_done(paths[f]) for f in run):
            times.clear()
            times.append(time.time())
            scene.frame_start, scene.frame_end = run[0], run[-1]
            print("FILM part %d/%d frames %d-%d" % (n, len(runs), run[0], run[-1]), flush=True)
            bpy.ops.render.render(animation=True, scene=scene.name)
        missing = [f for f in run if not _done(paths[f])]
        if missing:
            raise RuntimeError("Frames missing after part %d: %s" % (n, missing[:5]))
        if len(runs) > 1 and job.get("video"):   # every section also as its own video (to watch it)
            status(state="encoding")
            part_name = names[n - 1] if n - 1 < len(names) else "part_%02d" % n
            encode(scene, [paths[f] for f in run], "%s_%s" % (base, part_name), job.get("video") or "MP4")
            status(state="rendering")
    if job.get("video"):                  # a video too (since 4.0 only when asked: the export makes it)
        status(state="encoding")
        encode(scene, [paths[f] for f in frames], base, job["video"])
    status(state="done", done=len(frames), finished=time.time())
    print("FILM done", flush=True)


try:
    main()
except Exception:
    print("FILM failed", traceback.format_exc(), flush=True)
    try:
        status(state="failed", error=traceback.format_exc(limit=2).strip().splitlines()[-1])
    except Exception:
        pass
    sys.exit(3)

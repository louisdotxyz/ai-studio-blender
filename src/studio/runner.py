# SPDX-License-Identifier: GPL-3.0-or-later
"""Works through the render queue.

Every entry is rendered by a second Blender without a window (render_worker.py):
the runner saves a copy of the file with the entry's settings applied (camera at
the stored position, quality, output path; the scene itself is restored right
away), starts `Blender -b` on that copy and watches it from a timer. Your Blender
never renders a queue image itself, so it stays fully usable: cameras, lights,
materials and the chat can be changed while the queue runs without disturbing it.
Removing the entry that is rendering, or cancelling, ends the worker at once.

The queue reads the scene's list live: entries that are moved or removed
during a render take effect right away, entries added during a run are
rendered too. Every entry carries its own quality, mode and the camera as it
was when it was added; finished entries leave the list.
"""

import atexit
import json
import os
import re
import subprocess
import sys
import time

import bpy

from . import core, sync

TICK = 0.2
# After asking the worker to stop: how long until it is killed hard.
KILL_AFTER = 3.0
WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "render_worker.py")
# Cycles: "Sample 16/64", EEVEE: "Rendering 16 / 64 samples"; big Cycles images add " tile 3/9"
_SAMPLE_RE = re.compile(r"(?:Sample|Rendering)\s+(\d+)\s*/\s*(\d+)(?:\s+tile\s+(\d+)/(\d+))?")
DRAFT = {"max_size": 640, "samples": 16}   # the quick first image of an entry (preview)


def prefs(context=None):
    context = context or bpy.context
    from .paths import ADDON
    addon = context.preferences.addons.get(ADDON)
    return getattr(addon.preferences, "studio", None) if addon else None


def settings_from_prefs():
    p = prefs()
    if p is None:
        return "", 50, 50
    return p.output_root, p.low_resolution, p.low_samples_percent


def notify_enabled():
    p = prefs()
    return True if p is None else p.notify


def _applescript_string(text):
    return '"%s"' % text.replace("\\", "\\\\").replace('"', '\\"')


def notify_command(message):
    """macOS notification with sound. None on other systems."""
    if sys.platform != "darwin":
        return None
    script = "display notification %s with title %s sound name %s" % (
        _applescript_string(message), _applescript_string("Render Studio"), _applescript_string("Glass"))
    return ["osascript", "-e", script]


def notify(message):
    cmd = notify_command(message)
    if cmd is None:
        return
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as ex:
        print("Render Studio: notification failed:", ex)


def render_job_running():
    return bpy.app.is_job_running('RENDER')


def tag_redraw():
    wm = bpy.context.window_manager
    if wm is None:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type in {'VIEW_3D', 'STATUSBAR'}:
                area.tag_redraw()


def with_render_window(func):
    """Calls `func`. With the render display set to "Keep User Interface", Blender
    never opens a render window. In that case switch to "New Window" briefly."""
    view = bpy.context.preferences.view
    old = view.render_display_type
    if old == 'NONE':
        view.render_display_type = 'WINDOW'
    try:
        return func()
    finally:
        if view.render_display_type != old:
            view.render_display_type = old


class Worker:
    """The second Blender. It starts without a file (ahead of time when the Queue tab opens,
    the GPU kernels load meanwhile) and keeps your scene loaded between entries as long as the
    scene and the camera stay the same (see _on_depsgraph_update); otherwise it opens a fresh
    copy of the file – a new process only after it was stopped."""

    def __init__(self):
        self.proc = None
        self.generation = -1
        self.camera_key = None
        self.fingerprint = None
        self.content = None
        self.base = ""
        self.idle_since = 0.0
        self.started = 0.0
        self._log = None

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def fits(self, scene, generation, camera_key):
        # The fingerprint and the content too: settings changed from Python, new cameras, undo and
        # some data changes do not always send a depsgraph update. Checked before every render.
        return (self.alive() and self.generation == generation and self.camera_key == camera_key
                and self.fingerprint == scene_fingerprint(scene) and self.content == scene_content(scene))

    def launch(self):
        """Start the empty worker process (nothing to save, Blender does not wait)."""
        if self.alive():
            return
        self.kill()
        cmd = [bpy.app.binary_path, "-b", "--factory-startup", "-noaudio",
               "--python-exit-code", "3", "--python", WORKER_SCRIPT, "--", json.dumps(_device_settings())]
        self._log = open(worker_log_path(), "w", encoding="utf-8")
        self.proc = subprocess.Popen(cmd, stdout=self._log, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
                                     text=True)
        self.started = time.monotonic()
        self.idle_since = time.monotonic()
        log("worker launched")

    def start(self, scene, generation, camera_key):
        """Save a plain copy of the file (the scene itself is not changed) and open it in the worker."""
        self.launch()
        base = os.path.join(jobs_dir(), "base_%d_%d.blend" % (os.getpid(), generation))
        RUN.preparing = True   # the save handlers must not take this copy for the user's save
        try:
            bpy.ops.wm.save_as_mainfile(filepath=base, copy=True, compress=False,
                                        relative_remap=True, check_existing=False)
        finally:
            RUN.preparing = False
        try:
            scripts = bpy.context.preferences.filepaths.use_scripts_auto_execute   # drivers with Python
        except AttributeError:
            scripts = False
        self.send({"load": base, "scripts": bool(scripts)})   # the worker deletes it once it is open
        self.generation, self.camera_key, self.base = generation, camera_key, base
        self.fingerprint = scene_fingerprint(scene)
        self.content = scene_content(scene)
        log("worker loads the scene (state %d, camera %s)" % (generation, camera_key))

    def send(self, job):
        self.proc.stdin.write(json.dumps(job) + "\n")
        self.proc.stdin.flush()
        self.idle_since = 0.0

    def stop(self):
        """Let it end on its own (between jobs), kill it if it does not."""
        if self.alive():
            try:
                self.proc.stdin.write(json.dumps({"quit": True}) + "\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=KILL_AFTER)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
        self.kill()

    def kill(self):
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=KILL_AFTER)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=KILL_AFTER)
                except subprocess.TimeoutExpired:
                    pass
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None
        # Copies it did not get to open any more
        prefix = "base_%d_" % os.getpid()
        for name in os.listdir(jobs_dir()):
            if name.startswith(prefix) and name.endswith(".blend"):
                _remove_file(os.path.join(jobs_dir(), name))
        self.proc, self.base, self.generation, self.camera_key = None, "", -1, None


WORKER = Worker()


def prewarm():
    """Launch the worker ahead of the next entry (Queue tab opened, a render was stopped)."""
    if bpy.app.background or WORKER.alive() or RUN.busy:
        return
    p = prefs()
    if p is not None and not p.auto_render:
        return
    try:
        WORKER.launch()
    except OSError as ex:
        log("worker did not launch: %s" % ex)
        return
    if not bpy.app.timers.is_registered(_idle_timer):
        bpy.app.timers.register(_idle_timer, first_interval=5.0)
_previews = []            # quick renders for Claude: {"camera", "max_size", "samples", "callback"}
_preview_counter = [0]


def request_preview(scene, camera, max_size, samples, callback):
    """Render a quick preview in the worker (Blender stays usable) and call
    callback(path, error) on the main thread when it is done."""
    if camera is None or camera.type != 'CAMERA':
        raise RuntimeError("No camera to render from")
    _previews.append({"camera": camera, "max_size": max_size, "samples": samples, "callback": callback})
    RUN.start(scene)
IDLE_KEEP = 300.0   # seconds a worker stays open after the queue, for the next entry


def _idle_timer():
    """Close a worker nobody needs any more (it holds the scene in memory and on the GPU)."""
    if RUN.busy:
        return 5.0
    if not WORKER.alive():
        WORKER.kill()
        return None
    if not WORKER.idle_since:
        WORKER.idle_since = time.monotonic()
    if time.monotonic() - WORKER.idle_since > IDLE_KEEP:
        log("worker closed (idle)")
        WORKER.stop()
        return None
    return 5.0


# ---------------------------------------------------------------- scene changes
# The worker renders its copy of the file. Any change of yours that matters for the image
# (objects, materials, world, lights, render settings …) bumps the generation, and the next
# entry starts a fresh worker. Moving a camera does not count: every entry carries its own
# camera position.

_generation = [0]
_fingerprints = {}
_FINGERPRINT_PATHS = (
    "render.engine", "render.resolution_x", "render.resolution_y", "render.resolution_percentage",
    "render.film_transparent", "render.use_compositing", "render.pixel_aspect_x", "render.pixel_aspect_y",
    "cycles.samples", "cycles.use_denoising", "cycles.use_adaptive_sampling", "cycles.adaptive_threshold",
    "cycles.max_bounces", "eevee.taa_render_samples", "view_settings.view_transform", "view_settings.look",
    "view_settings.exposure", "view_settings.gamma", "world", "frame_current",
    "render_studio.background_mode", "render_studio.background_color", "render_studio.background_color_top",
    "render_studio.use_gradient", "render_studio.noise_size", "render_studio.noise_intensity",
    "render_studio.look_glow", "render_studio.look_contrast", "render_studio.look_vignette",
)


def scene_fingerprint(scene):
    values = []
    for path in _FINGERPRINT_PATHS:
        try:
            value = scene.path_resolve(path)
        except ValueError:
            value = None
        if isinstance(value, bpy.types.ID):
            value = value.name
        elif hasattr(value, "__len__") and not isinstance(value, str):
            value = tuple(round(v, 5) for v in value)
        values.append(value)
    return tuple(values)


def scene_content(scene):
    """Hash of what the image is made of, apart from the geometry and node details (those send
    depsgraph updates): objects with their data (a new camera must reach the worker), placement,
    visibility, materials and modifiers, plus the material and world names. A camera's place and
    viewport visibility don't count: every entry carries its camera."""
    items = []
    for obj in scene.objects:
        camera = obj.type == 'CAMERA'
        items.append((
            obj.name, obj.type, obj.data.name if obj.data is not None else "",
            obj.parent.name if obj.parent else "", obj.hide_render, False if camera else obj.hide_get(),
            obj.visible_camera, () if camera else tuple(round(v, 5) for row in obj.matrix_world for v in row),
            tuple(s.material.name if s.material else "" for s in obj.material_slots),
            tuple((m.name, m.type, m.show_render) for m in obj.modifiers),
        ))
    items.sort()
    return hash((tuple(items), tuple(sorted(m.name for m in bpy.data.materials)),
                 scene.world.name if scene.world else ""))


def _changed(reason):
    _generation[0] += 1
    if WORKER.alive():
        log("scene changed (%s): next entry starts a fresh worker" % reason)


@bpy.app.handlers.persistent
def _on_depsgraph_update(scene, depsgraph):
    if not WORKER.alive() and not RUN.busy:
        return
    for update in depsgraph.updates:
        data = update.id
        if isinstance(data, bpy.types.Object):
            flags = update.is_updated_geometry or update.is_updated_shading or (
                update.is_updated_transform and data.type != 'CAMERA')
            if flags:
                _changed(data.name)
                return
        elif isinstance(data, bpy.types.Scene):
            original = data.original
            fingerprint = scene_fingerprint(original)
            if _fingerprints.get(original.name) != fingerprint:
                if original.name in _fingerprints:
                    _fingerprints[original.name] = fingerprint
                    _changed("settings of %s" % original.name)
                    return
                _fingerprints[original.name] = fingerprint
        elif isinstance(data, bpy.types.Image) and data.name in {PREVIEW_IMAGE, "Render Result"}:
            continue
        else:
            _changed(data.name)
            return


@bpy.app.handlers.persistent
def _on_undo(*_args):
    if WORKER.alive() or RUN.busy:
        _changed("undo/redo")


# ---------------------------------------------------------------- durations (for "… left")

_durations = {}   # (quality, animation) -> [seconds of finished entries], this session


def _average(key):
    values = _durations.get(key)
    return sum(values[-8:]) / len(values[-8:]) if values else None


def format_time(seconds):
    if seconds is None:
        return ""
    if seconds < 60:
        return "%d s" % max(1, round(seconds))
    return "%d min" % round(seconds / 60)


class Runner:
    def __init__(self):
        self.last_path = ""        # newest saved image (or animation folder), for "View Render"
        self._reset()
        self.message = ""
        # Eine feste Referenz, damit timers.is_registered/unregister sie wiederfinden.
        self.timer = self._timer

    def _reset(self):
        self.busy = False
        self.preparing = False     # True while the worker's copy of the file is saved
        self.scene_name = ""
        self.quality = 'LOW'
        self.animation = False
        self.default_quality = None
        self.default_animation = None
        self.notify = False
        self.processed = set()     # uids of finished/skipped entries
        self.done = 0
        self.discarded = 0         # removed or cancelled, not counted
        self.failed = []
        self.stop_requested = False
        self.sample_factor = 0.0
        self.status = ""           # what the worker is doing, e.g. "preparing scene"
        self.phase = 'START'
        self.current = None        # {"uid", "camera", "path", "frames", "variant", "started", "sampling"}

    # ------------------------------------------------------------------ API

    @property
    def proc(self):
        return WORKER.proc

    @property
    def current_camera(self):
        return self.current["camera"] if self.current else ""

    def _remaining_items(self):
        scene = bpy.data.scenes.get(self.scene_name)
        if scene is None:
            return []
        cur = self.current["uid"] if self.current else None
        return [item for item in scene.render_studio.items
                if item.uid not in self.processed and item.uid != cur and item.camera is not None]

    def _remaining(self):
        return len(self._remaining_items())

    @property
    def total(self):
        return len(self.processed) + self._remaining() + (1 if self.current else 0)

    @property
    def position(self):
        """Number of the entry that renders now (1-based)."""
        return min(len(self.processed) + 1, self.total)

    @property
    def list_progress(self):
        total = self.total
        if not total:
            return 0.0
        return min(1.0, (len(self.processed) + self.render_progress * (1 if self.current else 0)) / total)

    @property
    def render_progress(self):
        if not self.current:
            return 0.0
        if self.animation:
            frames = self.current["frames"]
            if not frames:
                return 0.0
            return min(1.0, len(core.frame_files(self.current["path"])) / frames)
        return self.sample_factor

    def current_left(self):
        """Seconds until the current entry is done (None if unknown)."""
        job = self.current
        if not job:
            return None
        now = time.monotonic()
        progress = self.render_progress
        if job.get("sampling") and progress >= 0.05:
            sampled = now - job["sampling"]
            return sampled * (1 - progress) / progress + 2.0   # + denoising/saving
        average = _average((self.quality, self.animation))
        return max(1.0, average - (now - job["started"])) if average else None

    def total_left(self):
        """Seconds until the whole queue is done (None if unknown)."""
        current = self.current_left()
        if current is None:
            return None
        total = current
        own = None
        if self.current and self.render_progress >= 0.05:
            own = time.monotonic() - self.current["started"] + current
        for item in self._remaining_items():
            animation = item.mode == 'ANIM'
            average = _average((item.quality, animation)) or own
            if average is None:
                return None
            total += average
        return total

    def start(self, scene, quality=None, animation=None):
        """Renders the list. quality/animation are only defaults for entries without
        their own (older files); every entry normally carries its own."""
        if self.busy:
            return   # already running: it reads the list live and takes new entries along
        if not any(item.camera for item in scene.render_studio.items) and not _previews:
            raise RuntimeError("No camera in the queue")
        self._reset()
        self.message = ""
        self.busy = True
        self.scene_name = scene.name
        self.default_quality = quality
        self.default_animation = animation
        self.notify = notify_enabled()
        _fingerprints[scene.name] = scene_fingerprint(scene)
        try:
            if bpy.app.background:
                # Without a window (command line, tests) there is no timer loop.
                while self.busy:
                    self._tick()
                    if self.busy:
                        time.sleep(TICK)
                return
            bpy.app.timers.register(self.timer, first_interval=0.0)
        except Exception:
            self._finish("Error while starting")
            raise

    def cancel(self):
        """Cancel the queue: the running render is stopped right away and not saved."""
        if not self.busy:
            return
        self.stop_requested = True
        log("cancelled")
        self.notify = False
        self._finish("Cancelled, %d saved" % self.done)

    def abort(self):
        """Stop right away, e.g. before another file loads."""
        if self.busy:
            self.notify = False
            self._finish("Cancelled")
        WORKER.kill()

    # ------------------------------------------------------------ internals

    def _timer(self):
        if not self.busy:
            return None
        try:
            self._tick()
        except Exception as ex:
            import traceback
            traceback.print_exc()
            log("ERROR " + traceback.format_exc(limit=4).replace("\n", " | "))
            self._finish("Error: %s" % ex)
        tag_redraw()
        return TICK if self.busy else None

    def _scene(self):
        scene = bpy.data.scenes.get(self.scene_name)
        if scene is None:
            raise RuntimeError("Scene %r not found" % self.scene_name)
        return scene

    def _finish(self, message=None):
        job = self.current
        if job:
            # Stopped in the middle of an entry: the worker goes, never leave half an image.
            WORKER.kill()
            core.remove_output(job["path"])
        for request in ([job["preview"]] if job and job.get("preview") else []) + _previews:
            request["callback"](None, "The render was cancelled")
        _previews.clear()
        scene = bpy.data.scenes.get(self.scene_name)
        if scene is not None:
            purge_deleted(scene)
        if message is None and not self.processed:
            message = ""   # only previews for Claude
        elif message is None:
            message = "%d of %d saved" % (self.done, len(self.processed) - self.discarded)
            if self.failed:
                message += ", failed: " + ", ".join(self.failed)
        # A notification only for several images, a single one is quick to check.
        send = self.notify and len(self.processed) - self.discarded > 1
        self._reset()
        self.message = message
        if send:
            notify(message)
        if WORKER.alive() and not bpy.app.background and not bpy.app.timers.is_registered(_idle_timer):
            bpy.app.timers.register(_idle_timer, first_interval=5.0)
        elif bpy.app.background:
            WORKER.stop()
        else:
            prewarm()   # stopped in the middle: the next entry should not wait for a new Blender
        tag_redraw()

    def _next_item(self, scene):
        for item in scene.render_studio.items:
            if item.uid not in self.processed and item.camera is not None:
                return item
        return None

    @staticmethod
    def _removed(scene, uid):
        return not any(item.uid == uid for item in scene.render_studio.items)

    def _tick(self):
        scene = self._scene()
        if self.phase == 'START':
            if self.stop_requested:
                self._finish("Cancelled, %d saved" % self.done)
                return
            if _previews:   # quick renders for Claude go first
                self._start_preview(scene, _previews.pop(0))
                return
            item = self._next_item(scene)
            if item is None:
                self._finish()
                return
            self._start_render(scene, item)
        elif self.phase == 'RENDER':
            self._watch(scene)

    def _start_render(self, scene, item):
        camera = item.camera
        rq = scene.render_studio
        root, low_res, low_samples = settings_from_prefs()
        name = rq.output_name
        self.quality = item.quality if item.quality in {'LOW', 'HIGH'} else (self.default_quality or rq.quality)
        if item.mode in {'STILL', 'ANIM'}:
            self.animation = item.mode == 'ANIM'
        else:
            self.animation = bool(self.default_animation) if self.default_animation is not None else rq.mode == 'ANIM'
        label = item_label(scene, item)
        if item.variant:
            label = "%s · %s" % (item.variant, label)
        if self.animation:
            path = core.anim_dir(root, camera.name, self.quality, name)
            frames = len(range(scene.frame_start, scene.frame_end + 1, scene.frame_step))
        else:
            path = core.job_path(root, camera.name, self.quality, name, ext=core.still_ext(rq))
            frames = 1
        os.makedirs(os.path.dirname(path) if not self.animation else path, exist_ok=True)
        self.current = {"uid": item.uid, "camera": label, "path": path, "frames": frames,
                        "variant": item.variant, "started": time.monotonic(), "sampling": 0.0}
        self.sample_factor = 0.0
        self.phase = 'RENDER'

        output = core.anim_frame_pattern(path, camera.name, name) if self.animation else path
        try:
            job = self._job(scene, item, camera, output, low_res, low_samples)
        except Exception as ex:
            log("job could not be prepared: %s" % ex)
            self._job_failed(scene, item.uid, label)
            return
        if not self.animation:
            draft = os.path.join(jobs_dir(), "draft_%d.png" % item.uid)
            job["draft"] = dict(DRAFT, path=draft)
            self.current["draft_path"] = draft
        camera_key = camera.name
        reuse = WORKER.fits(scene, _generation[0], camera_key)
        self.status = "rendering" if reuse else ("loading scene" if WORKER.alive() else "starting Blender")
        log("render %s → %s%s" % (label, path, " (same worker)" if reuse else ""))
        try:
            if not reuse:
                WORKER.start(scene, _generation[0], camera_key)
            WORKER.send(job)
        except (OSError, ValueError) as ex:
            log("worker did not start: %s" % ex)
            WORKER.kill()
            self._job_failed(scene, item.uid, label)

    def _start_preview(self, scene, request):
        """A quick render for Claude (render_preview): not in the list, nothing saved for the user."""
        camera = request["camera"]
        _preview_counter[0] += 1
        uid = -_preview_counter[0]
        path = os.path.join(jobs_dir(), "preview_%d.png" % _preview_counter[0])
        render = scene.render
        plan = core.PlanOverrides(scene)
        plan.set(scene, "camera", camera)
        long_side = max(render.resolution_x, render.resolution_y, 1)
        plan.set(scene, "render.resolution_percentage", max(1, min(100, int(request["max_size"] * 100 / long_side))))
        if render.engine == 'CYCLES':
            plan.set(scene, "cycles.samples", min(scene.cycles.samples, request["samples"]))
        elif hasattr(scene, "eevee") and hasattr(scene.eevee, "taa_render_samples"):
            plan.set(scene, "eevee.taa_render_samples", min(scene.eevee.taa_render_samples, request["samples"]))
        plan.set(scene, "render.image_settings.file_format", 'PNG')
        plan.set(scene, "render.image_settings.color_mode", 'RGB')
        plan.set(scene, "render.image_settings.color_depth", '8')
        plan.set(scene, "render.use_file_extension", False)
        plan.set(scene, "render.filepath", path)
        # Always from where the camera is now (the worker may still have an older copy of the scene).
        spec = {"source": camera.name, "matrix": [v for row in camera.matrix_world for v in row],
                "lens": camera.data.lens}
        job = {"uid": uid, "scene": scene.name, "animation": False, "overrides": plan.as_list(),
               "camera": spec, "variant": []}
        self.animation = False
        self.current = {"uid": uid, "camera": "Preview " + camera.name, "path": path, "frames": 1,
                        "variant": "", "started": time.monotonic(), "sampling": 0.0, "preview": request}
        self.sample_factor = 0.0
        self.phase = 'RENDER'
        reuse = WORKER.fits(scene, _generation[0], camera.name)
        self.status = "rendering" if reuse else ("loading scene" if WORKER.alive() else "starting Blender")
        log("preview %s%s" % (camera.name, " (same worker)" if reuse else ""))
        try:
            if not reuse:
                WORKER.start(scene, _generation[0], camera.name)
            WORKER.send(job)
        except (OSError, ValueError) as ex:
            WORKER.kill()
            self.current = None
            self.phase = 'START'
            request["callback"](None, "The render worker did not start: %s" % ex)

    def _job(self, scene, item, camera, output, low_res, low_samples):
        """The entry's settings as data for the worker – your scene is not changed for it."""
        rq = scene.render_studio
        snapshot = None
        camera_spec = None
        if item.has_snapshot:
            snapshot = {"percentage": item.percentage}
            resolution = (item.resolution_x, item.resolution_y)
            # The worker renders from a copy at the stored position: the real camera may have moved on.
            camera_spec = {"source": camera.name, "matrix": list(item.matrix), "lens": item.lens}
        else:
            # Determine before prepare_job: the scene format depends on the currently active camera.
            resolution = sync.effective_resolution(scene, camera)
        plan = core.PlanOverrides(scene)
        core.prepare_job(scene, camera, self.quality, output, rq.background_mode, low_res, low_samples,
                         resolution, snapshot, animation=self.animation, ov=plan)
        variant = []
        if item.variant:
            from .variants import V
            variant = V.plan(item.variant)
        return {"uid": item.uid, "scene": scene.name, "animation": self.animation,
                "overrides": plan.as_list(), "camera": camera_spec, "variant": variant}

    def _watch(self, scene):
        job = self.current
        if self.stop_requested or (not job.get("preview") and self._removed(scene, job["uid"])):
            # Removed from the list (or cancelled) while rendering: stop the worker right away.
            log("stopped: %s" % job["camera"])
            WORKER.kill()
            self._end_render(scene, False, discard=True)
            return
        result = self._read_progress(job["uid"])
        if result and not self.animation and not os.path.exists(job["path"]) \
                and time.monotonic() - job.setdefault("done_at", time.monotonic()) < 3.0:
            return   # "done" came a moment before the image showed up on disk: look again
        if result is not None:
            self._end_render(scene, result)
        elif not WORKER.alive():
            log("worker ended unexpectedly (exit %s)" % (WORKER.proc.poll() if WORKER.proc else "?"))
            self._end_render(scene, False)
            WORKER.kill()

    def _read_progress(self, uid):
        """Reads the worker's output for job `uid`. True/False when it is done, else None."""
        try:
            with open(worker_log_path(), "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(0, f.tell() - 8192))
                tail = f.read().decode("utf-8", "replace")
        except OSError:
            return None
        lines = tail.replace("\r", "\n").splitlines()
        marker = "RS job %s" % uid
        start = max((i for i, line in enumerate(lines) if line.strip() == marker), default=None)
        if start is None:
            return None
        lines = lines[start + 1:]
        job = self.current
        for line in lines:
            if line.startswith("RS done %s" % uid):
                return True
            if line.startswith("RS fail %s" % uid):
                log("worker: " + line[:300])
                return False
        for i, line in enumerate(lines):
            if line.startswith("RS draft-start %s" % uid):
                job["drafting"] = True
            elif line.startswith("RS draft %s" % uid):
                # The draft's samples are not the entry's: only what comes after it counts.
                job["drafting"] = False
                if not job.get("draft") and os.path.isfile(job.get("draft_path", "")):
                    job["draft"] = job["draft_path"]
                    log("draft after %.1f s" % (time.monotonic() - job["started"]))
                    tag_redraw()
                lines = lines[i + 1:]
                break
        if job.get("drafting"):
            self.status = "draft"
            return None
        for line in reversed(lines):
            line = line.strip()
            if line.startswith("RS Sample"):
                match = _SAMPLE_RE.search(line)
                done, total = (int(match.group(1)), int(match.group(2))) if match else (0, 0)
                tile, tiles = (int(match.group(3)), int(match.group(4))) if match and match.group(4) else (0, 1)
                if total and not self.animation:
                    self.sample_factor = min(1.0, (tile + done / total) / max(tiles, 1))
                if done and not job["sampling"]:
                    job["sampling"] = time.monotonic()
                    log("first sample after %.1f s" % (job["sampling"] - job["started"]))
                last_tile = tile + 1 >= tiles
                self.status = "finishing" if total and done >= total and last_tile else "rendering"
                return None
            if line.startswith("RS Status"):
                self.status = _status_label(line[len("RS Status"):].strip())
                return None
        return None

    def _job_failed(self, scene, uid, label):
        self.failed.append(label)
        self.processed.add(uid)
        remove_item(scene, uid)
        self.current = None
        self.phase = 'START'

    def original(self, path, current):
        """The user's value of a scene setting. The queue never changes the scene, so this is
        simply the current value."""
        return current

    def set_scene_camera(self, scene, camera):
        """Switch the user's camera. Safe any time: the queue renders from its own copy."""
        scene.camera = camera

    def _end_render(self, scene, ok, discard=False):
        job = self.current
        self.phase = 'START'
        self.current = None
        _remove_file(job.get("draft_path", ""))
        if job.get("preview"):
            exists = bool(ok) and os.path.exists(job["path"])
            log("preview %s: %s" % ("done" if exists else "failed", job["camera"]))
            job["preview"]["callback"](job["path"] if exists else None,
                                       None if exists else "The preview render failed: " + " | ".join(_log_tail(3)))
            return
        self.processed.add(job["uid"])

        if self.animation:
            ok = ok and len(core.frame_files(job["path"])) > 0
        else:
            ok = ok and os.path.exists(job["path"])
        if discard:
            log("discarded: %s" % job["camera"])
            core.remove_output(job["path"])
            self.discarded += 1
            return
        seconds = time.monotonic() - job["started"]
        log("render ended (%s, %.0f s): %s" % ("ok" if ok else "failed", seconds, job["camera"]))
        remove_item(scene, job["uid"])   # done: the entry leaves the list
        if not ok:
            log("FAILED, worker log: " + " | ".join(_log_tail(8)))
            self.failed.append(job["camera"])
            return
        _durations.setdefault((self.quality, self.animation), []).append(seconds)

        self.done += 1
        self.last_path = job["path"]
        if job.get("variant"):
            from .variants import log_render
            log_render(job["path"], job["variant"])
        for hook in list(on_render_saved):
            try:
                hook(job["path"], job["camera"], self.quality, self.animation)
            except Exception:
                import traceback
                traceback.print_exc()


_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".webp")
_images_cache = {"key": None, "time": 0.0, "paths": []}


def queue_images(scene, limit=60):
    """The queue's images, newest first: <name>_output/preview and HQ (also the newest
    of this session if it was moved away since). Looked up at most every 2 seconds."""
    root, _res, _samples = settings_from_prefs()
    project = core.project_name(name=scene.render_studio.output_name)
    key = (root, project, RUN.last_path)
    now = time.monotonic()
    if _images_cache["key"] == key and now - _images_cache["time"] < 2.0:
        return _images_cache["paths"]
    found = []
    for quality in core.QUALITIES:
        folder = core.quality_dir(root, project, quality)
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for n in names:
            if n.lower().endswith(_IMAGE_EXTS):
                path = os.path.join(folder, n)
                try:
                    found.append((os.path.getmtime(path), path))
                except OSError:
                    pass
    paths = [p for _t, p in sorted(found, reverse=True)][:limit]
    if RUN.last_path and os.path.isfile(RUN.last_path) and RUN.last_path not in paths:
        paths.insert(0, RUN.last_path)
    _images_cache.update(key=key, time=now, paths=paths)
    return paths


def variant_of(path):
    """The variant an image was rendered with (from varianten.txt next to it), or ""."""
    log_path = os.path.join(os.path.dirname(path), "varianten.txt")
    name = os.path.basename(path)
    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                file, _, variant = line.rstrip("\n").partition("\t")
                if file == name:
                    return variant
    except OSError:
        pass
    return ""


def _status_label(status):
    """Cycles/EEVEE steps in a few words."""
    lowered = status.lower()
    if "kernel" in lowered or ("shader" in lowered and "compil" in lowered):
        return "loading GPU kernels"
    if "denois" in lowered:
        return "denoising"
    if "finish" in lowered or "compositing" in lowered:
        return "finishing"
    return "preparing scene"


def jobs_dir():
    from .paths import data_dir
    path = os.path.join(data_dir(), "jobs")
    os.makedirs(path, exist_ok=True)
    return path


def worker_log_path():
    """Output of the render worker of the current (or last) entry."""
    return os.path.join(jobs_dir(), "worker.log")


def _log_tail(lines):
    try:
        with open(worker_log_path(), encoding="utf-8", errors="replace") as f:
            return [l.strip() for l in f.read().replace("\r", "\n").splitlines() if l.strip()][-lines:]
    except OSError:
        return []


def _remove_file(path):
    if path:
        try:
            os.remove(path)
        except OSError:
            pass


def _device_settings():
    """Your Cycles devices (GPU): the worker starts with factory settings and needs them.
    "GPU Only" (default) leaves the CPU out when a GPU is on: on Apple Silicon CPU + GPU
    together took 96 s for an image the GPU alone renders in 39 s."""
    try:
        cycles = bpy.context.preferences.addons["cycles"].preferences
        devices = [[d.type, d.name] for d in cycles.devices if d.use]
    except (KeyError, AttributeError):
        return {}
    p = prefs()
    if (p is None or p.worker_gpu_only) and any(kind != 'CPU' for kind, _name in devices):
        devices = [d for d in devices if d[0] != 'CPU']
    return {"compute_device_type": cycles.compute_device_type, "devices": devices}


PREVIEW_IMAGE = "Render Studio · Last"


def show_in_preview(path):
    """The image datablock that shows the newest render (Queue tab preview, View Render).
    Main thread only, never from a draw function."""
    image = bpy.data.images.get(PREVIEW_IMAGE)
    if image is None:
        image = bpy.data.images.load(path)
        image.name = PREVIEW_IMAGE
    elif os.path.normpath(bpy.path.abspath(image.filepath)) != os.path.normpath(path):
        image.filepath = path
        image.reload()
    else:
        return image
    image.preview_ensure()
    image.preview.reload()
    return image


RUN = Runner()


def log(text):
    """Append a line to <extension user dir>/studio/queue.log (kept short) – for crash hunting."""
    try:
        from .paths import data_dir
        path = os.path.join(data_dir(), "queue.log")
        if os.path.exists(path) and os.path.getsize(path) > 200_000:
            with open(path, encoding="utf-8") as f:
                tail = f.readlines()[-500:]
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(tail)
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), text))
    except OSError:
        pass
# Called with (path, label, quality, animation) after every saved render (e.g. the chat shows it).
on_render_saved = []


def item_label(scene, item):
    """"45 grad 3": the camera and how often it was added (counted per camera)."""
    if item.camera is None:
        return "?"
    return "%s %d" % (item.camera.name, item.number) if item.number > 0 else item.camera.name


TEMP_KEY = "render_studio_temp_camera"


def remove_temp_camera(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        return
    data = obj.data
    bpy.data.objects.remove(obj)
    if data is not None and data.users == 0:
        bpy.data.cameras.remove(data)


def remove_temp_cameras():
    """Leftovers (e.g. Blender was closed during a render)."""
    for obj in [o for o in bpy.data.objects if TEMP_KEY in o]:
        remove_temp_camera(obj.name)


def purge_deleted(scene):
    """Entries whose camera no longer exists leave the list."""
    items = scene.render_studio.items
    for i in reversed(range(len(items))):
        if items[i].camera is None:
            items.remove(i)


def remove_item(scene, uid):
    items = scene.render_studio.items
    for i, item in enumerate(items):
        if item.uid == uid:
            items.remove(i)
            return


@bpy.app.handlers.persistent
def _on_load_pre(*_args):
    RUN.abort()


_HANDLERS = (
    (bpy.app.handlers.load_pre, _on_load_pre),
    (bpy.app.handlers.depsgraph_update_post, _on_depsgraph_update),
    (bpy.app.handlers.undo_post, _on_undo),
    (bpy.app.handlers.redo_post, _on_undo),
)


@atexit.register
def _stop_worker_at_exit():
    """Blender quits: don't leave a render running in the background."""
    proc = WORKER.proc
    if proc is not None and proc.poll() is None:
        proc.terminate()


def register():
    for handlers, func in _HANDLERS:
        if func not in handlers:
            handlers.append(func)


def unregister():
    RUN.abort()
    if bpy.app.timers.is_registered(_idle_timer):
        bpy.app.timers.unregister(_idle_timer)
    for handlers, func in _HANDLERS:
        if func in handlers:
            handlers.remove(func)
    if bpy.app.timers.is_registered(RUN.timer):
        bpy.app.timers.unregister(RUN.timer)

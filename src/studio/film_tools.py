# SPDX-License-Identifier: GPL-3.0-or-later
"""Working on a film's sections before and while they render (Video Editor · Studio · Sections):

- Proof: one quick viewport image per section from its camera, at its middle frame
  (Frames/<section>/.proof.png), shown next to the section: a wrong camera shows before hours of rendering.
- Check: what would go wrong in a render: a section without a camera, two sections sharing one, a strip
  whose length no longer fits; each with a fix.
- Times: how long a frame took per section and quality (Frames/<section>/.times.json); the estimate of
  the next render comes from it (High Quality from Low Quality times the ratio seen in other sections).
- Draft: the section in seconds with Workbench in its own process (<section>_draft_0001.png next to the
  real frames): the strip is there at once and every frame not rendered yet shows its draft.
- Lock: a section keeps its camera (set back when the marker's camera changes).
- Marks: under every section strip a band shows each frame: green High Quality, orange Low Quality,
  grey draft, red not rendered yet (it shows the nearest rendered frame); its state at the top right.
"""

import json
import os
import statistics
import time

import bpy
import bpy.utils.previews
from bpy.props import StringProperty
from bpy.types import Operator

from . import cut, motion

PROOF = ".proof.png"
PROOF_INFO = ".proof.json"
TIMES = ".times.json"
DRAFT_DIR = ".draft"
RATIO = 4.0                 # High Quality takes about this many times as long as Low Quality (no data yet)
BLOCKING = {'NO_CAMERA', 'CAMERA'}
MIN_SAMPLES = 64            # Cycles: fewer samples than this is no High Quality


def _sections(scene):
    return [(n, s, e, m) for n, s, e, m in cut.sections_of(scene)]


def _camera_of(scene, marker):
    cam = marker.camera if marker is not None else scene.camera
    return cam if cam is not None and cam.type == 'CAMERA' else None


# ------------------------------------------------------------------ proof

def proof_path(section):
    return os.path.join(cut.frames_folder(section), PROOF)


def proof_info(section):
    try:
        with open(os.path.join(cut.frames_folder(section), PROOF_INFO), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def make_proofs(scene, names=None):
    """A viewport image from each section's camera at its middle frame (OpenGL, seconds; the scene is set
    back as it was). Returns the sections done."""
    from .tab_queue import _user_resolution
    render = scene.render
    images = render.image_settings
    view = bpy.context.preferences.view
    keep = {"frame": scene.frame_current, "camera": scene.camera, "x": render.resolution_x,
            "y": render.resolution_y, "pct": render.resolution_percentage, "format": images.file_format,
            "media": getattr(images, "media_type", None), "mode": images.color_mode,
            "display": view.render_display_type}
    made = []
    try:
        view.render_display_type = 'NONE'           # no render window popping up
        if keep["media"] is not None:
            images.media_type = 'IMAGE'
        images.file_format = 'PNG'
        images.color_mode = 'RGB'
        render.resolution_percentage = 50
        for name, start, end, marker in _sections(scene):
            if names and name not in names:
                continue
            cam = _camera_of(scene, marker)
            if cam is None:
                continue
            frame = (start + end) // 2
            scene.frame_set(frame)
            scene.camera = cam
            render.resolution_x, render.resolution_y = _user_resolution(scene, cam)
            bpy.ops.render.opengl(write_still=False, view_context=False, sequencer=False)
            result = bpy.data.images.get("Render Result")
            if result is None:
                continue
            folder = cut.frames_folder(name)
            os.makedirs(folder, exist_ok=True)
            result.save_render(proof_path(name), scene=scene)
            with open(os.path.join(folder, PROOF_INFO), "w", encoding="utf-8") as f:
                json.dump({"camera": cam.name, "frame": frame, "when": time.time()}, f)
            made.append(name)
    finally:
        view.render_display_type = keep["display"]
        render.resolution_x, render.resolution_y = keep["x"], keep["y"]
        render.resolution_percentage = keep["pct"]
        if keep["media"] is not None:
            images.media_type = keep["media"]
        images.file_format = keep["format"]
        try:
            images.color_mode = keep["mode"]
        except TypeError:
            pass
        scene.frame_set(keep["frame"])
        scene.camera = keep["camera"]
    return made


_previews = [None]


def proof_icon(section):
    """The proof as an icon for the sidebar (0 = none)."""
    path = proof_path(section)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return 0
    if _previews[0] is None:
        _previews[0] = bpy.utils.previews.new()
    key = "%s|%d" % (path, int(mtime * 1000))
    pcoll = _previews[0]
    if key not in pcoll:
        try:
            pcoll.load(key, path, 'IMAGE')
        except (RuntimeError, KeyError):
            return 0
    return pcoll[key].icon_id


# ------------------------------------------------------------------ check

def rendered_camera(section):
    """The camera the section's frames were rendered from ("" = not known): Blender writes it into every
    frame, so this is what really rendered, whatever the job asked for."""
    hit = _cameras.get(section)
    if hit and time.monotonic() - hit[0] < 2.0:      # panels draw often
        return hit[1]
    name = _rendered_camera(section)
    _cameras[section] = (time.monotonic(), name)
    return name


_cameras = {}


def _rendered_camera(section):
    folder = cut.frames_folder(section)
    stem = cut.frame_stem(section) + "_"
    first = min((n for n in cut.finished_files(folder) if n.startswith(stem) and n.lower().endswith(".png")
                 and not n.startswith(stem + cut.DRAFT)), default=None)
    if first:
        name = cut.png_text(os.path.join(folder, first)).get("Camera", "")
        if name:
            return name
    job = cut._job_of(folder)
    value = cut._overrides(job).get("camera")
    return job.get("camera_name", "") or (value.get("object", "") if isinstance(value, dict) else "")


_stopped = set()


def check_camera(film_entry, st):
    """A film that runs: once its first frame is there, the camera in it must be the section's. If not,
    the film is stopped (hours of frames from a wrong camera help nobody) and the Sections tab says so."""
    section = film_entry.get("section", "")
    key = film_entry.get("dir", "")
    if not section or key in _stopped or film_entry.get("draft") or not st.get("done"):
        return
    scene = bpy.data.scenes.get(film_entry.get("scene", ""))
    found = motion.find(scene, section) if scene is not None else None
    if found is None or found[0].camera is None:
        return
    used = rendered_camera(section)
    if used and used != found[0].camera.name:
        from . import film, runner
        _stopped.add(key)
        film.stop(key)
        runner.log("film %s stopped: rendered from %s, its camera is %s" % (section, used, found[0].camera.name))
        runner.RUN.message = "%s stopped: rendered from %s instead of %s" % (section, used, found[0].camera.name)


def _exposure_keys(scene):
    bag = cut._channelbag(scene)
    curve = next((fc for fc in (bag.fcurves if bag is not None else [])
                  if fc.data_path == "view_settings.exposure"), None)
    return [(k.co[0], k.co[1]) for k in curve.keyframe_points] if curve is not None else []


def problems(scene):
    """[(section, code, text)] code NO_CAMERA (blocks its render), CAMERA (its frames are from another
    camera: render again), SHARED, OUTDATED, EXPOSURE (a fade keyed into the render), SAMPLES (few
    samples for High Quality; section "")."""
    out, users = [], {}
    keys = _exposure_keys(scene)
    if scene.render.engine == 'CYCLES' and scene.cycles.samples < MIN_SAMPLES:
        out.append(("", 'SAMPLES', "High Quality renders with %d samples only (Render tab)" % scene.cycles.samples))
    for name, start, end, marker in _sections(scene):
        inside = {round(v, 4) for f, v in keys if start <= f <= end}
        if len(inside) > 1:
            out.append((name, 'EXPOSURE', "%s: a fade is keyed into the render (exposure); a fade in the cut "
                                          "can still be changed later" % name))
        if marker is not None and marker.camera is not None:
            used = rendered_camera(name)
            st = cut.frame_status(name, start, end)
            if used and st["done"] and used != marker.camera.name and not cut._rendering(name):
                out.append((name, 'CAMERA', "%s was rendered from %s, its camera is %s" % (
                    name, used, marker.camera.name)))
        if marker is None:
            if _camera_of(scene, None) is None:
                out.append((name, 'NO_CAMERA', "No active camera"))
            continue
        cam = marker.camera
        if cam is None:
            out.append((name, 'NO_CAMERA', "%s has no camera" % name))
        elif cam.type != 'CAMERA':
            out.append((name, 'NO_CAMERA', "%s: %s is no camera" % (name, cam.name)))
        else:
            users.setdefault(cam.name, []).append(name)
        try:
            if cut.outdated(scene, name, start, end):
                out.append((name, 'OUTDATED', "%s: the strip has another length than the section" % name))
        except (AttributeError, ReferenceError):
            pass
    items = {c.section: c for c in scene.studio.cut_items}
    for cam, names in users.items():
        for name in names[1:]:
            if not getattr(items.get(name), "shared_ok", False):
                out.append((name, 'SHARED', "%s uses %s like %s" % (name, cam, names[0])))
    return out


def own_camera(scene, section):
    """The section gets its own camera: where its camera (or the active one) is at the section's start.
    The moves of a shared camera stay on it."""
    found = motion.find(scene, section)
    if found is None:
        raise ValueError("No section %s" % section)
    marker, start, _end = found
    from . import cameras
    source = _camera_of(scene, marker) or cameras.active_camera(scene)
    here = scene.frame_current
    scene.frame_set(start)
    try:
        cam = motion.section_camera(scene, motion.plain_name(section), source)
    finally:
        scene.frame_set(here)
    marker.camera = cam
    item = cut.settings(scene, section)
    if item.camera_lock:
        item.camera = cam
    return cam


# ------------------------------------------------------------------ times

_written = {}


def _times_file(folder):
    return os.path.join(folder, TIMES)


def times(section):
    try:
        with open(_times_file(cut.frames_folder(section)), encoding="utf-8") as f:
            data = json.load(f)
        return {k: float(v) for k, v in data.items() if isinstance(v, (int, float)) and v > 0}
    except (OSError, ValueError):
        return {}


def _quality_of(film):
    if film.get("draft"):
        return 'DRAFT'
    quality = film.get("quality", "")
    if quality in {'LOW', 'HIGH'}:
        return quality
    label = film.get("label", "")
    return 'HIGH' if "· HQ" in label else 'LOW' if "· LQ" in label else ""


def record_time(film, st):
    """A running film's seconds per frame, kept per section and quality (written every 20 s at most)."""
    per = st.get("per_frame")
    folder = film.get("folder", "")
    quality = _quality_of(film)
    if not per or not folder or not quality or film.get("label", "").startswith("Export"):
        return
    key = (folder, quality)
    if time.monotonic() - _written.get(key, 0.0) < 20.0:
        return
    _written[key] = time.monotonic()
    path = _times_file(folder)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data[quality] = round(float(per), 2)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def next_quality(scene, name, st):
    """What a render of the section would do now: 'LOW', 'HIGH' or None (High Quality and up to date)."""
    from . import autoquality
    key = autoquality.section_key(name)
    complete = st["done"] == st["total"]
    if complete and st["quality"] == 'HIGH' and autoquality.clean(scene, key):
        return None
    return autoquality.next_quality(scene, key, done=complete)


def estimate(scene, name, quality, st):
    """Seconds the section's next render takes in `quality`, or None (no times yet)."""
    mine = times(name)
    per = mine.get(quality)
    if per is None:
        ratios, same = [], []
        for other, _s, _e, _m in _sections(scene):
            if other == name:
                continue
            t = times(other)
            if 'HIGH' in t and 'LOW' in t:
                ratios.append(t['HIGH'] / t['LOW'])
            if quality in t:
                same.append(t[quality])
        ratio = statistics.median(ratios) if ratios else RATIO
        if quality == 'HIGH' and 'LOW' in mine:
            per = mine['LOW'] * ratio
        elif quality == 'LOW' and 'HIGH' in mine:
            per = mine['HIGH'] / ratio
        elif same:
            per = statistics.median(same)
    if per is None:
        return None
    left = st["total"] - st["done"] if st["quality"] == quality and st["done"] < st["total"] else st["total"]
    return per * left


def estimate_text(scene, name, st):
    """"HQ ~4 h 10" (the next render), "" when unknown, "up to date" when nothing is left."""
    from .runner import format_time
    quality = next_quality(scene, name, st)
    if quality is None:
        return "up to date"
    secs = estimate(scene, name, quality, st)
    tag = "HQ" if quality == 'HIGH' else "LQ"
    return "%s ~%s" % (tag, format_time(secs)) if secs else tag


def total_estimate(scene):
    total, known = 0.0, False
    for name, start, end, _m in _sections(scene):
        st = cut.frame_status(name, start, end)
        quality = next_quality(scene, name, st)
        secs = estimate(scene, name, quality, st) if quality else 0.0
        if secs:
            total += secs
            known = True
    return total if known else None


# ------------------------------------------------------------------ draft

_drafts = []               # (scene name, section) waiting for their draft (one at a time)


def _draft_running():
    from . import film
    return any(f.get("draft") and k in {'RENDERING', 'ENCODING'} for f, _s, k in film.films(fresh=True))


def queue_drafts(scene, names=None, kind='DRAFT'):
    """Drafts (kind 'DRAFT') or preview images ('POSTER') of the sections (all, or `names`), one after
    another in the background. Returns the sections queued."""
    queued = []
    for name, _s, _e, marker in _sections(scene):
        if names and name not in names:
            continue
        if _camera_of(scene, marker) is None or (scene.name, name, kind) in _drafts:
            continue
        _drafts.insert(0, (scene.name, name, kind)) if kind == 'POSTER' else _drafts.append((scene.name, name, kind))
        queued.append(name)
    if queued and not bpy.app.timers.is_registered(_draft_tick):
        bpy.app.timers.register(_draft_tick, first_interval=0.1)
    return queued


def _draft_tick():
    try:
        if _draft_running():
            return 2.0
        while _drafts:
            scene_name, name, kind = _drafts.pop(0)
            scene = bpy.data.scenes.get(scene_name)
            if scene is not None:
                (start_poster if kind == 'POSTER' else start_draft)(scene, name)
                return 2.0
    except Exception as ex:                       # never stop the timer with an error
        print("AI Studio: draft:", ex)
        return 2.0 if _drafts else None
    return None


def start_draft(scene, name):
    """The section's draft in its own process: Workbench, in the size of its rendered frames (else the Low
    Quality size), into <section>_draft_0001.png …"""
    from . import film
    from .runner import settings_from_prefs
    from .tab_queue import _user_resolution
    found = next(((s, e, m) for n, s, e, m in _sections(scene) if n == name), None)
    if found is None:
        raise ValueError("No section %s" % name)
    start, end, marker = found
    cam = _camera_of(scene, marker)
    if cam is None:
        raise ValueError("%s has no camera" % name)
    st = cut.frame_status(name, start, end, fresh=True)
    rx, ry = _user_resolution(scene, cam)
    width = st.get("job_width", 0)
    pct = round(width * 100 / rx) if width and rx else settings_from_prefs()[1]
    pct = max(5, min(100, int(pct)))
    overrides = [["camera", {"object": cam.name}], ["render.engine", "BLENDER_WORKBENCH"],
                 ["render.resolution_x", rx], ["render.resolution_y", ry], ["render.resolution_percentage", pct],
                 ["render.image_settings.media_type", "IMAGE"], ["render.image_settings.file_format", "PNG"],
                 ["render.image_settings.color_mode", "RGB"], ["render.image_settings.color_depth", "8"],
                 ["render.film_transparent", False], ["render.use_motion_blur", False],
                 ["render.use_compositing", False], ["display.render_aa", "FXAA"],
                 ["display.shading.light", "STUDIO"], ["display.shading.color_type", "MATERIAL"]]
    job = {"overrides": overrides, "camera": None, "variant": [], "runs": None, "titles": None,
           "quality": "DRAFT", "section": name}
    stem = "%s_%s" % (cut.frame_stem(name), cut.DRAFT)
    return film.start(scene, job, cut.frames_folder(name), stem, (start, end), 1, None, "%s · Draft" % name,
                      fresh=False, film_dir=DRAFT_DIR, extra={"draft": True})


POSTER_DIR = ".poster"
POSTER_SAMPLES = 16


def start_poster(scene, name):
    """One Low Quality image of the section (its middle frame, few samples, denoised) at full size, in
    its own process: until frames are rendered, its strip shows this image over its whole length."""
    from . import film
    from .tab_queue import _user_resolution
    found = next(((s, e, m) for n, s, e, m in _sections(scene) if n == name), None)
    if found is None:
        raise ValueError("No section %s" % name)
    start, end, marker = found
    cam = _camera_of(scene, marker)
    if cam is None:
        raise ValueError("%s has no camera" % name)
    middle = (start + end) // 2
    folder = cut.frames_folder(name)
    stem = "%s_%s" % (cut.frame_stem(name), cut.POSTER)
    for old in os.listdir(folder) if os.path.isdir(folder) else []:      # one preview image per section
        if old.startswith(stem + "_"):
            try:
                os.remove(os.path.join(folder, old))
            except OSError:
                pass
    rx, ry = _user_resolution(scene, cam)
    overrides = [["render.resolution_x", rx], ["render.resolution_y", ry], ["render.resolution_percentage", 100],
                 ["render.image_settings.media_type", "IMAGE"], ["render.image_settings.file_format", "PNG"],
                 ["render.image_settings.color_mode", "RGB"], ["render.image_settings.color_depth", "8"]]
    if scene.render.engine == 'CYCLES':
        overrides += [["cycles.samples", min(POSTER_SAMPLES, scene.cycles.samples)], ["cycles.use_denoising", True],
                      ["cycles.use_adaptive_sampling", True]]
    elif hasattr(scene, "eevee"):
        overrides += [["eevee.taa_render_samples", 8]]
    overrides.append(["camera", {"object": cam.name}])
    job = {"overrides": overrides, "camera": None, "variant": [], "runs": None, "titles": None,
           "quality": "POSTER", "section": name}
    return film.start(scene, job, folder, stem, (middle, middle), 1, None, "%s · Preview" % name,
                      fresh=False, film_dir=POSTER_DIR, extra={"draft": True})


def queue_posters(scene, names=None):
    """Preview images for the sections that have none (or `names`, made again)."""
    wanted = []
    for name, start, end, marker in _sections(scene):
        if names is not None and name not in names:
            continue
        st = cut.frame_status(name, start, end, fresh=True)
        if names is not None or not (st["poster"] or st["done"] == st["total"]):
            wanted.append(name)
    return queue_drafts(scene, wanted, kind='POSTER') if wanted else []


# ------------------------------------------------------------------ lock

def toggle_lock(scene, section):
    found = motion.find(scene, section)
    item = cut.settings(scene, section)
    item.camera_lock = not item.camera_lock
    if item.camera_lock:
        item.camera = found[0].camera if found is not None else None
    return item.camera_lock


def _keep_locks():
    try:
        for scene in bpy.data.scenes:
            for item in scene.studio.cut_items:
                if item.camera_lock and item.camera is not None:
                    found = motion.find(scene, item.section)
                    if found is not None and found[0].camera != item.camera:
                        found[0].camera = item.camera
    except (AttributeError, ReferenceError, RuntimeError):
        pass
    return 1.0


# ------------------------------------------------------------------ marks in the timeline

COLORS = {'HIGH': (0.33, 0.80, 0.40, 0.95), 'LOW': (1.0, 0.60, 0.16, 0.95), 'DRAFT': (0.62, 0.62, 0.66, 0.95),
          'POSTER': (0.45, 0.55, 0.75, 0.95),
          '': (0.85, 0.22, 0.22, 0.95)}
BAND = (0.07, 0.19)        # the band's bottom and top inside a channel


def strip_codes(scene, strip, sections):
    """[(frame, code)] of a section strip's visible frames, or []."""
    section = cut._get(strip, cut.SECTION_KEY)
    span = sections.get(section)
    if span is None or strip.type != 'IMAGE':
        return []
    st = cut.frame_status(section, span[0], span[1])
    codes = st.get("codes") or []
    if len(strip.elements) != len(codes):
        return []
    first = int(round(strip.frame_start))
    out = []
    for frame in range(int(strip.frame_final_start), int(strip.frame_final_end)):
        index = frame - first
        if 0 <= index < len(codes):
            out.append((frame, codes[index]))
    return out


def label_of(st, rendering=False):
    codes = st.get("codes") or []
    if codes and all(c == 'HIGH' for c in codes):
        text = "HQ"
    elif st.get("done"):
        tag = "HQ" if st.get("quality") == 'HIGH' else "LQ"
        text = tag if st["done"] == st["total"] else "%s %d/%d" % (tag, st["done"], st["total"])
    elif st.get("drafts"):
        text = "Draft"
    elif st.get("poster"):
        text = "Preview"
    else:
        text = ""
    return text + (" · rendering" if rendering and text else "")


def _timeline(context):
    space = context.space_data
    region = context.region
    if space is None or getattr(space, "view_type", "") not in {'SEQUENCER', 'SEQUENCER_PREVIEW'}:
        return None
    if region is None or region.type != 'WINDOW':
        return None
    scene = getattr(context, "sequencer_scene", None) or context.scene
    return scene if scene is not None and scene.sequence_editor is not None else None


def _draw_bands():
    context = bpy.context
    try:
        scene = _timeline(context)
        if scene is None:
            return
        strips = [s for s in cut.ours(scene) if cut._get(s, cut.KIND_KEY) == "SECTION"]
        if not strips:
            return
        import gpu
        from gpu_extras.batch import batch_for_shader
        sections = {n: (s, e) for n, s, e, _m in cut.sections_of(scene)}
        quads = {}
        for strip in strips:
            run = None
            for frame, code in strip_codes(scene, strip, sections) + [(None, None)]:
                if run is not None and (code != run[1] or frame is None):
                    x0, x1, y = run[0], run[2] + 1, strip.channel
                    verts = quads.setdefault(run[1], [])
                    verts += [(x0, y + BAND[0]), (x1, y + BAND[0]), (x1, y + BAND[1]),
                              (x0, y + BAND[0]), (x1, y + BAND[1]), (x0, y + BAND[1])]
                    run = None
                if frame is None:
                    break
                run = [frame, code, frame] if run is None else [run[0], run[1], frame]
        if not quads:
            return
        shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        gpu.state.blend_set('ALPHA')
        for code, verts in quads.items():
            batch = batch_for_shader(shader, 'TRIS', {"pos": verts})
            shader.uniform_float("color", COLORS.get(code, COLORS['']))
            batch.draw(shader)
        gpu.state.blend_set('NONE')
    except Exception as ex:                 # a drawing error must never stop the Video Editor
        print("AI Studio: marks:", ex)


def _draw_labels():
    context = bpy.context
    try:
        scene = _timeline(context)
        if scene is None:
            return
        import blf
        v2d = context.region.view2d
        sections = {n: (s, e) for n, s, e, _m in cut.sections_of(scene)}
        ui = context.preferences.system.ui_scale
        font = 0
        blf.size(font, 11 * ui)
        for strip in cut.ours(scene):
            if cut._get(strip, cut.KIND_KEY) != "SECTION":
                continue
            section = cut._get(strip, cut.SECTION_KEY)
            span = sections.get(section)
            if span is None:
                continue
            text = label_of(cut.frame_status(section, span[0], span[1]), cut._rendering(section))
            if not text:
                continue
            left = v2d.view_to_region(strip.frame_final_start, strip.channel + 0.9, clip=False)
            right = v2d.view_to_region(strip.frame_final_end, strip.channel + 0.9, clip=False)
            w, h = blf.dimensions(font, text)
            if right[0] - left[0] < w + 80 * ui:
                continue
            blf.position(font, right[0] - w - 6 * ui, right[1] - h - 3 * ui, 0)
            blf.color(font, 1.0, 1.0, 1.0, 0.85)
            blf.draw(font, text)
    except Exception as ex:
        print("AI Studio: marks:", ex)


_handlers = []


# ------------------------------------------------------------------ operators

def _scene(context):
    return getattr(context, "sequencer_scene", None) or context.scene


class STUDIO_OT_film_proof(Operator):
    bl_idname = "studio.film_proof"
    bl_label = "Proof"
    bl_description = ("A quick viewport image from each section's camera at its middle frame (seconds): check "
                      "camera and framing before rendering")
    bl_options = {'INTERNAL'}

    section: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        try:
            made = make_proofs(_scene(context), [self.section] if self.section else None)
        except (RuntimeError, OSError, ValueError) as ex:
            self.report({'WARNING'}, "Proof failed: %s" % ex)
            return {'CANCELLED'}
        self.report({'INFO'}, "Proof: %s" % (", ".join(made) or "no section with a camera"))
        return {'FINISHED'}


class STUDIO_OT_film_draft(Operator):
    bl_idname = "studio.film_draft"
    bl_label = "Draft"
    bl_description = ("The sections in seconds with Workbench in the background, straight into the Video Editor: "
                      "timing and camera moves before rendering; rendered frames replace them")
    bl_options = {'INTERNAL'}

    section: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        queued = queue_drafts(_scene(context), [self.section] if self.section else None)
        if not queued:
            self.report({'INFO'}, "No draft: no section with a camera (or already waiting)")
            return {'CANCELLED'}
        self.report({'INFO'}, "Draft: %s" % ", ".join(queued))
        return {'FINISHED'}


FIXES = {'NO_CAMERA': "Own Camera", 'SHARED': "Own Camera", 'OUTDATED': "Update Strip", 'IGNORE': "Fine",
         'CAMERA': "Render Again"}


class STUDIO_OT_film_fix(Operator):
    bl_idname = "studio.film_fix"
    bl_label = "Fix"
    bl_options = {'UNDO', 'INTERNAL'}

    section: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    code: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return {'NO_CAMERA': "A camera of its own for this section, where the active camera is",
                'SHARED': "A camera of its own, where the shared camera is at the section's start (its moves "
                          "stay on the other camera)",
                'OUTDATED': "The strip made again from the section's frames (same place and look)",
                'CAMERA': "Render the section again from its own camera (the frames there go to archiv)",
                'IGNORE': "The shared camera is meant like this: no warning for this section"}.get(props.code, "")

    def execute(self, context):
        scene = _scene(context)
        try:
            if self.code in {'NO_CAMERA', 'SHARED'}:
                cam = own_camera(scene, self.section)
                self.report({'INFO'}, "%s: own camera %s" % (self.section, cam.name))
            elif self.code == 'OUTDATED':
                cut.replace_section(scene, self.section)
            elif self.code == 'CAMERA':
                bpy.ops.studio.add(mode='ANIM', section=self.section, force=True)
            elif self.code == 'IGNORE':
                cut.settings(scene, self.section).shared_ok = True
        except (ValueError, RuntimeError, AttributeError) as ex:
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class STUDIO_OT_section_lock(Operator):
    bl_idname = "studio.section_lock"
    bl_label = "Lock Camera"
    bl_description = "The section keeps its camera (changed on its marker, it is set back). Click again: unlocked"
    bl_options = {'UNDO', 'INTERNAL'}

    section: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        locked = toggle_lock(_scene(context), self.section)
        self.report({'INFO'}, "%s: camera %s" % (self.section, "locked" if locked else "unlocked"))
        return {'FINISHED'}


classes = (STUDIO_OT_film_proof, STUDIO_OT_film_draft, STUDIO_OT_film_fix, STUDIO_OT_section_lock)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    space = bpy.types.SpaceSequenceEditor
    _handlers.append(space.draw_handler_add(_draw_bands, (), 'WINDOW', 'POST_VIEW'))
    _handlers.append(space.draw_handler_add(_draw_labels, (), 'WINDOW', 'POST_PIXEL'))
    if not bpy.app.timers.is_registered(_keep_locks):
        bpy.app.timers.register(_keep_locks, first_interval=1.0, persistent=True)


def unregister():
    for handler in _handlers:
        try:
            bpy.types.SpaceSequenceEditor.draw_handler_remove(handler, 'WINDOW')
        except (ValueError, RuntimeError):
            pass
    _handlers.clear()
    for timer in (_keep_locks, _draft_tick):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    if _previews[0] is not None:
        bpy.utils.previews.remove(_previews[0])
        _previews[0] = None
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

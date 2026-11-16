# SPDX-License-Identifier: GPL-3.0-or-later
"""The film in the Video Editor of its own scene: every section's frames are a strip there.

- Renders: a section renders as frames into animation/Frames/<section>/ (<section>_0001.png …, the
  scene's frame numbers). Low Quality first, High Quality once nothing changed (autoquality.py). A new
  render of a section moves its older frames to animation/archiv/Frames/; a frame or a range rendered
  again replaces only those frames. No video per section: the MP4 is made once, at the end (Export).
- The strips: one image strip per section, in the order of the sections; without holds and crosses each
  strip is where its frames are in the timeline (the Video Editor's frame = the 3D frame). They come in by
  themselves after every render (and fill in while it renders); Low Quality frames are scaled to the
  full size. Between two sections a transition (cut, cross, dip to black or white), after a section a
  hold (its last frame standing still), music under it.
- Our strips carry the section's name (strip["studio_section"]); building again replaces only them, your
  own strips stay. The scene renders the 3D scene (render.use_sequencer off); only the export renders
  the strips.
- In the Video Editor's sidebar (Studio): the sections with their camera, frames and what is rendered,
  Render (all sections that need it), re-rendering a frame or the rest of a strip, a still for labels,
  transitions and holds, music, Export MP4 (in its own process) and an EDL.
"""

import glob
import json
import os
import re
import subprocess
import time

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, StringProperty
from bpy.types import Operator, Panel

from . import motion

WHOLE = "Film"                        # the film without sections: one "section" over the scene's frames
FRAMES = "Frames"
EXPORT = "Export"
SECTION_KEY = "studio_section"        # on our strips: the section
KIND_KEY = "studio_kind"              # SECTION, HOLD, FREEZE, CROSS, DIP, FADE, LOOK, BARS, TITLE, MUSIC
BEAT = "♩"
_EXTS = {'JPEG': ".jpg", 'OPEN_EXR': ".exr", 'OPEN_EXR_MULTILAYER': ".exr", 'TIFF': ".tif", 'WEBP': ".webp"}


# ------------------------------------------------------------------ where the frames are

def _anim_folder():
    from . import core
    from .runner import settings_from_prefs
    root = settings_from_prefs()[0]
    scene = bpy.context.scene
    name = scene.studio.output_name if scene is not None else ""
    names = core.folder_names()
    return os.path.join(core.project_dir(root, core.project_name(None, name)), names['ANIM'])


def video_name(section):
    return re.sub(r'[/\\:*?"<>|]', "_", section).strip() or "Section"


def frames_folder(section):
    return os.path.join(_anim_folder(), FRAMES, video_name(section))


def frame_stem(section):
    return video_name(section)


def archive_folder():
    return os.path.join(_anim_folder(), "archiv", FRAMES)


def cut_folder():
    """Where the export, the EDL and the stills go."""
    return os.path.join(_anim_folder(), EXPORT)


def sections_of(scene):
    """[(name, start, end, marker or None)]: the sections, or the whole film as one."""
    found = [(m.name, s, e, m) for m, s, e in motion.sections(scene)]
    return found or [(WHOLE, scene.frame_start, scene.frame_end, None)]


def _job_of(folder):
    try:
        with open(os.path.join(folder, ".film", "job.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _overrides(job):
    out = {}
    for pair in job.get("overrides") or []:
        try:
            out[pair[0]] = pair[1]
        except (IndexError, TypeError):
            continue
    return out


def frame_names(section, start, end, ext=".png"):
    stem = frame_stem(section)
    return ["%s_%04d%s" % (stem, f, ext) for f in range(start, end + 1)]


_status = {}


def frame_status(section, start, end, fresh=False):
    """What is rendered of a section: {"folder", "names", "done", "total", "quality" ('LOW'/'HIGH'/''),
    "width" (pixels of its frames, 0 = unknown), "job" (a render was started)}. Read again after 1 s."""
    folder = frames_folder(section)
    key = (folder, start, end)
    now = time.monotonic()
    hit = _status.get(key)
    if hit and not fresh and now - hit[0] < 1.0:
        return hit[1]
    job = _job_of(folder)
    ov = _overrides(job)
    ext = ".png" if job.get("video") else _EXTS.get(ov.get("render.image_settings.file_format"), ".png")
    names = frame_names(section, start, end, ext)
    try:
        there = {e.name for e in os.scandir(folder) if e.is_file() and e.stat().st_size > 0}
    except OSError:
        there = set()
    done = sum(1 for n in names if n in there)
    width = 0
    scene = bpy.context.scene
    if job and scene is not None:
        x = ov.get("render.resolution_x", scene.render.resolution_x)
        pct = ov.get("render.resolution_percentage", scene.render.resolution_percentage)
        try:
            width = int(round(float(x) * float(pct) / 100))
        except (TypeError, ValueError):
            width = 0
    out = {"folder": folder, "names": names, "done": done, "total": len(names), "ext": ext,
           "quality": job.get("quality", "") if done else "", "width": width, "job": bool(job)}
    _status[key] = (now, out)
    return out


def status_text(st):
    if not st["done"]:
        return "—"
    quality = {"HIGH": "HQ", "LOW": "LQ"}.get(st["quality"], "")
    return quality if st["done"] == st["total"] else "%s %d/%d" % (quality, st["done"], st["total"])


# ------------------------------------------------------------------ films of 3.x (still shown)

def _quality(folder):
    from . import core
    names = core.folder_names()
    base = os.path.basename(folder.rstrip(os.sep)).lower()
    return 2 if "_%s_" % names['HIGH'].lower() in base + "_" else 1


def _frame_path(pattern, frame):
    path = "%s%04d.png" % (pattern, frame)
    if os.path.isfile(path):
        return path
    hits = glob.glob(glob.escape(pattern) + "*%d.png" % frame)
    return next((h for h in hits if re.fullmatch(r"0*%d" % frame, h[len(pattern):-4])), None)


def films(scene_name):
    """[(when, quality, frames (a, b), pattern)] of the scene's films from before 4.0, newest first."""
    out = []
    for job_path in glob.glob(os.path.join(glob.escape(_anim_folder()), "*", ".film", "job.json")):
        try:
            with open(job_path, encoding="utf-8") as f:
                job = json.load(f)
            if job.get("scene") != scene_name or job.get("cut"):
                continue
            a, b = job["frames"]
            out.append((os.path.getmtime(job_path), _quality(os.path.dirname(os.path.dirname(job_path))),
                        (int(a), int(b)), job["pattern"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(out, key=lambda f: -f[0])


def source(scene_name, start, end, found=None):
    """The frame paths of the best older film of these frames (High before Low, newest first), or None."""
    found = films(scene_name) if found is None else found
    for _when, _q, (a, b), pattern in sorted(found, key=lambda f: (-f[1], -f[0])):
        if a <= start and end <= b:
            paths = [_frame_path(pattern, f) for f in range(start, end + 1)]
            if all(paths):
                return paths
    return None


def section_video(section):
    """A section's video of 3.9 (<animation>/<section>.mp4), or ""."""
    for ext in (".mp4", ".mov"):
        path = os.path.join(_anim_folder(), video_name(section) + ext)
        if os.path.isfile(path):
            return path
    return ""


# ------------------------------------------------------------------ strips

def _strips(sed):
    return sed.strips if hasattr(sed, "strips") else sed.sequences        # an empty collection is falsy


def _all(sed):
    return list(sed.strips_all if hasattr(sed, "strips_all") else sed.sequences_all)


def _get(strip, key, default=""):
    try:
        return strip.get(key, default)
    except (TypeError, AttributeError):
        return default


def _tag(strip, kind, section=""):
    try:
        strip[KIND_KEY] = kind
        strip[SECTION_KEY] = section
    except (TypeError, AttributeError):
        pass


def ours(scene):
    sed = scene.sequence_editor
    return [s for s in _all(sed) if _get(s, KIND_KEY)] if sed is not None else []


def section_strip(scene, section):
    """The section's (first) strip; a clip cut in parts (freeze frame) has several."""
    found = [s for s in ours(scene) if _get(s, SECTION_KEY) == section and _get(s, KIND_KEY) == "SECTION"]
    return min(found, key=lambda s: s.frame_final_start) if found else None


def _effect(strips, name, kind, channel, start, length, **inputs):
    """new_effect across Blender versions (length since 5.0, frame_end before)."""
    try:
        return strips.new_effect(name=name, type=kind, channel=channel, frame_start=start, length=length, **inputs)
    except TypeError:
        return strips.new_effect(name=name, type=kind, channel=channel, frame_start=start,
                                 frame_end=start + length, **inputs)


def _new_image(strips, name, path, channel, start, fit):
    try:
        return strips.new_image(name=name, filepath=path, channel=channel, frame_start=start, fit_method=fit)
    except TypeError:
        return strips.new_image(name=name, filepath=path, channel=channel, frame_start=start)


def _image(strips, name, paths, channel, start, fit='FIT'):
    strip = _new_image(strips, name, paths[0], channel, start, fit)
    for path in paths[1:]:
        strip.elements.append(os.path.basename(path))
    return strip


def _fit(scene, strip, width):
    """Frames of another size (Low Quality) fill the picture: scaled to the scene's width."""
    if width > 0 and hasattr(strip, "transform"):
        scale = scene.render.resolution_x / width
        if abs(strip.transform.scale_x - scale) > 1e-4:
            strip.transform.scale_x = strip.transform.scale_y = scale


def settings(scene, section):
    """The cut settings of a section (made the first time)."""
    items = scene.studio.cut_items
    item = next((c for c in items if c.section == section), None)
    if item is None:
        item = items.add()
        item.section = section
    return item


def _channelbag(block):
    ad = block.animation_data
    if ad is None or ad.action is None:
        return None
    try:
        from bpy_extras import anim_utils
        return anim_utils.action_get_channelbag_for_slot(ad.action, ad.action_slot)
    except (ImportError, AttributeError):
        return ad.action                                 # before Blender 4.4: the action has the curves


def _remove_ours(scene, keep=()):
    sed = scene.sequence_editor
    gone = [s for s in ours(scene) if _get(s, KIND_KEY) not in keep]
    names = {strip.name for strip in gone}
    strips = _strips(sed)
    for strip in sorted(gone, key=lambda s: _get(s, KIND_KEY) not in {"FADE", "CROSS", "DIP"}):
        try:
            strips.remove(strip)
        except (RuntimeError, ReferenceError):
            pass
    bag = _channelbag(scene)
    if bag is not None and names:
        for fc in list(bag.fcurves):
            if fc.data_path.startswith("sequence_editor.") and any('"%s"' % n in fc.data_path for n in names):
                bag.fcurves.remove(fc)
    for marker in list(scene.timeline_markers):    # beat markers of 3.9 (the cut had its own scene)
        if marker.name.startswith(BEAT):
            scene.timeline_markers.remove(marker)


def _sources(scene):
    """[(name, start, end, kind, data)] kind FRAMES (data: the status), OLD (paths), VIDEO (path) or None."""
    out = []
    old = None
    for name, start, end, _marker in sections_of(scene):
        st = frame_status(name, start, end, fresh=True)
        if st["job"] or st["done"]:
            out.append((name, start, end, "FRAMES", st))
            continue
        if old is None:
            old = films(scene.name)
        paths = source(scene.name, start, end, old)
        video = "" if paths else section_video(name)
        out.append((name, start, end, "OLD", paths) if paths else (name, start, end, "VIDEO", video) if video
                   else (name, start, end, None, None))
    return out


def show_in_video_editor(scene):
    """The Video Editing workspace shows this scene (Blender 5: each workspace has its sequencer scene);
    the strips show their names, not their file paths."""
    for space in bpy.data.workspaces:
        if "video" in space.name.lower() and hasattr(space, "sequencer_scene") and space.sequencer_scene is not scene:
            try:
                space.sequencer_scene = scene
            except (AttributeError, TypeError, RuntimeError):
                pass
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type != 'SEQUENCE_EDITOR':
                continue
            for sp in area.spaces:
                overlay = getattr(sp, "timeline_overlay", None) if sp.type == 'SEQUENCE_EDITOR' else None
                if overlay is not None and getattr(overlay, "show_strip_source", False):
                    try:
                        overlay.show_strip_source = False
                    except (AttributeError, TypeError):
                        pass


def _make_section_strip(scene, name, kind, data, channel, at):
    """(strip, paths) of a section: its frames, an older film's frames or a 3.9 video."""
    strips = _strips(scene.sequence_editor)
    if kind == "FRAMES":
        paths = [os.path.join(data["folder"], n) for n in data["names"]]
        strip = _image(strips, name, paths, channel, at, fit='ORIGINAL')
        _fit(scene, strip, data["width"])
    elif kind == "OLD":
        paths = data
        strip = _image(strips, name, paths, channel, at)
    else:
        paths = None
        try:
            strip = strips.new_movie(name=name, filepath=data, channel=channel, frame_start=at, fit_method='FIT')
        except TypeError:
            strip = strips.new_movie(name=name, filepath=data, channel=channel, frame_start=at)
    _tag(strip, "SECTION", name)
    return strip, paths


def _music(scene):
    """The music strip under the film (made again when the file changes)."""
    from . import film_edit
    for strip in [s for s in ours(scene) if _get(s, KIND_KEY) == "MUSIC"]:
        film_edit.remove(scene, strip)
    rq = scene.studio
    music = bpy.path.abspath(rq.cut_music) if rq.cut_music else ""
    if music and os.path.isfile(music):
        span = film_edit.content_range(scene)
        sound = _strips(scene.sequence_editor).new_sound(name="Music", filepath=music, channel=film_edit.TOP + 6,
                                                         frame_start=span[0] if span else scene.frame_start)
        _tag(sound, "MUSIC")


def build(scene):
    """(scene, report): the sections as strips in the scene's own Video Editor (their frames; films and
    videos of 3.x if a section has no frames yet), back to back, then their holds and transitions and the
    music. Replaces our strips (and changes made to them); your own strips stay."""
    from . import film_edit
    sed = scene.sequence_editor or scene.sequence_editor_create()
    scene.render.use_sequencer = False              # F12 and the renders: the 3D scene, not the strips
    show_in_video_editor(scene)
    _remove_ours(scene, keep={"LOOK", "BARS", "TITLE", "FADE"})   # the film's look, bars, titles, fades stay
    strips = _strips(sed)
    found = _sources(scene)
    report = {"sections": [], "missing": [], "stills": [], "scene": scene.name}
    at = found[0][1] if found else scene.frame_start
    for name, start, end, kind, data in found:
        length = end - start + 1
        if kind is None or (kind == "FRAMES" and not data["done"] and not data["job"]):
            report["missing"].append(name)
            at += length
            continue
        item = settings(scene, name)
        strip, paths = _make_section_strip(scene, name, kind, data, 1, at)
        if kind == "FRAMES" and data["done"] < data["total"]:
            report["missing"].append(name)
        last = strip
        if item.hold:
            if paths:
                hold = _image(strips, "%s hold" % name, paths[-1:], 1, strip.frame_final_end,
                              fit='ORIGINAL' if kind == "FRAMES" else 'FIT')
                if kind == "FRAMES":
                    _fit(scene, hold, data["width"])
            else:                                   # only the video: its last image held
                hold = strips.new_movie(name="%s hold" % name, filepath=data, channel=1,
                                        frame_start=strip.frame_final_end)
                hold.frame_offset_start = max(0, hold.frame_duration - 1)
            hold.frame_final_duration = item.hold
            _tag(hold, "HOLD", name)
            last = hold
        report["sections"].append(name)
        at = last.frame_final_end
    for name in report["sections"]:
        item = settings(scene, name)
        film_edit.apply_transition(scene, name, item.transition, item.frames)
    _music(scene)
    film_edit.fit_overlays(scene)
    return scene, report


def _reload(scene):
    """The Video Editor reads the strips' files again (frames rendered again or new while rendering)."""
    for strip in ours(scene):
        try:
            if hasattr(strip, "directory"):
                strip.directory = strip.directory
            elif hasattr(strip, "filepath"):
                strip.filepath = strip.filepath
        except (AttributeError, RuntimeError):
            pass
    wm = bpy.context.window_manager
    for window in (wm.windows if wm else ()):
        for area in window.screen.areas:
            if area.type == 'SEQUENCE_EDITOR':
                try:
                    with bpy.context.temp_override(window=window, area=area, scene=scene):
                        bpy.ops.sequencer.refresh_all()
                except (RuntimeError, TypeError):
                    pass
                return


def outdated(scene, section, start, end):
    """The section's strip has another number of frames than the section now (its length changed)."""
    strip = section_strip(scene, section)
    elements = getattr(strip, "elements", None) if strip is not None else None
    parts = [s for s in ours(scene) if _get(s, SECTION_KEY) == section and _get(s, KIND_KEY) == "SECTION"]
    return elements is not None and len(parts) == 1 and len(elements) != end - start + 1


def sync(scene):
    """After a render: only adds, never rearranges. The first time all strips are made; later the strips
    read their files again and fit their size, and a section without a strip yet comes in at the end.
    Your moves, trims and cuts stay. Returns "built" or "synced"."""
    if not ours(scene):
        build(scene)
        return "built"
    from . import film_edit
    for name, start, _end, kind, data in _sources(scene):
        if section_strip(scene, name) is None:
            if kind is None:
                continue
            span = film_edit.content_range(scene)
            _make_section_strip(scene, name, kind, data, 1, span[1] if span else start)
            film_edit.fit_overlays(scene)
        elif kind == "FRAMES":
            for strip in ours(scene):
                if _get(strip, SECTION_KEY) == name and _get(strip, KIND_KEY) in {"SECTION", "HOLD", "FREEZE"}:
                    _fit(scene, strip, data["width"])
    _reload(scene)
    return "synced"


def replace_section(scene, section):
    """The section's strip made again from its frames (its length changed): same place, same look; the
    strips after it move by the difference."""
    from . import film_edit
    found = next(((n, s, e, k, d) for n, s, e, k, d in _sources(scene) if n == section), None)
    old = section_strip(scene, section)
    if found is None or old is None or found[3] is None:
        raise ValueError("Nothing rendered of %s" % section)
    names = [n for n, _s, _e, _m in sections_of(scene)]
    before = names[names.index(section) - 1] if names.index(section) > 0 else None
    for owner in (before, section):
        if owner:
            film_edit.clear_transition(scene, owner)
    at, channel, length = old.frame_final_start, old.channel, old.frame_final_duration
    free = film_edit._free_channel(scene, at, at + found[2] - found[1] + 1, channel + 1)
    new, _paths = _make_section_strip(scene, section, found[3], found[4], free, at)
    film_edit.copy_look(old, new)
    film_edit.remove(scene, old)
    film_edit.ripple(scene, at + length, new.frame_final_duration - length, skip=(new,))
    new.channel = channel
    for owner in (before, section):
        if owner:
            item = settings(scene, owner)
            film_edit.apply_transition(scene, owner, item.transition, item.frames)
    return new


def refresh(scene):
    """Kept for older calls: the strips take what is rendered now."""
    sync(scene)
    return len(ours(scene))


def _scene_named(name):
    scene = bpy.data.scenes.get(name) if name else bpy.context.scene
    return scene if scene is not None and (scene.timeline_markers or scene.sequence_editor or name) else None


def on_film_done(scene_name=None):
    """A section finished (or started): its frames are in the Video Editor (the strips made the first time)."""
    scene = _scene_named(scene_name)
    if scene is None:
        return
    try:
        sync(scene)
    except (RuntimeError, ReferenceError, OSError, ValueError) as ex:
        print("AI Studio: cut:", ex)


def on_progress(scene_name=None):
    """While a section renders: its new frames show up."""
    scene = _scene_named(scene_name)
    if scene is not None and ours(scene):
        try:
            sync(scene)
        except (RuntimeError, ReferenceError, OSError, ValueError) as ex:
            print("AI Studio: cut:", ex)


_soon = []


def _apply_now():
    from . import film_edit
    for name, section in list(dict.fromkeys(_soon)):
        scene = bpy.data.scenes.get(name)
        if scene is None or not ours(scene):
            continue
        try:
            if section:
                film_edit.apply_section(scene, section)
            else:
                _music(scene)
        except (RuntimeError, ReferenceError, OSError, ValueError, AttributeError) as ex:
            print("AI Studio: cut:", ex)
    _soon.clear()
    return None


def apply_soon(scene, section=None):
    """A transition or hold (section) or the music (None) changed: changed in place in a moment, on the
    strips as they are (several changes at once)."""
    if scene is None or not hasattr(scene, "sequence_editor"):
        return
    _soon.append((scene.name, section))
    if not bpy.app.timers.is_registered(_apply_now):
        bpy.app.timers.register(_apply_now, first_interval=0.3)


# ------------------------------------------------------------------ a strip's frame

def scene_frame(scene, strip, frame=None):
    """(section, its frame in the 3D scene) under `frame` of a section strip (default: the playhead)."""
    section = _get(strip, SECTION_KEY)
    found = next(((s, e) for n, s, e, _m in sections_of(scene) if n == section), None)
    if found is None:
        return section, None
    frame = scene.frame_current if frame is None else frame
    from . import film_edit
    shown = film_edit.frame_of(strip, frame)          # from the file name: <section>_0042.png
    if shown is not None:
        return section, shown
    index = int(frame - strip.frame_start)
    return section, max(found[0], min(found[1], found[0] + index))


def still_for_labels(scene, strip, frame=None):
    """The image of a section strip at `frame` (default: the playhead, else its last frame) as a PNG
    in the export folder: "<section> Standbild.png" (for labels in Keynote). Returns the path."""
    import shutil
    frame = scene.frame_current if frame is None else frame
    elements = list(getattr(strip, "elements", []))
    if not elements:
        raise ValueError("The strip has no images")
    index = int(frame - strip.frame_start) if strip.frame_final_start <= frame < strip.frame_final_end \
        else len(elements) - 1
    index = max(0, min(len(elements) - 1, index))
    source_path = os.path.join(bpy.path.abspath(strip.directory), elements[index].filename)
    if not os.path.isfile(source_path):
        raise ValueError("This frame is not rendered yet")
    os.makedirs(cut_folder(), exist_ok=True)
    name = re.sub(r"[^\w\- ]", "_", _get(strip, SECTION_KEY) or strip.name)
    target = os.path.join(cut_folder(), "%s Standbild%s" % (name, os.path.splitext(source_path)[1]))
    shutil.copyfile(source_path, target)
    return target


def beat_frames(scene):
    fps = scene.render.fps / scene.render.fps_base
    return 60.0 * fps / scene.studio.cut_bpm if scene.studio.cut_bpm > 0 else 0.0


def snap_to_beats(scene):
    """Every section a whole number of beats long (the sections after it move along with their keys)."""
    beat = beat_frames(scene)
    if beat <= 0:
        raise ValueError("Set the BPM first")
    changed = 0
    for marker, start, end in list(motion.sections(scene)):
        found = motion.find(scene, marker.name)
        if found is None:
            continue
        _m, start, end = found
        length = end - start + 1
        beats = max(1, round(length / beat))
        target = max(1, int(round(beats * beat)))
        if target != length:
            motion.set_length(scene, marker.name, length=target)
            changed += 1
    return changed


def strip_range(scene):
    """(first, last) frame of all strips (what the export renders)."""
    sed = scene.sequence_editor
    found = [s for s in _all(sed) if not s.mute] if sed is not None else []
    if not found:
        return None
    return min(s.frame_final_start for s in found), max(s.frame_final_end for s in found) - 1


# ------------------------------------------------------------------ export

def export(scene, path=None):
    """The strips as one video (MP4 H.264 high, or ProRes when that is the animation format) in its own
    Blender process; the older export goes to archiv. Returns (video path, log path)."""
    if not ours(scene):
        build(scene)
    frames = strip_range(scene)
    if frames is None:
        raise ValueError("Nothing rendered yet: render the sections first")
    folder = cut_folder()
    from . import film
    work = os.path.join(folder, film.FILM_DIR)       # like a film: its bar shows the progress
    os.makedirs(work, exist_ok=True)
    path = path or os.path.join(folder, video_name(scene.name))
    prores = scene.studio.anim_format == 'PRORES'
    ext = ".mov" if prores else ".mp4"
    if os.path.isfile(path + ext):
        old = os.path.join(folder, "archiv", "%s %s%s" % (os.path.basename(path), time.strftime("%Y-%m-%d %H%M%S"), ext))
        os.makedirs(os.path.dirname(old), exist_ok=True)
        os.replace(path + ext, old)
    render = scene.render
    images = render.image_settings
    ff = render.ffmpeg
    keep = (render.filepath, images.file_format, getattr(images, "media_type", None), render.use_file_extension,
            render.use_sequencer, scene.frame_start, scene.frame_end, ff.format, ff.codec, ff.audio_codec,
            ff.constant_rate_factor)
    from .runner import RUN
    try:
        if hasattr(images, "media_type"):
            images.media_type = 'VIDEO'
        images.file_format = 'FFMPEG'
        if prores:
            ff.format, ff.codec = 'QUICKTIME', 'PRORES'
        else:
            ff.format, ff.codec, ff.constant_rate_factor = 'MPEG4', 'H264', 'HIGH'
        if any(_get(s, KIND_KEY) == "MUSIC" for s in ours(scene)):
            ff.audio_codec = 'AAC'
        render.filepath = path
        render.use_file_extension = True
        render.use_sequencer = True                   # the copy renders the strips
        RUN.preparing = True
        scene.frame_start, scene.frame_end = frames
        blend = os.path.join(work, "cut.blend")
        bpy.ops.wm.save_as_mainfile(filepath=blend, copy=True, compress=False, relative_remap=True,
                                    check_existing=False)
    finally:
        render.filepath, images.file_format = keep[0], keep[1]
        if keep[2] is not None:
            images.media_type = keep[2]
        render.use_file_extension, render.use_sequencer = keep[3], keep[4]
        scene.frame_start, scene.frame_end = keep[5], keep[6]
        ff.format, ff.codec, ff.audio_codec, ff.constant_rate_factor = keep[7], keep[8], keep[9], keep[10]
        RUN.preparing = False
    with open(os.path.join(work, "job.json"), "w", encoding="utf-8") as f:
        json.dump({"scene": scene.name, "out": path, "frames": list(frames), "cut": True}, f)
    status = os.path.join(work, "status.json")
    total = frames[1] - frames[0] + 1
    log = os.path.join(work, "log.txt")
    with open(log, "w", encoding="utf-8") as f:
        script = (                                         # render with progress, then <path>0001-0108.mp4 → .mp4
            "import bpy, glob, json, os, re, time\n"
            "S, T, P = %r, %d, %r\n"
            "t0 = time.time()\n"
            "def put(**v):\n"
            "    v.update(updated=time.time(), pid=os.getpid(), total=T, parts=1, part=1, started=t0)\n"
            "    json.dump(v, open(S + '.tmp', 'w')); os.replace(S + '.tmp', S)\n"
            "n = [0]\n"
            "def step(*a):\n"
            "    n[0] += 1; put(state='rendering', done=n[0], per_frame=(time.time() - t0) / n[0])\n"
            "bpy.app.handlers.render_write.append(step)\n"
            "put(state='rendering', done=0)\n"
            "bpy.ops.render.render(animation=True)\n"
            "made = [f for f in glob.glob(glob.escape(P) + '*') if re.fullmatch(r'\\d+-\\d+\\.(mp4|mov)', f[len(P):])]\n"
            "if made:\n"
            "    m = max(made, key=os.path.getmtime); os.replace(m, P + os.path.splitext(m)[1])\n"
            "put(state='done', done=T, finished=time.time())\n" % (status, total, path))
        proc = subprocess.Popen([bpy.app.binary_path, "-b", blend, "--factory-startup", "-S", scene.name,
                                 "--python-expr", script],
                                stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    film.register(work, folder, "Export %s" % scene.name, proc.pid)
    return path + ext, log


def edl(scene, path=None):
    """A simple CMX 3600 EDL of our section strips (for other editing programs)."""
    if not ours(scene):
        raise ValueError("Nothing rendered yet")
    fps = max(1, round(scene.render.fps / scene.render.fps_base))

    def tc(frame):
        frame = max(0, int(frame) - 1)
        return "%02d:%02d:%02d:%02d" % (frame // (3600 * fps), frame // (60 * fps) % 60, frame // fps % 60,
                                        frame % fps)
    lines = ["TITLE: %s" % scene.name, "FCM: NON-DROP FRAME", ""]
    clips = sorted((s for s in ours(scene) if _get(s, KIND_KEY) in {"SECTION", "HOLD"}),
                   key=lambda s: s.frame_final_start)
    for n, strip in enumerate(clips, 1):
        lines.append("%03d  AX       V     C        %s %s %s %s" % (
            n, tc(1), tc(1 + strip.frame_final_duration), tc(strip.frame_final_start), tc(strip.frame_final_end)))
        lines.append("* FROM CLIP NAME: %s" % strip.name)
    path = path or os.path.join(cut_folder(), "%s.edl" % video_name(scene.name))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


# ------------------------------------------------------------------ opening it

def open_cut(context, scene):
    """Show the film: the Video Editing workspace (appended once if the file has none) with this scene."""
    window = context.window
    space = bpy.data.workspaces.get("Video Editing")
    if space is None:
        template = os.path.join(bpy.utils.resource_path('LOCAL'), "scripts", "startup",
                                "bl_app_templates_system", "Video_Editing", "startup.blend")
        if os.path.isfile(template):
            try:
                bpy.ops.workspace.append_activate(idname="Video Editing", filepath=template)
                space = bpy.data.workspaces.get("Video Editing")
            except RuntimeError:
                space = None
    if space is not None:
        window.workspace = space
    else:                                          # no template: a sequencer in the biggest editor
        area = max((a for a in window.screen.areas if a.type != 'VIEW_3D'), key=lambda a: a.width * a.height,
                   default=None)
        if area is not None:
            area.type = 'SEQUENCE_EDITOR'
    if window.scene is not scene:
        window.scene = scene
    show_in_video_editor(scene)
    target = space or window.workspace
    for screen in ([window.screen] + ([s for s in getattr(target, "screens", [])] if target else [])):
        for area in screen.areas:                  # the sidebar with the Studio tab open
            if area.type == 'SEQUENCE_EDITOR':
                for sp in area.spaces:
                    if sp.type == 'SEQUENCE_EDITOR' and getattr(sp, "view_type", "") != 'PREVIEW':
                        sp.show_region_ui = True


# ------------------------------------------------------------------ operators

CUT_ACTIONS = (
    ('OPEN', "Video Editor", "The film in the Video Editor of this scene (every rendered section is a strip there)"),
    ('BUILD', "Build Again", "Put all rendered sections in again, back to back, with their transitions, holds and "
                             "music. Our strips are made again (your changes to them go), your own strips stay"),
    ('BACK', "3D View", "Back to the 3D view (Layout)"),
    ('SNAP', "Sections on Beats", "Every section a whole number of beats long (the sections after it move along, "
                                  "with their keys)"),
    ('EXPORT', "Export", "All strips as one video (MP4 H.264, or ProRes when that is the animation format) in its "
                         "own process, into animation/Export"),
    ('EDL', "Export EDL", "A list of the cuts for other editing programs (CMX 3600)"))
STRIP_ACTIONS = (
    ('GO', "3D View", "To the 3D view at this frame of the section (its camera)"),
    ('FRAME', "This Frame", "Render the frame under the playhead again (in the quality of the section's frames)"),
    ('REST', "From Here", "Render this section again from the playhead to its end (the frames before stay)"),
    ('LENGTH', "Take Length", "This strip's length becomes the section's length (the sections after it move "
                              "along with their keys); then render it again"),
    ('UPDATE', "Update Strip", "The section's length changed: its strip is made again from its frames, at the "
                               "same place with the same look (the strips after it move along)"),
    ('STILL', "Still", "The image under the playhead as a PNG in animation/Export, \"<section> Standbild.png\", "
                       "for labels in Keynote"))
TOOL_ACTIONS = (
    ('FADE_IN', "Fade In", "The clip comes from black (Shift: from white); again with 0 frames: removed"),
    ('FADE_OUT', "Fade Out", "The clip goes to black (Shift: to white)"),
    ('FILM_FADE_IN', "Fade In", "The whole film comes from black (Shift: from white)"),
    ('FILM_FADE_OUT', "Fade Out", "The whole film goes to black (Shift: to white)"),
    ('FREEZE', "Freeze", "The image under the playhead stands still for the frames next to it; the rest of the "
                         "clip and everything after it moves along"),
    ('LOOK_ALL', "To All Clips", "This clip's look (brightness, contrast, saturation, warmth) on every clip"),
    ('LOOK_RESET', "Reset", "The clip's look back to neutral"),
    ('FILM_LOOK', "Film Look", "An adjustment strip over the whole film: its look applies to everything under it "
                               "(again: removed)"),
    ('TITLE', "Title", "A text at the playhead (centred, or as a lower third)"))


def _scene(context):
    """The scene the Video Editor shows (Blender 5: the workspace's sequencer scene)."""
    return getattr(context, "sequencer_scene", None) or context.scene


class STUDIO_OT_cut(Operator):
    bl_idname = "studio.cut"
    bl_label = "Film"
    bl_options = {'UNDO', 'INTERNAL'}

    action: EnumProperty(items=CUT_ACTIONS, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return next(i[2] for i in CUT_ACTIONS if i[0] == props.action)

    def invoke(self, context, event):
        if self.action == 'BUILD' and ours(_scene(context)):    # it replaces changes made to our strips
            try:
                return context.window_manager.invoke_confirm(
                    self, event, title="Build the Film Again?", confirm_text="Build Again",
                    message="Our strips are made again from the renders; your changes to them go (your own "
                            "strips stay).")
            except TypeError:
                return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        scene = context.scene if self.action == 'OPEN' else _scene(context)
        try:
            if self.action == 'OPEN':
                sync(scene)
                open_cut(context, scene)
            elif self.action == 'BUILD':
                scene, report = build(scene)
                text = "%d sections" % len(report["sections"])
                if report["missing"]:
                    text += " · not (all) rendered yet: %s" % ", ".join(report["missing"])
                self.report({'INFO'}, text)
            elif self.action == 'BACK':
                space = bpy.data.workspaces.get("Layout")
                if space is not None:
                    context.window.workspace = space
            elif self.action == 'SNAP':
                self.report({'INFO'}, "%d sections on whole beats" % snap_to_beats(scene))
            elif self.action == 'EXPORT':
                video, _log = export(scene)
                self.report({'INFO'}, "Exporting in the background: %s" % video)
            elif self.action == 'EDL':
                self.report({'INFO'}, "EDL: %s" % edl(scene))
        except (ValueError, OSError, RuntimeError) as ex:
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class STUDIO_OT_cut_strip(Operator):
    bl_idname = "studio.cut_strip"
    bl_label = "Strip"
    bl_options = {'UNDO', 'INTERNAL'}

    section: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    action: EnumProperty(items=STRIP_ACTIONS, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return next(i[2] for i in STRIP_ACTIONS if i[0] == props.action)

    def execute(self, context):
        scene = _scene(context)
        found = next(((s, e, m) for n, s, e, m in sections_of(scene) if n == self.section), None)
        sed = scene.sequence_editor
        active = sed.active_strip if sed is not None else None
        strip = active if active is not None and _get(active, SECTION_KEY) == self.section else \
            section_strip(scene, self.section)
        if found is None:
            self.report({'WARNING'}, "No section %s" % self.section)
            return {'CANCELLED'}
        start, end, marker = found
        frame = scene_frame(scene, strip)[1] if strip is not None else None
        try:
            if self.action == 'STILL':
                if strip is None:
                    return {'CANCELLED'}
                self.report({'INFO'}, "Still: %s" % still_for_labels(scene, strip))
            elif self.action == 'GO':
                if frame is not None:
                    scene.frame_current = frame
                if marker is not None and marker.camera is not None:
                    scene.camera = marker.camera
                space = bpy.data.workspaces.get("Layout")
                if space is not None:
                    context.window.workspace = space
            elif self.action in {'FRAME', 'REST'}:
                frame = start if frame is None else frame
                last = frame if self.action == 'FRAME' else end
                quality = frame_status(self.section, start, end)["quality"] or 'DEFAULT'
                with context.temp_override(scene=scene):
                    bpy.ops.studio.add(mode='ANIM', section=self.section, quality=quality,
                                       frames="%d-%d" % (frame, last))
                self.report({'INFO'}, "%s: frames %d–%d render again" % (self.section, frame, last))
            elif self.action == 'UPDATE':
                replace_section(scene, self.section)
            else:
                if strip is None or marker is None:
                    return {'CANCELLED'}
                moved = motion.set_length(scene, self.section, length=strip.frame_final_duration)
                self.report({'INFO'}, "%s: %d frames (%d keys moved along); render it again" % (
                    self.section, strip.frame_final_duration, moved))
        except (ValueError, OSError, RuntimeError) as ex:
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class STUDIO_OT_cut_tool(Operator):
    bl_idname = "studio.cut_tool"
    bl_label = "Film Tool"
    bl_options = {'UNDO', 'INTERNAL'}

    action: EnumProperty(items=TOOL_ACTIONS, options={'HIDDEN', 'SKIP_SAVE'})
    white: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})
    text: StringProperty(name="Text", options={'SKIP_SAVE'})
    place: EnumProperty(name="Place", items=(('CENTER', "Centre", ""), ('LOWER', "Lower Third", "")),
                        options={'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return next(i[2] for i in TOOL_ACTIONS if i[0] == props.action)

    def invoke(self, context, event):
        self.white = event.shift
        if self.action == 'TITLE':
            return context.window_manager.invoke_props_dialog(self)
        return self.execute(context)

    def execute(self, context):
        from . import film_edit
        scene = _scene(context)
        wm = context.window_manager
        sed = scene.sequence_editor
        active = sed.active_strip if sed is not None else None
        color = 'WHITE' if self.white else 'BLACK'
        try:
            if self.action in {'FADE_IN', 'FADE_OUT'}:
                if active is None:
                    raise ValueError("Pick a clip first")
                film_edit.fade(scene, self.action[5:], wm.studio_cut_fade, color, active)
            elif self.action in {'FILM_FADE_IN', 'FILM_FADE_OUT'}:
                film_edit.fade(scene, self.action[10:], wm.studio_cut_fade, color)
            elif self.action == 'FREEZE':
                film_edit.freeze(scene, scene.frame_current, wm.studio_cut_hold)
            elif self.action == 'LOOK_ALL':
                if active is None:
                    raise ValueError("Pick a clip first")
                film_edit.look_to_all(scene, active)
            elif self.action == 'LOOK_RESET':
                if active is not None:
                    film_edit.reset_look(active)
            elif self.action == 'FILM_LOOK':
                found = film_edit.film_look(scene, create=False)
                if found is not None:
                    film_edit.remove(scene, found)
                else:
                    film_edit.film_look(scene)
            elif self.action == 'TITLE':
                if self.text.strip():
                    film_edit.title(scene, self.text.strip(), place=self.place)
        except (ValueError, RuntimeError, AttributeError, TypeError) as ex:
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class STUDIO_OT_cut_go(Operator):
    bl_idname = "studio.cut_go"
    bl_label = "Go to Section"
    bl_description = "The playhead to this section's strip (it becomes the active strip)"
    bl_options = {'INTERNAL'}

    section: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        scene = _scene(context)
        strip = section_strip(scene, self.section)
        found = next(((s, m) for n, s, _e, m in sections_of(scene) if n == self.section), None)
        if strip is not None:
            scene.frame_current = strip.frame_final_start
            for other in _all(scene.sequence_editor):
                other.select = other == strip
            scene.sequence_editor.active_strip = strip
        elif found is not None:
            scene.frame_current = found[0]
        if found is not None and found[1] is not None and found[1].camera is not None:
            scene.camera = found[1].camera
        return {'FINISHED'}


class STUDIO_OT_cut_settings(Operator):
    bl_idname = "studio.cut_settings"
    bl_label = "Transition"
    bl_description = "A transition into the next section and a hold after this one"
    bl_options = {'UNDO', 'INTERNAL'}

    section: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        settings(_scene(context), self.section)
        return {'FINISHED'}


# ------------------------------------------------------------------ the sidebar of the Video Editor

def _rendering(section, fresh=False):
    """A film of this file renders the section now."""
    from . import film
    folder = os.path.normcase(frames_folder(section))
    return any(os.path.normcase(f.get("folder", "")) == folder and k in {'RENDERING', 'ENCODING'} and film.mine(f)
               for f, _s, k in film.films(fresh=fresh))


def _draw_look(layout, strip):
    col = layout.column(align=True)
    row = col.row(align=True)
    row.prop(strip, "studio_brightness", text="Brightness", slider=True)
    row.prop(strip, "studio_contrast", text="Contrast", slider=True)
    row = col.row(align=True)
    row.prop(strip, "studio_saturation", text="Saturation", slider=True)
    row.prop(strip, "studio_warmth", text="Warmth", slider=True)


def _draw_clip(layout, scene, context):
    """The active strip: render again, its look, fades, freeze, still."""
    sed = scene.sequence_editor
    strip = sed.active_strip if sed is not None else None
    if strip is None or _get(strip, KIND_KEY) in {"BARS", "MUSIC", "DIP", "CROSS", "FADE"} or \
            strip.type not in {'IMAGE', 'MOVIE', 'ADJUSTMENT', 'TEXT'}:
        return
    section = _get(strip, SECTION_KEY)
    box = layout.box()
    head = box.row(align=True)
    head.label(text=section or strip.name, icon='SEQUENCE', translate=False)
    if section and strip.type == 'IMAGE':
        shown = scene_frame(scene, strip)[1]
        sub = head.row()
        sub.alignment = 'RIGHT'
        sub.active = False
        sub.label(text="3D frame %s" % shown if shown is not None else "", translate=False)
    if section and _get(strip, KIND_KEY) == "SECTION":
        row = box.row(align=True)
        row.label(text="Render again:")
        for action, icon in (('FRAME', 'IMAGE_DATA'), ('REST', 'FRAME_NEXT')):
            op = row.operator(STUDIO_OT_cut_strip.bl_idname, icon=icon,
                              text=next(i[1] for i in STRIP_ACTIONS if i[0] == action))
            op.action, op.section = action, section
    if strip.type in {'IMAGE', 'MOVIE', 'ADJUSTMENT'}:
        _draw_look(box, strip)
        row = box.row(align=True)
        row.operator(STUDIO_OT_cut_tool.bl_idname, text="To All Clips", icon='DUPLICATE').action = 'LOOK_ALL'
        row.operator(STUDIO_OT_cut_tool.bl_idname, text="", icon='LOOP_BACK').action = 'LOOK_RESET'
    wm = context.window_manager
    row = box.row(align=True)
    row.operator(STUDIO_OT_cut_tool.bl_idname, text="In", icon='TRIA_RIGHT_BAR').action = 'FADE_IN'
    row.operator(STUDIO_OT_cut_tool.bl_idname, text="Out", icon='TRIA_LEFT_BAR').action = 'FADE_OUT'
    row.prop(wm, "studio_cut_fade", text="Fade")
    if strip.type == 'IMAGE':
        row = box.row(align=True)
        row.operator(STUDIO_OT_cut_tool.bl_idname, text="Freeze Here", icon='PAUSE').action = 'FREEZE'
        row.prop(wm, "studio_cut_hold", text="Frames")
    if section:
        row = box.row(align=True)
        for action, icon in (('STILL', 'OUTPUT'), ('LENGTH', 'ARROW_LEFTRIGHT'), ('GO', 'VIEW3D')):
            op = row.operator(STUDIO_OT_cut_strip.bl_idname, icon=icon,
                              text=next(i[1] for i in STRIP_ACTIONS if i[0] == action))
            op.action, op.section = action, section


def _draw_sections(layout, scene):
    from . import autoquality
    from .ui_common import section
    box, head, _open = section(layout, "vse_sections", "Sections", 'MARKER_HLT')
    head.operator("studio.section", text="", icon='ADD', emboss=False).action = 'ADD'
    if not _open:
        return
    frame = scene.frame_current
    sed = scene.sequence_editor
    active = sed.active_strip if sed is not None else None
    found = sections_of(scene)
    for n, (name, start, end, marker) in enumerate(found):
        st = frame_status(name, start, end)
        strip = section_strip(scene, name)
        here = (strip is not None and active is not None and _get(active, SECTION_KEY) == name) or \
            (strip is None and start <= frame <= end)
        card = box.box() if here else box.column()
        col = card.column(align=True)
        row = col.row(align=True)                    # ▶ name · state · render
        row.operator(STUDIO_OT_cut_go.bl_idname, text="", icon='PLAY' if here else 'TRIA_RIGHT',
                     emboss=here).section = name
        if marker is not None:
            row.prop(marker, "name", text="")
        else:
            row.label(text=name, translate=False)
        state = row.row(align=True)
        state.ui_units_x = 3.4
        rendering = _rendering(name)
        state.label(text=status_text(st), icon='SORTTIME' if rendering else 'NONE', translate=False)
        if outdated(scene, name, start, end):
            fix = row.operator(STUDIO_OT_cut_strip.bl_idname, text="", icon='FILE_REFRESH')
            fix.action, fix.section = 'UPDATE', name
        clean = autoquality.clean(scene, autoquality.section_key(name)) and st["done"] == st["total"]
        op = row.operator("studio.add", text="", icon='CHECKMARK' if clean and st["quality"] == 'HIGH'
                          else 'RENDER_ANIMATION')
        op.mode, op.section = 'ANIM', name
        row = col.row(align=True)                    # camera · frames · seconds
        if marker is not None:
            row.prop(marker, "camera", text="")
            edit = row.operator("studio.section_edit", text="%d–%d · %.1f s" % (
                start, end, motion.seconds(scene, end - start + 1)), emboss=False, translate=False)
            edit.name = name
        else:
            sub = row.row()
            sub.active = False
            sub.label(text="%d–%d · %.1f s" % (start, end, motion.seconds(scene, end - start + 1)),
                      translate=False)
        if n == len(found) - 1 or marker is None:
            continue
        item = next((c for c in scene.studio.cut_items if c.section == name), None)
        row = box.row(align=True)                    # between two sections: transition · hold
        row.label(text="", icon='SORT_ASC')
        if item is None:
            row.operator(STUDIO_OT_cut_settings.bl_idname, text="Cut", emboss=False).section = name
            continue
        row.prop(item, "transition", text="")
        sub = row.row(align=True)
        sub.active = item.transition != 'CUT'
        sub.prop(item, "frames", text="")
        row.prop(item, "hold", text="Hold")


def _draw_film(layout, scene, context):
    """The whole film: its look, letterbox, fades, a title."""
    from . import film_edit
    from .ui_common import section
    box, _head, is_open = section(layout, "vse_film", "Film Look", 'COLOR', default_open=False)
    if not is_open:
        return
    look = film_edit.film_look(scene, create=False)
    row = box.row(align=True)
    row.operator(STUDIO_OT_cut_tool.bl_idname, text="Look over Everything", icon='CHECKBOX_HLT' if look else
                 'CHECKBOX_DEHLT', depress=look is not None).action = 'FILM_LOOK'
    if look is not None:
        _draw_look(box, look)
    wm = context.window_manager
    row = box.row(align=True)
    row.label(text="Letterbox")
    row.prop(wm, "studio_cut_ratio", text="")
    row = box.row(align=True)
    row.operator(STUDIO_OT_cut_tool.bl_idname, text="In", icon='TRIA_RIGHT_BAR').action = 'FILM_FADE_IN'
    row.operator(STUDIO_OT_cut_tool.bl_idname, text="Out", icon='TRIA_LEFT_BAR').action = 'FILM_FADE_OUT'
    row.prop(wm, "studio_cut_fade", text="Fade")
    box.operator(STUDIO_OT_cut_tool.bl_idname, text="Title at the Playhead", icon='FONT_DATA').action = 'TITLE'


class STUDIO_PT_cut(Panel):
    """The film's place: render, the clip under the playhead, the sections, the film's look, music, export."""
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Studio"
    bl_label = "Film"
    bl_options = {'HIDE_HEADER'}

    def draw(self, context):
        layout = self.layout
        scene = _scene(context)
        if scene is not context.scene and not scene.timeline_markers and context.scene.timeline_markers:
            col = layout.column()
            col.label(text="The film is in %s" % context.scene.name, icon='INFO', translate=False)
            col.operator(STUDIO_OT_cut.bl_idname, text="Show it Here", icon='SEQUENCE').action = 'OPEN'
            return
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("studio.anim_preview", text="Check", icon='PLAY')
        row.operator("studio.add", text="Render", icon='RENDER_ANIMATION').mode = 'ANIM'
        row.operator(STUDIO_OT_cut.bl_idname, text="Export", icon='EXPORT').action = 'EXPORT'
        from . import film
        film.draw(layout)

        _draw_clip(layout, scene, context)
        _draw_sections(layout, scene)
        _draw_film(layout, scene, context)

        from .ui_common import section
        box, _head, is_open = section(layout, "vse_music", "Music", 'SOUND', default_open=False)
        if is_open:
            box.prop(scene.studio, "cut_music", text="")
            row = box.row(align=True)
            row.prop(scene.studio, "cut_bpm")
            row.operator(STUDIO_OT_cut.bl_idname, text="On Beats", icon='SNAP_ON').action = 'SNAP'

        row = layout.row(align=True)
        span = strip_range(scene)
        info = row.row()
        info.active = False
        info.label(text="Strips %d–%d · %.1f s" % (span[0], span[1], motion.seconds(scene, span[1] - span[0] + 1))
                   if span else "Nothing rendered yet", translate=False)
        row.operator(STUDIO_OT_cut.bl_idname, text="", icon='FILE_REFRESH', emboss=False).action = 'BUILD'
        row.operator(STUDIO_OT_cut.bl_idname, text="", icon='TEXT', emboss=False).action = 'EDL'
        row.operator(STUDIO_OT_cut.bl_idname, text="", icon='VIEW3D', emboss=False).action = 'BACK'


def _ratio_changed(wm, context):
    from . import film_edit
    scene = _scene(context)
    try:
        film_edit.letterbox(scene, wm.studio_cut_ratio)
    except (ValueError, RuntimeError, AttributeError) as ex:
        print("AI Studio: letterbox:", ex)


OLD_SOURCE_KEY = "studio_cut_source"    # 3.9: the cut had its own scene ("Schnitt") with this key


def _migrate():
    """A file from 3.9: the Video Editor showed the cut's own scene; now it shows the film's scene with the
    strips in it (the old cut scene stays, it can be deleted)."""
    for space in bpy.data.workspaces:
        old = getattr(space, "sequencer_scene", None)
        main = bpy.data.scenes.get(old.get(OLD_SOURCE_KEY, "")) if old is not None else None
        if main is None or main is old:
            continue
        try:
            sync(main)
            space.sequencer_scene = main
            print("AI Studio: the film of %s is now in its own Video Editor (%s can be deleted)" % (main.name,
                                                                                                old.name))
        except (RuntimeError, ReferenceError, OSError, ValueError, AttributeError) as ex:
            print("AI Studio: cut:", ex)
    return None


@bpy.app.handlers.persistent
def _loaded(_dummy=None):
    if not bpy.app.timers.is_registered(_migrate):
        bpy.app.timers.register(_migrate, first_interval=2.0)


classes = (STUDIO_OT_cut, STUDIO_OT_cut_strip, STUDIO_OT_cut_tool, STUDIO_OT_cut_go, STUDIO_OT_cut_settings,
           STUDIO_PT_cut)


def register():
    from . import film_edit
    film_edit.register()
    for cls in classes:
        bpy.utils.register_class(cls)
    wm = bpy.types.WindowManager
    wm.studio_cut_fade = IntProperty(name="Fade", description="Frames of a fade", default=12, min=0, max=480,
                                     options={'SKIP_SAVE'})
    wm.studio_cut_hold = IntProperty(name="Freeze", description="Frames the image stands still", default=24, min=1,
                                     max=1200, options={'SKIP_SAVE'})
    wm.studio_cut_ratio = EnumProperty(name="Letterbox", items=film_edit.RATIOS, default='0', update=_ratio_changed,
                                       options={'SKIP_SAVE'})
    if _loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_loaded)
    _loaded()


def unregister():
    if _loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_loaded)
    for timer in (_apply_now, _migrate):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    for prop in ("studio_cut_fade", "studio_cut_hold", "studio_cut_ratio"):
        if hasattr(bpy.types.WindowManager, prop):
            delattr(bpy.types.WindowManager, prop)
    from . import film_edit
    film_edit.unregister()

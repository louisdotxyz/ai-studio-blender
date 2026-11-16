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


SETTLE = 3.0     # seconds a frame file must be unchanged before the Video Editor reads it


def finished_files(folder):
    """Names of the frame files that are done: not empty (a placeholder of the film) and not being written."""
    now = time.time()
    try:
        return {e.name for e in os.scandir(folder) if e.is_file() and e.name[:1] != "."
                and e.stat().st_size > 0 and now - e.stat().st_mtime > SETTLE}
    except OSError:
        return set()


def shown_names(names, there):
    """The file each frame of a strip shows: its own when it is done, else the nearest done frame
    before it (after it at the start). Blender's Video Editor crashes on frame files that are empty or
    still being written, so a strip never points at one. None when no frame is done."""
    done = [i for i, n in enumerate(names) if n in there]
    if not done:
        return None
    out, last = [], names[done[0]]
    for n in names:
        if n in there:
            last = n
        out.append(last)
    return out


DRAFT = "draft"     # a draft's frames: <section>_draft_0001.png next to the real ones
POSTER = "poster"   # one Low Quality image of the section: <section>_poster_0119.png (its middle frame)


def draft_names(section, start, end):
    stem = frame_stem(section)
    return ["%s_%s_%04d.png" % (stem, DRAFT, f) for f in range(start, end + 1)]


_widths = {}


def png_width(path):
    """The width of a PNG from its header (cached per file and change), 0 if it is no PNG."""
    try:
        st = os.stat(path)
    except OSError:
        return 0
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in _widths:
        width = 0
        try:
            with open(path, "rb") as f:
                head = f.read(24)
            if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
                width = int.from_bytes(head[16:20], "big")
        except OSError:
            pass
        if len(_widths) > 20000:
            _widths.clear()
        _widths[key] = width
    return _widths[key]


_texts = {}


def png_text(path):
    """What Blender wrote into a rendered PNG (its tEXt chunks): "Camera", "RenderTime", "Hostname",
    "Frame" … Cached per file and change; {} if it is no PNG. Only reads the file (safe in a thread)."""
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in _texts:
        out = {}
        try:
            with open(path, "rb") as f:
                if f.read(8) == b"\x89PNG\r\n\x1a\n":
                    while True:
                        head = f.read(8)
                        if len(head) < 8:
                            break
                        size, kind = int.from_bytes(head[:4], "big"), head[4:]
                        if kind in (b"IDAT", b"IEND") or size > 1 << 20:
                            break
                        data = f.read(size)
                        f.read(4)
                        if kind == b"tEXt":
                            name, _, value = data.partition(b"\0")
                            out[name.decode("latin-1")] = value.decode("latin-1")
        except OSError:
            pass
        if len(_texts) > 20000:
            _texts.clear()
        _texts[key] = out
    return _texts[key]


def render_seconds(text):
    """"01:02.35" (Blender's RenderTime) in seconds; 0.0 if it is none."""
    try:
        secs = 0.0
        for part in text.split(":"):
            secs = secs * 60 + float(part)
        return secs
    except (ValueError, AttributeError):
        return 0.0


def fill_gaps(base):
    """Every None takes the nearest file before it (after it at the start); None when all are None."""
    known = [b for b in base if b is not None]
    if not known:
        return None
    out, last = [], known[0]
    for b in base:
        if b is not None:
            last = b
        out.append(last)
    return out


def frame_status(section, start, end, fresh=False):
    """What is rendered of a section: {"folder", "names", "done", "total", "quality" ('LOW'/'HIGH'/''),
    "width" (pixels of the frames the strip shows, 0 = unknown), "job" (a render was started), "drafts"
    (draft frames shown), "codes" (per frame 'HIGH' / 'LOW' / 'DRAFT' / ''), "shown" (the file each
    frame of the strip shows)}. Read again after 1 s.
    A frame not rendered yet shows its draft when the draft has the size of the real frames (one strip
    has one scale), else the nearest rendered frame."""
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
    drafts = draft_names(section, start, end)
    there = finished_files(folder)
    real = [n in there for n in names]
    done = sum(real)
    job_width = base_x = 0
    scene = bpy.context.scene
    if job and scene is not None:
        x = ov.get("render.resolution_x", scene.render.resolution_x)
        pct = ov.get("render.resolution_percentage", scene.render.resolution_percentage)
        try:
            job_width = int(round(float(x) * float(pct) / 100))
            base_x = int(x)
        except (TypeError, ValueError):
            job_width = base_x = 0
    first_real = next((n for n, r in zip(names, real) if r), None)
    real_width = (png_width(os.path.join(folder, first_real)) if first_real else 0) or job_width
    first_draft = next((d for d in drafts if d in there), None)
    draft_width = png_width(os.path.join(folder, first_draft)) if first_draft else 0
    use_drafts = bool(first_draft) and (not done or abs(draft_width - real_width) <= 1)
    poster = max((n for n in there if n.startswith("%s_%s_" % (frame_stem(section), POSTER))), default=None)
    poster_width = png_width(os.path.join(folder, poster)) if poster else 0
    use_poster = bool(poster) and (not done or abs(poster_width - real_width) <= 1) and \
        (not use_drafts or abs(poster_width - draft_width) <= 1)
    quality = job.get("quality", "") if done else ""
    codes, base = [], []
    for n, d, r in zip(names, drafts, real):
        if r:
            code = quality or 'LOW'
            w = png_width(os.path.join(folder, n)) if ext == ".png" else 0
            if w and job_width and abs(w - job_width) > 1:     # rendered by an earlier job in another size
                code = 'HIGH' if base_x and w >= base_x * 0.98 else 'LOW'
            codes.append(code)
            base.append(n)
        elif use_drafts and d in there:
            codes.append('DRAFT')
            base.append(d)
        elif use_poster:                             # not rendered yet: the section's preview image
            codes.append('POSTER')
            base.append(poster)
        else:
            codes.append('')
            base.append(None)
    out = {"folder": folder, "names": names, "done": done, "total": len(names), "ext": ext,
           "quality": quality, "width": real_width if done else (draft_width if use_drafts else
                                                                 poster_width if use_poster else job_width),
           "poster": poster if use_poster else "",
           "job": bool(job), "job_width": job_width, "drafts": codes.count('DRAFT'), "codes": codes,
           "shown": fill_gaps(base)}
    _status[key] = (now, out)
    return out


def status_text(st):
    if not st["done"]:
        return "Draft" if st.get("drafts") else "Preview" if st.get("poster") else "—"
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


MARKS = {'HIGH': 'COLOR_04', 'LOW': 'COLOR_02', 'DRAFT': 'COLOR_09'}   # green, orange, grey (draft, preview)


def mark(strip, st):
    """A section strip's colour: green when all its frames are High Quality, orange when rendered (Low
    Quality or not all yet), grey with drafts only."""
    codes = st.get("codes") or []
    tag = MARKS['HIGH'] if codes and all(c == 'HIGH' for c in codes) else \
        MARKS['LOW'] if st.get("done") else MARKS['DRAFT']
    try:
        if strip.color_tag != tag:
            strip.color_tag = tag
    except (AttributeError, TypeError):
        pass


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
        if st["job"] or st["done"] or st["drafts"] or st["poster"]:
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
        if not data["shown"]:
            return None, None                      # nothing done yet: it comes with its first frames
        paths = [os.path.join(data["folder"], n) for n in data["shown"]]
        strip = _image(strips, name, paths, channel, at, fit='ORIGINAL')
        _fit(scene, strip, data["width"])
        mark(strip, data)
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
        strip, paths = _make_section_strip(scene, name, kind, data, 1, at) if kind else (None, None)
        if strip is None:
            report["missing"].append(name)
            at += length
            continue
        item = settings(scene, name)
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


def _place_for(scene, name, length, start):
    """Where a section's new strip goes: right after the strips of the section before it (the sections
    after it make room), else right before the section after it, else at its own frames."""
    from . import film_edit
    names = [n for n, _s, _e, _m in sections_of(scene)]
    index = names.index(name) if name in names else 0

    def strips_of(section):
        return [s for s in ours(scene) if _get(s, SECTION_KEY) == section
                and _get(s, KIND_KEY) in {"SECTION", "HOLD", "FREEZE"}]
    later = [s for n in names[index + 1:] for s in strips_of(n)]
    for before in reversed(names[:index]):
        found = strips_of(before)
        if found:
            at = max(int(s.frame_final_end) for s in found)
            first = min((int(s.frame_final_start) for s in later), default=None)
            if first is not None and first < at + length:
                film_edit.ripple(scene, first, at + length - first)
            return at
    if later:
        return min(int(s.frame_final_start) for s in later) - length
    return start


def sync(scene):
    """After a render: only adds. The first time all strips are made; later the strips read their files
    again and fit their size, and a section without a strip yet comes in at its place in the film (after
    the section before it). Your moves, trims and cuts stay. Returns "built" or "synced"."""
    if not ours(scene):
        build(scene)
        return "built"
    from . import film_edit
    for name, start, _end, kind, data in _sources(scene):
        if section_strip(scene, name) is None:
            if kind is None:
                continue
            if kind == "FRAMES" and not data["shown"]:
                continue                            # nothing rendered yet: it comes with its first frames
            _make_section_strip(scene, name, kind, data, 1, _place_for(scene, name, _end - start + 1, start))
            names = [n for n, _s, _e, _m in sections_of(scene)]
            before = names[names.index(name) - 1] if names.index(name) > 0 else None
            for owner in (before, name):            # the transition into it, and its own
                if owner and section_strip(scene, owner) is not None:
                    try:
                        film_edit.clear_transition(scene, owner)
                        item = settings(scene, owner)
                        film_edit.apply_transition(scene, owner, item.transition, item.frames)
                    except (RuntimeError, ValueError, AttributeError) as ex:
                        print("AI Studio: cut: transition of %s: %s" % (owner, ex))
            film_edit.fit_overlays(scene)
        elif kind == "FRAMES":
            for strip in ours(scene):
                if _get(strip, SECTION_KEY) == name and _get(strip, KIND_KEY) in {"SECTION", "HOLD", "FREEZE"}:
                    _fit(scene, strip, data["width"])
                    if _get(strip, KIND_KEY) == "SECTION" and data["shown"]:
                        _point_to(strip, data["names"], data["shown"])
                        mark(strip, data)
    repair(scene)
    _reload(scene)
    return "synced"


def _point_to(strip, names, shown):
    """A section strip's frames as they are now: each points at its own file once it is done (a strip
    with one element per frame of its section; strips cut into parts keep theirs)."""
    elements = list(getattr(strip, "elements", []))
    if len(elements) != len(names):
        return
    for element, wanted in zip(elements, shown):
        if element.filename != wanted:
            element.filename = wanted


def repair(scene):
    """Our image strips never point at a frame file that is empty or missing (Blender's Video Editor
    crashes drawing one): such frames show the nearest done frame; a strip without any is removed
    (it comes again with its first frames)."""
    from . import film_edit
    fixed = 0
    for strip in [s for s in ours(scene) if s.type == 'IMAGE']:
        folder = bpy.path.abspath(strip.directory)
        there = finished_files(folder)
        names = [e.filename for e in strip.elements]
        if all(n in there for n in names):
            continue
        shown = shown_names(names, there)
        if shown is None:
            film_edit.remove(scene, strip)
        else:
            for element, name in zip(strip.elements, shown):
                if element.filename != name:
                    element.filename = name
        fixed += 1
    return fixed


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

VIDEO_FORMATS = {     # export format → (container, codec, extension, audio codec)
    'MP4': ('MPEG4', 'H264', ".mp4", 'AAC'), 'H265': ('MPEG4', 'H265', ".mp4", 'AAC'),
    'PRORES': ('QUICKTIME', 'PRORES', ".mov", 'AAC'), 'WEBM': ('WEBM', 'WEBM', ".webm", 'OPUS')}


def _set(owner, attr, value):
    """Set an option if this Blender has it (codecs differ between versions)."""
    try:
        setattr(owner, attr, value)
        return True
    except (AttributeError, TypeError, ValueError):
        return False


def export_range(scene):
    """(first, last) frame the export renders: all strips, the preview range or the active clip."""
    rq = scene.studio
    choice = getattr(rq, "cut_export_range", 'ALL')
    if choice == 'PREVIEW' and scene.use_preview_range:
        return scene.frame_preview_start, scene.frame_preview_end
    sed = scene.sequence_editor
    active = sed.active_strip if sed is not None else None
    if choice == 'CLIP' and active is not None:
        return int(active.frame_final_start), int(active.frame_final_end) - 1
    return strip_range(scene)


def export_target(scene):
    """(path without extension, extension) of the export with its settings now."""
    rq = scene.studio
    fmt = getattr(rq, "cut_export_format", 'MP4')
    name = video_name(getattr(rq, "cut_export_name", "").strip() or scene.name)
    if fmt == 'PNG':
        return os.path.join(cut_folder(), name, name + "_"), ".png"
    return os.path.join(cut_folder(), name), VIDEO_FORMATS[fmt][2]


def export(scene, path=None):
    """The strips as one video (or PNG frames) with the export settings of the Video Editor's Export tab
    (format, quality, size, range, music) in its own Blender process; the older export of that name goes
    to archiv. Returns (video path or frames folder, log path)."""
    if not ours(scene):
        build(scene)
    frames = export_range(scene)
    if frames is None:
        raise ValueError("Nothing rendered yet: render the sections first")
    rq = scene.studio
    fmt = getattr(rq, "cut_export_format", 'MP4')
    folder = cut_folder()
    from . import film
    work = os.path.join(folder, film.FILM_DIR)       # like a film: its bar shows the progress
    os.makedirs(work, exist_ok=True)
    target, ext = export_target(scene)
    path = path or target
    if fmt == 'PNG':
        frames_dir = os.path.dirname(path)
        if os.path.isdir(frames_dir) and os.listdir(frames_dir):
            old = os.path.join(folder, "archiv", "%s %s" % (os.path.basename(frames_dir), time.strftime("%Y-%m-%d %H%M%S")))
            os.makedirs(os.path.dirname(old), exist_ok=True)
            os.replace(frames_dir, old)
        os.makedirs(frames_dir, exist_ok=True)
    elif os.path.isfile(path + ext):
        old = os.path.join(folder, "archiv", "%s %s%s" % (os.path.basename(path), time.strftime("%Y-%m-%d %H%M%S"), ext))
        os.makedirs(os.path.dirname(old), exist_ok=True)
        os.replace(path + ext, old)
    render = scene.render
    images = render.image_settings
    ff = render.ffmpeg
    keep = (render.filepath, images.file_format, getattr(images, "media_type", None), render.use_file_extension,
            render.use_sequencer, scene.frame_start, scene.frame_end, ff.format, ff.codec, ff.audio_codec,
            ff.constant_rate_factor, render.resolution_percentage, images.color_mode,
            getattr(ff, "ffmpeg_prores_profile", None))
    muted = []
    from .runner import RUN
    try:
        if fmt == 'PNG':
            _set(images, "media_type", 'IMAGE')
            images.file_format = 'PNG'
            images.color_mode = 'RGB'
        else:
            container, codec, _ext, audio = VIDEO_FORMATS[fmt]
            _set(images, "media_type", 'VIDEO')
            images.file_format = 'FFMPEG'
            ff.format = container
            if not _set(ff, "codec", codec):
                ff.format, ff.codec = 'MPEG4', 'H264'
            if fmt == 'PRORES':
                _set(ff, "ffmpeg_prores_profile", 'HQ')
            else:
                _set(ff, "constant_rate_factor", getattr(rq, "cut_export_quality", 'HIGH'))
            music = [s for s in ours(scene) if _get(s, KIND_KEY) == "MUSIC"]
            if music and getattr(rq, "cut_export_music", True):
                if not _set(ff, "audio_codec", audio):
                    _set(ff, "audio_codec", 'AAC')
            else:
                _set(ff, "audio_codec", 'NONE')
                for strip in music:                       # no music: muted in the copy only
                    if not strip.mute:
                        strip.mute = True
                        muted.append(strip)
        render.resolution_percentage = int(getattr(rq, "cut_export_size", '100'))
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
            _set(images, "media_type", keep[2])
            images.file_format = keep[1]
        render.use_file_extension, render.use_sequencer = keep[3], keep[4]
        scene.frame_start, scene.frame_end = keep[5], keep[6]
        _set(ff, "format", keep[7])
        _set(ff, "codec", keep[8])
        _set(ff, "audio_codec", keep[9])
        _set(ff, "constant_rate_factor", keep[10])
        render.resolution_percentage = keep[11]
        _set(images, "color_mode", keep[12])
        if keep[13] is not None:
            _set(ff, "ffmpeg_prores_profile", keep[13])
        for strip in muted:
            strip.mute = False
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
            "made = [f for f in glob.glob(glob.escape(P) + '*') if re.fullmatch(r'\\d+-\\d+\\.(mp4|mov|webm)', f[len(P):])]\n"
            "if made:\n"
            "    m = max(made, key=os.path.getmtime); os.replace(m, P + os.path.splitext(m)[1])\n"
            "put(state='done', done=T, finished=time.time())\n" % (status, total, path))
        proc = subprocess.Popen([bpy.app.binary_path, "-b", blend, "--factory-startup", "-S", scene.name,
                                 "--python-expr", script],
                                stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    film.register_process(work, folder, "Export %s" % scene.name, proc.pid)
    return (os.path.dirname(path) if fmt == 'PNG' else path + ext), log


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
    space = bpy.data.workspaces.get("Video Editing") or bpy.data.workspaces.get("Video") or next(
        (w for w in bpy.data.workspaces if "video" in w.name.lower()), None)       # also when renamed
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
    from . import film, renderfarm
    folder = os.path.normcase(frames_folder(section))
    if renderfarm.renders_section(folder):
        return True
    return any(os.path.normcase(f.get("folder", "")) == folder and k in {'RENDERING', 'ENCODING'} and film.mine(f)
               and not f.get("draft") for f, _s, k in film.films(fresh=fresh))


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
    try:
        scenes = list(bpy.data.scenes)       # (not while the add-on registers)
    except AttributeError:
        scenes = []
    for scene in scenes:                     # before the first redraw: no strip on an empty frame file
        try:
            if scene.sequence_editor is not None and repair(scene):
                print("AI Studio: %s: strips on unfinished frames repaired" % scene.name)
        except (RuntimeError, ReferenceError, AttributeError) as ex:
            print("AI Studio: repair:", ex)
    if not bpy.app.timers.is_registered(_migrate):
        bpy.app.timers.register(_migrate, first_interval=2.0)


classes = (STUDIO_OT_cut, STUDIO_OT_cut_strip, STUDIO_OT_cut_tool, STUDIO_OT_cut_go, STUDIO_OT_cut_settings)


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
    from . import cut_panel, film_tools
    cut_panel.register()
    film_tools.register()
    if _loaded not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_loaded)
    _loaded()


def unregister():
    if _loaded in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_loaded)
    for timer in (_apply_now, _migrate):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    from . import cut_panel, film_tools
    film_tools.unregister()
    cut_panel.unregister()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    for prop in ("studio_cut_fade", "studio_cut_hold", "studio_cut_ratio"):
        if hasattr(bpy.types.WindowManager, prop):
            delattr(bpy.types.WindowManager, prop)
    from . import film_edit
    film_edit.unregister()

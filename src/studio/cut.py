# SPDX-License-Identifier: GPL-3.0-or-later
"""The cut: the rendered sections one after the other in a scene of their own ("Schnitt", same fps and
size), edited in Blender's Video Sequencer.

- Build: every section's newest film (High Quality before Low, the frames of the film's PNG
  sequence) becomes a strip, in the order of the sections; between two sections a transition (cut,
  cross, dip to black or white, n frames); after a section a hold (its last image standing still,
  also saved as a PNG); music under it, beat markers from the BPM.
- Our strips carry the section's name (strip["studio_section"]); building again replaces only them,
  your own strips stay. When a section is rendered again, its strips take the new frames (they keep
  where and how long they are).
- In the sequencer's sidebar (Studio): a strip's section (go there, render again, take the strip's
  length as the section's length), the transitions and holds, music and BPM, export.
- Export: the cut as MP4 (H.264, high) in its own Blender process; the 3D file is not touched.
"""

import glob
import json
import os
import re
import shutil
import subprocess

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator, Panel

from . import motion

CUT_SCENE = "Schnitt"
SOURCE_KEY = "studio_cut_source"      # on the cut scene: the scene it was made from
SECTION_KEY = "studio_section"        # on our strips: the section
KIND_KEY = "studio_kind"              # SECTION, HOLD, FADE, MUSIC
BEAT = "♩"


# ------------------------------------------------------------------ where the films are

def _anim_folder():
    from . import core
    from .runner import settings_from_prefs
    root = settings_from_prefs()[0]
    scene = bpy.context.scene
    name = scene.studio.output_name if scene is not None else ""
    names = core.folder_names()
    return os.path.join(core.project_dir(root, core.project_name(None, name)), names['ANIM'])


def cut_folder():
    return os.path.join(_anim_folder(), CUT_SCENE)


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
    """[(when, quality, frames (a, b), pattern)] of every film of the scene, newest first."""
    out = []
    for job_path in glob.glob(os.path.join(glob.escape(_anim_folder()), "*", ".film", "job.json")):
        try:
            with open(job_path, encoding="utf-8") as f:
                job = json.load(f)
            if job.get("scene") != scene_name:
                continue
            a, b = job["frames"]
            out.append((os.path.getmtime(job_path), _quality(os.path.dirname(os.path.dirname(job_path))),
                        (int(a), int(b)), job["pattern"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return sorted(out, key=lambda f: -f[0])


def source(scene_name, start, end, found=None):
    """The frame paths of the best film of these frames (High before Low, newest first), or None."""
    found = films(scene_name) if found is None else found
    for _when, _q, (a, b), pattern in sorted(found, key=lambda f: (-f[1], -f[0])):
        if a <= start and end <= b:
            paths = [_frame_path(pattern, f) for f in range(start, end + 1)]
            if all(paths):
                return paths
    return None


# ------------------------------------------------------------------ the cut scene

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


def ours(cut):
    sed = cut.sequence_editor
    return [s for s in _all(sed) if _get(s, KIND_KEY)] if sed is not None else []


def cut_scene(scene=None, create=False):
    """The cut scene of a scene (or the cut scene itself)."""
    scene = scene or bpy.context.scene
    if scene.get(SOURCE_KEY):
        return scene
    cut = next((s for s in bpy.data.scenes if s.get(SOURCE_KEY) == scene.name), None)
    if cut is None and create:
        cut = _editing_scene(scene)
        if cut is None:
            name = CUT_SCENE if CUT_SCENE not in bpy.data.scenes else "%s %s" % (CUT_SCENE, scene.name)
            cut = bpy.data.scenes.new(name)
        cut[SOURCE_KEY] = scene.name
    return cut


def _editing_scene(scene):
    """A scene the user already edits in: the one open in a Video Editing window, else one named like a
    cut ("Schnitt", "schnitt", "Cut", "Edit") – not the film's own scene, not another film's cut."""
    free = [s for s in bpy.data.scenes if s is not scene and not s.get(SOURCE_KEY)]
    wm = bpy.context.window_manager
    for window in (wm.windows if wm else ()):
        if "video" in window.workspace.name.lower() and window.scene in free:
            return window.scene
    for s in free:
        if s.name.lower().split(".")[0].strip() in {"schnitt", "cut", "edit", "video", "video editing"}:
            return s
    return None


def source_scene(cut):
    return bpy.data.scenes.get(cut.get(SOURCE_KEY, "")) if cut is not None else None


def _effect(strips, name, kind, channel, start, length, **inputs):
    """new_effect across Blender versions (length since 5.0, frame_end before)."""
    try:
        return strips.new_effect(name=name, type=kind, channel=channel, frame_start=start, length=length, **inputs)
    except TypeError:
        return strips.new_effect(name=name, type=kind, channel=channel, frame_start=start,
                                 frame_end=start + length, **inputs)


def _image(strips, name, paths, channel, start):
    strip = strips.new_image(name=name, filepath=paths[0], channel=channel, frame_start=start)
    for path in paths[1:]:
        strip.elements.append(os.path.basename(path))
    return strip


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


def _remove_ours(cut):
    sed = cut.sequence_editor
    names = set()
    for strip in ours(cut):
        names.add(strip.name)
    strips = _strips(sed)
    for strip in sorted(ours(cut), key=lambda s: _get(s, KIND_KEY) not in {"FADE", "CROSS"}):
        try:
            strips.remove(strip)
        except (RuntimeError, ReferenceError):
            pass
    bag = _channelbag(cut)
    if bag is not None:
        for fc in list(bag.fcurves):
            if any('"%s"' % n in fc.data_path for n in names):
                bag.fcurves.remove(fc)
    for marker in list(cut.timeline_markers):
        if marker.name.startswith(BEAT):
            cut.timeline_markers.remove(marker)


def build(scene):
    """(cut scene, report): the sections as strips with transitions, holds and music."""
    cut = cut_scene(scene, create=True)
    render, out = scene.render, cut.render
    pct = render.resolution_percentage / 100
    out.resolution_x = max(2, round(render.resolution_x * pct) // 2 * 2)
    out.resolution_y = max(2, round(render.resolution_y * pct) // 2 * 2)
    out.resolution_percentage = 100
    out.fps, out.fps_base = render.fps, render.fps_base
    sed = cut.sequence_editor or cut.sequence_editor_create()
    _remove_ours(cut)
    strips = _strips(sed)
    found = films(scene.name)
    report = {"sections": [], "missing": [], "stills": []}
    at, previous, previous_item, placed = 1, None, None, 0
    for marker, start, end in motion.sections(scene):
        paths = source(scene.name, start, end, found)
        if paths is None:
            report["missing"].append(marker.name)
            continue
        item = settings(scene, marker.name)
        if previous is not None and previous_item.transition == 'CROSS':
            at -= min(previous_item.frames, previous.frame_final_duration, len(paths))
        channel = 1 + placed % 2
        placed += 1
        strip = _image(strips, marker.name, paths, channel, at)
        _tag(strip, "SECTION", marker.name)
        if previous is not None and previous_item.transition == 'CROSS':
            overlap = previous.frame_final_end - strip.frame_final_start
            if overlap > 0:
                fx = _effect(strips, "%s ×" % marker.name, 'CROSS', 3, strip.frame_final_start, overlap,
                             input1=previous, input2=strip)
                _tag(fx, "CROSS", marker.name)
        if previous is not None and previous_item.transition in {'BLACK', 'WHITE'}:
            _dip(cut, strips, previous_item, strip.frame_final_start, marker.name)
        last = strip
        if item.hold:
            hold = _image(strips, "%s hold" % marker.name, paths[-1:], channel, strip.frame_final_end)
            hold.frame_final_duration = item.hold
            _tag(hold, "HOLD", marker.name)
            os.makedirs(cut_folder(), exist_ok=True)
            still = os.path.join(cut_folder(), "%s_still.png" % re.sub(r"[^\w\- ]", "_", marker.name))
            shutil.copyfile(paths[-1], still)
            report["stills"].append(still)
            last = hold
        report["sections"].append(marker.name)
        at = last.frame_final_end
        previous, previous_item = last, item
    rq = scene.studio
    music = bpy.path.abspath(rq.cut_music) if rq.cut_music else ""
    if music and os.path.isfile(music):
        sound = strips.new_sound(name="Music", filepath=music, channel=6, frame_start=1)
        _tag(sound, "MUSIC")
    cut.frame_start, cut.frame_end = 1, max(1, at - 1)
    if rq.cut_bpm > 0:
        beat = beat_frames(scene)
        frame, k = 1.0, 1
        while frame <= cut.frame_end:
            cut.timeline_markers.new("%s%d" % (BEAT, k), frame=int(round(frame)))
            frame += beat
            k += 1
    return cut, report


def _dip(cut, strips, item, cut_at, section):
    """A colour over the boundary, fading in to it and out after it."""
    half = max(1, item.frames // 2)
    color = _effect(strips, "%s dip" % section, 'COLOR', 4, cut_at - half, 2 * half)
    color.color = (1.0, 1.0, 1.0) if item.transition == 'WHITE' else (0.0, 0.0, 0.0)
    color.blend_type = 'ALPHA_OVER'
    _tag(color, "FADE", section)
    for frame, alpha in ((cut_at - half, 0.0), (cut_at, 1.0), (cut_at + half, 0.0)):
        color.blend_alpha = alpha
        color.keyframe_insert("blend_alpha", frame=frame)


def refresh(scene):
    """Rendered again: our strips take the newest frames of their section (where and how long they are
    stays). Returns how many strips changed."""
    cut = cut_scene(scene)
    if cut is None or cut.sequence_editor is None:
        return 0
    found = films(scene.name)
    changed = 0
    for marker, start, end in motion.sections(scene):
        paths = source(scene.name, start, end, found)
        if not paths:
            continue
        folder = os.path.dirname(paths[0]) + os.sep
        for strip in ours(cut):
            if _get(strip, SECTION_KEY) == marker.name and _get(strip, KIND_KEY) in {"SECTION", "HOLD"}:
                if bpy.path.abspath(strip.directory) != folder:
                    strip.directory = folder
                    names = [os.path.basename(p) for p in (paths if _get(strip, KIND_KEY) == "SECTION"
                                                           else paths[-1:])]
                    for element, name in zip(strip.elements, names):
                        element.filename = name
                    changed += 1
    _reload(cut)
    return changed


def _reload(cut):
    """The sequencer reads the strips' files again (a section rendered again under the same name)."""
    for strip in ours(cut):
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
                    with bpy.context.temp_override(window=window, area=area, scene=cut):
                        bpy.ops.sequencer.refresh_all()
                except (RuntimeError, TypeError):
                    pass
                return


def still_for_labels(cut, strip, frame=None):
    """The image of a section strip at `frame` (default: the playhead, else its last frame) as a PNG
    in the cut folder: "<section> Standbild.png" (for labels in Keynote). Returns the path."""
    frame = cut.frame_current if frame is None else frame
    elements = list(strip.elements)
    if not elements:
        raise ValueError("The strip has no images")
    index = int(frame - strip.frame_start) if strip.frame_final_start <= frame < strip.frame_final_end \
        else len(elements) - 1
    index = max(0, min(len(elements) - 1, index))
    source_path = os.path.join(bpy.path.abspath(strip.directory), elements[index].filename)
    os.makedirs(cut_folder(), exist_ok=True)
    name = re.sub(r"[^\w\- ]", "_", _get(strip, SECTION_KEY) or strip.name)
    target = os.path.join(cut_folder(), "%s Standbild.png" % name)
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


def export(scene, path=None):
    """The cut as MP4 (H.264, high) in its own Blender process. Returns (video path, log path)."""
    cut = cut_scene(scene)
    if cut is None:
        raise ValueError("Build the cut first")
    folder = cut_folder()
    from . import film
    work = os.path.join(folder, film.FILM_DIR)       # like a film: its bar shows the progress
    os.makedirs(work, exist_ok=True)
    path = path or os.path.join(folder, "%s_%s" % (scene.name, CUT_SCENE))
    render = cut.render
    images = render.image_settings
    keep = (render.filepath, images.file_format, getattr(images, "media_type", None), render.use_file_extension)
    try:
        if hasattr(images, "media_type"):
            images.media_type = 'VIDEO'
        images.file_format = 'FFMPEG'
        ff = render.ffmpeg
        ff.format, ff.codec, ff.constant_rate_factor = 'MPEG4', 'H264', 'HIGH'
        if cut.sequence_editor and any(_get(s, KIND_KEY) == "MUSIC" for s in ours(cut)):
            ff.audio_codec = 'AAC'
        render.filepath = path
        render.use_file_extension = True
        from .runner import RUN
        RUN.preparing = True
        try:
            blend = os.path.join(work, "cut.blend")
            bpy.ops.wm.save_as_mainfile(filepath=blend, copy=True, compress=False, relative_remap=True,
                                        check_existing=False)
        finally:
            RUN.preparing = False
    finally:
        render.filepath, images.file_format = keep[0], keep[1]
        if keep[2] is not None:
            images.media_type = keep[2]
        render.use_file_extension = keep[3]
    with open(os.path.join(work, "job.json"), "w", encoding="utf-8") as f:
        json.dump({"scene": cut.name, "out": path, "frames": [cut.frame_start, cut.frame_end]}, f)
    status = os.path.join(work, "status.json")
    total = cut.frame_end - cut.frame_start + 1
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
            "made = [f for f in glob.glob(glob.escape(P) + '*') if re.fullmatch(r'\\d+-\\d+\\.mp4', f[len(P):])]\n"
            "if made: os.replace(max(made, key=os.path.getmtime), P + '.mp4')\n"
            "put(state='done', done=T, finished=time.time())\n" % (status, total, path))
        proc = subprocess.Popen([bpy.app.binary_path, "-b", blend, "--factory-startup", "-S", cut.name,
                                 "--python-expr", script],
                                stdout=f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
    film.register(work, folder, "Cut %s" % scene.name, proc.pid)
    return path + ".mp4", log


def edl(scene, path=None):
    """A simple CMX 3600 EDL of our section strips (for other editing programs)."""
    cut = cut_scene(scene)
    if cut is None:
        raise ValueError("Build the cut first")
    fps = max(1, round(cut.render.fps / cut.render.fps_base))

    def tc(frame):
        frame = max(0, int(frame) - 1)
        return "%02d:%02d:%02d:%02d" % (frame // (3600 * fps), frame // (60 * fps) % 60, frame // fps % 60,
                                        frame % fps)
    lines = ["TITLE: %s" % cut.name, "FCM: NON-DROP FRAME", ""]
    clips = sorted((s for s in ours(cut) if _get(s, KIND_KEY) in {"SECTION", "HOLD"}),
                   key=lambda s: s.frame_final_start)
    for n, strip in enumerate(clips, 1):
        lines.append("%03d  AX       V     C        %s %s %s %s" % (
            n, tc(1), tc(1 + strip.frame_final_duration), tc(strip.frame_final_start), tc(strip.frame_final_end)))
        lines.append("* FROM CLIP NAME: %s" % strip.name)
    path = path or os.path.join(cut_folder(), "%s_%s.edl" % (scene.name, CUT_SCENE))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


# ------------------------------------------------------------------ opening it

def open_cut(context, cut):
    """Show the cut: the Video Editing workspace (appended once if the file has none), with the cut."""
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
    window.scene = cut


# ------------------------------------------------------------------ operators

CUT_ACTIONS = (
    ('OPEN', "Open Cut", "The cut in the Video Sequencer (made the first time from the rendered sections)"),
    ('BUILD', "Build Again", "Put the rendered sections in again with the transitions, holds and music (our "
                             "strips are replaced, your own stay)"),
    ('REFRESH', "Newest Frames", "The section strips take the newest renders (where and how long they are stays)"),
    ('BACK', "Back to the Film", "Back to the 3D scene"),
    ('SNAP', "Sections on Beats", "Every section a whole number of beats long (the sections after it move along, "
                                  "with their keys)"),
    ('EXPORT', "Export MP4", "The cut as MP4 (H.264, high) in its own process"),
    ('EDL', "Export EDL", "A list of the cuts for other editing programs (CMX 3600)"))
STRIP_ACTIONS = (
    ('GO', "Go to Section", "Back to the 3D scene, at this section (its camera, the timeline on it)"),
    ('RENDER', "Render Again", "Render this section again in High Quality; the strip takes the new frames"),
    ('LENGTH', "Take Length", "This strip's length becomes the section's length (the sections after it move "
                              "along with their keys); then render it again"),
    ('STILL', "Still for Labels", "The image under the playhead (else the strip's last) as a PNG in the cut folder, "
                                  "\"<section> Standbild.png\", for labels in Keynote"))


class STUDIO_OT_cut(Operator):
    bl_idname = "studio.cut"
    bl_label = "Cut"
    bl_options = {'UNDO', 'INTERNAL'}

    action: EnumProperty(items=CUT_ACTIONS, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return next(i[2] for i in CUT_ACTIONS if i[0] == props.action)

    def execute(self, context):
        scene = context.scene
        main = source_scene(scene) or scene
        try:
            if self.action == 'OPEN':
                cut = cut_scene(main)
                report = None
                if cut is None or not ours(cut):
                    cut, report = build(main)
                else:
                    refresh(main)
                open_cut(context, cut)
                if report and report["missing"]:
                    self.report({'WARNING'}, "Not rendered yet: %s" % ", ".join(report["missing"]))
            elif self.action == 'BUILD':
                cut, report = build(main)
                text = "Cut: %d sections" % len(report["sections"])
                if report["missing"]:
                    text += " · not rendered yet: %s" % ", ".join(report["missing"])
                self.report({'WARNING'} if report["missing"] else {'INFO'}, text)
            elif self.action == 'REFRESH':
                self.report({'INFO'}, "%d strips took newer frames" % refresh(main))
            elif self.action == 'BACK':
                context.window.scene = main
                space = bpy.data.workspaces.get("Layout")
                if space is not None:
                    context.window.workspace = space
            elif self.action == 'SNAP':
                self.report({'INFO'}, "%d sections on whole beats" % snap_to_beats(main))
            elif self.action == 'EXPORT':
                video, _log = export(main)
                self.report({'INFO'}, "Exporting in the background: %s" % video)
            elif self.action == 'EDL':
                self.report({'INFO'}, "EDL: %s" % edl(main))
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
        cut = cut_scene(context.scene)
        main = source_scene(cut)
        found = motion.find(main, self.section) if main is not None else None
        if found is None:
            self.report({'WARNING'}, "No section %s" % self.section)
            return {'CANCELLED'}
        marker, start, end = found
        if self.action == 'STILL':
            strip = next((s for s in ours(cut) if _get(s, SECTION_KEY) == self.section
                          and _get(s, KIND_KEY) == "SECTION"), None)
            if strip is None:
                return {'CANCELLED'}
            try:
                path = still_for_labels(cut, strip)
            except (ValueError, OSError) as ex:
                self.report({'WARNING'}, str(ex))
                return {'CANCELLED'}
            self.report({'INFO'}, "Still: %s" % path)
            return {'FINISHED'}
        if self.action == 'GO':
            context.window.scene = main
            space = bpy.data.workspaces.get("Layout")
            if space is not None:
                context.window.workspace = space
            motion.go(main, marker, start, end)
        elif self.action == 'RENDER':
            camera = marker.camera or main.camera
            with context.temp_override(scene=main):
                motion.render_section(main, camera, start, end, 'HIGH', marker.name)
            self.report({'INFO'}, "%s renders; the strip takes the frames when it is done" % marker.name)
        else:
            strip = next((s for s in ours(cut) if _get(s, SECTION_KEY) == self.section
                          and _get(s, KIND_KEY) == "SECTION"), None)
            if strip is None:
                return {'CANCELLED'}
            moved = motion.set_length(main, self.section, length=strip.frame_final_duration)
            self.report({'INFO'}, "%s: %d frames (%d keys moved along); render it again" % (
                marker.name, strip.frame_final_duration, moved))
        return {'FINISHED'}


# ------------------------------------------------------------------ panels

def draw_sections(layout, scene):
    """Transitions and holds of the sections."""
    for marker, start, end in motion.sections(scene):
        item = next((c for c in scene.studio.cut_items if c.section == marker.name), None)
        row = layout.row(align=True)
        row.label(text=marker.name, translate=False)
        if item is None:
            op = row.operator(STUDIO_OT_cut_settings.bl_idname, text="Settings", icon='ADD')
            op.section = marker.name
            continue
        row.prop(item, "transition", text="")
        sub = row.row(align=True)
        sub.active = item.transition != 'CUT'
        sub.ui_units_x = 3
        sub.prop(item, "frames", text="")
        hold = row.row(align=True)
        hold.ui_units_x = 3.5
        hold.prop(item, "hold", text="Hold")


class STUDIO_OT_cut_settings(Operator):
    bl_idname = "studio.cut_settings"
    bl_label = "Cut Settings"
    bl_description = "Transition and hold for this section"
    bl_options = {'UNDO', 'INTERNAL'}

    section: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        main = source_scene(cut_scene(context.scene)) or context.scene
        settings(main, self.section)
        return {'FINISHED'}


class STUDIO_PT_cut(Panel):
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Studio"
    bl_label = "Cut"

    @classmethod
    def poll(cls, context):
        return context.scene is not None and bool(context.scene.get(SOURCE_KEY))

    def draw(self, context):
        layout = self.layout
        cut = context.scene
        main = source_scene(cut)
        if main is None:
            layout.label(text="The film's scene is gone", icon='ERROR')
            return
        row = layout.row(align=True)
        row.operator(STUDIO_OT_cut.bl_idname, text="Back to the Film", icon='LOOP_BACK').action = 'BACK'
        row.operator(STUDIO_OT_cut.bl_idname, text="", icon='FILE_REFRESH').action = 'REFRESH'
        row.operator(STUDIO_OT_cut.bl_idname, text="", icon='SEQ_SEQUENCER').action = 'BUILD'

        sed = cut.sequence_editor
        strip = sed.active_strip if sed is not None else None
        section = _get(strip, SECTION_KEY) if strip is not None else ""
        if section:
            box = layout.box()
            box.label(text=section, icon='MARKER_HLT', translate=False)
            found = motion.find(main, section)
            if found is not None:
                info = box.row()
                info.active = False
                info.label(text="Section %d–%d · strip %d frames" % (
                    found[1], found[2], strip.frame_final_duration), translate=False)
            row = box.row(align=True)
            for action, icon in (('GO', 'VIEW_CAMERA'), ('RENDER', 'RENDER_ANIMATION'), ('LENGTH', 'ARROW_LEFTRIGHT')):
                op = row.operator(STUDIO_OT_cut_strip.bl_idname, text={'GO': "Go", 'RENDER': "Render",
                                                                       'LENGTH': "Length"}[action], icon=icon)
                op.action, op.section = action, section
            op = box.operator(STUDIO_OT_cut_strip.bl_idname, text="Still for Labels", icon='IMAGE_DATA')
            op.action, op.section = 'STILL', section

        box = layout.box()
        box.label(text="Transitions · Holds", icon='SEQ_STRIP_DUPLICATE')
        draw_sections(box, main)

        box = layout.box()
        box.label(text="Music", icon='SOUND')
        box.prop(main.studio, "cut_music", text="")
        row = box.row(align=True)
        row.prop(main.studio, "cut_bpm")
        row.operator(STUDIO_OT_cut.bl_idname, text="On Beats", icon='SNAP_ON').action = 'SNAP'

        row = layout.row(align=True)
        row.scale_y = 1.2
        row.operator(STUDIO_OT_cut.bl_idname, text="Export MP4", icon='RENDER_ANIMATION').action = 'EXPORT'
        row.operator(STUDIO_OT_cut.bl_idname, text="EDL", icon='TEXT').action = 'EDL'
        from . import film
        film.draw(layout)


def on_film_done():
    """A film finished: the cut takes its frames."""
    for scene in bpy.data.scenes:
        if not scene.get(SOURCE_KEY) and cut_scene(scene) is not None:
            try:
                refresh(scene)
            except (RuntimeError, ReferenceError, OSError) as ex:
                print("AI Studio: cut:", ex)


classes = (STUDIO_OT_cut, STUDIO_OT_cut_strip, STUDIO_OT_cut_settings, STUDIO_PT_cut)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Paths, file names and temporary render settings."""

import os
import re
import shutil

import bpy

# <output folder>/<file><suffix>/{low, high, animation, archive}: names from the preferences (Renders)
QUALITIES = ('LOW', 'HIGH')
EXT = ".png"


def folder_names():
    """{'LOW', 'HIGH', 'ANIM', 'ARCHIVE', 'SUFFIX'} as set in the preferences."""
    from .runner import prefs
    p = prefs()

    def get(attr, default):
        value = getattr(p, attr, "") if p is not None else ""
        return value.strip() or default
    return {'LOW': get("low_dir", "low"), 'HIGH': get("high_dir", "high"), 'ANIM': get("anim_dir", "animation"),
            'ARCHIVE': get("render_archive_dir", "archive"),
            'SUFFIX': p.output_suffix if p is not None else "_renders"}

# Output presets (gear menu in the Queue tab): key -> (file_format, extension, bit depths, alpha)
STILL_FORMATS = {
    'PNG': ('PNG', ".png", ('8', '16'), True),
    'JPEG': ('JPEG', ".jpg", ('8',), False),
    'TIFF': ('TIFF', ".tif", ('8', '16'), True),
    'EXR': ('OPEN_EXR', ".exr", ('16', '32'), True),
}
# key -> (media_type, file_format, extension or None for video, ffmpeg (container, codec) or None, alpha)
ANIM_FORMATS = {
    'PNG_SEQ': ('IMAGE', 'PNG', ".png", None, True),
    'JPEG_SEQ': ('IMAGE', 'JPEG', ".jpg", None, False),
    'EXR_SEQ': ('IMAGE', 'OPEN_EXR', ".exr", None, True),
    'MP4': ('VIDEO', 'FFMPEG', None, ('MPEG4', 'H264'), False),
    'PRORES': ('VIDEO', 'FFMPEG', None, ('QUICKTIME', 'PRORES'), False),
}
OUTPUT_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".mp4", ".mov", ".mkv")


def still_ext(rq):
    return STILL_FORMATS.get(rq.still_format, STILL_FORMATS['PNG'])[1]

# Characters that break file names: path separators, characters forbidden on
# macOS/Windows, `#` (Blender replaces it with the frame number) and `{}` (path
# templates since Blender 5).
_BAD_CHARS = re.compile(r'[\\/:*?"<>|#{}\x00-\x1f]+')


def safe_name(text):
    text = re.sub(r"_{2,}", "_", _BAD_CHARS.sub("_", text)).strip(" ._")
    return text or "untitled"


def project_name(blend_filepath=None, name=""):
    """Custom name from the Queue panel, otherwise the name of the blend file."""
    if name and name.strip():
        return safe_name(name)
    filepath = bpy.data.filepath if blend_filepath is None else blend_filepath
    if not filepath:
        return "untitled"
    return safe_name(os.path.splitext(os.path.basename(filepath))[0])


UNSAVED_ROOT = "~/Documents/AI Studio"   # renders of a file that was never saved


def default_root():
    """No output folder set: right next to the .blend file (in its project folder, also for a
    backup that is open), for an unsaved file ~/Documents/AI Studio."""
    if bpy.data.filepath:
        from .. import autosave
        return autosave.main_folder(bpy.data.filepath)
    return os.path.expanduser(UNSAVED_ROOT)


def output_root(root):
    return os.path.abspath(os.path.expanduser(bpy.path.abspath(root))) if root else default_root()


def project_dir(root, project):
    """`<folder of the .blend>/<name>_renders` (or `<output folder>/<name>_renders`)"""
    return os.path.join(output_root(root), project + folder_names()['SUFFIX'])


def quality_dir(root, project, quality):
    """`<name>_renders/low` or `<name>_renders/high` (names from the preferences)."""
    return os.path.join(project_dir(root, project), folder_names()[quality])


def archive_dir(root, project):
    """`<name>_renders/archive` – one folder for all renders, for sorting by hand."""
    return os.path.join(project_dir(root, project), folder_names()['ARCHIVE'])


def _highest_number(folder, stem):
    """Highest number among files `<stem>_001.png` and folders `<stem>_001`."""
    pattern = re.compile(re.escape(stem) + r"_(\d+)(?:\.[^.]+)?$", re.IGNORECASE)
    highest = 0
    if os.path.isdir(folder):
        for name in os.listdir(folder):
            match = pattern.match(name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest


def next_path(folder, stem, ext=EXT, also=()):
    """Next free path `<stem>_001<ext>`. Uses the highest number + 1 (also counting the
    folders in `also`, e.g. the archiv) so the order is kept even if files were deleted
    or sorted away."""
    highest = max(_highest_number(f, stem) for f in (folder,) + tuple(also))
    return os.path.join(folder, "%s_%03d%s" % (stem, highest + 1, ext))


NAME_PATTERNS = (
    ('NAME_CAM', "Name_Camera_001", "File name, camera, number (default)"),
    ('CAM', "Camera_001", "Only camera and number"),
    ('NAME_CAM_DATE', "Name_Camera_Date_001", "With today's date"),
    ('NAME_CAM_Q', "Name_Camera_HQ_001", "With the quality (preview or HQ)"),
)


def _stem(camera_name, name="", blend_filepath=None, quality=None):
    """File name before the number, after the pattern chosen in the Queue settings."""
    pattern = 'NAME_CAM'
    try:
        pattern = bpy.context.scene.render_studio.name_pattern
    except AttributeError:
        pass
    cam = safe_name(camera_name)
    project = project_name(blend_filepath, name)
    if pattern == 'CAM':
        return cam
    if pattern == 'NAME_CAM_DATE':
        import time
        return "%s_%s_%s" % (project, cam, time.strftime("%Y-%m-%d"))
    if pattern == 'NAME_CAM_Q' and quality in QUALITIES:
        return "%s_%s_%s" % (project, cam, folder_names()[quality])
    return "%s_%s" % (project, cam)


def job_path(root, camera_name, quality, name="", blend_filepath=None, ext=EXT):
    """`<name>_renders/<low|high>/<name>_<camera>_001.png` (extension from the still format).
    Folders are made only when something is rendered into them (the archive never)."""
    project = project_name(blend_filepath, name)
    folder = quality_dir(root, project, quality)
    # low and high share one counter, so their files never clash in the shared archive
    others = [quality_dir(root, project, q) for q in QUALITIES if q != quality]
    return next_path(folder, _stem(camera_name, name, blend_filepath, quality), ext=ext,
                     also=others + [archive_dir(root, project)])


def anim_dir(root, camera_name, quality, name="", blend_filepath=None):
    """Folder for an animation: `<name>_renders/animation/<name>_<camera>_<low|high>_001/`."""
    project = project_name(blend_filepath, name)
    names = folder_names()
    folder = os.path.join(project_dir(root, project), names['ANIM'])
    stem = "%s_%s" % (_stem(camera_name, name, blend_filepath), names[quality])
    return next_path(folder, stem, ext="", also=[archive_dir(root, project)])


def anim_frame_pattern(folder, camera_name, name="", blend_filepath=None):
    """Blender appends the frame number: `<name>_<camera>_0001.png`."""
    return os.path.join(folder, _stem(camera_name, name, blend_filepath) + "_")


def frame_files(folder):
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, n) for n in os.listdir(folder) if n.lower().endswith(OUTPUT_EXTS))


def remove_output(path):
    """Delete a discarded result (image or animation folder of this queue)."""
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)
    except OSError as ex:
        print("Render Studio: could not delete %s: %s" % (path, ex))


# ----------------------------------------------------------------- Overrides

class Overrides:
    """Sets values on a scene and restores them later.

    Only the scene name and RNA paths are stored, no Python references to
    Blender data. That keeps restoring safe even if undo ran in between or data
    was recreated.
    """

    dry = False   # PlanOverrides: only records

    def __init__(self, scene):
        self.scene_name = scene.name
        self._saved = []

    def get(self, scene, path):
        """The value at `path` as the render will see it."""
        head, attr = self._split(path)
        return getattr(self._owner(scene, head), attr)

    @staticmethod
    def _split(path):
        head, _, attr = path.rpartition(".")
        return head, attr

    @staticmethod
    def _owner(scene, head):
        return scene.path_resolve(head) if head else scene

    @staticmethod
    def _pack(value):
        if isinstance(value, bpy.types.Object):
            return ('OBJECT', value.name)
        if hasattr(value, "copy"):   # mathutils values are live views of the data
            value = value.copy()
        elif type(value).__name__ == "bpy_prop_array":
            value = tuple(value)
        return ('VALUE', value)

    @staticmethod
    def _unpack(packed):
        kind, value = packed
        if kind == 'OBJECT':
            return bpy.data.objects.get(value)
        return value

    def save(self, scene, path):
        """Remembers the current value without changing it (for values Blender
        adjusts automatically when other values are set)."""
        head, attr = self._split(path)
        self._saved.append((path, self._pack(getattr(self._owner(scene, head), attr))))

    def set(self, scene, path, value):
        head, attr = self._split(path)
        owner = self._owner(scene, head)
        old = getattr(owner, attr)
        if old == value:
            return
        self._saved.append((path, self._pack(old)))
        setattr(owner, attr, value)

    def set_original(self, path, value):
        """Change the value that restore() puts back. False if this did not set `path`."""
        for i, (saved_path, _packed) in enumerate(self._saved):
            if saved_path == path:
                self._saved[i] = (path, self._pack(value))
                return True
        return False

    def original(self, path, current):
        """The value before this set it, otherwise `current`."""
        for saved_path, packed in self._saved:
            if saved_path == path:
                return self._unpack(packed)
        return current

    def restore(self):
        scene = bpy.data.scenes.get(self.scene_name)
        pending, self._saved = list(reversed(self._saved)), []
        if scene is None:
            return
        # Some values depend on each other (color_mode only applies to certain
        # formats). What fails in the first pass works in the second.
        failed = []
        for _attempt in range(3):
            failed = []
            for path, packed in pending:
                head, attr = self._split(path)
                try:
                    setattr(self._owner(scene, head), attr, self._unpack(packed))
                except Exception as ex:
                    failed.append((path, packed, ex))
            if not failed:
                return
            pending = [(path, packed) for path, packed, _ex in failed]
        for path, _packed, ex in failed:
            print("Render Studio: could not restore %s: %s" % (path, ex))


def _output_settings(ov, scene, animation, background_mode):
    """File format, bit depth, alpha and video codec from the Queue's output presets."""
    rq = scene.render_studio
    images = "render.image_settings."
    for key in ("media_type", "file_format", "color_mode", "color_depth", "quality", "exr_codec"):
        ov.save(scene, images + key)
    want_alpha = background_mode == 'TRANSPARENT'
    if animation:
        media, file_format, _ext, ffmpeg, alpha_ok = ANIM_FORMATS.get(rq.anim_format, ANIM_FORMATS['PNG_SEQ'])
        depths = STILL_FORMATS['EXR'][2] if file_format == 'OPEN_EXR' else ('8', '16') if file_format == 'PNG' else ('8',)
    else:
        file_format, _ext, depths, alpha_ok = STILL_FORMATS.get(rq.still_format, STILL_FORMATS['PNG'])
        media, ffmpeg = 'IMAGE', None
    ov.set(scene, images + "media_type", media)
    ov.set(scene, images + "file_format", file_format)
    ov.set(scene, images + "color_mode", 'RGBA' if want_alpha and alpha_ok else 'RGB')
    if ffmpeg is None:
        depth = rq.still_depth if rq.still_depth in depths else depths[-1 if file_format == 'OPEN_EXR' else 0]
        ov.set(scene, images + "color_depth", depth)
        if file_format == 'JPEG':
            ov.set(scene, images + "quality", rq.jpeg_quality)
        if file_format == 'OPEN_EXR':
            ov.set(scene, images + "exr_codec", 'ZIP')
    else:
        container, codec = ffmpeg
        for key in ("format", "codec", "constant_rate_factor"):
            ov.save(scene, "render.ffmpeg." + key)
        ov.set(scene, "render.ffmpeg.format", container)
        ov.set(scene, "render.ffmpeg.codec", codec)
        if codec == 'H264':
            ov.set(scene, "render.ffmpeg.constant_rate_factor", rq.video_quality)


class PlanOverrides(Overrides):
    """Like Overrides, but nothing is changed: set() only records the values (in order), for
    the render worker to apply in its own copy of the file (runner)."""
    dry = True

    def __init__(self, scene):
        super().__init__(scene)
        self.plan = {}

    def get(self, scene, path):
        return self.plan[path] if path in self.plan else super().get(scene, path)

    def save(self, scene, path):
        pass

    def set(self, scene, path, value):
        if path not in self.plan and super().get(scene, path) == value:
            return
        self.plan[path] = value

    def set_original(self, path, value):
        return False

    def restore(self):
        self.plan.clear()

    def as_list(self):
        """[[path, value]] with JSON values; objects as {"object": name}."""
        return [[path, _jsonable(value)] for path, value in self.plan.items()]


def _jsonable(value):
    if isinstance(value, bpy.types.ID):
        return {"object": value.name}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    try:
        return [float(v) for v in value]
    except TypeError:
        return str(value)


def prepare_job(scene, camera, quality, filepath, background_mode, low_resolution, low_samples,
                resolution=None, snapshot=None, animation=False, ov=None):
    """Sets up the scene for a render. Returns the overrides for restoring.

    snapshot: settings from when the entry was added ({"percentage": %}), or None.
    The camera itself is a temporary copy at the stored position (see runner).

    background_mode: 'NORMAL' (World), 'TRANSPARENT' (PNG with alpha) or
    'COLOR' (Alpha Over in the compositor, see background.py).
    """
    ov = ov if ov is not None else Overrides(scene)
    try:
        ov.set(scene, "camera", camera)
        if snapshot:
            if snapshot.get("percentage"):
                ov.set(scene, "render.resolution_percentage", snapshot["percentage"])
        # Camera markers in the timeline would switch the camera back while
        # rendering (BKE_scene_camera_switch_update), so unbind them briefly.
        for i, marker in enumerate(scene.timeline_markers):
            if marker.camera is not None:
                ov.set(scene, "timeline_markers[%d].camera" % i, None)

        _output_settings(ov, scene, animation, background_mode)
        ov.set(scene, "render.film_transparent", background_mode != 'NORMAL')
        if background_mode == 'COLOR':
            from . import background
            if not ov.dry:   # (a plan uses the scene as it is, kept in sync by background.py)
                background.sync(scene, force=True)      # color matched to the current view transform
            ov.set(scene, "render.use_compositing", True)
        # 72 ppi in the PNG
        ov.set(scene, "render.ppm_factor", 72)
        ov.set(scene, "render.ppm_base", 0.0254)

        if resolution:
            ov.set(scene, "render.resolution_x", int(resolution[0]))
            ov.set(scene, "render.resolution_y", int(resolution[1]))

        if quality == 'LOW':
            percent = ov.get(scene, "render.resolution_percentage")
            ov.set(scene, "render.resolution_percentage",
                   max(1, round(percent * low_resolution / 100)))
            engine = scene.render.engine
            # low_samples: share of the High (scene) samples in percent
            if engine == 'CYCLES':
                ov.set(scene, "cycles.samples", max(1, round(ov.get(scene, "cycles.samples") * low_samples / 100)))
                if scene.render_studio.low_denoise:
                    ov.set(scene, "cycles.use_denoising", True)
            elif engine == 'BLENDER_EEVEE':
                ov.set(scene, "eevee.taa_render_samples",
                       max(1, round(ov.get(scene, "eevee.taa_render_samples") * low_samples / 100)))

        if animation and ANIM_FORMATS.get(scene.render_studio.anim_format, ("IMAGE",))[0] == 'VIDEO':
            # Video codecs (H.264) need even pixel sizes: bake the percentage into an even size.
            pct = ov.get(scene, "render.resolution_percentage")
            w = max(2, round(ov.get(scene, "render.resolution_x") * pct / 100) // 2 * 2)
            h = max(2, round(ov.get(scene, "render.resolution_y") * pct / 100) // 2 * 2)
            ov.set(scene, "render.resolution_x", w)
            ov.set(scene, "render.resolution_y", h)
            ov.set(scene, "render.resolution_percentage", 100)

        ov.set(scene, "render.filepath", filepath)
        ov.set(scene, "render.use_file_extension", True)
    except Exception:
        ov.restore()
        raise
    return ov

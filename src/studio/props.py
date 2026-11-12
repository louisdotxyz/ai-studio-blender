# SPDX-License-Identifier: GPL-3.0-or-later
"""Settings of Render Studio: scene, camera, light and queue entry properties and the preferences."""

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty,
                       IntProperty, PointerProperty, StringProperty)
from bpy.types import PropertyGroup

from . import background, cameras, core, hdri, lights, matpresets, orbit, sync
from .matpresets import _color_input
from .keymaps import SHORTCUTS, _user_keymap_item

# Length fields of camera and light: 1 mm per click on the arrows and per pixel when dragging
# (Blender turns the step of a length into its nearest unit: 0.05 gives millimeters).
FINE_STEP = 0.05


def _get_short_name(self):
    return self.id_data.name


def _set_short_name(self, value):
    cameras.rename(self.id_data, value)


def _cam_value(key, default=0.0):
    def get(self):
        cam = cameras.active_camera(self.id_data)
        if cam is None:
            return default
        owner = cam.data.dof if key == "focus_distance" else cam.data
        return getattr(owner, key)

    def set(self, value):
        cam = cameras.active_camera(self.id_data)
        if cam is not None:
            owner = cam.data.dof if key == "focus_distance" else cam.data
            setattr(owner, key, value)
    return get, set


# ---------------------------------------------------------------------- Data

# (short label, long label, width, height)
FORMAT_PRESETS = (
    ("IG Portrait", "Instagram Portrait", 1080, 1350),
    ("IG Landscape", "Instagram Landscape", 1080, 566),
    ("HD", "HD 16:9", 1920, 1080),
    ("Square", "Square", 1080, 1080),
)


_FORMAT_ITEMS = [('CUSTOM', "Custom Format", "The resolution matches none of the presets", 'NONE', 0)] + [
    ("P%d" % i, "%s (%d × %d)" % (long, w, h), "%d × %d pixels" % (w, h), 'NONE', i + 1)
    for i, (_short, long, w, h) in enumerate(FORMAT_PRESETS)
]


# Aspect ratio: the short side is BASE_SIZE pixels at 1x.
BASE_SIZE = 1080


PPI = 72


def aspect_ratio(width, height):
    """(a, b) as small whole numbers (4:5, 16:9 …); odd sizes get the closest whole-number ratio."""
    target = width / height
    for b in range(1, 33):
        a = round(target * b)
        if a and abs(a / b - target) < 0.004 and a <= 64:
            return a, b
    from fractions import Fraction
    f = Fraction(width, height).limit_denominator(99)
    return max(1, f.numerator), max(1, f.denominator)


def size_for_ratio(a, b):
    """Pixels for the aspect ratio a:b with the short side BASE_SIZE."""
    if a <= 0 or b <= 0:
        return None
    if a >= b:
        return max(4, round(BASE_SIZE * a / b)), BASE_SIZE
    return BASE_SIZE, max(4, round(BASE_SIZE * b / a))


def _current_aspect(settings):
    """The ratio as typed (e.g. 16:9) while it still matches the resolution, otherwise
    the ratio of the resolution (after a preset or a change in the Output panel)."""
    render = settings.id_data.render
    w, h = render.resolution_x, render.resolution_y
    stored = settings.get("aspect_typed")
    if stored and min(stored) >= 1 and abs(stored[0] / stored[1] - w / h) < 0.004:
        return [int(round(v)) for v in stored]
    return list(aspect_ratio(w, h))


def _get_aspect(index):
    def get(settings):
        return _current_aspect(settings)[index]
    return get


def _set_aspect(index):
    def set_(settings, value):
        ratio = _current_aspect(settings)
        ratio[index] = value
        size = size_for_ratio(*ratio)
        if size:
            render = settings.id_data.render
            render.resolution_x, render.resolution_y = size
            settings["aspect_typed"] = ratio
            sync.schedule_fit()
    return set_


def _get_format(settings):
    render = settings.id_data.render
    for i, (_s, _l, w, h) in enumerate(FORMAT_PRESETS):
        if (render.resolution_x, render.resolution_y) == (w, h):
            return i + 1
    return 0


def _set_format(settings, value):
    if value <= 0:
        return
    _s, _l, w, h = FORMAT_PRESETS[value - 1]
    render = settings.id_data.render
    render.resolution_x, render.resolution_y = w, h
    sync.schedule_fit()


def _is_camera(_self, obj):
    return obj.type == 'CAMERA'


def _is_not_camera(_self, obj):
    return obj.type != 'CAMERA'


def _is_light(_self, obj):
    return obj.type == 'LIGHT'


def _is_not_camera_or_light(_self, obj):
    return obj.type not in {'CAMERA', 'LIGHT'}


def _has_material(_self, obj):
    return hasattr(obj.data, "materials") and obj.active_material is not None


def _color_pick_update(settings, context):
    """Pipette "Take color from…": copies only the color into the active material."""
    source = settings.color_source
    if source is None:
        return
    target_obj = context.active_object
    src = _color_input(source.active_material)
    dst = _color_input(target_obj.active_material) if target_obj is not None else None
    if src is not None and dst is not None and target_obj != source:
        if src.is_linked:
            print("Render Studio: the picked color comes from a texture, only its default value is copied")
        if dst.is_linked:
            dst.links[0].from_socket.node.id_data.links.remove(dst.links[0])
        dst.default_value = tuple(src.default_value)
    elif source.active_material is not None and target_obj is not None and target_obj.active_material:
        target_obj.active_material.diffuse_color = source.active_material.diffuse_color
    settings.color_source = None


def _material_pick_update(settings, context):
    """Picker in the Material tab: the selected objects take the material of the picked
    object. Then the field empties again, ready for the next pick."""
    source = settings.material_source
    if source is None:
        return
    material = source.active_material
    selected = set(getattr(context, "selected_objects", None) or ())
    if context.active_object is not None:
        selected.add(context.active_object)
    selected.discard(source)
    if material is not None:
        matpresets.assign(material, sorted(selected, key=lambda o: o.name))
    settings.material_source = None


class RS_CameraSettings(PropertyGroup):
    short_name: StringProperty(
        name="Name", description="Rename the camera: \"side\" becomes \"P_side_01\" (Preferences › Studio › "
        "Name Scheme)", get=_get_short_name, set=_set_short_name, options=set())
    use_resolution: BoolProperty(
        name="Resolution per Camera",
        description="Pin the resolution to this camera. When it becomes active, the scene switches to it",
        default=False,
        update=sync.on_use_resolution,
        options=set(),
    )
    resolution_x: IntProperty(name="Width", default=1920, min=4, max=65536, subtype='PIXEL',
                              update=sync.on_camera_resolution, options=set())
    resolution_y: IntProperty(name="Height", default=1080, min=4, max=65536, subtype='PIXEL',
                              update=sync.on_camera_resolution, options=set())
    queue_count: IntProperty(options={'HIDDEN'})   # how often this camera was added to the queue


class RS_Item(PropertyGroup):
    camera: PointerProperty(type=bpy.types.Object, poll=_is_camera)
    # Unique number so the running queue still recognizes entries after they
    # are moved or removed.
    uid: IntProperty(options={'HIDDEN'})
    # What was set when the entry was added ("" in files from before 0.6).
    quality: StringProperty(options={'HIDDEN'})          # 'LOW' / 'HIGH'
    mode: StringProperty(options={'HIDDEN'})             # 'STILL' / 'ANIM'
    number: IntProperty(options={'HIDDEN'})              # "45 grad 3"
    # The camera as it was when added, so the same camera can be added several times.
    has_snapshot: BoolProperty(options={'HIDDEN'})
    # Material variant from Varianten.json, applied only to this entry's render (see variants.py)
    variant: StringProperty(options={'HIDDEN'})
    matrix: FloatVectorProperty(size=16, options={'HIDDEN'})
    lens: FloatProperty(options={'HIDDEN'})
    resolution_x: IntProperty(options={'HIDDEN'})
    resolution_y: IntProperty(options={'HIDDEN'})
    percentage: IntProperty(options={'HIDDEN'})


class RS_LightSettings(PropertyGroup):
    """Per light (Object.render_studio_light): its own target and height."""
    short_name: StringProperty(
        name="Name", description="Rename the light: \"key\" becomes \"A_key_01\" (Preferences › Studio › "
        "Name Scheme)", get=_get_short_name, set=_set_short_name, options=set())
    target: PointerProperty(
        type=bpy.types.Object, poll=_is_not_camera_or_light, update=lights.on_light_target,
        name="Target", description="This light points at this object. Empty = the setup's object")
    lift: FloatProperty(subtype='DISTANCE', options={'HIDDEN'})
    height: FloatProperty(
        name="Height", description="The light and the point it looks at move up or down together",
        step=FINE_STEP, precision=3, subtype='DISTANCE', get=lights.get_light_height, set=lights.set_light_height, options=set())


def _look_changed(settings, context):
    from . import look
    look.on_change(settings, context)


class RS_Settings(PropertyGroup):
    items: CollectionProperty(type=RS_Item)
    # Output presets (gear menu in the Queue tab)
    still_format: EnumProperty(
        name="Still Format",
        items=[('PNG', "PNG", "Lossless, with alpha"), ('JPEG', "JPG", "Small files, no alpha"),
               ('TIFF', "TIFF", "Lossless, for print and editing"), ('EXR', "EXR", "HDR, for compositing")],
        default='PNG')
    still_depth: EnumProperty(
        name="Bit Depth",
        items=[('8', "8 Bit", "Standard"), ('16', "16 Bit", "More headroom for editing (PNG, TIFF, EXR half)"),
               ('32', "32 Bit", "Full float (EXR)")],
        default='8')
    jpeg_quality: IntProperty(name="JPG Quality", subtype='PERCENTAGE', min=10, max=100, default=92)
    anim_format: EnumProperty(
        name="Animation Format",
        items=[('PNG_SEQ', "PNG Frames", "One PNG per frame, lossless"),
               ('JPEG_SEQ', "JPG Frames", "One JPG per frame, small"),
               ('EXR_SEQ', "EXR Frames", "One EXR per frame, for compositing"),
               ('MP4', "MP4 (H.264)", "One video file, plays everywhere"),
               ('PRORES', "ProRes (.mov)", "One video file for editing (large)")],
        default='PNG_SEQ')
    video_quality: EnumProperty(
        name="Video Quality",
        items=[('LOW', "Low", "Small file, for previews"), ('MEDIUM', "Medium", "Smaller file"),
               ('HIGH', "High", "Recommended"), ('PERC_LOSSLESS', "Lossless", "Visually lossless, large file")],
        default='HIGH')
    low_denoise: BoolProperty(name="Denoise", description="Always denoise Low Quality renders (Cycles)",
                              default=True)
    name_pattern: EnumProperty(name="File Names", items=core.NAME_PATTERNS, default='NAME_CAM')
    output_name: StringProperty(
        name="Name",
        description="Name for the folder and the files. Empty = name of the blend file",
        default="", options=set(),
    )
    aspect_x: IntProperty(name="Width", description="Aspect ratio, width part (the short side is 1080 px at 1x)",
                          min=1, max=999, soft_max=64, get=_get_aspect(0), set=_set_aspect(0), options=set())
    aspect_y: IntProperty(name="Height", description="Aspect ratio, height part (the short side is 1080 px at 1x)",
                          min=1, max=999, soft_max=64, get=_get_aspect(1), set=_set_aspect(1), options=set())
    format_preset: EnumProperty(
        name="Format", description="Resolution of the active camera or the scene",
        items=_FORMAT_ITEMS, get=_get_format, set=_set_format, options=set(),
    )
    next_uid: IntProperty(default=1, options={'HIDDEN'})
    quality: EnumProperty(
        name="Quality",
        items=[
            ('LOW', "Low Quality", "Quick test: reduced resolution and samples (see Preferences)"),
            ('HIGH', "High Quality", "With the render settings of the scene"),
        ],
        default='LOW',
        options=set(),
    )
    mode: EnumProperty(
        name="Mode",
        items=[
            ('STILL', "Still", "One image per camera (current frame)"),
            ('ANIM', "Animation", "The frame range per camera, as a PNG sequence in its own folder"),
        ],
        default='STILL',
        options=set(),
    )
    look_glow: FloatProperty(name="Glow", description="Soft glow around bright parts (compositor)",
                             default=0.0, min=0.0, max=1.0, subtype='FACTOR', options=set(), update=_look_changed)
    look_contrast: FloatProperty(name="Contrast", description="More contrast (compositor)",
                                 default=0.0, min=0.0, max=1.0, subtype='FACTOR', options=set(), update=_look_changed)
    look_vignette: FloatProperty(name="Vignette", description="Darker corners (compositor)",
                                 default=0.0, min=0.0, max=1.0, subtype='FACTOR', options=set(), update=_look_changed)
    background_mode: EnumProperty(
        name="Background",
        items=[
            ('NORMAL', "World", "The World (HDRI or color) as background: pick, rotate or generate an HDRI"),
            ('TRANSPARENT', "Transparent", "Transparent background, saved as PNG with alpha channel"),
            ('COLOR', "Color", "A color behind the image: Alpha Over in the compositor, shown in the viewport "
                       "(Material Preview / Rendered, camera view). Renders get exactly this color"),
        ],
        default='NORMAL',
        options=set(),
        update=background.on_mode,
    )
    background_color: FloatVectorProperty(
        name="Color", subtype='COLOR_GAMMA', size=3, min=0.0, max=1.0,
        default=(0.95, 0.95, 0.95), options=set(), update=background.on_color,
    )
    use_gradient: BoolProperty(name="Gradient", description="Vertical gradient: bottom color → top color",
                               default=False, options=set(), update=background.on_color)
    background_color_top: FloatVectorProperty(
        name="Top Color", subtype='COLOR_GAMMA', size=3, min=0.0, max=1.0,
        default=(1.0, 1.0, 1.0), options=set(), update=background.on_color,
    )
    noise_intensity: FloatProperty(
        name="Noise", description="Monochrome grain on the background (0 = none)",
        default=0.0, min=0.0, max=1.0, subtype='FACTOR', options=set(), update=background.on_color,
    )
    noise_size: FloatProperty(
        name="Noise Size", description="Size of the grain in pixels",
        default=1.0, min=1.0, soft_max=20.0, subtype='PIXEL', options=set(), update=background.on_color,
    )
    world_background_strength: FloatProperty(
        name="World Strength", description="Strength of the World's Background node (HDRI brightness)",
        min=0.0, soft_max=5.0, get=background.get_world_strength, set=background.set_world_strength,
        options=set(),
    )
    # Cameras (see cameras.py)
    show_cameras: BoolProperty(
        name="Show Cameras", description="Show the cameras in the viewport (hidden by default)",
        get=cameras.get_show, set=cameras.set_show, options=set(),
    )
    camera_locked: BoolProperty(
        name="Lock Camera",
        description="Fix position and rotation of the active camera so it cannot be moved by accident",
        get=cameras.get_lock, set=cameras.set_lock, options=set(),
    )
    cameras_hidden_once: BoolProperty(options={'HIDDEN'})
    samples_set_once: BoolProperty(options={'HIDDEN'})
    # Orbit (see orbit.py): applies to all cameras, stays until changed
    orbit_target: PointerProperty(
        type=bpy.types.Object, poll=_is_not_camera, update=orbit.on_target,
        name="Orbit Around",
        description="The camera circles around this object and looks at it. Empty = around the 3D cursor",
    )
    orbit_horizontal: FloatProperty(
        name="Horizontal", description="Angle around the object",
        subtype='ANGLE', get=orbit.get_horizontal, set=orbit.set_horizontal, options=set(),
    )
    orbit_vertical: FloatProperty(
        name="Vertical", description="Height above (+) or below (−) the object",
        subtype='ANGLE', min=-orbit.MAX_VERTICAL, max=orbit.MAX_VERTICAL,
        get=orbit.get_vertical, set=orbit.set_vertical, options=set(),
    )
    orbit_distance: FloatProperty(
        name="Distance", description="Distance to the center of the object",
        step=FINE_STEP, precision=3, subtype='DISTANCE', min=orbit.MIN_DISTANCE,
        get=orbit.get_distance, set=orbit.set_distance, options=set(),
    )
    orbit_height: FloatProperty(
        name="Height", description="Camera and the point it looks at move up or down together",
        step=FINE_STEP, precision=3, subtype='DISTANCE', get=orbit.get_height, set=orbit.set_height, options=set(),
    )
    orbit_side_offset: FloatProperty(
        name="Left / Right", description="Camera and the point it looks at move sideways together (0 = centered)",
        step=FINE_STEP, precision=3, subtype='DISTANCE', get=orbit.get_side, set=orbit.set_side, options=set(),
    )
    orbit_up_offset: FloatProperty(
        name="Up / Down", description="Camera and the point it looks at move up or down in the picture together "
                                      "(also in a straight top view; 0 = centered)",
        step=FINE_STEP, precision=3, subtype='DISTANCE', get=orbit.get_up, set=orbit.set_up, options=set(),
    )
    cam_ortho_scale: FloatProperty(
        name="Ortho Scale", description="Width the orthographic camera shows", min=0.001,
        step=FINE_STEP, precision=3, subtype='DISTANCE', get=_cam_value("ortho_scale", 1.0)[0],
        set=_cam_value("ortho_scale")[1], options=set())
    cam_focus_distance: FloatProperty(
        name="Distance", description="Distance to the sharp plane (depth of field)", min=0.0,
        step=FINE_STEP, precision=3, subtype='DISTANCE', get=_cam_value("focus_distance", 1.0)[0],
        set=_cam_value("focus_distance")[1], options=set())
    cam_clip_start: FloatProperty(
        name="Start", description="Nothing closer to the camera is drawn or rendered", min=0.0001,
        step=FINE_STEP, precision=4, subtype='DISTANCE', get=_cam_value("clip_start", 0.1)[0],
        set=_cam_value("clip_start")[1], options=set())
    cam_clip_end: FloatProperty(
        name="End", description="Nothing farther from the camera is drawn or rendered", min=0.001,
        step=FINE_STEP, precision=3, subtype='DISTANCE', get=_cam_value("clip_end", 100.0)[0],
        set=_cam_value("clip_end")[1], options=set())
    orbit_lift: FloatProperty(subtype='DISTANCE', options={'HIDDEN'})
    orbit_side: FloatProperty(subtype='DISTANCE', options={'HIDDEN'})
    orbit_up: FloatProperty(subtype='DISTANCE', options={'HIDDEN'})
    material_source: PointerProperty(
        type=bpy.types.Object, poll=_has_material, update=_material_pick_update,
        name="Link Material From",
        description="Pick an object (pipette): the selected objects use the same material – a change "
                    "applies to all of them. Unlink separates them again",
    )
    color_source: PointerProperty(
        type=bpy.types.Object, poll=_has_material, update=lambda self, ctx: _color_pick_update(self, ctx),
        name="Take Color From",
        description="Pick an object (pipette): only its color goes into the active material, "
                    "nothing is linked",
    )
    # Lights (see lights.py): the light edited in the Light tab and its own orbit object
    active_light: PointerProperty(type=bpy.types.Object, poll=_is_light, name="Light")
    light_orbit_target: PointerProperty(
        type=bpy.types.Object, poll=_is_not_camera_or_light, update=lights.on_target,
        name="Orbit Around",
        description="Lights circle around this object and point at it. Independent of the camera's orbit object",
    )
    light_orbit_horizontal: FloatProperty(
        name="Horizontal", description="Angle around the object",
        subtype='ANGLE', get=lights.get_horizontal, set=lights.set_horizontal, options=set(),
    )
    light_orbit_vertical: FloatProperty(
        name="Vertical", description="Height above (+) or below (−) the object",
        subtype='ANGLE', min=-orbit.MAX_VERTICAL, max=orbit.MAX_VERTICAL,
        get=lights.get_vertical, set=lights.set_vertical, options=set(),
    )
    light_orbit_distance: FloatProperty(
        name="Distance", description="Distance to the center of the object",
        step=FINE_STEP, precision=3, subtype='DISTANCE', min=orbit.MIN_DISTANCE,
        get=lights.get_distance, set=lights.set_distance, options=set(),
    )
    # World / HDRI (see hdri.py)
    previous_world: PointerProperty(type=bpy.types.World, options={'HIDDEN'})
    hdri_choice: EnumProperty(
        name="Environment", description="HDRI from the HDRI folder (BlenderKit downloads are copied there)",
        items=hdri.enum_items, get=hdri.get_choice, set=hdri.set_choice, options=set(),
    )
    hdri_only_favorites: BoolProperty(name="Favorites Only", description="Show only favorite HDRIs",
                                      default=False, options=set())
    world_strength: FloatProperty(
        name="Strength", description="Brightness of the HDRI", min=0.0, soft_max=10.0,
        get=hdri.get_strength, set=hdri.set_strength, options=set(),
    )
    world_rotation: FloatProperty(
        name="Rotation", description="Rotation of the HDRI around the vertical axis",
        subtype='ANGLE', get=hdri.get_rotation, set=hdri.set_rotation, options=set(),
    )
    # For the per-camera resolution (see sync.py)
    last_camera: PointerProperty(type=bpy.types.Object, options={'HIDDEN'})
    base_resolution_x: IntProperty(options={'HIDDEN'})
    base_resolution_y: IntProperty(options={'HIDDEN'})


def _frame_update(_self, _context):
    sync.schedule_fit()


class RS_Preferences(PropertyGroup):
    """Studio settings, shown in the extension's preferences (formerly their own add-on)."""

    # Camera frame (passepartout) in the viewport, see sync.py
    frame_auto: BoolProperty(
        name="Auto Fit",
        description="Keep the camera frame centered and sized in the free part of the viewport",
        default=True, update=_frame_update,
    )
    frame_size: IntProperty(
        name="Size", description="How much of the free part of the viewport the frame fills",
        subtype='PERCENTAGE', min=20, max=100, default=50, update=_frame_update,
    )
    frame_shift_x: IntProperty(
        name="Horizontal", description="Move the frame left (−) or right (+), in % of the free width",
        subtype='PERCENTAGE', min=-50, max=50, default=0, update=_frame_update,
    )
    frame_shift_y: IntProperty(
        name="Vertical", description="Move the frame down (−) or up (+), in % of the free height",
        subtype='PERCENTAGE', min=-50, max=50, default=0, update=_frame_update,
    )

    min_clipping: BoolProperty(
        name="Minimal Clipping",
        description="Clipping start of all cameras at 1 mm, so nothing close to the camera is cut off "
                    "(applied to every camera, also new ones and when a file is opened)",
        default=True, update=lambda self, ctx: _apply_clipping(),
    )
    # Material tab: what is shown
    mat_view: EnumProperty(
        name="Material View",
        items=[('COMPACT', "Compact", "Only the values chosen below"),
               ('FULL', "All Settings", "Every setting of the shader, like Properties › Material")],
        default='COMPACT')
    mat_show_color: BoolProperty(name="Color", default=True)
    mat_show_roughness: BoolProperty(name="Roughness", default=True)
    mat_show_metallic: BoolProperty(name="Metallic", default=True)
    mat_show_normal: BoolProperty(name="Normal / Bump", default=True)
    mat_show_emission: BoolProperty(name="Emission", default=False)
    mat_show_transmission: BoolProperty(name="Transmission", default=False)
    mat_show_coat: BoolProperty(name="Coat", default=False)
    mat_preview: BoolProperty(name="Preview", description="Show a preview image of the material", default=True)
    mat_preview_size: EnumProperty(
        name="Preview Size",
        items=[('4', "S", "Small"), ('7', "M", "Medium"), ('10', "L", "Large")], default='7')
    material_preset_folder: StringProperty(
        name="Material Presets Folder",
        description="Folder for your material presets (material_presets.json). Empty = the Studio data folder",
        subtype='DIR_PATH', default="",
        update=lambda self, ctx: matpresets.reload(),
    )

    output_root: StringProperty(
        name="Output Folder",
        description="Where the renders go: a folder per file (<name> + suffix) with subfolders for "
                    "Low, High, Animation and one archive folder for sorting by hand. Empty: right next to "
                    "the .blend file (unsaved files: Documents/AI Studio)",
        subtype='DIR_PATH',
        default="",
    )
    output_suffix: StringProperty(
        name="Folder Suffix", description="Appended to the file name for its render folder, e.g. Chair_renders",
        default="_renders")
    low_dir: StringProperty(name="Low Quality", description="Subfolder for Low Quality renders", default="low")
    high_dir: StringProperty(name="High Quality", description="Subfolder for High Quality renders", default="high")
    anim_dir: StringProperty(name="Animation", description="Subfolder for animations", default="animation")
    render_archive_dir: StringProperty(
        name="Archive", description="Subfolder for sorting images by hand (the add-on never writes there)",
        default="archive")
    camera_collection: StringProperty(
        name="Cameras", description="Collection for new cameras (an existing one of this name is used)",
        default="Camera")
    light_collection: StringProperty(
        name="Lights", description="Collection for new lights (an existing one of this name is used)",
        default="Light")
    archive_collection: StringProperty(
        name="Archive", description="Collection for replaced objects (e.g. the old CAD version): hidden, "
        "you delete them yourself", default="Archive")
    name_scheme: BoolProperty(
        name="Name Scheme",
        description="Cameras P_ (perspective) or O_ (orthographic), lights A_ (area), P_ (point), S_ (spot) or "
        "D_ (sun), then what they show or do and a number: P_side_01, O_front_01, A_key_01. Off: only "
        "\".001\" and old prefixes are fixed", default=True)
    temp_collection: StringProperty(
        name="Temporary", description="Collection for Claude's helper objects, emptied after each answer",
        default="temp")
    material_library: StringProperty(
        name="Base Materials",
        description="A .blend file with materials marked as assets: shown as a group in the Material tab's "
                    "library (empty: a 'Base Materials.blend' in one of your asset libraries)",
        subtype='FILE_PATH', default="")
    low_resolution: IntProperty(
        name="Low: Resolution",
        description="Share of the scene resolution used for Low Quality",
        subtype='PERCENTAGE',
        default=50, min=1, max=100,
    )
    low_samples_percent: IntProperty(
        name="Low: Samples",
        description="Share of the High Quality samples (Render tab > Sampling) used for Low Quality",
        subtype='PERCENTAGE', default=50, min=1, max=100,
    )
    default_samples: IntProperty(
        name="High: Samples",
        description="Cycles samples for High Quality in new files and in files that still have Blender's "
                    "4096 (set once per scene; with Denoise and Noise Threshold 512 is enough for product shots)",
        default=512, min=1, max=65536,
    )
    auto_render: BoolProperty(
        name="Render Right Away",
        description="Start rendering as soon as a camera is added to the queue",
        default=True,
    )
    worker_gpu_only: BoolProperty(
        name="GPU Only",
        description="The queue renders on the GPU alone, without the CPU (on Apple Silicon much faster: "
                    "39 s instead of 96 s in a test). Off = the devices from Preferences › System",
        default=True,
    )
    notify: BoolProperty(
        name="Notify When Done",
        description="macOS notification with sound when a queue with several cameras is done",
        default=True,
    )
    cad_jobs: BoolProperty(
        name="CAD Jobs",
        description="Send changes from Blender to a Claude chat that works on the CAD model (Send CAD Job). "
                    "Off: the CAD box only imports versions and notes comments",
        default=True,
    )
    hdri_folder: StringProperty(
        name="HDRI Folder",
        description="HDRI files (.hdr, .exr) in this folder and its subfolders appear in the Light tab "
                    "(empty: an 'HDRI' folder in the add-on's data folder)",
        subtype='DIR_PATH',
        default="",
    )

    def draw_renders(self, layout):
        """Preferences › Renders: where images go and how good they are."""
        col = layout.column()
        col.use_property_split = True
        col.prop(self, "output_root")
        col.prop(self, "default_samples")
        col.prop(self, "low_resolution")
        col.prop(self, "low_samples_percent")
        col.separator()
        col.prop(self, "auto_render")
        col.prop(self, "notify")

    def draw_studio(self, layout):
        """Preferences › Studio: HDRIs, base materials, names, CAD jobs."""
        col = layout.column()
        col.use_property_split = True
        col.prop(self, "hdri_folder")
        col.prop(self, "material_library")
        col.prop(self, "name_scheme")
        col.prop(self, "cad_jobs")

    def draw_advanced(self, layout):
        """Preferences › Advanced: the Studio part (folder names, collections, shortcuts)."""
        box = layout.box()
        box.label(text="Render Folders", icon='RENDER_STILL')
        col = box.column()
        col.use_property_split = True
        col.prop(self, "output_suffix")
        sub = col.column(heading="Subfolders", align=True)
        for key in ("low_dir", "high_dir", "anim_dir", "render_archive_dir"):
            sub.prop(self, key)
        col.prop(self, "worker_gpu_only")
        col.prop(self, "material_preset_folder")

        box = layout.box()
        box.label(text="Collections and Shortcuts", icon='OUTLINER_COLLECTION')
        col = box.column()
        col.use_property_split = True
        sub = col.column(heading="Collections")
        sub.prop(self, "camera_collection")
        sub.prop(self, "light_collection")
        sub.prop(self, "archive_collection")
        sub.prop(self, "temp_collection")
        col.separator()
        for idname, _key, _modifiers, props, label in SHORTCUTS:
            kmi = _user_keymap_item(idname, props)
            if kmi is not None:
                row = col.row()
                row.label(text=label)
                row.prop(kmi, "type", text="", full_event=True)
        col.label(text="The shortcuts also work in the Node and Image Editor and in Edit Mode")


def _apply_clipping():
    try:
        cameras.apply_min_clipping()
    except Exception as ex:
        print("Render Studio: clipping:", ex)

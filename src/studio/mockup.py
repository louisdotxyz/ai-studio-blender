# SPDX-License-Identifier: GPL-3.0-or-later
"""Mockup: your product in a photo, at the place, size and angle you set – Krea only blends it in.

A mockup is a scene of its own ("Mockup <photo>") next to your studio scene, which stays untouched:
- the product: the same objects, linked (never moved – the CAMERA moves around it instead);
- a camera with the photo behind it: you see the product over the photo in the viewport;
- the photo's ground (catches the product's shadow, shows as a grid to line it up with the street or table),
  a key light, and the blurred photo as the light around the product.

What you set (StudioMockup, the panel's sliders – all live):
  the photo's camera   focal length, where the horizon is, roll, how high it was held
  the product          where in the photo (x, y), how big (or: standing on the ground → the size follows
                       from the place, true to perspective), turned / tilted / leaning
  the light            where it comes from, how strong, how warm, how soft the shadow
  the picture          the photo's format or another one (bars are left for Krea to fill)

Then: Render (in the background, transparent) → the montage (mockup_image.compose: shadow, exposure, warmth,
softness, glow, grain, night – sliders, nothing is rendered again) → Krea blends it in (krea-3 only inside
the product's box, or the whole picture) → the product is secured again (mockup_image.protect).
"""

import hashlib
import json
import math
import os
import time

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Matrix, Vector

from . import mockup_image

MOCK_KEY = "as_mockup"             # obj: made by the mockup (camera, ground, light)
HOLD_KEY = "as_mockup_front"       # obj in the studio scene: is in FRONT of the product in the photo (cuts it)
OF_KEY = "studio_mockup_of"        # scene: the studio scene it was made from
PRODUCT_COLL, FRONT_COLL = "Mockup Product", "Mockup In Front"
GROUND, KEY_LIGHT, WORLD = "Mockup Ground", "Mockup Key", "Mockup Light"
FORMATS = [('PHOTO', "Photo", "The photo's own format"),
           ('16:9', "16:9", "Wide; Krea fills what the photo does not cover"),
           ('3:2', "3:2", ""), ('4:3', "4:3", ""), ('1:1', "1:1", ""), ('4:5', "4:5", ""), ('9:16', "9:16", "")]
KREA_MODELS = [
    ('REGION', "Blend In", "Krea 3, only inside the product's box: light, shadow and grain are matched, the rest "
                           "of the photo stays pixel for pixel (about 77 credits at 2K)"),
    ('NANO21', "Whole Picture", "Nano Banana 2.1 paints the whole picture anew from your montage: for night, "
                                "another mood or filling a wider format (about 44 credits at 2K). People and things "
                                "can change a little; your product is put back afterwards"),
    ('PRECISE', "Around It", "Ideogram 4.5 Precise changes everything but the product (a mask keeps it as it "
                             "is): another environment, weather, time of day (about 163 credits)"),
]
COMPOSE_KEYS = ("shadow_strength", "exposure", "warmth", "saturation", "soften", "glow", "grain", "night")
_busy = [False]


def is_mockup(scene):
    return scene is not None and bool(scene.get(OF_KEY)) and bool(scene.studio_mockup.photo)


def source_scene(scene):
    return bpy.data.scenes.get(scene.get(OF_KEY, "")) if scene is not None else None


def mockups():
    return [s for s in bpy.data.scenes if s.get(OF_KEY)]


def _unit(scene):
    """Meters per Blender unit."""
    unit = scene.unit_settings
    return (unit.scale_length or 1.0) if unit.system != 'NONE' else 1.0


# ------------------------------------------------------------------ settings

def _changed(self, context):
    scene = self.id_data
    if not _busy[0] and is_mockup(scene):
        try:
            apply(scene)
        except Exception as ex:                    # a slider must never raise
            print("AI Studio mockup:", ex)


def _montage_changed(self, context):
    scene = self.id_data
    if not _busy[0] and is_mockup(scene) and self.render:
        _later["scenes"].add(scene.name)
        if not bpy.app.timers.is_registered(_montage_timer):
            bpy.app.timers.register(_montage_timer, first_interval=0.35)


class StudioMockup(PropertyGroup):
    photo: StringProperty(name="Photo", subtype='FILE_PATH', options=set())
    render: StringProperty(options={'HIDDEN'})          # the product's render the montage is made of
    result: StringProperty(options={'HIDDEN'})          # the montage
    waiting: FloatProperty(options={'HIDDEN'})          # time a render was asked for (0 = none)
    product_name: StringProperty(
        name="Product", default="product", options=set(),
        description="What the product is, in English, for Krea (\"small bike light\", \"wooden stool\")")
    format: EnumProperty(name="Format", items=FORMATS, default='PHOTO', update=_changed, options=set())
    # the photo's camera
    focal: FloatProperty(name="Focal Length", default=35.0, min=8.0, max=600.0, soft_max=200.0, step=100,
                         precision=0, update=_changed, options=set(), subtype='NONE',
                         description="The lens the photo was taken with (as on a full-frame camera, in mm). "
                                     "A phone: 24-28, a portrait: 50-85. Wrong = the product looks too wide or too flat")
    horizon: FloatProperty(name="Horizon", default=0.5, min=-2.0, max=3.0, soft_min=0.0, soft_max=1.0, step=1,
                           precision=3, update=_changed, options=set(),
                           description="Where the horizon is in the photo (0 = bottom edge, 1 = top edge): "
                                       "the height where street, floor or table edges would meet far away")
    roll: FloatProperty(name="Roll", default=0.0, min=-math.pi / 2, max=math.pi / 2, soft_min=-0.35, soft_max=0.35,
                        subtype='ANGLE', update=_changed, options=set(),
                        description="The photo was taken with the camera tilted sideways")
    camera_height: FloatProperty(name="Camera Height", default=1.5, min=0.02, max=100.0, soft_max=3.0, unit='LENGTH',
                                 update=_changed, options=set(),
                                 description="How high above the ground the photo was taken (in meters)")
    # the product in the photo
    pos_x: FloatProperty(name="X", default=0.5, min=-0.5, max=1.5, soft_min=0.0, soft_max=1.0, step=0.2, precision=3,
                         update=_changed, options=set(), description="Where in the photo: 0 = left, 1 = right")
    pos_y: FloatProperty(name="Y", default=0.5, min=-0.5, max=1.5, soft_min=0.0, soft_max=1.0, step=0.2, precision=3,
                         update=_changed, options=set(), description="Where in the photo: 0 = bottom, 1 = top")
    on_ground: BoolProperty(
        name="Stands on the Ground", default=False, update=_changed, options=set(),
        description="The product stands on the photo's ground: its size follows from where it stands (true to "
                    "perspective, from the camera's height). Off: you set the size yourself (held, mounted, hanging)")
    size: FloatProperty(name="Size", default=0.3, min=0.002, max=4.0, soft_max=1.0, step=0.5, precision=3,
                        update=_changed, options=set(), subtype='FACTOR',
                        description="How big the product is in the picture (its longest side as a share of the "
                                    "picture's height)")
    turn: FloatProperty(name="Turn", default=0.0, min=-math.pi, max=math.pi, subtype='ANGLE', update=_changed,
                        options=set(), description="Turns the product around its upright axis")
    tilt: FloatProperty(name="Tilt", default=0.0, min=-math.pi, max=math.pi, subtype='ANGLE', update=_changed,
                        options=set(), description="Tilts the product towards or away from the camera's side")
    lean: FloatProperty(name="Lean", default=0.0, min=-math.pi, max=math.pi, subtype='ANGLE', update=_changed,
                        options=set(), description="Leans the product sideways")
    # light
    light_from: FloatProperty(name="Light From", default=math.radians(40), min=-math.pi, max=math.pi,
                              subtype='ANGLE', update=_changed, options=set(),
                              description="Where the main light comes from, seen from the camera: 0° = from "
                                          "behind the camera, 90° = from the right, -90° = from the left, "
                                          "180° = from behind the product (backlight)")
    light_height: FloatProperty(name="Height", default=math.radians(45), min=0.0, max=math.pi / 2, subtype='ANGLE',
                                update=_changed, options=set(),
                                description="How high the main light stands (90° = straight from above)")
    light_strength: FloatProperty(name="Strength", default=3.0, min=0.0, max=50.0, soft_max=10.0, update=_changed,
                                  options=set(), description="The main light (0 = only the photo's own light)")
    light_warmth: FloatProperty(name="Warmth", default=5500.0, min=1500.0, max=12000.0, step=10000, precision=0,
                                update=_changed, options=set(),
                                description="The main light's colour in Kelvin (candle 1900, bulb 2700, "
                                            "street lamp 2200-4000, daylight 5500, shade 7500)")
    light_soft: FloatProperty(name="Shadow Softness", default=0.15, min=0.0, max=1.0, subtype='FACTOR',
                              update=_changed, options=set(),
                              description="0 = a hard shadow (sun, flash), 1 = very soft (overcast)")
    ambient: FloatProperty(name="Photo Light", default=1.0, min=0.0, max=10.0, soft_max=3.0, update=_changed,
                           options=set(),
                           description="How strongly the photo itself lights the product (its colours all around)")
    use_ground: BoolProperty(name="Shadow on the Ground", default=True, update=_changed, options=set(),
                             description="The photo's ground catches the product's shadow (and its light)")
    show_grid: BoolProperty(name="Grid", default=True, update=_changed, options=set(),
                            description="Show the photo's ground as a grid: move Horizon, Focal Length and Camera "
                                        "Height until it lies on the street, floor or table of the photo")
    # the montage (no new render for these)
    shadow_strength: FloatProperty(name="Shadow", default=1.0, min=0.0, max=1.5, subtype='FACTOR',
                                   update=_montage_changed, options=set())
    exposure: FloatProperty(name="Exposure", default=0.0, min=-4.0, max=4.0, soft_min=-2.0, soft_max=2.0,
                            update=_montage_changed, options=set(),
                            description="The product brighter or darker (in stops)")
    warmth: FloatProperty(name="Warmth", default=0.0, min=-1.0, max=1.0, subtype='FACTOR',
                          update=_montage_changed, options=set(), description="The product warmer or cooler")
    saturation: FloatProperty(name="Saturation", default=1.0, min=0.0, max=2.0, subtype='FACTOR',
                              update=_montage_changed, options=set())
    soften: FloatProperty(name="Softness", default=0.0, min=0.0, max=5.0, soft_max=2.0,
                          update=_montage_changed, options=set(),
                          description="The product as soft as the photo (a phone photo, motion, a little out of focus)")
    glow: FloatProperty(name="Glow", default=0.0, min=0.0, max=3.0, soft_max=1.0, subtype='FACTOR',
                        update=_montage_changed, options=set(),
                        description="What shines on the product (a lamp, a display) glows over the photo")
    grain: FloatProperty(name="Grain", default=0.0, min=0.0, max=3.0, soft_max=1.0, subtype='FACTOR',
                         update=_montage_changed, options=set(), description="Film grain on the product, as the photo has")
    night: FloatProperty(name="Darker Photo", default=0.0, min=0.0, max=1.0, subtype='FACTOR',
                         update=_montage_changed, options=set(),
                         description="The photo darker and cooler (towards dusk and night); the product keeps its light")
    # Krea
    krea_model: EnumProperty(name="Krea", items=KREA_MODELS, default='REGION', options=set())
    fidelity: FloatProperty(name="Keep My Product", default=1.0, min=0.0, max=1.0, subtype='FACTOR', options=set(),
                            description="After Krea: 100 % = every edge and detail of the product from your render "
                                        "(Krea's light and colour stay); 0 % = Krea's product as it painted it")
    exact: BoolProperty(name="Pixel-Exact", default=False, options=set(),
                        description="After Krea the product is put back exactly as rendered (Krea changes only "
                                    "what is around it)")
    wish: StringProperty(name="Wish", options={'SKIP_SAVE'},
                         description="What Krea should do besides blending the product in (\"night, wet street\")")


# ------------------------------------------------------------------ geometry

def photo_size(path):
    from . import krea
    return krea.image_size(bpy.path.abspath(path))


def frame_of(m):
    """((width, height) of the picture, (x, y, w, h) of the photo in it)."""
    w, h = photo_size(m.photo)
    return mockup_image.frame_size(w, h, m.format)


def product_bounds(scene):
    from . import krea_scene
    return krea_scene.product_bounds(scene)


def solve(m, lo, hi, frame, rect, unit=1.0):
    """Where the camera stands (the product stays where it is). Returns {"camera": world matrix, "lens",
    "ground": world matrix of the photo's ground under the product, "light": world rotation (3x3) of the key
    light, "to_world": the photo's world (camera at x = y = 0, ground z = 0, looking along +Y) → Blender's,
    "distance", "above_ground", "pixels": the product's longest side in the picture}."""
    (W, H), (rx, ry, rw, rh) = frame, rect
    f_px = m.focal / 36.0 * rw
    px, py = rx + m.pos_x * rw, ry + m.pos_y * rh
    ray = Vector(((px - W / 2) / f_px, (py - H / 2) / f_px, -1.0))
    down = math.atan((ry + m.horizon * rh - H / 2) / f_px)       # horizon above the middle: looking down
    rot_cv = Matrix.Rotation(math.pi / 2 - down, 4, 'X') @ Matrix.Rotation(-m.roll, 4, 'Z')
    cam_h = m.camera_height / unit
    center = (lo + hi) / 2
    size = hi - lo
    longest = max(size.length * 0.75, max(size), 1e-6)            # what "size" measures: about the longest side
    dir_v = rot_cv.to_3x3() @ ray
    depth = None
    if m.on_ground and dir_v.z < -1e-3:
        t = (cam_h - size.z / 2) / -dir_v.z                      # its foot on the ground
        if t > 0:
            depth = t
    if depth is None:
        depth = f_px * longest / max(1e-6, m.size * H)
    p_cam = ray * depth
    rot_obj = Matrix.Rotation(m.turn, 4, 'Z') @ Matrix.Rotation(m.tilt, 4, 'X') @ Matrix.Rotation(m.lean, 4, 'Y')
    product_in_cam = Matrix.Translation(p_cam) @ rot_cv.inverted() @ rot_obj
    camera = Matrix.Translation(center) @ product_in_cam.inverted()
    cam_v = Matrix.Translation((0.0, 0.0, cam_h)) @ rot_cv
    to_world = camera @ cam_v.inverted()
    p_v = cam_v @ p_cam
    above = p_v.z - size.z / 2
    ground_z = min(0.0, above)                                    # never above the product's foot
    ground = to_world @ Matrix.Translation((p_v.x, p_v.y, ground_z))
    a, e = m.light_from, m.light_height
    to_light = Vector((math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e)))
    light = to_world.to_3x3() @ to_light.to_track_quat('Z', 'Y').to_matrix()
    return {"camera": camera, "lens": f_px * 36.0 / W, "ground": ground, "light": light, "to_world": to_world,
            "distance": depth * ray.length, "above_ground": above * unit, "pixels": f_px * longest / depth}


def _kelvin(k):
    """A light colour (linear RGB) for a temperature, daylight = white."""
    t = max(1000.0, min(40000.0, k)) / 100.0
    r = 255.0 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * (t - 60) ** -0.0755148492
    b = 255.0 if t >= 66 else (0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307)
    rgb = [max(0.0, min(255.0, v)) / 255.0 for v in (r, g, b)]
    white = (1.0, 0.9357, 0.8773)                                 # what this formula gives at 5500 K
    lin = [(c / w) ** 2.2 for c, w in zip(rgb, white)]
    top = max(lin)
    return [c / top for c in lin]


def apply(scene):
    """Camera, ground, light and picture size from the settings."""
    m = scene.studio_mockup
    bounds = product_bounds(scene)
    camera = scene.camera
    if bounds is None or camera is None or not m.photo:
        return None
    frame, rect = frame_of(m)
    unit = _unit(scene)
    found = solve(m, bounds[0], bounds[1], frame, rect, unit)
    r = scene.render
    r.resolution_x, r.resolution_y, r.resolution_percentage = frame[0], frame[1], 100
    camera.matrix_world = found["camera"]
    cam = camera.data
    cam.sensor_fit, cam.sensor_width, cam.lens = 'HORIZONTAL', 36.0, found["lens"]
    cam.clip_start = max(0.0005 / unit, found["distance"] / 200.0)
    cam.clip_end = max(cam.clip_end, found["distance"] * 50.0, 100.0 / unit)
    ground = scene.objects.get(GROUND + _suffix(scene))
    if ground is not None:
        side = max(20.0 / unit, found["distance"] * 6.0)
        ground.matrix_world = found["ground"] @ Matrix.Diagonal((side, side, side, 1.0))
        ground.hide_render = not m.use_ground
        ground.hide_viewport = not m.show_grid
    light = scene.objects.get(KEY_LIGHT + _suffix(scene))
    if light is not None:
        mat = found["light"].to_4x4()
        mat.translation = found["camera"].translation
        light.matrix_world = mat
        light.data.energy = m.light_strength
        light.data.color = _kelvin(m.light_warmth)
        light.data.angle = math.radians(0.5 + 60.0 * m.light_soft)
        light.hide_render = light.hide_viewport = m.light_strength <= 0.0
    world = scene.world
    if world is not None and world.node_tree is not None:
        back = world.node_tree.nodes.get("Background")
        mapping = world.node_tree.nodes.get("Mapping")
        if back is not None:
            back.inputs["Strength"].default_value = m.ambient
        if mapping is not None:                               # the photo around the product, turned with the camera
            mapping.inputs["Rotation"].default_value = found["to_world"].to_3x3().inverted().to_euler()
    return found


def _suffix(scene):
    return " " + scene.name[len("Mockup "):] if scene.name.startswith("Mockup ") else " " + scene.name


# ------------------------------------------------------------------ the scene

def _copy_rna(src, dst, skip=()):
    for prop in src.bl_rna.properties:
        key = prop.identifier
        if key in skip or key == "rna_type" or prop.is_readonly or prop.type in {'POINTER', 'COLLECTION'}:
            continue
        try:
            setattr(dst, key, getattr(src, key))
        except (AttributeError, TypeError, ValueError, RuntimeError):
            pass


def _data_dir():
    from .paths import data_dir
    folder = os.path.join(data_dir(), "mockup")
    os.makedirs(folder, exist_ok=True)
    return folder


def _key(path):
    return hashlib.sha1(os.path.normcase(os.path.abspath(path)).encode("utf-8")).hexdigest()[:16]


def _read(path, longest=0):
    """(h, w, 4) float array of an image file (row 0 at the bottom)."""
    from . import image_edit
    pixels, (w, h), _is_float = image_edit._read(path, longest)
    return pixels.reshape(h, w, 4)


def _write(array, path):
    from . import image_edit
    h, w = array.shape[:2]
    image_edit._write(array.reshape(-1), (w, h), path)


def _environment_image(photo):
    """The blurred photo as a small all-round picture (a file in the add-on's data folder)."""
    path = os.path.join(_data_dir(), _key(photo) + "_env.png")
    if not os.path.exists(path) or os.path.getmtime(path) < os.path.getmtime(photo):
        import numpy as np
        env = mockup_image.environment(_read(photo, 256))
        full = np.ones(env.shape[:2] + (4,), dtype=np.float32)
        full[..., :3] = env
        _write(full, path)
    return bpy.data.images.load(path, check_existing=True)


def _world(scene, photo):
    world = bpy.data.worlds.new(WORLD + _suffix(scene))
    world.use_nodes = True
    tree = world.node_tree
    tree.nodes.clear()
    coord = tree.nodes.new('ShaderNodeTexCoord')
    mapping = tree.nodes.new('ShaderNodeMapping')
    mapping.name, mapping.vector_type = "Mapping", 'VECTOR'
    env = tree.nodes.new('ShaderNodeTexEnvironment')
    env.image = _environment_image(photo)
    back = tree.nodes.new('ShaderNodeBackground')
    back.name = "Background"
    out = tree.nodes.new('ShaderNodeOutputWorld')
    for n, node in enumerate((coord, mapping, env, back, out)):
        node.location = (n * 220 - 600, 0)
    tree.links.new(coord.outputs["Generated"], mapping.inputs["Vector"])
    tree.links.new(mapping.outputs["Vector"], env.inputs["Vector"])
    tree.links.new(env.outputs["Color"], back.inputs["Color"])
    tree.links.new(back.outputs["Background"], out.inputs["Surface"])
    scene.world = world


def _ground(scene, collection):
    import bmesh
    mesh = bpy.data.meshes.new(GROUND + _suffix(scene))
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=20, y_segments=20, size=0.5)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(mesh.name, mesh)
    obj[MOCK_KEY] = True
    obj.is_shadow_catcher = True
    obj.display_type = 'WIRE'                            # the viewport shows it as a grid; renders catch shadows
    obj.hide_select = True
    collection.objects.link(obj)
    return obj


def create(context, photo, source=None):
    """A mockup scene for `photo` from the studio scene `source` (default: the one shown). The window shows
    it; returns the scene."""
    from . import core, krea_scene
    photo = bpy.path.abspath(photo)
    if not os.path.isfile(photo):
        raise ValueError("The photo is missing: %s" % photo)
    source = source or context.scene
    if source.get(OF_KEY):
        source = source_scene(source) or source
    products = krea_scene.product_objects(source)
    if not products:
        raise ValueError("No product in the scene: nothing to put into the photo")
    stem = core.safe_name(os.path.splitext(os.path.basename(photo))[0])[:40]
    _busy[0] = True
    try:
        scene = bpy.data.scenes.new("Mockup " + stem)
        scene[OF_KEY] = source.name
        _copy_rna(source.render, scene.render, skip={"filepath", "resolution_x", "resolution_y",
                                                     "resolution_percentage", "use_border", "use_crop_to_border"})
        for part in ("cycles", "view_settings", "display_settings", "unit_settings"):
            if hasattr(source, part):
                _copy_rna(getattr(source, part), getattr(scene, part))
        for key in source.keys():                         # the values your materials and drivers read
            if key not in {"studio", "studio_mockup", OF_KEY} and not key.startswith(("studio_", "_")):
                try:
                    scene[key] = source[key]
                except (TypeError, ValueError):
                    pass
        try:
            for key in source.view_layers[0].keys():
                scene.view_layers[0][key] = source.view_layers[0][key]
        except (IndexError, TypeError, ValueError):
            pass
        scene.render.film_transparent = True
        scene.render.use_compositing = False
        try:
            scene.studio.background_mode = 'TRANSPARENT'
            scene.studio.output_name = source.studio.output_name
        except (AttributeError, TypeError):
            pass
        coll = bpy.data.collections.new(PRODUCT_COLL + _suffix(scene))
        scene.collection.children.link(coll)
        wanted = set(products)
        for obj in ([] if krea_scene.is_marked(source) else source.objects):   # instances (a collection as one object)
            if obj.type == 'EMPTY' and obj.instance_type == 'COLLECTION' and not obj.hide_render \
                    and obj.instance_collection is not None and not obj.instance_collection.name.startswith("ENV |"):
                wanted.add(obj)
        front = [o for o in source.objects if o.get(HOLD_KEY)]
        for obj in wanted - set(front):
            coll.objects.link(obj)
        if front:
            hold = bpy.data.collections.new(FRONT_COLL + _suffix(scene))
            scene.collection.children.link(hold)
            for obj in front:
                hold.objects.link(obj)
            scene.view_layers[0].layer_collection.children[hold.name].holdout = True
        own = bpy.data.collections.new("Mockup" + _suffix(scene))
        scene.collection.children.link(own)
        cam_data = bpy.data.cameras.new("M_" + stem)
        camera = bpy.data.objects.new("M_" + stem, cam_data)
        camera[MOCK_KEY] = True
        own.objects.link(camera)
        scene.camera = camera
        cam_data.show_background_images = True
        back = cam_data.background_images.new()
        back.image = bpy.data.images.load(photo, check_existing=True)
        back.alpha, back.display_depth, back.frame_method = 1.0, 'BACK', 'FIT'
        cam_data.passepartout_alpha = 1.0
        _ground(scene, own)
        sun = bpy.data.objects.new(KEY_LIGHT + _suffix(scene), bpy.data.lights.new(KEY_LIGHT + _suffix(scene), 'SUN'))
        sun[MOCK_KEY] = True
        own.objects.link(sun)
        _world(scene, photo)
        m = scene.studio_mockup
        m.photo = photo
        focal = mockup_image.exif_focal(photo)
        if focal:
            m.focal = focal
        colour, bright = mockup_image.mean_colour(_read(photo, 128))
        m.light_strength = round(max(0.3, min(6.0, bright * 8.0)), 2)     # a dark photo: little light
        warm = colour[0] / max(1e-4, colour[2])
        m.light_warmth = max(2200.0, min(9000.0, 5500.0 / max(0.4, min(2.5, warm ** 0.8))))
    finally:
        _busy[0] = False
    apply(scene)
    show(context, scene)
    return scene


def show(context, scene):
    """The window shows the scene, its 3D views look through its camera."""
    window = context.window or (context.window_manager.windows[0] if context.window_manager.windows else None)
    if window is None:
        return
    window.scene = scene
    if not is_mockup(scene):
        return
    for area in window.screen.areas:
        if area.type == 'VIEW_3D':
            space = area.spaces.active
            space.region_3d.view_perspective = 'CAMERA'
            if space.shading.type in {'SOLID', 'WIREFRAME'}:
                space.shading.type = 'MATERIAL'
                space.shading.use_scene_lights = space.shading.use_scene_world = True
            area.tag_redraw()


def remove(scene):
    """The mockup scene goes (its camera, ground, light and world); the product and the files stay."""
    if not scene.get(OF_KEY):
        return
    world = scene.world
    for obj in [o for o in scene.objects if o.get(MOCK_KEY)]:
        data = obj.data
        bpy.data.objects.remove(obj)
        if data is not None and data.users == 0:
            getattr(bpy.data, {"Mesh": "meshes", "Camera": "cameras"}.get(type(data).__name__, "lights")).remove(data)
    colls = [c for c in scene.collection.children if c.name.startswith("Mockup")]
    bpy.data.scenes.remove(scene)
    for coll in colls:
        if coll.users == 0:
            bpy.data.collections.remove(coll)
    if world is not None and world.users == 0:
        bpy.data.worlds.remove(world)


def state(scene):
    """Everything about the mockup, for the chat: the settings and what follows from them."""
    m = scene.studio_mockup
    out = {"scene": scene.name, "of": scene.get(OF_KEY, ""), "photo": m.photo}
    for prop in m.bl_rna.properties:
        key = prop.identifier
        if key in {"rna_type", "name", "photo", "waiting", "wish"}:
            continue
        value = getattr(m, key)
        if prop.type == 'FLOAT' and prop.subtype == 'ANGLE':
            value = round(math.degrees(value), 1)          # angles in degrees
        elif isinstance(value, float):
            value = round(value, 4)
        out[key] = value
    bounds = product_bounds(scene)
    if bounds is not None and m.photo and os.path.isfile(bpy.path.abspath(m.photo)):
        frame, rect = frame_of(m)
        found = solve(m, bounds[0], bounds[1], frame, rect, _unit(scene))
        size = (bounds[1] - bounds[0]) * _unit(scene) * 100.0
        out.update(picture=list(frame), photo_in_picture=list(rect),
                   product_cm=[round(v, 1) for v in size],
                   product_px=round(found["pixels"]), distance_m=round(found["distance"] * _unit(scene), 2),
                   above_ground_m=round(found["above_ground"], 2))
    return out


ANGLES = {"roll", "turn", "tilt", "lean", "light_from", "light_height"}


def set_values(scene, **values):
    """Sets settings by name (angles in degrees); one update at the end. Returns state(scene)."""
    m = scene.studio_mockup
    unknown = [k for k in values if k not in m.bl_rna.properties.keys()]
    if unknown:
        raise ValueError("No such mockup setting: %s (see api.mockup_state())" % ", ".join(unknown))
    montage = False
    _busy[0] = True
    try:
        for key, value in values.items():
            setattr(m, key, math.radians(value) if key in ANGLES else value)
            montage = montage or key in COMPOSE_KEYS
    finally:
        _busy[0] = False
    apply(scene)
    if montage and m.render:
        compose(scene)
    return state(scene)


def load_fspy(scene, path):
    """The photo's camera from an fSpy (or Blenspy) project file: focal length, horizon, roll, height."""
    found = mockup_image.read_fspy(bpy.path.abspath(path))
    m = scene.studio_mockup
    frame, rect = frame_of(m)
    f_px = found["focal"] / 36.0 * rect[2]
    values = {"focal": round(found["focal"], 1), "roll": math.degrees(found["roll"]),
              "horizon": 0.5 + math.tan(-found["pitch"]) * f_px / rect[3]}
    if found["height"] > 0.02:
        values["camera_height"] = found["height"]
    return set_values(scene, **values)


# ------------------------------------------------------------------ render and montage

def _hidden_for_mask(scene):
    from . import krea_scene
    products = set(krea_scene.product_objects(scene))
    front = {o for c in scene.collection.children if c.name.startswith(FRONT_COLL) for o in c.objects}
    instances = {o for o in scene.objects if o.type == 'EMPTY' and o.instance_type == 'COLLECTION'}
    return [o for o in scene.objects if o not in products | front | instances and not o.hide_render], bool(front)


def product_render(scene, longest=0):
    """(h, w, 4): only the product through the mockup's camera, quickly (Workbench; with things in front of
    it EEVEE, which can cut them out). Its alpha is the product's coverage; its colours a plain shading."""
    from . import krea_scene
    r = scene.render
    keep = {key: getattr(r, key) for key in krea_scene.RENDER_KEYS if hasattr(r, key)}
    image_keep = (r.image_settings.file_format, r.image_settings.color_mode)
    hidden, has_front = _hidden_for_mask(scene)
    target = os.path.join(_data_dir(), "product_%d.png" % os.getpid())
    try:
        for o in hidden:
            o.hide_render = True
        engine = 'BLENDER_WORKBENCH'
        if has_front:
            engines = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
            engine = next((e for e in ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT') if e in engines), engine)
        r.engine = engine
        if longest and max(r.resolution_x, r.resolution_y) > longest:
            r.resolution_percentage = max(1, round(100 * longest / max(r.resolution_x, r.resolution_y)))
        r.film_transparent = True
        for key in ("use_compositing", "use_sequencer", "use_border"):
            if hasattr(r, key):
                setattr(r, key, False)
        r.image_settings.file_format, r.image_settings.color_mode = 'PNG', 'RGBA'
        bpy.ops.render.render(scene=scene.name)
        bpy.data.images["Render Result"].save_render(target, scene=scene)
    finally:
        for o in hidden:
            o.hide_render = False
        for key, value in keep.items():
            setattr(r, key, value)
        try:
            r.image_settings.file_format, r.image_settings.color_mode = image_keep
        except (TypeError, ValueError):
            pass
    try:
        return _read(target)
    finally:
        try:
            os.remove(target)
        except OSError:
            pass


def _photo_on_frame(m, width, height):
    """The photo on the picture's frame at the render's size: ((h, w, 3), the bars (h, w))."""
    frame, rect = frame_of(m)
    f = width / frame[0]
    rect = (round(rect[0] * f), round(rect[1] * f), min(width, round(rect[2] * f)), min(height, round(rect[3] * f)))
    return mockup_image.place_photo(_read(bpy.path.abspath(m.photo), max(rect[2], rect[3])), (width, height), rect)


def placement_preview(scene, longest=1024):
    """A quick look for checking place, size and angle (no Cycles): the product plainly shaded over the photo.
    Returns the JPEG's path."""
    shaded = product_render(scene, longest)
    h, w = shaded.shape[:2]
    photo, _bars = _photo_on_frame(scene.studio_mockup, w, h)
    picture = mockup_image.compose(photo, shaded, shaded[..., 3])
    path = os.path.join(_data_dir(), "placement_%s.jpg" % _key(scene.name))
    image = bpy.data.images.new("Mockup Placement", w, h, alpha=False)
    try:
        image.pixels.foreach_set(picture.reshape(-1))
        image.filepath_raw, image.file_format = path, 'JPEG'
        image.save()
    finally:
        bpy.data.images.remove(image)
    return path


def render(scene, quality='LOW'):
    """Renders the product (transparent, in the background through the render queue). The montage follows
    by itself when it is done."""
    from . import runner
    m = scene.studio_mockup
    apply(scene)
    try:
        scene.studio.still_format = 'PNG'
        scene.studio.background_mode = 'TRANSPARENT'
    except (AttributeError, TypeError):
        pass
    with bpy.context.temp_override(scene=scene):
        result = bpy.ops.studio.add(quality=quality)
    if 'FINISHED' not in result:
        raise RuntimeError("The render could not be started")
    m.waiting = time.time()
    if not runner.RUN.busy:
        runner.RUN.start(scene)
    elif runner.RUN.scene_name != scene.name and not bpy.app.timers.is_registered(_watch):
        bpy.app.timers.register(_watch, first_interval=2.0)   # another scene renders: this one after it
    return "rendering"


def _watch():
    """Timer: the queue was busy with another scene; a waiting mockup starts when it is free."""
    from . import runner
    waiting = [s for s in mockups() if s.studio_mockup.waiting and len(s.studio.items)]
    if not waiting:
        return None
    if not runner.RUN.busy:
        try:
            runner.RUN.start(waiting[0])
        except RuntimeError as ex:
            print("AI Studio mockup:", ex)
            waiting[0].studio_mockup.waiting = 0.0
    return 2.0


_pending = []


def on_saved(path, _camera, _quality, animation):
    """(runner.on_render_saved) A mockup scene's render is saved: its montage, a moment later."""
    from . import runner
    scene = bpy.data.scenes.get(runner.RUN.scene_name)
    if animation or scene is None or not is_mockup(scene) or not str(path).lower().endswith(".png"):
        return
    _pending.append((scene.name, path))
    if not bpy.app.timers.is_registered(_after_render):
        bpy.app.timers.register(_after_render, first_interval=0.3)


def _after_render():
    while _pending:
        name, path = _pending.pop(0)
        scene = bpy.data.scenes.get(name)
        if scene is None:
            continue
        scene.studio_mockup.waiting = 0.0
        try:
            use_render(scene, path)
        except Exception as ex:
            print("AI Studio mockup: montage:", ex)
    return None


def _alpha_file(render_path):
    return os.path.join(_data_dir(), _key(render_path) + "_alpha.npy")


def use_render(scene, path):
    """`path` (the product's transparent render) becomes the mockup's render: its coverage is measured and
    the montage made. Returns the montage's path."""
    import numpy as np
    from . import krea
    m = scene.studio_mockup
    width, height = krea.image_size(path)
    frame, _rect = frame_of(m)
    longest = max(width, height) if (width, height) != tuple(frame) else 0
    cover = product_render(scene, longest)[..., 3]
    if cover.shape != (height, width):
        cover = mockup_image.resize(cover, width, height)
    np.save(_alpha_file(path), cover.astype(np.float16))
    m.render = path
    return compose(scene)


def montage_path(render_path):
    return os.path.splitext(render_path)[0] + "_mockup.png"


def compose(scene):
    """The montage from the last render and the montage sliders → <render>_mockup.png, shown. Returns it."""
    import numpy as np
    m = scene.studio_mockup
    path = bpy.path.abspath(m.render)
    if not m.render or not os.path.isfile(path):
        raise ValueError("Render the mockup first")
    rendered = _read(path)
    h, w = rendered.shape[:2]
    try:
        cover = np.load(_alpha_file(path)).astype(np.float32)
    except (OSError, ValueError):
        cover = rendered[..., 3]
    photo, _bars = _photo_on_frame(m, w, h)
    values = {"shadow": m.shadow_strength, "exposure": m.exposure, "warmth": m.warmth, "saturation": m.saturation,
              "soften": m.soften, "glow": m.glow, "grain": m.grain, "night": m.night}
    picture = mockup_image.compose(photo, rendered, cover, values)
    target = montage_path(path)
    _write(picture, target)
    m.result = target
    _show_file(target)
    return target


def _show_file(path):
    from . import runner, tab_queue
    runner._images_cache["time"] = 0.0
    tab_queue._view["path"] = path
    image = bpy.data.images.get(runner.PREVIEW_IMAGE)
    if image is not None and os.path.normpath(bpy.path.abspath(image.filepath)) == os.path.normpath(path):
        image.reload()
    wm = bpy.context.window_manager
    for window in (wm.windows if wm else ()):
        for area in window.screen.areas:
            area.tag_redraw()


_later = {"scenes": set()}


def _montage_timer():
    names, _later["scenes"] = _later["scenes"], set()
    for name in names:
        scene = bpy.data.scenes.get(name)
        if scene is not None and is_mockup(scene):
            try:
                compose(scene)
            except Exception as ex:
                print("AI Studio mockup: montage:", ex)
    return None


# ------------------------------------------------------------------ Krea

BLEND = ("so it looks photographed in place: match the scene's light direction, colour temperature, contrast, "
         "lens softness and film grain, with a natural contact shadow and light spill on what it touches")
_sent = {}       # the file sent to Krea → what is needed to finish its result


def krea_prompt(key, name, wish="", bars=False, close_up=False, scale=""):
    """The prompt for a mockup, after the models' own guides: the change first, each picture's role named,
    what must stay in one sentence at the end, said as what is wanted (not what is not)."""
    wish = wish.strip().rstrip(".")
    wish = (wish[0].upper() + wish[1:] + ". ") if wish else ""
    if key == 'REGION':
        return ("%sBlend the %s in @product into this photograph %s. Keep its shape, size, position, proportions, "
                "materials and every detail exactly as they are, keeping everything else the same."
                % (wish, name, BLEND))
    if key == 'PRECISE':
        return ("%sMatch the light on and around the %s to the scene, with a natural contact shadow. "
                "Keep the %s itself exactly as it is." % (wish, name, name))
    text = ("Turn image 1 into one realistic photograph. Image 1 is a photo with a %s already composited in at "
            "the correct position, size and perspective. " % name)
    if close_up:
        text += "Image 2 shows the same %s up close, for its details only. " % name
    text += wish + "Blend the %s into the scene %s. " % (name, BLEND)
    if bars:
        text += "Fill the black bars at the edges by continuing the scene naturally. "
    if scale:
        text += scale.strip() + " "
    text += ("Keep exactly the same: the %s's position, size, shape, materials and details, every person and "
             "object, the camera angle and the framing." % name)
    return text


def _close_up(render_path, cover, target):
    """The product alone on grey, cropped (the model's reference for its details). '' when it is tiny."""
    import numpy as np
    ys, xs = np.nonzero(cover > 0.5)
    if not len(xs) or max(xs.max() - xs.min(), ys.max() - ys.min()) < 160:
        return ""
    pad = 12
    y0, y1 = max(0, ys.min() - pad), min(cover.shape[0], ys.max() + pad)
    x0, x1 = max(0, xs.min() - pad), min(cover.shape[1], xs.max() + pad)
    rendered = _read(render_path)[y0:y1, x0:x1]
    a = np.minimum(rendered[..., 3], cover[y0:y1, x0:x1])[..., None]
    out = np.ones(rendered.shape, dtype=np.float32)
    out[..., :3] = rendered[..., :3] * a + 0.5 * (1.0 - a)
    _write(out, target)
    return target


def krea_plan(scene, wish=None, model=None):
    """What goes to Krea for this mockup: {"key", "prompt", "send" (the montage, padded to a format Krea
    knows), "mask" (Around It), "close_up", "box" (Blend In), "size"}. Nothing is sent yet."""
    import numpy as np
    from . import krea, krea_scene
    m = scene.studio_mockup
    if not m.result or not os.path.isfile(m.result):
        raise ValueError("Render the mockup first")
    key = (model or m.krea_model).upper()
    if key not in {k for k, _n, _d in KREA_MODELS}:
        raise ValueError("Model: REGION, NANO21 or PRECISE")
    wish = m.wish if wish is None else wish
    montage = _read(m.result)
    h, w = montage.shape[:2]
    render_path = bpy.path.abspath(m.render)
    try:
        cover = np.load(_alpha_file(render_path)).astype(np.float32)
    except (OSError, ValueError):
        cover = _read(render_path)[..., 3]
    whole = _read(render_path)[..., 3]                     # product and its shadow
    frame, rect = frame_of(m)
    bars = tuple(frame) != (rect[2], rect[3])
    # Krea's models give back one of their own formats: send that format (edges repeated), cut it off again
    ratio = krea.nearest_ratio(w, h)
    rw, rh = (int(v) for v in ratio.split(":"))
    W, H = max(w, math.ceil(h * rw / rh)), max(h, math.ceil(w * rh / rw))
    x0, y0 = (W - w) // 2, (H - h) // 2
    padded = np.pad(montage, ((y0, H - h - y0), (x0, W - w - x0), (0, 0)), mode="edge")
    base = os.path.join(_data_dir(), _key(m.result))
    send = base + "_send.png"
    _write(padded, send)
    plan = {"key": key, "send": send, "size": [W, H], "crop": [x0, y0, w, h], "montage": m.result, "ratio": ratio,
            "mask": "", "close_up": "", "box": None, "fidelity": m.fidelity, "exact": m.exact}
    padded_cover = np.pad(np.maximum(cover, (whole > 0.25) * 1.0), ((y0, H - h - y0), (x0, W - w - x0)))
    if key == 'REGION':
        plan["box"] = mockup_image.box_of(padded_cover)
        if plan["box"] is None:
            raise ValueError("The product is not in the picture")
    if key == 'PRECISE':
        mask = np.ones((H, W, 4), dtype=np.float32)
        mask[..., :3] = (np.pad(cover, ((y0, H - h - y0), (x0, W - w - x0))) < 0.02)[..., None]
        plan["mask"] = base + "_mask.png"
        _write(mask, plan["mask"])
    if key == 'NANO21':
        plan["close_up"] = _close_up(render_path, cover, base + "_product.png")
    scale = ""
    if key == 'NANO21':
        info = krea_scene.product_scale(scene, scene.camera, w, h)
        if info:
            x, y, z = info["size_cm"]
            scale = "True scale: the %s is about %s × %s × %s cm." % (
                m.product_name, krea_scene._num(x), krea_scene._num(y), krea_scene._num(z))
    plan["prompt"] = krea_prompt(key, m.product_name.strip() or "product", wish, bars, bool(plan["close_up"]), scale)
    _sent[os.path.normpath(send)] = plan
    try:
        with open(base + "_plan.json", "w", encoding="utf-8") as f:
            json.dump(plan, f)
    except OSError:
        pass
    return plan


MODEL_PATHS = {'REGION': "krea/krea-3", 'NANO21': "google/nano-banana-2.1", 'PRECISE': "ideogram/ideogram-4.5-precise"}
UNITS = {'REGION': 77, 'NANO21': 44, 'PRECISE': 163}     # Krea's quotes at 2K, Nov 2026


def krea_body(plan, image_url, mask_url="", close_up_url=""):
    """Krea's request for a plan (after its pictures were uploaded)."""
    W, H = plan["size"]
    resolution = "2K" if max(W, H) > 1100 else "1K"
    if plan["key"] == 'REGION':
        x, y, w, h = plan["box"]
        return {"prompt": plan["prompt"], "image_urls": [image_url], "thinking_mode": "fast",
                "resolution": resolution, "aspect_ratio": plan["ratio"], "regional_edit": True,
                "regions": [{"label": "product", "x": x, "y": y, "width": w, "height": h, "image_idx": 0,
                             "note": "The composited product: only its light, shadow and grain may change."}]}
    if plan["key"] == 'PRECISE':
        body = {"prompt": plan["prompt"], "image_url": image_url, "quality": "high"}
        if mask_url:
            body["mask_url"] = mask_url
        return body
    return {"prompt": plan["prompt"], "image_urls": [image_url] + ([close_up_url] if close_up_url else []),
            "aspect_ratio": plan["ratio"], "resolution": resolution, "thinking_level": "minimal"}


def _plan_of(source):
    plan = _sent.get(os.path.normpath(source))
    if plan is None and source.endswith("_send.png"):
        try:
            with open(source[:-len("_send.png")] + "_plan.json", encoding="utf-8") as f:
                plan = json.load(f)
        except (OSError, ValueError):
            plan = None
    return plan


def after_krea(source, result):
    """Krea's picture for a mockup (already at the sent size): cut to the picture, the product secured,
    saved next to the montage as …_mockup_krea_01.png. Returns (path, what happened) or None when `source`
    is not a mockup's."""
    import numpy as np
    from . import krea
    plan = _plan_of(source)
    if plan is None:
        return None
    x0, y0, w, h = plan["crop"]
    got = _read(result)[y0:y0 + h, x0:x0 + w]
    montage = _read(plan["montage"])
    render_path = plan["montage"][:-len("_mockup.png")] + ".png"
    try:
        cover = np.load(_alpha_file(render_path)).astype(np.float32)
    except (OSError, ValueError):
        cover = np.zeros((h, w), dtype=np.float32)
    picture, info = mockup_image.protect(got, montage, cover, plan.get("fidelity", 1.0),
                                         plan.get("exact", False) or plan["key"] == 'PRECISE')
    target = krea.result_path(plan["montage"])
    _write(picture, target)
    try:
        os.remove(result)
    except OSError:
        pass
    last.update(info, path=target, key=plan["key"])
    return target, info


last = {}        # what happened to the last Krea result (for the panel and the chat)


def krea_start(scene, wish=None, model=None):
    """Without Claude (API token): sends the montage to Krea in the background. Returns (plan, target)."""
    import threading
    from . import krea
    key = krea.token()
    if not key:
        raise ValueError("Add your Krea API token in the Refine tab's settings (or send with Claude: Krea plan)")
    if krea.busy():
        raise ValueError("Krea is still working on the last image")
    plan = krea_plan(scene, wish, model)
    target = krea.result_path(plan["send"])
    krea.job.update(state="upload", text="", source=plan["send"], result="", started=time.monotonic(),
                    model=plan["key"], quote=None)

    def work():
        try:
            image_url = krea.upload(key, plan["send"])
            mask_url = krea.upload(key, plan["mask"]) if plan["mask"] else ""
            close_url = krea.upload(key, plan["close_up"]) if plan["close_up"] else ""
            krea.run_job(key, MODEL_PATHS[plan["key"]], krea_body(plan, image_url, mask_url, close_url), target,
                         plan["key"], plan["size"][0], plan["size"][1])
        except Exception as exc:
            krea.job.update(state="error", text=str(exc))
    thread = threading.Thread(target=work, daemon=True)
    krea.job["thread"] = thread
    thread.start()
    if not bpy.app.timers.is_registered(krea._finish):
        bpy.app.timers.register(krea._finish, first_interval=1.0)
    return plan, target


def krea_prepare(scene, upload_url, wish=None, model=None):
    """For Claude with the Krea connector: plan, uploads to `upload_url`, the request. Returns {"model",
    "input", "credits_about", "prompt"}; the result goes to api.krea_save as for a render."""
    from . import krea, tab_finish
    plan = krea_plan(scene, wish, model)
    image_url = krea.upload_to(upload_url, plan["send"])
    mask_url = krea.upload_to(upload_url, plan["mask"]) if plan["mask"] else ""
    close_url = krea.upload_to(upload_url, plan["close_up"]) if plan["close_up"] else ""
    tab_finish._plan.clear()
    tab_finish._plan.update(source=plan["send"], model=plan["key"], size=plan["size"])
    krea.job.update(state="", text="", quote=None)
    return {"model": MODEL_PATHS[plan["key"]], "key": plan["key"], "input": krea_body(plan, image_url, mask_url,
                                                                                    close_url),
            "credits_about": UNITS[plan["key"]], "prompt": plan["prompt"], "size": plan["size"]}


# ------------------------------------------------------------------ operators

class STUDIO_OT_mockup_new(Operator):
    bl_idname = "studio.mockup_new"
    bl_label = "Mockup from a Photo"
    bl_description = ("Put the product into a photo: a scene of its own with the photo behind the camera. "
                      "Your studio scene stays as it is")
    bl_options = {'REGISTER'}

    filepath: StringProperty(subtype='FILE_PATH', options={'HIDDEN', 'SKIP_SAVE'})
    filter_image: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})
    filter_folder: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            scene = create(context, self.filepath)
        except (ValueError, OSError, RuntimeError) as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        self.report({'INFO'}, "%s: move the product with Place, then Render" % scene.name)
        return {'FINISHED'}


class STUDIO_OT_mockup_go(Operator):
    bl_idname = "studio.mockup_go"
    bl_label = "Show Scene"
    bl_options = {'INTERNAL'}

    scene: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    remove: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return "Remove this mockup (its pictures stay)" if props.remove else "Show this scene"

    def invoke(self, context, event):
        if self.remove:
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        scene = bpy.data.scenes.get(self.scene)
        if scene is None:
            return {'CANCELLED'}
        if self.remove:
            back = source_scene(scene) or next((s for s in bpy.data.scenes if s is not scene), None)
            if back is None:
                return {'CANCELLED'}
            show(context, back)
            remove(scene)
        else:
            show(context, scene)
        return {'FINISHED'}


class STUDIO_OT_mockup_place(Operator):
    bl_idname = "studio.mockup_place"
    bl_label = "Place"
    bl_description = ("Move the product in the photo with the mouse. Wheel: size · Ctrl + wheel: turn · "
                      "Shift + wheel: tilt · Alt + wheel: lean · click: done · right click: back")
    bl_options = {'REGISTER', 'UNDO', 'BLOCKING', 'GRAB_CURSOR'}

    @classmethod
    def poll(cls, context):
        return is_mockup(context.scene) and context.area is not None and context.area.type == 'VIEW_3D'

    def invoke(self, context, event):
        m = context.scene.studio_mockup
        self.keep = {k: getattr(m, k) for k in ("pos_x", "pos_y", "size", "turn", "tilt", "lean")}
        self.start = (event.mouse_region_x, event.mouse_region_y)
        self.frame = self._frame(context)
        if self.frame is None:
            self.report({'WARNING'}, "Look through the mockup's camera first")
            return {'CANCELLED'}
        context.window_manager.modal_handler_add(self)
        context.area.header_text_set("Place: move the mouse · wheel size · Ctrl turn · Shift tilt · Alt lean · "
                                     "click done · right click back")
        return {'RUNNING_MODAL'}

    @staticmethod
    def _frame(context):
        """The photo in the 3D view's region: (x, y, width, height) in region pixels."""
        from bpy_extras.view3d_utils import location_3d_to_region_2d
        scene, region = context.scene, context.region
        rv3d = context.space_data.region_3d if context.space_data else None
        camera = scene.camera
        if rv3d is None or camera is None or rv3d.view_perspective != 'CAMERA':
            return None
        corners = [location_3d_to_region_2d(region, rv3d, camera.matrix_world @ c)
                   for c in camera.data.view_frame(scene=scene)]
        if any(c is None for c in corners):
            return None
        x0, x1 = min(c.x for c in corners), max(c.x for c in corners)
        y0, y1 = min(c.y for c in corners), max(c.y for c in corners)
        (fw, fh), (rx, ry, rw, rh) = frame_of(scene.studio_mockup)
        sx, sy = (x1 - x0) / fw, (y1 - y0) / fh
        return (x0 + rx * sx, y0 + ry * sy, max(1.0, rw * sx), max(1.0, rh * sy))

    def modal(self, context, event):
        m = context.scene.studio_mockup
        if event.type == 'MOUSEMOVE':
            slow = 0.25 if event.shift else 1.0
            _busy[0] = True
            m.pos_x = self.keep["pos_x"] + (event.mouse_region_x - self.start[0]) / self.frame[2] * slow
            m.pos_y = self.keep["pos_y"] + (event.mouse_region_y - self.start[1]) / self.frame[3] * slow
            _busy[0] = False
            apply(context.scene)
        elif event.type in {'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            step = 1.0 if event.type == 'WHEELUPMOUSE' else -1.0
            if event.ctrl:
                m.turn += math.radians(5.0) * step
            elif event.shift:
                m.tilt += math.radians(2.5) * step
            elif event.alt:
                m.lean += math.radians(2.5) * step
            else:
                m.size *= 1.05 ** step
        elif event.type in {'LEFTMOUSE', 'RET', 'NUMPAD_ENTER'} and event.value == 'PRESS':
            context.area.header_text_set(None)
            return {'FINISHED'}
        elif event.type in {'RIGHTMOUSE', 'ESC'} and event.value == 'PRESS':
            set_values(context.scene, **{k: (math.degrees(v) if k in ANGLES else v) for k, v in self.keep.items()})
            context.area.header_text_set(None)
            return {'CANCELLED'}
        context.area.tag_redraw()
        return {'RUNNING_MODAL'}


class STUDIO_OT_mockup_render(Operator):
    bl_idname = "studio.mockup_render"
    bl_label = "Render Mockup"
    bl_options = {'INTERNAL'}

    quality: EnumProperty(items=[('LOW', "Preview", ""), ('HIGH', "High Quality", "")], default='LOW',
                          options={'HIDDEN', 'SKIP_SAVE'})
    look: BoolProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        if props.look:
            return "A quick look (a second): the product plainly shaded over the photo, to check place and size"
        return "Renders the product in the background and makes the montage with the photo (%s)" % (
            "High Quality" if props.quality == 'HIGH' else "Low Quality: quick")

    @classmethod
    def poll(cls, context):
        return is_mockup(context.scene)

    def execute(self, context):
        try:
            if self.look:
                _show_file(placement_preview(context.scene))
            else:
                render(context.scene, self.quality)
        except (ValueError, OSError, RuntimeError) as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


class STUDIO_OT_mockup_fspy(Operator):
    bl_idname = "studio.mockup_fspy"
    bl_label = "Camera from fSpy"
    bl_description = ("Take the photo's camera (focal length, horizon, roll, height) from an fSpy or Blenspy "
                      "project of the same photo: for photos where the sliders are hard to judge")
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH', options={'HIDDEN', 'SKIP_SAVE'})
    filter_glob: StringProperty(default="*.fspy", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return is_mockup(context.scene)

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        try:
            load_fspy(context.scene, self.filepath)
        except (ValueError, OSError, KeyError, TypeError) as ex:
            self.report({'ERROR'}, "fSpy: %s" % ex)
            return {'CANCELLED'}
        return {'FINISHED'}


class STUDIO_OT_mockup_front(Operator):
    bl_idname = "studio.mockup_front"
    bl_label = "In Front of the Product"
    bl_description = ("The selected objects stand for things of the photo that are in FRONT of the product "
                      "(a fork, a hand, a table edge): they cut the product out and are not rendered. "
                      "Click again to take them out. A new mockup uses them")
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.selected_objects)

    def execute(self, context):
        objects = [o for o in context.selected_objects if o.type in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'}]
        on = not all(o.get(HOLD_KEY) for o in objects)
        for obj in objects:
            if on:
                obj[HOLD_KEY] = True
            elif HOLD_KEY in obj:
                del obj[HOLD_KEY]
        scene = context.scene
        if is_mockup(scene):                               # this mockup: at once
            layer = scene.view_layers[0].layer_collection
            hold = next((c for c in scene.collection.children if c.name.startswith(FRONT_COLL)), None)
            product = next((c for c in scene.collection.children if c.name.startswith(PRODUCT_COLL)), None)
            if hold is None and on:
                hold = bpy.data.collections.new(FRONT_COLL + _suffix(scene))
                scene.collection.children.link(hold)
                layer.children[hold.name].holdout = True
            for obj in objects:
                for coll, wanted in ((hold, on), (product, not on)):
                    if coll is None:
                        continue
                    if wanted and obj.name not in coll.objects:
                        coll.objects.link(obj)
                    elif not wanted and obj.name in coll.objects:
                        coll.objects.unlink(obj)
        self.report({'INFO'}, "%d objects %s" % (len(objects), "cut the product out" if on else "are product again"))
        return {'FINISHED'}


class STUDIO_OT_mockup_krea(Operator):
    bl_idname = "studio.mockup_krea"
    bl_label = "Blend In with Krea"
    bl_options = {'INTERNAL'}

    claude: BoolProperty(default=True, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        if props.claude:
            return ("Claude looks at the montage, writes Krea's prompt from your wish, shows the price and makes "
                    "it with your Krea plan. Your product is secured again afterwards")
        return "Send the montage to Krea as it is (without Claude, with the API token)"

    @classmethod
    def poll(cls, context):
        from . import krea
        return is_mockup(context.scene) and not krea.busy()

    def execute(self, context):
        scene = context.scene
        m = scene.studio_mockup
        if not m.result or not os.path.isfile(m.result):
            self.report({'ERROR'}, "Render the mockup first")
            return {'CANCELLED'}
        if not self.claude:
            try:
                plan, _target = krea_start(scene)
            except (ValueError, OSError, RuntimeError) as ex:
                self.report({'ERROR'}, str(ex))
                return {'CANCELLED'}
            self.report({'INFO'}, "Krea (%s): about %d credits" % (plan["key"], UNITS[plan["key"]]))
            return {'FINISHED'}
        from .. import core
        if core.is_local():
            self.report({'ERROR'}, "Krea through Claude needs Claude (not a local model)")
            return {'CANCELLED'}
        text = "\n".join(["Blend this mockup in with Krea: %s" % (m.wish.strip() or "(only blend the product in)"),
                          "Mockup scene: %s" % scene.name, "Montage: %s" % m.result,
                          "Product: %s" % m.product_name, "Model: %s" % m.krea_model])
        try:
            core.add_attachment(m.result)
        except Exception as ex:
            print("AI Studio mockup: attachment:", ex)
        m.wish = ""
        if core.is_busy():
            names = [s["name"] for s in core.skills.list_skills(core.workdir()) if s.get("studio_tab") == 'MOCKUP']
            core.queue_chat(text, names, connectors=("Krea",))
            self.report({'INFO'}, "Claude is busy: Krea comes as the next chat")
            return {'FINISHED'}
        core.request_connector("Krea")
        if not core.studio_prompt('MOCKUP', text):
            core.connectors_next.discard("Krea")
            return {'CANCELLED'}
        return {'FINISHED'}


class STUDIO_OT_mockup_ask(Operator):
    bl_idname = "studio.mockup_ask"
    bl_label = "Ask Claude"
    bl_description = ("Claude looks at the photo, sets camera, place, size, angle and light, checks its own "
                      "preview and renders – from one sentence (\"on the fork of the bike, at night\")")
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return is_mockup(context.scene)

    def execute(self, context):
        from .. import core
        scene = context.scene
        m = scene.studio_mockup
        text = "\n".join(["Mockup: %s" % (m.wish.strip() or "place the product in this photo where it belongs"),
                          "Mockup scene: %s" % scene.name, "Photo: %s" % m.photo])
        try:
            core.add_attachment(bpy.path.abspath(m.photo))
        except Exception as ex:
            print("AI Studio mockup: attachment:", ex)
        m.wish = ""
        if not core.studio_prompt('MOCKUP', text):
            return {'CANCELLED'}
        return {'FINISHED'}


# ------------------------------------------------------------------ panel

def _info(layout, text, icon='NONE', alert=False):
    row = layout.row()
    row.active = alert
    row.alert = alert
    row.label(text=text, icon=icon, translate=False)


def draw(layout, context, compact=False):
    """The mockup's controls (3D view sidebar and the Photo workspace's Refine panel)."""
    from . import krea, runner
    scene = context.scene
    if not is_mockup(scene):
        col = layout.column(align=True)
        col.scale_y = 1.2
        col.operator(STUDIO_OT_mockup_new.bl_idname, text="Product into a Photo…", icon='IMAGE_REFERENCE')
        for other in mockups():
            row = layout.row(align=True)
            row.operator(STUDIO_OT_mockup_go.bl_idname, text=other.name, icon='SCENE_DATA').scene = other.name
            op = row.operator(STUDIO_OT_mockup_go.bl_idname, text="", icon='X')
            op.scene, op.remove = other.name, True
        return
    m = scene.studio_mockup
    back = source_scene(scene)
    row = layout.row(align=True)
    row.label(text=os.path.basename(m.photo), icon='IMAGE_REFERENCE', translate=False)
    if back is not None:
        row.operator(STUDIO_OT_mockup_go.bl_idname, text=back.name, icon='LOOP_BACK').scene = back.name
    try:
        info = state(scene)
    except (OSError, ValueError):
        info = {}
        _info(layout, "The photo is missing", 'ERROR', True)
    # one sentence
    row = layout.row(align=True)
    row.scale_y = 1.3
    row.prop(m, "wish", text="", placeholder="What do you want? (Claude does it)")
    row.operator(STUDIO_OT_mockup_ask.bl_idname, text="", icon='EVENT_RETURN')
    from .. import core
    status, reply = core.studio_reply('MOCKUP')
    if status:
        _info(layout, reply[:90], 'TIME' if status == "working" else 'OUTLINER_DATA_GP_LAYER')

    box = layout.box()
    head = box.row(align=True)
    head.label(text="Product", icon='OBJECT_ORIGIN')
    if context.area is not None and context.area.type == 'VIEW_3D':
        head.operator(STUDIO_OT_mockup_place.bl_idname, text="Place", icon='VIEW_PAN')
    col = box.column(align=True)
    row = col.row(align=True)
    row.prop(m, "pos_x", slider=True)
    row.prop(m, "pos_y", slider=True)
    sub = col.row(align=True)
    sub.enabled = not m.on_ground
    sub.prop(m, "size", slider=True)
    col.prop(m, "on_ground", toggle=True, icon='TRIA_DOWN_BAR')
    col = box.column(align=True)
    col.prop(m, "turn", slider=True)
    row = col.row(align=True)
    row.prop(m, "tilt", slider=True)
    row.prop(m, "lean", slider=True)
    if info.get("product_px") is not None:
        _info(box, "%s cm · %d px in the picture" % (" × ".join("%g" % v for v in info["product_cm"]),
                                                     info["product_px"]))
        _info(box, "%.2f m away · %.2f m above the ground" % (info["distance_m"], info["above_ground_m"]))
    row = box.row(align=True)
    row.prop(m, "product_name", text="", icon='FONT_DATA')
    row.operator(STUDIO_OT_mockup_front.bl_idname, text="", icon='MOD_MASK')

    box = layout.box()
    head = box.row(align=True)
    head.label(text="The Photo's Camera", icon='VIEW_CAMERA')
    head.prop(m, "show_grid", text="", icon='MESH_GRID')
    head.operator(STUDIO_OT_mockup_fspy.bl_idname, text="", icon='FILE_FOLDER')
    col = box.column(align=True)
    col.prop(m, "focal", slider=True)
    col.prop(m, "horizon", slider=True)
    col.prop(m, "roll", slider=True)
    col.prop(m, "camera_height")
    box.row(align=True).prop(m, "format", expand=False)

    box = layout.box()
    head = box.row(align=True)
    head.label(text="Light", icon='LIGHT_SUN')
    head.prop(m, "use_ground", text="", icon='AXIS_TOP')
    col = box.column(align=True)
    row = col.row(align=True)
    row.prop(m, "light_from", slider=True)
    row.prop(m, "light_height", slider=True)
    col.prop(m, "light_strength", slider=True)
    col.prop(m, "light_warmth", slider=True)
    col.prop(m, "light_soft", slider=True)
    col.prop(m, "ambient", slider=True)

    row = layout.row(align=True)
    row.scale_y = 1.4
    row.operator(STUDIO_OT_mockup_render.bl_idname, text="Look", icon='HIDE_OFF').look = True
    row.operator(STUDIO_OT_mockup_render.bl_idname, text="Render", icon='RENDER_STILL').quality = 'LOW'
    row.operator(STUDIO_OT_mockup_render.bl_idname, text="HQ", icon='RENDER_RESULT').quality = 'HIGH'
    if runner.RUN.busy and (runner.RUN.scene_name == scene.name or m.waiting):
        _info(layout, "Rendering the product… the montage follows", 'TIME')

    box = layout.box()
    box.label(text="Montage", icon='IMAGE_RGB_ALPHA')
    col = box.column(align=True)
    col.enabled = bool(m.render)
    for key in ("shadow_strength", "exposure", "warmth", "saturation", "soften", "glow", "grain", "night"):
        col.prop(m, key, slider=True)
    if not m.render:
        _info(box, "Render first: then these change the picture at once")

    box = layout.box()
    box.label(text="Blend In with Krea", icon='BRUSHES_ALL')
    box.prop(m, "krea_model", text="")
    row = box.row(align=True)
    sub = row.row(align=True)
    sub.enabled = not m.exact
    sub.prop(m, "fidelity", slider=True)
    row.prop(m, "exact", text="", icon='LOCKED' if m.exact else 'UNLOCKED')
    text = "~%d credits" % UNITS[m.krea_model]
    balance = krea.last_balance()
    if balance is not None:
        text += " · %g left" % round(balance)
    _info(box, text)
    job_state = krea.job["state"]
    if krea.busy():
        words = {"upload": "Uploading…", "working": "Krea is working…", "download": "Downloading…"}
        _info(box, "%s %d s" % (words[job_state], time.monotonic() - krea.job["started"]), 'TIME')
    elif job_state == "error":
        _info(box, krea.job["text"][:90], 'ERROR', True)
    elif last.get("path") and job_state == "done":
        note = "Your product was put back (Krea had changed it)" if last.get("restored") else (
            "Product secured" + (" · moved back %d px" % max(abs(v) for v in last["moved"])
                                 if any(last.get("moved", (0, 0))) else ""))
        _info(box, note, 'CHECKMARK')
    row = box.row(align=True)
    row.scale_y = 1.3
    row.enabled = bool(m.result)
    row.operator(STUDIO_OT_mockup_krea.bl_idname, text="Blend In", icon='EVENT_RETURN').claude = True
    if krea.token():
        row.operator(STUDIO_OT_mockup_krea.bl_idname, text="", icon='PLAY').claude = False


class STUDIO_PT_mockup(Panel):
    """In a studio scene: closed, one button to start a mockup."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Studio"
    bl_label = "Mockup"
    bl_order = 1
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return not is_mockup(context.scene)

    def draw_header(self, context):
        self.layout.label(text="", icon='IMAGE_REFERENCE')

    def draw(self, context):
        draw(self.layout, context)


class STUDIO_PT_mockup_on(Panel):
    """In a mockup scene: open, all its controls."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Studio"
    bl_label = "Mockup"
    bl_order = 1

    @classmethod
    def poll(cls, context):
        return is_mockup(context.scene)

    def draw_header(self, context):
        self.layout.label(text="", icon='IMAGE_REFERENCE')

    def draw(self, context):
        draw(self.layout, context)


classes = (StudioMockup, STUDIO_OT_mockup_new, STUDIO_OT_mockup_go, STUDIO_OT_mockup_place, STUDIO_OT_mockup_render,
           STUDIO_OT_mockup_fspy, STUDIO_OT_mockup_front, STUDIO_OT_mockup_krea, STUDIO_OT_mockup_ask,
           STUDIO_PT_mockup, STUDIO_PT_mockup_on)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.studio_mockup = bpy.props.PointerProperty(type=StudioMockup)
    from . import runner
    if on_saved not in runner.on_render_saved:
        runner.on_render_saved.append(on_saved)


def unregister():
    from . import runner
    if on_saved in runner.on_render_saved:
        runner.on_render_saved.remove(on_saved)
    for timer in (_watch, _montage_timer, _after_render):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    del bpy.types.Scene.studio_mockup
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

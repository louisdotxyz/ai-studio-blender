# SPDX-License-Identifier: GPL-3.0-or-later
"""Render Studio's functions for Claude (in execute_python as `api`, and behind the studio_* tools):
light setups, generated HDRIs, environments (baked rooms, HDRI files), the scene context they need.
"""

import json
import math
import re

import bpy
from mathutils import Vector

from . import core, hdri, hdri_gen, lights, runner

ENVIRONMENT_FOLDER = "Environment"   # subfolder of the HDRI folder for baked rooms

LIGHT_SETUP_FORMAT = """\
Answer with JSON only, in this format:
{"name": "<short preset name>",
 "lights": [
   {"name": "Key", "type": "AREA",
    "horizontal": -45, "vertical": 35, "distance": 3.0,
    "size": 1.2, "power": 1.0,
    "color": [1.0, 0.95, 0.9]}
 ]}
Fields per light (all optional except where noted):
- type: "AREA" (soft box, default), "POINT", "SPOT" or "SUN".
- horizontal: degrees around the subject. 0 = camera side, negative = left, positive = right, 180 = behind.
- vertical: degrees above (+) or below (-) the subject, -89.5..89.5.
- distance: in subject radii (half the subject's bounding box diagonal), 0.5..50. Typical 2.5..5.
- size: area size (or point/spot radius) in subject radii. Bigger = softer shadows.
- power: 1.0 = a standard key light; fill typically 0.2..0.5, rim 0.5..1.0. Scaled with distance automatically.
- color: sRGB 0..1, or instead "kelvin": 2000..12000 (3200 warm tungsten, 5600 daylight, 7500 cool shade).
- shape (AREA): "RECTANGLE", "SQUARE", "DISK", "ELLIPSE".
- spot_angle, spot_blend (SPOT): degrees, 0..1.  sun_angle (SUN): softness in degrees.
At most 16 lights. The setup replaces the previous setup; the user's own lights stay."""

HDRI_SETUP_FORMAT = """\
Answer with JSON only, in this format:
{"name": "<short name>",
 "sky": {"top": [0.85, 0.9, 1.0], "horizon": [1.0, 1.0, 1.0], "ground": [0.3, 0.3, 0.3]},
 "strength": 1.0,
 "sun": {"horizontal": -30, "vertical": 45, "size": 2.0, "strength": 50, "kelvin": 5600},
 "softboxes": [{"horizontal": -60, "vertical": 25, "width": 40, "height": 30, "strength": 8,
                "color": [1, 1, 1]}],
 "resolution": 2048}
- sky: colors of the zenith, the horizon and the ground (sRGB 0..1). strength scales the sky.
- sun (optional): a bright disk. size in degrees (0.5 sharp .. 10 soft), strength 10..1000.
- softboxes (optional, up to 16): bright rectangles like studio lights, width/height in degrees,
  strength 1..50. Leave out the sun for a pure studio look.
- horizontal/vertical: degrees as seen from the subject. 0 = camera side, negative = left,
  positive = right, 180 = behind; vertical above (+) or below (-) the horizon.
- colors may use "kelvin" instead of "color" (sun and softboxes).
- resolution: width in pixels (height is half), 1024..4096; 2048 is enough for lighting."""


def _light_info(obj, center, radius, front):
    data = obj.data
    offset = obj.matrix_world.translation - center
    d = offset.length
    info = {"name": obj.name, "type": data.type, "power_watts": round(data.energy, 2)}
    if d > 1e-6:
        info["horizontal"] = round((math.degrees(math.atan2(offset.y, offset.x) - front) + 180) % 360 - 180, 1)
        info["vertical"] = round(math.degrees(math.atan2(offset.z, offset.xy.length)), 1)
        info["distance"] = round(d / radius, 2)
    info["color"] = [round(lights.linear_to_srgb(c), 3) for c in data.color]
    return info


def scene_context(scene):
    """What Claude should know about the scene, as a dict."""
    rs = scene.render_studio
    target = rs.light_orbit_target
    center, radius = lights.subject(scene, target)
    front = lights.front_angle(scene, center)
    cam = scene.camera
    current = hdri.current_path(scene)
    return {
        "subject": {
            "name": target.name if target else None,
            "radius_m": round(radius, 3),
            "note": "positions are relative to this subject and the active camera",
        },
        "camera": {"name": cam.name, "focal_length_mm": round(cam.data.lens, 1)}
        if cam is not None and cam.type == 'CAMERA' else None,
        "render": {"engine": scene.render.engine, "view_transform": scene.view_settings.view_transform,
                   "background": rs.background_mode},
        "lights": [_light_info(o, center, radius, front) for o in lights.scene_lights(scene)],
        "world": {"hdri": current.rsplit("/", 1)[-1] if current else None,
                  "name": scene.world.name if scene.world else None},
        "saved_light_presets": sorted(lights.saved_presets()),
    }


def extract_json(text):
    """Takes the JSON object out of an answer (```json fences or text around it)."""
    if not isinstance(text, str):
        return text
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object found in the answer")
    return text[start:end + 1]


def light_setup(spec, scene=None, save=True):
    """Applies a light setup (dict or JSON text) and, with save=True, stores it as a preset.
    Returns the created light objects."""
    scene = scene or bpy.context.scene
    parsed = lights.parse_setup(extract_json(spec))
    created = lights.apply_setup(scene, scene.render_studio.light_orbit_target, parsed)
    if save:
        lights.save_preset(parsed)
    if created:
        scene.render_studio.active_light = created[0]
    return created


def hdri_setup(spec, scene=None):
    """Generates an HDRI file from a world setup, uses it and returns its path."""
    scene = scene or bpy.context.scene
    p = runner.prefs()
    return hdri_gen.generate(scene, extract_json(spec), p.hdri_folder if p else "", scene.render_studio)


def environment_context(scene):
    """What Claude needs to build a room around the subject, as a dict."""
    center, radius = lights.subject(scene, scene.render_studio.light_orbit_target)
    floor = None
    for obj in scene.objects:
        if obj.type != 'MESH' or obj.hide_render or not obj.visible_get():
            continue
        if any(c.name.startswith("ENV |") for c in obj.users_collection):
            continue
        z = min((obj.matrix_world @ Vector(c)).z for c in obj.bound_box)
        floor = z if floor is None else min(floor, z)
    p = runner.prefs()
    cam = scene.camera
    return {
        "subject": {"center_m": [round(v, 3) for v in center], "radius_m": round(radius, 3),
                    "floor_z_m": round(floor if floor is not None else center.z - radius, 3)},
        "camera": {"name": cam.name, "location_m": [round(v, 3) for v in cam.matrix_world.translation]}
        if cam is not None and cam.type == 'CAMERA' else None,
        "hdri_folder": hdri.folder_path(p.hdri_folder if p else ""),
        "environment_folder": ENVIRONMENT_FOLDER,
        "environments": sorted(c.name for c in bpy.data.collections if c.name.startswith("ENV |")),
        "worlds": [w.name for w in bpy.data.worlds],
    }


def environment_prompt(scene):
    return ("You build a real room around the subject and bake it into an HDRI.\n"
            "Current scene:\n" + json.dumps(environment_context(scene), indent=1))


def use_hdri(path, scene=None):
    """Use an existing HDRI/EXR (e.g. a baked room) as the World. Brighter/darker and rotation:
    scene.render_studio.world_strength / world_rotation, never bake again for that."""
    scene = scene or bpy.context.scene
    hdri.apply(scene, path, scene.render_studio)
    return path


def environment_bake(name, collection, location=None, world=None, resolution=4096, samples=256,
                     scene=None, use=True):
    """Renders a modelled room (the objects of `collection`) as a 32-bit equirectangular EXR into
    <HDRI folder>/Environment and, with use=True, uses it as the World. Returns the file path.

    location: panorama position (default: center of the subject). world: World that lights the
    room while baking (default: the scene's World). Everything outside the collection is hidden
    while rendering; camera, render settings, World and hide_render states are restored."""
    import os
    from .. import autosave
    autosave.save_now("before environment bake", force=True)   # long Cycles render
    scene = scene or bpy.context.scene
    coll = bpy.data.collections.get(collection) if isinstance(collection, str) else collection
    if coll is None:
        raise ValueError("Collection %r not found" % collection)
    p = runner.prefs()
    root = hdri.folder_path(p.hdri_folder if p else "")
    if not root:
        raise ValueError("Set an HDRI folder in the preferences first")
    folder = os.path.join(root, ENVIRONMENT_FOLDER)
    os.makedirs(folder, exist_ok=True)
    path = core.next_path(folder, core.safe_name(name), ".exr")
    if location is None:
        location, _radius = lights.subject(scene, scene.render_studio.light_orbit_target)

    render, cycles, image = scene.render, scene.cycles, scene.render.image_settings
    saved_render = {k: getattr(render, k) for k in ("engine", "resolution_x", "resolution_y",
                    "resolution_percentage", "filepath", "film_transparent", "use_compositing", "use_sequencer")}
    saved_cycles = {k: getattr(cycles, k) for k in ("samples", "use_denoising", "sample_clamp_indirect")}
    saved_image = {k: getattr(image, k) for k in ("media_type", "file_format", "color_depth", "color_mode")
                   if hasattr(image, k)}
    saved_camera, saved_world = scene.camera, scene.world
    keep = set(coll.all_objects)
    hidden = {obj: obj.hide_render for obj in scene.objects}

    data = bpy.data.cameras.new("RS Environment Bake")
    data.type = 'PANO'
    data.panorama_type = 'EQUIRECTANGULAR'
    cam = bpy.data.objects.new("RS Environment Bake", data)
    cam.location = location
    cam.rotation_euler = (math.pi / 2, 0.0, -math.pi / 2)   # image center = +X, as the World maps it
    scene.collection.objects.link(cam)
    try:
        for obj in hidden:
            if obj not in keep:
                obj.hide_render = True
        if world is not None:
            scene.world = world
        scene.camera = cam
        render.engine = 'CYCLES'
        render.resolution_x, render.resolution_y = int(resolution), int(resolution) // 2
        render.resolution_percentage = 100
        render.film_transparent = False
        render.use_compositing = render.use_sequencer = False
        cycles.samples = int(samples)
        cycles.use_denoising = True
        cycles.sample_clamp_indirect = 10.0
        if hasattr(image, "media_type"):
            image.media_type = 'IMAGE'
        image.file_format = 'OPEN_EXR'
        image.color_depth = '32'
        image.color_mode = 'RGB'
        render.filepath = path
        bpy.ops.render.render(write_still=True)
    finally:
        for obj, state in hidden.items():
            obj.hide_render = state
        scene.camera, scene.world = saved_camera, saved_world
        for k, v in saved_render.items():
            setattr(render, k, v)
        for k, v in saved_cycles.items():
            setattr(cycles, k, v)
        for k, v in saved_image.items():
            try:
                setattr(image, k, v)
            except (TypeError, ValueError):
                pass
        bpy.data.objects.remove(cam)
        bpy.data.cameras.remove(data)
    hdri._cache["folder"] = None   # scan again, the new file shows up in the gallery
    if use:
        hdri.apply(scene, path, scene.render_studio)
        hdri.set_rotation(None, 0.0)   # baked in world axes
    return path

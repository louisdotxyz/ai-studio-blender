# SPDX-License-Identifier: GPL-3.0-or-later
"""Generate an HDRI from a description (for "/hdri setup").

A world setup is a plain dict (JSON):

    {"name": "Soft Studio",
     "sky": {"top": [0.9, 0.92, 1.0], "horizon": [1.0, 1.0, 1.0], "ground": [0.35, 0.33, 0.3]},
     "strength": 1.0,
     "sun": {"horizontal": -30, "vertical": 45, "size": 2.0, "strength": 50, "kelvin": 5600},
     "softboxes": [{"horizontal": -60, "vertical": 25, "width": 40, "height": 30,
                    "strength": 8, "color": [1, 1, 1]}],
     "resolution": 2048}

- Colors are sRGB 0..1, or "kelvin" for a color temperature.
- horizontal/vertical are degrees as for lights: 0 = camera side, negative =
  left, positive = right, 180 = behind; vertical above (+) or below (−) the horizon.
- width, height and size are angles in degrees.

The image is computed directly (equirectangular, 2:1) and saved as Radiance
.hdr. "Front" is -Y; when the HDRI is used, the world is rotated so that front
faces the active camera (see generate()).
"""

import math
import os

import bpy

from . import core, hdri, lights

FRONT = -math.pi / 2   # front direction of a generated HDRI: -Y


def kelvin_to_rgb(kelvin):
    """Approximate sRGB color of a black body (Tanner Helland's fit), 0..1."""
    t = kelvin / 100.0
    if t <= 66:
        r = 255.0
        g = 99.4708025861 * math.log(t) - 161.1195681661
        b = 0.0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    else:
        r = 329.698727446 * (t - 60) ** -0.1332047592
        g = 288.1221695283 * (t - 60) ** -0.0755148492
        b = 255.0
    return tuple(max(0.0, min(255.0, c)) / 255.0 for c in (r, g, b))


def _color(spec, default):
    if "kelvin" in spec:
        k = spec["kelvin"]
        if not isinstance(k, (int, float)):
            raise ValueError("\"kelvin\" must be a number")
        return kelvin_to_rgb(max(800.0, min(20000.0, float(k))))
    value = spec.get("color", default)
    if not (isinstance(value, (list, tuple)) and len(value) == 3 and all(isinstance(c, (int, float)) for c in value)):
        raise ValueError("colors must be three numbers between 0 and 1")
    return tuple(max(0.0, min(1.0, float(c))) for c in value)


def _num(spec, key, default, lo, hi):
    value = spec.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("%r must be a number" % key)
    return max(lo, min(hi, float(value)))


def parse_world(spec):
    """Checks a world setup (dict or JSON text) and fills in defaults. Raises ValueError."""
    import json
    if isinstance(spec, str):
        try:
            spec = json.loads(spec)
        except ValueError as ex:
            raise ValueError("Not valid JSON: %s" % ex)
    if not isinstance(spec, dict):
        raise ValueError("A world setup must be a JSON object")
    sky = spec.get("sky", {})
    if not isinstance(sky, dict):
        raise ValueError("\"sky\" must be an object with top, horizon and ground colors")
    out = {
        "name": str(spec.get("name") or "Generated HDRI")[:60],
        "strength": _num(spec, "strength", 1.0, 0.0, 100.0),
        "resolution": int(_num(spec, "resolution", 2048, 256, 8192)) // 2 * 2,
        "top": _color({"color": sky.get("top", [0.85, 0.9, 1.0])}, None),
        "horizon": _color({"color": sky.get("horizon", [1.0, 1.0, 1.0])}, None),
        "ground": _color({"color": sky.get("ground", [0.3, 0.3, 0.3])}, None),
        "sun": None,
        "softboxes": [],
    }
    if spec.get("sun"):
        sun = spec["sun"]
        if not isinstance(sun, dict):
            raise ValueError("\"sun\" must be an object")
        out["sun"] = {
            "horizontal": math.radians(_num(sun, "horizontal", -30, -360, 360)),
            "vertical": math.radians(_num(sun, "vertical", 45, -90, 90)),
            "size": math.radians(_num(sun, "size", 2.0, 0.2, 30.0)),
            "strength": _num(sun, "strength", 50.0, 0.0, 100000.0),
            "color": _color(sun, [1.0, 0.95, 0.85]),
        }
    boxes = spec.get("softboxes", [])
    if not isinstance(boxes, list) or len(boxes) > 16:
        raise ValueError("\"softboxes\" must be a list with at most 16 entries")
    for box in boxes:
        if not isinstance(box, dict):
            raise ValueError("every softbox must be an object")
        out["softboxes"].append({
            "horizontal": math.radians(_num(box, "horizontal", 0, -360, 360)),
            "vertical": math.radians(_num(box, "vertical", 20, -90, 90)),
            "width": math.radians(_num(box, "width", 30, 1, 180)),
            "height": math.radians(_num(box, "height", 20, 1, 180)),
            "strength": _num(box, "strength", 5.0, 0.0, 10000.0),
            "color": _color(box, [1.0, 1.0, 1.0]),
            "round": str(box.get("shape", "RECT")).upper() == "ROUND",
            # None: the classic hard-edged softbox; 0..1: how far the falloff reaches in from the edge
            "softness": _num(box, "softness", 0.0, 0.0, 1.0) if "softness" in box else None,
        })
    return out


def _linear(rgb):
    return [lights.srgb_to_linear(c) for c in rgb]


def render_pixels(world):
    """Equirectangular float image (height, width, 4), row 0 = bottom.

    Blender maps a direction to (u, v) as u = 0.5 - atan2(y, x) / 2π and
    v = 1 - acos(z) / π (Cycles: direction_to_equirectangular)."""
    import numpy as np

    width = world["resolution"]
    height = width // 2
    u = (np.arange(width, dtype=np.float32) + 0.5) / width
    v = (np.arange(height, dtype=np.float32) + 0.5) / height
    azimuth = (np.pi - 2 * np.pi * u)[None, :]           # world angle around Z
    elevation = (np.pi * (v - 0.5))[:, None]              # -π/2 .. π/2
    az = np.broadcast_to(azimuth, (height, width))
    el = np.broadcast_to(elevation, (height, width))
    dirs = np.stack([np.cos(el) * np.cos(az), np.cos(el) * np.sin(az), np.sin(el)], axis=-1)

    top, horizon, ground = (np.asarray(_linear(c), np.float32) for c in (world["top"], world["horizon"], world["ground"]))
    up = np.clip(el / (np.pi / 2), 0, 1)[..., None]
    down = np.clip(-el / np.radians(10), 0, 1)[..., None]     # short blend into the ground
    smooth = up * up * (3 - 2 * up)
    sky = horizon * (1 - smooth) + top * smooth
    rgb = np.where(el[..., None] >= 0, sky, horizon * (1 - down) + ground * down) * world["strength"]

    def direction(h, v):
        a = FRONT + h
        return np.array([math.cos(v) * math.cos(a), math.cos(v) * math.sin(a), math.sin(v)], np.float32)

    sun = world["sun"]
    if sun:
        cos_angle = np.clip(dirs @ direction(sun["horizontal"], sun["vertical"]), -1, 1)
        angle = np.arccos(cos_angle)
        radius = sun["size"] / 2
        disk = np.clip((radius * 1.15 - angle) / (radius * 0.15), 0, 1)[..., None]
        glow = (np.exp(-angle / (radius * 6)) * 0.02)[..., None]
        rgb = rgb + (disk + glow) * np.asarray(_linear(sun["color"]), np.float32) * sun["strength"]

    for box in world["softboxes"]:
        a = FRONT + box["horizontal"]
        d_az = (az - a + np.pi) % (2 * np.pi) - np.pi
        d_el = el - box["vertical"]
        x = np.abs(d_az * np.cos(box["vertical"])) / (box["width"] / 2)
        y = np.abs(d_el) / (box["height"] / 2)
        r = np.sqrt(x * x + y * y) if box.get("round") else np.maximum(x, y)
        if box.get("softness") is None:
            edge = np.clip((1.08 - r) / 0.08, 0, 1)[..., None]
        else:
            fall = np.clip((1.0 - r) / max(box["softness"], 0.03), 0, 1)
            edge = (fall * fall * (3 - 2 * fall))[..., None]
        rgb = rgb + edge * np.asarray(_linear(box["color"]), np.float32) * box["strength"]

    return np.concatenate([rgb, np.ones((height, width, 1), np.float32)], axis=-1).astype(np.float32)


def save_hdr(pixels, path):
    import numpy as np

    height, width = pixels.shape[:2]
    img = bpy.data.images.new("_render_studio_hdri", width, height, float_buffer=True, alpha=False)
    try:
        img.pixels.foreach_set(np.ascontiguousarray(pixels).ravel())
        img.filepath_raw = path
        img.file_format = 'HDR'
        img.save()
    finally:
        bpy.data.images.remove(img)


def generate(scene, spec, folder, settings, use=True):
    """Generates the HDRI file in `folder` and (with use=True) makes it the world,
    rotated so its front faces the active camera. Returns the file path."""
    world = parse_world(spec)
    root = hdri.folder_path(folder)
    if not root:
        raise ValueError("Set an HDRI folder in the preferences first")
    os.makedirs(root, exist_ok=True)
    path = core.next_path(root, core.safe_name(world["name"]), ".hdr")
    save_hdr(render_pixels(world), path)
    hdri.scan(folder)
    if path not in hdri.favorites():
        hdri.toggle_favorite(path)
    if use:
        hdri.apply(scene, path, settings)
        hdri.set_strength(settings, 1.0)
        target = settings.light_orbit_target
        center, _r = lights.subject(scene, target)
        # The mapping rotates the lookup direction: a feature at FRONT + h appears at
        # FRONT + h - rotation. To put it at camera_front + h, rotate by FRONT - camera_front.
        hdri.set_rotation(settings, FRONT - lights.front_angle(scene, center))
    return path

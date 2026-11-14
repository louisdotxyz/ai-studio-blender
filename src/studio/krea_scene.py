# SPDX-License-Identifier: GPL-3.0-or-later
"""What Krea needs to know about the scene behind a render: the product's true size (so an added hand is
not too small), how much of the picture it fills, and a mask that keeps the product untouched.

The size comes from the product's bounding box in Blender (scene units → cm); the share of the picture
from projecting it through the camera the render was made with (its name is in the file name).
"""

import os
import re

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

from . import core as studio_core
from . import environment

HAND_LENGTH_CM = 19.0     # an adult hand, wrist to fingertip
HAND_WIDTH_CM = 9.0
RENDER_KEYS = ("engine", "resolution_x", "resolution_y", "resolution_percentage", "film_transparent",
               "use_compositing", "use_sequencer", "use_border", "pixel_aspect_x", "pixel_aspect_y")


def _plain(text):
    """Spaces and underscores alike, any case: file names and camera names compare by this."""
    return re.sub(r"[\s_]+", "_", text).lower()


def render_camera(scene, path):
    """The camera a render file was made with (its name is part of the file name), else the scene's."""
    name = _plain(os.path.basename(path or ""))
    best, best_len = None, 0
    for obj in scene.objects:
        if obj.type != 'CAMERA':
            continue
        key = _plain(studio_core.safe_name(obj.name))
        if key and key in name and len(key) > best_len:
            best, best_len = obj, len(key)
    return best or scene.camera


def _cm(scene):
    """Centimeters per Blender unit."""
    unit = scene.unit_settings
    return (unit.scale_length or 1.0) * 100.0 if unit.system != 'NONE' else 100.0


def product_scale(scene, camera=None, width=0, height=0):
    """{"size_cm": [x, y, z], "longest_cm", "frame": [share of the width, of the height] (with a camera),
    "px_per_cm" (with width and height: the image's pixels)} or None without a product."""
    bounds = environment.product_bounds(scene)
    if bounds is None:
        return None
    lo, hi = bounds
    cm = _cm(scene)
    size = [(hi[i] - lo[i]) * cm for i in range(3)]
    info = {"size_cm": [round(v, 1) for v in size], "longest_cm": round(max(size), 1)}
    if camera is None or camera.type != 'CAMERA':
        return info
    xs, ys, ahead = [], [], True
    for obj in environment.product_objects(scene):
        for corner in obj.bound_box:
            p = world_to_camera_view(scene, camera, obj.matrix_world @ Vector(corner))
            xs.append(p.x)
            ys.append(p.y)
            ahead = ahead and p.z > 0
    if not xs or not ahead:
        return info

    def share(values):
        return max(0.0, min(1.0, max(values))) - max(0.0, min(1.0, min(values)))

    info["frame"] = [round(share(xs), 3), round(share(ys), 3)]
    info["cut"] = min(xs) < 0 or max(xs) > 1 or min(ys) < 0 or max(ys) > 1
    if width and height:
        # pixels per cm along the product's least foreshortened side: one box edge per axis, through its center
        center = (lo + hi) / 2
        best = 0.0
        for axis in range(3):
            if size[axis] < 0.1:
                continue
            step = Vector((0.0, 0.0, 0.0))
            step[axis] = (hi[axis] - lo[axis]) / 2
            a = world_to_camera_view(scene, camera, center - step)
            b = world_to_camera_view(scene, camera, center + step)
            pixels = Vector(((b.x - a.x) * width, (b.y - a.y) * height)).length
            best = max(best, pixels / size[axis])
        if best:
            info["px_per_cm"] = round(best, 2)
    return info


def _num(value):
    return ("%.0f" % value) if value >= 10 else ("%.1f" % value).rstrip("0").rstrip(".")


def scale_text(info, width=0, height=0, framing_kept=True):
    """The sentence for the prompt: true size, share of the picture and how big a hand is next to it."""
    if not info:
        return ""
    x, y, z = info["size_cm"]
    longest = max(info["longest_cm"], 0.1)
    text = ("True scale: the product is about %s × %s × %s cm (width × depth × height), its longest side %s cm."
            % (_num(x), _num(y), _num(z), _num(longest)))
    if framing_kept and info.get("frame"):
        fx, fy = info["frame"]
        text += " In this image it fills about %d %% of the width and %d %% of the height." % (fx * 100, fy * 100)
    ratio = HAND_LENGTH_CM / longest
    text += (" An adult hand is about %d cm long and %d cm wide, so it is about %s times the product's longest side"
             % (HAND_LENGTH_CM, HAND_WIDTH_CM, _num(ratio)))
    if framing_kept and info.get("px_per_cm") and width:
        share = HAND_LENGTH_CM * info["px_per_cm"] / width
        text += ": a hand at the product spans roughly %d %% of the image width" % min(share * 100, 400)
    text += ". Draw every added hand, person or object at this real-world scale."
    return text


def describe(scene, path):
    """Everything about the render Krea's prompt needs. path: the render file."""
    from . import krea
    width, height = krea.image_size(path)
    camera = render_camera(scene, path)
    info = product_scale(scene, camera, width, height) or {}
    return {"render": path, "size": [width, height], "camera": camera.name if camera else "",
            "product_cm": info.get("size_cm"), "frame": info.get("frame"), "cut_off": info.get("cut", False),
            "px_per_cm": info.get("px_per_cm"), "scale": scale_text(info, width, height)}


# ------------------------------------------------------------------ mask: the product stays as it is

def mask_path():
    from .paths import data_dir
    folder = os.path.join(data_dir(), "krea")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "mask.png")


def render_mask(scene, path, target=None):
    """A black-and-white picture at exactly the render's size: the product black (Krea keeps it), the rest
    white (Krea may change it). Rendered with Workbench through the render's camera; only the product is
    visible for it. Everything is set back afterwards. Returns the mask file."""
    from . import krea
    import numpy as np
    width, height = krea.image_size(path)
    camera = render_camera(scene, path)
    if camera is None:
        raise ValueError("No camera for the mask")
    products = set(environment.product_objects(scene))
    if not products:
        raise ValueError("No product in the scene for the mask")
    target = target or mask_path()
    raw = target + ".alpha.png"
    r = scene.render
    keep = {key: getattr(r, key) for key in RENDER_KEYS if hasattr(r, key)}
    image_keep = {"file_format": r.image_settings.file_format, "color_mode": r.image_settings.color_mode}
    scene_camera = scene.camera
    hidden = [o for o in scene.objects if o not in products and not o.hide_render]
    try:
        for o in hidden:
            o.hide_render = True
        r.engine = 'BLENDER_WORKBENCH'
        r.resolution_x, r.resolution_y, r.resolution_percentage = width, height, 100
        r.pixel_aspect_x = r.pixel_aspect_y = 1.0
        r.film_transparent = True
        for key in ("use_compositing", "use_sequencer", "use_border"):
            if hasattr(r, key):
                setattr(r, key, False)
        r.image_settings.file_format = 'PNG'
        r.image_settings.color_mode = 'RGBA'
        scene.camera = camera
        bpy.ops.render.render(scene=scene.name)
        bpy.data.images["Render Result"].save_render(raw, scene=scene)
    finally:
        for o in hidden:
            o.hide_render = False
        for key, value in keep.items():
            setattr(r, key, value)
        for key, value in image_keep.items():
            try:
                setattr(r.image_settings, key, value)
            except (TypeError, ValueError):
                pass
        scene.camera = scene_camera
    alpha_image = bpy.data.images.load(raw, check_existing=False)
    try:
        pixels = np.empty(width * height * 4, dtype=np.float32)
        alpha_image.pixels.foreach_get(pixels)
    finally:
        bpy.data.images.remove(alpha_image)
        try:
            os.remove(raw)
        except OSError:
            pass
    keep_product = pixels[3::4] > 0.02          # the product, with its soft edge, stays black
    value = np.where(keep_product, 0.0, 1.0).astype(np.float32)
    out = np.empty_like(pixels)
    out[0::4] = out[1::4] = out[2::4] = value
    out[3::4] = 1.0
    mask = bpy.data.images.new("ID Krea Mask", width, height, alpha=False)
    try:
        mask.pixels.foreach_set(out)
        mask.filepath_raw = target
        mask.file_format = 'PNG'
        mask.save()
    finally:
        bpy.data.images.remove(mask)
    share = float(keep_product.mean())
    if share > 0.97:
        raise ValueError("The product fills the whole picture: nothing left for Krea to change")
    return target

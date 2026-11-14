# SPDX-License-Identifier: GPL-3.0-or-later
"""Finish tab: adjust a finished render like a raw photo (light: exposure, contrast, highlights, shadows;
color: temperature, tint, vibrance, saturation). The preview shows it right away (a small copy, made in a
timer); "Save" writes …_edit_01.png next to the render at full size. The render itself stays as it is.
"""

import os
import time

import bpy

# scene.render_studio property → (label, min, max): the values are -100 … 100 (exposure in stops)
KEYS = ("edit_exposure", "edit_contrast", "edit_highlights", "edit_shadows",
        "edit_temperature", "edit_tint", "edit_vibrance", "edit_saturation")
LIGHT = KEYS[:4]
COLOR = KEYS[4:]
PREVIEW_SIZE = 900          # longest side of the preview copy
_cache = {"key": None, "base": None, "size": (0, 0), "float": False, "file": "", "source": ""}
_state = {"want": None, "counter": 0}


def values(scene):
    rs = scene.render_studio
    return {key: float(getattr(rs, key)) for key in KEYS}


def is_neutral(vals):
    return all(abs(v) < 1e-6 for v in vals.values())


# ------------------------------------------------------------------ the math (numpy, on RGBA floats 0-1)

def _to_linear(c):
    import numpy as np
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def _to_srgb(c):
    import numpy as np
    c = np.clip(c, 0.0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def _smooth(edge0, edge1, x):
    import numpy as np
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def apply(pixels, vals, is_float=False):
    """pixels: flat RGBA float array (sRGB display values; linear when is_float). Returns a new one, sRGB."""
    import numpy as np
    px = np.asarray(pixels, dtype=np.float32).reshape(-1, 4)
    rgb = px[:, :3].copy()
    if is_float:
        rgb = _to_srgb(rgb)
    if is_neutral(vals):
        out = px.copy()
        out[:, :3] = np.clip(rgb, 0.0, 1.0)
        return out.reshape(-1)
    # in linear light: exposure and white balance
    lin = _to_linear(np.clip(rgb, 0.0, 1.0))
    lin *= 2.0 ** vals["edit_exposure"]
    t, g = vals["edit_temperature"] / 100.0, vals["edit_tint"] / 100.0
    lin *= np.array([1.0 + 0.25 * t + 0.1 * g, 1.0 - 0.2 * g, 1.0 - 0.25 * t + 0.1 * g], dtype=np.float32)
    rgb = _to_srgb(lin)
    # in display values: tone, then color
    lum = rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    highlights, shadows = vals["edit_highlights"] / 100.0, vals["edit_shadows"] / 100.0
    if highlights:
        rgb += (highlights * 0.35 * _smooth(0.45, 1.0, lum))[:, None] * np.where(highlights < 0, rgb, 1.0 - rgb)
    if shadows:
        rgb += (shadows * 0.35 * (1.0 - _smooth(0.0, 0.55, lum)))[:, None] * np.where(shadows > 0, 1.0 - rgb, rgb)
    contrast = vals["edit_contrast"] / 100.0
    if contrast:
        rgb = (rgb - 0.5) * (1.0 + contrast) + 0.5
    rgb = np.clip(rgb, 0.0, 1.0)
    lum = (rgb @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32))[:, None]
    vibrance, saturation = vals["edit_vibrance"] / 100.0, vals["edit_saturation"] / 100.0
    if vibrance:
        sat = (rgb.max(axis=1) - rgb.min(axis=1))[:, None]
        rgb = lum + (rgb - lum) * (1.0 + vibrance * (1.0 - sat))
    if saturation:
        rgb = lum + (rgb - lum) * (1.0 + saturation)
    out = px.copy()
    out[:, :3] = np.clip(rgb, 0.0, 1.0)
    return out.reshape(-1)


# ------------------------------------------------------------------ files

def _read(path, longest=0):
    """(pixels, (w, h), is_float) of an image file, scaled down to `longest` if it is bigger."""
    import numpy as np
    image = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = image.size
        if longest and max(w, h) > longest:
            f = longest / max(w, h)
            w, h = max(1, round(w * f)), max(1, round(h * f))
            image.scale(w, h)
        pixels = np.empty(w * h * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        return pixels, (w, h), bool(image.is_float)
    finally:
        bpy.data.images.remove(image)


def _write(pixels, size, path):
    image = bpy.data.images.new("ID Finish Edit", size[0], size[1], alpha=True)
    try:
        image.pixels.foreach_set(pixels)
        image.filepath_raw = path
        image.file_format = 'PNG'
        image.save()
    finally:
        bpy.data.images.remove(image)


def edit_path(source):
    """<render>_edit_01.png, _02 … next to the render (never overwrites)."""
    import re
    base = re.sub(r"_edit_\d+$", "", os.path.splitext(source)[0])
    for n in range(1, 1000):
        path = "%s_edit_%02d.png" % (base, n)
        if not os.path.exists(path):
            return path
    raise RuntimeError("Too many edited versions of %s" % os.path.basename(source))


def save(scene, source):
    """The render with the adjustments at full size, as a new file next to it. Returns the path."""
    if not source or not os.path.isfile(source):
        raise ValueError("Render an image first")
    pixels, size, is_float = _read(source)
    target = edit_path(source)
    _write(apply(pixels, values(scene), is_float), size, target)
    return target


# ------------------------------------------------------------------ the preview (a small copy, in a timer)

def _folder():
    from .paths import data_dir
    folder = os.path.join(data_dir(), "finish")
    os.makedirs(folder, exist_ok=True)
    return folder


def _key(source, vals):
    try:
        mtime = os.path.getmtime(source)
    except OSError:
        mtime = 0.0
    return (source, mtime, tuple(round(vals[k], 3) for k in KEYS))


def preview_file(scene, source):
    """The file the preview shows: the render itself when nothing is adjusted, else a small adjusted copy.
    Made in a timer: until it is ready the last one (or the render) is shown. Safe to call while drawing."""
    if not source:
        return source
    vals = values(scene)
    if is_neutral(vals):
        return source
    key = _key(source, vals)
    if _cache["key"] == key and os.path.exists(_cache["file"]):
        return _cache["file"]
    _state["want"] = (scene.name, source, vals, key)
    if not bpy.app.timers.is_registered(_make_preview):
        bpy.app.timers.register(_make_preview, first_interval=0.12)
    return _cache["file"] if _cache["source"] == source and os.path.exists(_cache["file"]) else source


def _make_preview():
    want, _state["want"] = _state["want"], None
    if want is None:
        return None
    _scene, source, vals, key = want
    try:
        if _cache.get("base_source") != (source, key[1]):
            base, size, is_float = _read(source, PREVIEW_SIZE)
            _cache.update(base=base, size=size, float=is_float, base_source=(source, key[1]))
        _state["counter"] += 1          # a new name each time: the preview image reloads by its path
        path = os.path.join(_folder(), "preview_%d_%d.png" % (os.getpid(), _state["counter"]))
        _write(apply(_cache["base"], vals, _cache["float"]), _cache["size"], path)
        old = _cache["file"]
        _cache.update(key=key, file=path, source=source, time=time.monotonic())
        if old and old != path:
            try:
                os.remove(old)
            except OSError:
                pass
    except Exception as exc:          # the preview just stays as it was
        print("ID Toolkit: Finish preview:", exc)
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()
    return None


def changed(_self=None, _context=None):
    """Update callback of the sliders: redraw, the preview follows in the timer."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()

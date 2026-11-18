# SPDX-License-Identifier: GPL-3.0-or-later
"""The picture side of a mockup (numpy only, no Blender): the montage of photo and render, putting the
product back after Krea, and reading what a photo file says about its camera.

All pictures are float arrays (height, width, channels) in display colours (sRGB), row 0 at the bottom
(as Blender's pixels).

- compose(): photo + render (product and its shadow on the ground) with the sliders of the panel:
  shadow, the product's exposure / warmth / saturation, edge softness, glow, grain, the photo darker (night).
  Nothing is rendered again for a slider.
- protect(): after Krea. Krea may change light, colour and grain ON the product, never its shape: inside the
  product the fine detail (edges, parts, text) comes from the render, the coarse light and colour from Krea.
  A result that slid by a few pixels is moved back first; a result that painted the product away or anew
  gets the render back as it was.
- exif_focal(), read_fspy(): the focal length a photo was taken with, a camera matched in fSpy.
"""

import json
import math
import struct

import numpy as np


# ------------------------------------------------------------------ helpers

def box_blur(a, radius):
    """Box blur of (h, w[, c]) by `radius` pixels (edges repeated)."""
    radius = int(round(radius))
    if radius < 1:
        return a
    out = a
    for axis in (0, 1):
        n = out.shape[axis]
        r = min(radius, max(1, n - 1))
        pad = [(0, 0)] * out.ndim
        pad[axis] = (r + 1, r)
        c = np.cumsum(np.pad(out, pad, mode="edge"), axis=axis, dtype=np.float64)
        hi = np.take(c, np.arange(2 * r + 1, 2 * r + 1 + n), axis=axis)
        lo = np.take(c, np.arange(0, n), axis=axis)
        out = ((hi - lo) / (2 * r + 1)).astype(np.float32)
    return out


def blur(a, radius):
    """Close to a Gaussian: three box blurs."""
    if radius < 0.5:
        return a
    r = max(1, int(round(radius * 0.6)))
    return box_blur(box_blur(box_blur(a, r), r), r)


def to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((np.maximum(c, 0.0) + 0.055) / 1.055) ** 2.4)


def to_srgb(c):
    c = np.maximum(c, 0.0)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * c ** (1 / 2.4) - 0.055)


def resize(a, width, height):
    """Nearest-neighbour resize (for small helper pictures and masks)."""
    h, w = a.shape[:2]
    ys = np.minimum((np.arange(height) + 0.5) * h / height, h - 1).astype(np.int64)
    xs = np.minimum((np.arange(width) + 0.5) * w / width, w - 1).astype(np.int64)
    return a[ys][:, xs]


def luminance(rgb):
    return rgb[..., 0] * 0.2126 + rgb[..., 1] * 0.7152 + rgb[..., 2] * 0.0722


# ------------------------------------------------------------------ frame

RATIOS = {'16:9': 16 / 9, '3:2': 3 / 2, '4:3': 4 / 3, '1:1': 1.0, '4:5': 4 / 5, '9:16': 9 / 16, '21:9': 21 / 9}


def frame_size(photo_w, photo_h, fmt='PHOTO', longest=4096):
    """(width, height) of the mockup's picture and where the photo sits in it (x, y, w, h in pixels, from
    the bottom left). Another format than the photo's keeps the whole photo and leaves bars for Krea to fill."""
    scale = min(1.0, longest / max(photo_w, photo_h))
    pw, ph = max(2, round(photo_w * scale)), max(2, round(photo_h * scale))
    ratio = RATIOS.get(fmt)
    if not ratio or abs(ratio - pw / ph) < 0.01:
        return (pw, ph), (0, 0, pw, ph)
    if ratio > pw / ph:
        w, h = round(ph * ratio), ph
    else:
        w, h = pw, round(pw / ratio)
    w, h = w + w % 2, h + h % 2
    return (w, h), ((w - pw) // 2, (h - ph) // 2, pw, ph)


def place_photo(photo, frame, rect):
    """The photo (h, w, 3+) on a black frame at rect. Returns (h, w, 3) and the mask of the bars (h, w)."""
    w, h = frame
    x, y, pw, ph = rect
    out = np.zeros((h, w, 3), dtype=np.float32)
    out[y:y + ph, x:x + pw] = resize(photo[..., :3], pw, ph)
    bars = np.ones((h, w), dtype=np.float32)
    bars[y:y + ph, x:x + pw] = 0.0
    return out, bars


# ------------------------------------------------------------------ the montage

DEFAULTS = {"shadow": 1.0, "exposure": 0.0, "warmth": 0.0, "saturation": 1.0, "soften": 0.0, "glow": 0.0,
            "grain": 0.0, "night": 0.0, "edge": 0.0}


def split_alpha(alpha, product):
    """The render's alpha is product + shadow on the ground. Returns the shadow's own alpha."""
    return np.clip((alpha - product) / np.maximum(1.0 - product, 1e-4), 0.0, 1.0) * (product < 0.999)


def compose(photo, render, product, values=None, seed=1):
    """photo (h, w, 3) already on the frame, render (h, w, 4) straight alpha, product (h, w) the product's
    coverage (0-1). Returns (h, w, 4) opaque."""
    v = dict(DEFAULTS, **(values or {}))
    h, w = render.shape[:2]
    bg = photo[..., :3].astype(np.float32)
    if v["night"] > 0:                                 # the photo darker and cooler: dusk to night
        n = float(v["night"])
        lin = to_linear(bg) * (1.0 - 0.9 * n) * np.array([1.0 - 0.25 * n, 1.0 - 0.1 * n, 1.0], dtype=np.float32)
        bg = to_srgb(lin).astype(np.float32)
    alpha = render[..., 3]
    product = np.minimum(product, alpha) if product.shape == alpha.shape else alpha
    shadow = split_alpha(alpha, product) * float(v["shadow"])
    rgb = render[..., :3].astype(np.float32)
    if v["exposure"] or v["warmth"] or v["saturation"] != 1.0:
        lin = to_linear(rgb) * (2.0 ** float(v["exposure"]))
        t = float(v["warmth"])
        lin = lin * np.array([1.0 + 0.3 * t, 1.0, 1.0 - 0.3 * t], dtype=np.float32)
        gray = luminance(lin)[..., None]
        lin = gray + (lin - gray) * float(v["saturation"])
        rgb = to_srgb(lin).astype(np.float32)
    cover = product
    if v["soften"] > 0:                                # as soft as the photo's own lens
        r = float(v["soften"]) * max(w, h) / 1000.0
        cover = blur(product, r)
        rgb = blur(rgb * product[..., None], r) / np.maximum(cover[..., None], 1e-4)
    elif v["edge"] > 0:                                # only the cut-out edge softer
        cover = np.minimum(product, blur(product, float(v["edge"]) * max(w, h) / 1000.0) * 1.0)
    out = bg * (1.0 - np.clip(shadow, 0.0, 1.0)[..., None])
    out = rgb * cover[..., None] + out * (1.0 - cover[..., None])
    if v["glow"] > 0:                                  # what shines on the product spills over the photo
        lin = to_linear(rgb) * cover[..., None]
        bright = np.maximum(lin - 0.6, 0.0)
        peak = float(bright.max())
        if peak > 1e-4:                                # a halo as bright as its source, however small that is
            halo = 0.0
            for r, weight in ((max(w, h) / 30.0, 0.6), (max(w, h) / 120.0, 0.8)):
                soft = blur(bright, r)
                halo = halo + soft * (weight * peak / max(float(soft.max()), 1e-6))
            out = to_srgb(to_linear(out) + halo * float(v["glow"])).astype(np.float32)
    if v["grain"] > 0:                                 # the photo has its grain, the render gets the same
        rng = np.random.default_rng(seed)
        noise = rng.normal(0.0, 1.0, (h, w, 1)).astype(np.float32)
        out = out + noise * (0.05 * float(v["grain"])) * cover[..., None]
    result = np.empty((h, w, 4), dtype=np.float32)
    result[..., :3] = np.clip(out, 0.0, 1.0)
    result[..., 3] = 1.0
    return result


def box_of(mask, pad=0.12):
    """The box around what is set in mask (h, w): (x, y, width, height) from the TOP left, 0-1 (Krea's
    regions), a little bigger than it. None when nothing is set."""
    ys, xs = np.nonzero(mask > 0.02)
    if not len(xs):
        return None
    h, w = mask.shape
    x0, x1, y0, y1 = xs.min() / w, (xs.max() + 1) / w, ys.min() / h, (ys.max() + 1) / h
    px, py = max(0.02, (x1 - x0) * pad), max(0.02, (y1 - y0) * pad)
    x0, x1, y0, y1 = max(0.0, x0 - px), min(1.0, x1 + px), max(0.0, y0 - py), min(1.0, y1 + py)
    return tuple(round(float(v), 4) for v in (x0, 1.0 - y1, x1 - x0, y1 - y0))


# ------------------------------------------------------------------ after Krea

def find_shift(a, b, limit=0.04, focus=None):
    """How far b is moved against a, in pixels (dx, dy), from their grey pictures (phase correlation on a
    small copy). (0, 0) when nothing clear is found or it is more than `limit` of the picture."""
    h, w = a.shape[:2]
    n = 512
    ga, gb = resize(luminance(a[..., :3]), n, n), resize(luminance(b[..., :3]), n, n)
    ga, gb = ga - ga.mean(), gb - gb.mean()
    fa, fb = np.fft.rfft2(ga), np.fft.rfft2(gb)
    cross = fa * np.conj(fb)
    corr = np.fft.irfft2(cross / np.maximum(np.abs(cross), 1e-8), s=(n, n))
    py, px = np.unravel_index(np.argmax(corr), corr.shape)
    if corr[py, px] < 0.08:
        return 0, 0

    def fine(lo, mid, hi):                             # the peak between the cells (parabola)
        d = lo - 2.0 * mid + hi
        return 0.0 if abs(d) < 1e-9 else 0.5 * (lo - hi) / d

    sy = fine(corr[py - 1, px], corr[py, px], corr[(py + 1) % n, px])
    sx = fine(corr[py, px - 1], corr[py, px], corr[py, (px + 1) % n])
    dy = (py if py <= n // 2 else py - n) + sy
    dx = (px if px <= n // 2 else px - n) + sx
    if abs(dx) > n * limit or abs(dy) > n * limit:
        return 0, 0
    dx, dy = int(round(dx * w / n)), int(round(dy * h / n))
    # to the pixel: the best of the shifts around it, on a window at full size (around `focus`)
    cy, cx = focus if focus else (h // 2, w // 2)
    half = min(256, h // 2 - 8, w // 2 - 8)
    if half < 32:
        return dx, dy
    cy, cx = int(min(max(cy, half + 8), h - half - 8)), int(min(max(cx, half + 8), w - half - 8))
    la, lb = luminance(a[..., :3]), luminance(b[..., :3])
    want = la[cy - half:cy + half, cx - half:cx + half]
    want = want - want.mean()
    best, found = None, (dx, dy)
    for oy in range(dy - 3, dy + 4):
        for ox in range(dx - 3, dx + 4):
            y0, x0 = cy - half - oy, cx - half - ox
            if y0 < 0 or x0 < 0 or y0 + 2 * half > h or x0 + 2 * half > w:
                continue
            other = lb[y0:y0 + 2 * half, x0:x0 + 2 * half]
            other = other - other.mean()               # Krea may have changed brightness: compare the shapes
            err = -float((other * want).sum() / max(1e-9, math.sqrt(float((other ** 2).sum()))))
            if best is None or err < best:
                best, found = err, (ox, oy)
    return found


def protect(result, montage, product, fidelity=1.0, exact=False):
    """Krea's result with the product secured. result, montage: (h, w, 3+) of the same size; product (h, w).
    fidelity 1: every detail of the product from the render (Krea's light and colour stay); 0: Krea's own.
    exact: the render's pixels as they were. Returns (picture (h, w, 4), {"moved": [dx, dy],
    "changed": how far Krea's product is from yours 0-1, "restored": the render was put back whole})."""
    h, w = montage.shape[:2]
    ai = result[..., :3].astype(np.float32)
    mine = montage[..., :3].astype(np.float32)
    ys, xs = np.nonzero(product > 0.5)
    focus = (int(ys.mean()), int(xs.mean())) if len(xs) else None
    dx, dy = find_shift(mine, ai, focus=focus)
    if dx or dy:                                       # Krea's picture slid: back by that much
        ai = np.roll(ai, (dy, dx), axis=(0, 1))
    radius = max(2.0, max(w, h) * 0.006)
    ai_low, mine_low = blur(ai, radius), blur(mine, radius)
    soft = np.clip(blur(product, max(1.0, max(w, h) * 0.0015)), 0.0, 1.0)
    core = product > 0.5
    changed = float(np.abs(ai_low - mine_low)[core].mean()) if core.any() else 0.0
    detail_ai, detail_mine = ai - ai_low, mine - mine_low
    edges = float(np.abs(detail_mine)[core].mean()) if core.any() else 0.0
    kept = float((detail_ai * detail_mine)[core].mean() / max(1e-6, (detail_mine ** 2)[core].mean())) \
        if core.any() and edges > 1e-4 else 1.0
    restored = exact or changed > 0.3                  # painted away or anew: no light of Krea's to keep
    if restored:
        inside = mine
    else:
        f = float(np.clip(fidelity, 0.0, 1.0))
        inside = ai_low + detail_ai * (1.0 - f) + detail_mine * f
    out = ai * (1.0 - soft[..., None]) + inside * soft[..., None]
    picture = np.empty((h, w, 4), dtype=np.float32)
    picture[..., :3] = np.clip(out, 0.0, 1.0)
    picture[..., 3] = 1.0
    return picture, {"moved": [dx, dy], "changed": round(changed, 3), "detail_kept": round(max(0.0, min(kept, 1.5)), 2),
                     "restored": bool(restored and not exact)}


# ------------------------------------------------------------------ light of the photo

def environment(photo, width=256):
    """A small all-round picture (equirectangular, (h, w, 3)) made of the blurred photo: what is in front
    of the camera lights the product from the front, mirrored behind it. For colour and soft reflections."""
    half = width // 2
    small = blur(resize(photo[..., :3].astype(np.float32), half, half), 6)
    out = np.empty((half, width, 3), dtype=np.float32)
    out[:, half:] = small                              # u 0.5-1.0: around the camera's view (+Y)
    out[:, :half] = small[:, ::-1]
    return out


def mean_colour(photo):
    """The photo's average colour (linear) and how bright it is (0-1, display)."""
    rgb = photo[..., :3].astype(np.float32)
    lin = to_linear(rgb).reshape(-1, 3).mean(axis=0)
    return [float(v) for v in lin], float(luminance(rgb).mean())


# ------------------------------------------------------------------ what the file knows

def exif_focal(path):
    """The focal length as on a 36 mm sensor from a JPEG's EXIF, or None."""
    try:
        with open(path, "rb") as f:
            data = f.read(256 * 1024)
    except OSError:
        return None
    start = data.find(b"Exif\x00\x00")
    if start < 0:
        return None
    tiff = data[start + 6:]
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return None
    e = "<" if tiff[:2] == b"II" else ">"

    def entries(offset):
        if offset + 2 > len(tiff):
            return {}
        count = struct.unpack(e + "H", tiff[offset:offset + 2])[0]
        found = {}
        for i in range(count):
            at = offset + 2 + 12 * i
            if at + 12 > len(tiff):
                break
            tag, kind, _n = struct.unpack(e + "HHI", tiff[at:at + 8])
            found[tag] = (kind, tiff[at + 8:at + 12])
        return found

    def number(item):
        kind, raw = item
        if kind == 3:
            return float(struct.unpack(e + "H", raw[:2])[0])
        if kind == 4:
            return float(struct.unpack(e + "I", raw)[0])
        if kind == 5:
            at = struct.unpack(e + "I", raw)[0]
            if at + 8 > len(tiff):
                return None
            a, b = struct.unpack(e + "II", tiff[at:at + 8])
            return a / b if b else None
        return None

    try:
        main = entries(struct.unpack(e + "I", tiff[4:8])[0])
        if 0x8769 not in main:
            return None
        sub = entries(struct.unpack(e + "I", main[0x8769][1])[0])
        if 0xA405 in sub:                              # FocalLengthIn35mmFilm
            value = number(sub[0xA405])
            if value and 8 <= value <= 1200:
                return value
    except struct.error:
        pass
    return None


def read_fspy(path):
    """A camera matched in fSpy (or Blenspy: the same file): {"focal" (on 36 mm), "pitch", "roll" (radians,
    pitch < 0 looks down), "height" (the camera above the ground in fSpy's units), "size": (w, h)}.
    The project must have Z as its up axis (fSpy's Blender setting)."""
    with open(path, "rb") as f:
        head = f.read(16)
        if head[:4] != b"fspy":
            raise ValueError("Not an fSpy project file")
        _version, state_size, _image_size = struct.unpack("<III", head[4:16])
        state = json.loads(f.read(state_size).decode("utf-8"))
    cam = state.get("cameraParameters")
    if not cam:
        raise ValueError("The fSpy project has no solved camera")
    rows = cam["cameraTransform"]["rows"]
    forward = [-rows[0][2], -rows[1][2], -rows[2][2]]     # the camera looks along its -Z
    right = [rows[0][0], rows[1][0], rows[2][0]]
    pitch = math.asin(max(-1.0, min(1.0, forward[2])))
    roll = math.asin(max(-1.0, min(1.0, right[2] / max(1e-6, math.cos(pitch)))))
    fov = float(cam["horizontalFieldOfView"])
    return {"focal": 18.0 / math.tan(fov / 2.0), "pitch": pitch, "roll": roll, "height": float(rows[2][3]),
            "size": (int(cam.get("imageWidth", 0)), int(cam.get("imageHeight", 0)))}

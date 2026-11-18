# SPDX-License-Identifier: GPL-3.0-or-later
"""Preview pictures of the flags, gobos and light textures (Light tab › More › Gobo).

Small PNGs drawn with numpy (white = light passes, dark = blocked), written once into the add-on's user
folder and loaded as preview icons. They show what each pattern looks like, not the exact shader.
"""

import os
import struct
import zlib

import bpy

SIZE = 96
_previews = [None]


def _folder():
    from .paths import data_dir
    path = os.path.join(data_dir(), "gobo_previews")
    os.makedirs(path, exist_ok=True)
    return path


def _png(path, pixels):
    """pixels: rows of grey values 0..255 (numpy uint8, h × w)."""
    h, w = pixels.shape
    raw = b"".join(b"\x00" + pixels[y].tobytes() for y in range(h))
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def _noise(np, scale, seed):
    rng = np.random.default_rng(seed)
    n = int(scale) + 2
    grid = rng.random((n, n))
    t = np.linspace(0, scale, SIZE, endpoint=False)
    i = t.astype(int)
    f = t - i
    f = f * f * (3 - 2 * f)
    a = grid[np.ix_(i, i)]
    b = grid[np.ix_(i, i + 1)]
    c = grid[np.ix_(i + 1, i)]
    d = grid[np.ix_(i + 1, i + 1)]
    fx, fy = f[None, :], f[:, None]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def _smooth(np, v, lo, hi):
    t = np.clip((v - lo) / (hi - lo), 0, 1)
    return t * t * (3 - 2 * t)


def _pattern(np, key):
    u = np.linspace(0, 1, SIZE)[None, :].repeat(SIZE, 0)
    v = np.linspace(0, 1, SIZE)[:, None].repeat(SIZE, 1)
    if key == 'FLAG':
        return (u > 0.55).astype(float)
    if key == 'BLINDS':
        return (np.sin(v * np.pi * 14) > 0).astype(float)
    if key == 'WINDOW':
        bars = (np.abs((u * 2) % 1 - 0.5) > 0.44) | (np.abs((v * 2) % 1 - 0.5) > 0.44) | (u < 0.06) | \
            (u > 0.94) | (v < 0.06) | (v > 0.94)
        return 1.0 - bars.astype(float)
    if key in {'DAPPLE', 'BRANCHES'}:
        n = _noise(np, 9, 3) * 0.6 + _noise(np, 3, 5) * 0.4
        light = _smooth(np, n, 0.48, 0.56)
        if key == 'BRANCHES':
            stripe = np.abs(np.sin((u + v * 0.8 + _noise(np, 4, 7) * 0.4) * np.pi * 3))
            light = light * (stripe > 0.12)
        return light
    if key == 'WATER':
        w = _noise(np, 5, 11)
        a = np.sin((u + w * 0.35) * np.pi * 9) + np.sin((v + w * 0.3) * np.pi * 8 + 1.3)
        return np.maximum(1 - _smooth(np, np.abs(a), 0.0, 0.5), 0.2)
    if key == 'CLOUDS':
        return np.maximum(_smooth(np, _noise(np, 3, 13), 0.4, 0.65), 0.12)
    if key == 'SOFTBOX':
        edge = np.maximum(np.abs(u - 0.5), np.abs(v - 0.5))
        soft = 1 - _smooth(np, edge, 0.18, 0.48)
        grid = ((np.abs((u * 6) % 1 - 0.5) > 0.47) | (np.abs((v * 6) % 1 - 0.5) > 0.47)).astype(float)
        return soft * (1 - grid * 0.35)
    if key == 'GRADIENT':
        return _smooth(np, u, 0.1, 0.9)
    return np.ones((SIZE, SIZE))


def previews():
    """The preview collection (pattern key -> icon); made and loaded on the first call."""
    if _previews[0] is not None:
        return _previews[0]
    import numpy as np
    import bpy.utils.previews
    from .environment import FLAG_PATTERNS
    coll = bpy.utils.previews.new()
    folder = _folder()
    for key, _name, _desc in FLAG_PATTERNS + (('NONE', "None", ""),):
        path = os.path.join(folder, "%s_v1.png" % key.lower())
        if not os.path.isfile(path):
            grey = 0.12 + 0.88 * (_pattern(np, key) if key != 'NONE' else np.full((SIZE, SIZE), 0.55))
            _png(path, (np.clip(grey, 0, 1) * 255).astype(np.uint8)[::-1].copy())
        coll.load(key, path, 'IMAGE')
    _previews[0] = coll
    return coll


def icon(key):
    try:
        item = previews().get(key)
        return item.icon_id if item else 0
    except Exception as ex:              # no preview is no reason to break the panel
        print("ID Toolkit: gobo preview:", ex)
        return 0


def unregister():
    if _previews[0] is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(_previews[0])
        _previews[0] = None

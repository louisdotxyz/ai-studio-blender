# SPDX-License-Identifier: GPL-3.0-or-later
"""Looking through the renders in the Render tab: small pictures of every render (Blender's preview
thumbnails, made in the background), stars (0 … 5, kept next to the images in .ratings.json, so they
move with the folder), sorting and filtering, and what was refined from a render (Krea, adjusted)."""

import json
import os
import re

RATINGS = ".ratings.json"
SORTS = (('NEW', "Newest", 'SORTTIME'), ('BEST', "Best Rated", 'SOLO_ON'), ('CAMERA', "By Camera", 'CAMERA_DATA'))
FILTERS = (('ALL', "All", 'RENDERLAYERS'), ('HIGH', "High Quality", 'SHADING_RENDERED'),
           ('LOW', "Low Quality", 'SORTTIME'), ('RATED', "With Stars", 'SOLO_ON'),
           ('REFINED', "Refined", 'NODE_COMPOSITING'))
_REFINED = re.compile(r"_(krea|edit)_\d+$")

state = {"sort": 'NEW', "filter": 'ALL'}
_ratings = {}           # folder: (mtime, {file name: stars})
_previews = None        # bpy.utils.previews collection


# ------------------------------------------------------------------ stars

def _load(folder):
    path = os.path.join(folder, RATINGS)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    cached = _ratings.get(folder)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, encoding="utf-8") as f:
            data = {k: int(v) for k, v in json.load(f).items()}
    except (OSError, ValueError, TypeError, AttributeError):
        data = {}
    _ratings[folder] = (mtime, data)
    return data


def rating(path):
    return _load(os.path.dirname(path)).get(os.path.basename(path), 0)


def set_rating(path, stars):
    """0 … 5 stars; the same number again: none."""
    folder, name = os.path.dirname(path), os.path.basename(path)
    data = dict(_load(folder))
    stars = max(0, min(5, int(stars)))
    if data.get(name) == stars:
        stars = 0
    if stars:
        data[name] = stars
    else:
        data.pop(name, None)
    target = os.path.join(folder, RATINGS)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, sort_keys=True)
    _ratings[folder] = (os.path.getmtime(target), data)
    return stars


def stars_text(stars):
    return "★" * stars + "☆" * (5 - stars) if stars else ""


# ------------------------------------------------------------------ what a render is

def refined_kind(path):
    """"krea" / "edit" for a refined version (<render>_krea_01.png), else ""."""
    match = _REFINED.search(os.path.splitext(os.path.basename(path))[0])
    return match.group(1) if match else ""


def source_of(path):
    """The render a refined version was made from (if it is still there), else ""."""
    base, _ext = os.path.splitext(path)
    if not _REFINED.search(base):
        return ""
    root = _REFINED.sub("", base)
    for ext in (".png", ".exr", ".jpg", ".tif", ".tiff", ".webp"):
        if os.path.isfile(root + ext):
            return root + ext
    return ""


def camera_key(path):
    """The camera part of a render's name (VELO_P_front_01_03 → VELO_P_front) for sorting by camera."""
    stem = _REFINED.sub("", os.path.splitext(os.path.basename(path))[0])
    stem = re.sub(r"^\d{2}_", "", stem)            # a batch's running number
    return re.sub(r"(_\d+)+$", "", stem) or stem


def quality_of(path):
    from . import core
    names = core.folder_names()
    parts = os.path.normpath(path).split(os.sep)
    return 'LOW' if names['LOW'] in parts else 'HIGH' if names['HIGH'] in parts else ""


def arrange(images):
    """The renders filtered and sorted as chosen in the Render tab."""
    flt, sort = state["filter"], state["sort"]
    if flt in {'HIGH', 'LOW'}:
        images = [p for p in images if quality_of(p) == flt]
    elif flt == 'RATED':
        images = [p for p in images if rating(p)]
    elif flt == 'REFINED':
        images = [p for p in images if refined_kind(p)]
    if sort == 'BEST':
        images = sorted(images, key=lambda p: -rating(p))           # stable: newest first among equals
    elif sort == 'CAMERA':
        images = sorted(images, key=camera_key)
    return list(images)


# ------------------------------------------------------------------ small pictures

def thumb(path):
    """Icon id of a small picture of the render (Blender makes it in the background), 0 if none."""
    global _previews
    if not path or not os.path.isfile(path):
        return 0
    try:
        if _previews is None:
            import bpy.utils.previews
            _previews = bpy.utils.previews.new()
        key = "%s|%d" % (path, int(os.path.getmtime(path)))
        item = _previews.get(key) or _previews.load(key, path, 'IMAGE')
        return item.icon_id
    except (OSError, RuntimeError, KeyError):
        return 0


def unregister():
    global _previews
    if _previews is not None:
        import bpy.utils.previews
        bpy.utils.previews.remove(_previews)
        _previews = None

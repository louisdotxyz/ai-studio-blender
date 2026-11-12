# SPDX-License-Identifier: GPL-3.0-or-later
"""Material variants for the render queue (in execute_python as V).

A queue entry can carry a variant (RS_Item.variant). The render worker applies it
to its own copy of the file (V.plan, runner), so your scene never changes. varianten.txt in the
output folder lists file -> variant.

The variants are a text datablock "Varianten.json" in the .blend (editable by hand):

    "targets":  {"BODY": "Wood/Stain", "TRIM": "Plastic/Principled BSDF"}   alias -> "Material/Node"
    "variants": {"01 Name": {"BODY": {"ncs": "S4040-R40B"}, "TRIM": {"color": "#F07A2A", "Roughness": 0.3}}}

Values: an input name (or its start / last word) with a number, "color" = the first color
input (sRGB hex), "ncs" = NCS code for a node group with the inputs Schwaerze, Buntheit, Buntton.
"""

import json
import os
import re

import bpy

TEXT = "Varianten.json"
LOG = "varianten.txt"
QUALITY_ALIASES = {"HQ": "HIGH", "LQ": "LOW", "PREVIEW": "LOW", "FINAL": "HIGH"}   # what models write
NCS_HUE = {"Y": 0, "R": 100, "B": 200, "G": 300}


def _linear(hexcode):
    h = hexcode.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return out + [1.0]


def _hex(color):
    out = ""
    for c in color[:3]:
        c = max(0.0, min(1.0, c))
        c = c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
        out += "%02X" % round(c * 255)
    return "#" + out


def ncs(code):
    """'S4040-R40B' -> (blackness 40, chromaticness 40, hue 140) with 0=Y 100=R 200=B 300=G."""
    match = re.fullmatch(r"S?\s*(\d\d)(\d\d)-?([YRBGN])(?:(\d\d)([YRBG]))?", code.strip().upper())
    if not match:
        raise ValueError("Cannot read NCS code: %r" % code)
    s, c, h1, step, _h2 = match.groups()
    hue = 0.0 if h1 == "N" else NCS_HUE[h1] + (int(step) if step else 0)
    return float(s), float(c), hue % 400


class V:
    # ------------------------------------------------------------ data
    @staticmethod
    def data():
        text = bpy.data.texts.get(TEXT)
        return json.loads(text.as_string()) if text else {"targets": {}, "variants": {}}

    @staticmethod
    def save(d):
        text = bpy.data.texts.get(TEXT) or bpy.data.texts.new(TEXT)
        text.from_string(json.dumps(d, indent=1, ensure_ascii=False))

    @staticmethod
    def names():
        return list(V.data()["variants"])

    @staticmethod
    def node(target):
        material_name, node_name = target.split("/", 1)
        return bpy.data.materials[material_name].node_tree.nodes[node_name]

    @staticmethod
    def _input(node, key):
        inputs = [i for i in node.inputs if hasattr(i, "default_value") and not i.is_linked]
        k = key.lower()
        if k == "color":
            return next(i for i in inputs if i.type == 'RGBA')
        for test in (lambda n: n == k, lambda n: n.startswith(k), lambda n: n.split()[-1] == k):
            hit = [i for i in inputs if test(i.name.lower())]
            if hit:
                return hit[0]
        raise KeyError("%s: no input %r" % (node.id_data.name, key))

    # ------------------------------------------------------------ apply
    @staticmethod
    def _resolve(target, values):
        """[(socket, value)] that `values` set on the node of `target`."""
        node = V.node(target)
        out = []
        for key, value in values.items():
            if key.lower() == "ncs":
                for k, v in zip(("Schwaerze", "Buntheit", "Buntton"), ncs(value)):
                    out.append((V._input(node, k), v))
            elif isinstance(value, str):
                out.append((V._input(node, key), _linear(value)))
            else:
                out.append((V._input(node, key), value))
        return out

    @staticmethod
    def set_values(target, values):
        for socket, value in V._resolve(target, values):
            socket.default_value = value

    @staticmethod
    def plan(name):
        """What the variant sets, without setting it: [[material, node, input identifier, value]]
        (the render worker applies it to its own copy)."""
        d = V.data()
        out = []
        for alias, values in d["variants"][name].items():
            target = d["targets"][alias]
            material, node = target.split("/", 1)
            for socket, value in V._resolve(target, values):
                value = list(value) if hasattr(value, "__len__") else value
                out.append([material, node, socket.identifier, value])
        return out

    @staticmethod
    def apply(name):
        """Set a variant in the scene (stays until changed; the queue does this by itself)."""
        d = V.data()
        for alias, values in d["variants"][name].items():
            V.set_values(d["targets"][alias], values)
        return name

    @staticmethod
    def capture(targets=None):
        """The current values of all targets as a variant dict (colors as hex)."""
        out = {}
        for alias, target in (targets or V.data()["targets"]).items():
            values = {}
            for i in V.node(target).inputs:
                if i.is_linked or not hasattr(i, "default_value"):
                    continue
                if i.type == 'RGBA':
                    values[i.name] = _hex(i.default_value)
                elif i.type in {'VALUE', 'INT'}:
                    values[i.name] = round(float(i.default_value), 4)
            out[alias] = values
        return out

    # ------------------------------------------------------------ queue
    @staticmethod
    def install():
        """Kept for older skills: variants are part of the queue now, nothing to install."""

    @staticmethod
    def queue(names, camera=None, quality='LOW', start=True):
        """One queue entry per variant (same camera), started right away by default."""
        from . import runner
        from .ui_common import active_camera, ensure_uids
        scene = bpy.context.scene
        rq = scene.render_studio
        variants = V.data()["variants"]
        missing = [n for n in names if n not in variants]
        if missing:
            raise KeyError("Unknown variants: %s" % missing)
        cam = bpy.data.objects[camera] if camera else active_camera(scene)
        runner.RUN.set_scene_camera(scene, cam)
        ensure_uids(rq)
        for name in names:
            bpy.ops.render_studio.add(quality=quality)
            rq.items[-1].variant = name
        if start and not runner.RUN.busy:
            runner.RUN.start(scene)
        return V.status()

    @staticmethod
    def status():
        from . import runner
        run = runner.RUN
        scene = bpy.context.scene
        left = [it.variant or runner.item_label(scene, it) for it in scene.render_studio.items if it.camera]
        return "busy=%s done=%d failed=%s now=%s %s | %s | open: %s" % (
            run.busy, run.done, run.failed, run.current_camera, run.status, run.message, left)

    # ------------------------------------------------------------ check
    @staticmethod
    def folder(quality=None):
        from . import core, runner
        root = runner.settings_from_prefs()[0]
        rq = bpy.context.scene.render_studio
        quality = QUALITY_ALIASES.get(str(quality or "").upper(), quality) or rq.quality
        return core.quality_dir(root, core.project_name(None, rq.output_name), quality)

    @staticmethod
    def renders(quality=None, last=None):
        """[(file, variant)] from varianten.txt in the preview/HQ folder, newest last. Without
        varianten.txt (a queue without variants, an unsaved file): the images in that folder by
        time, variant ""."""
        folder = V.folder(quality)
        path = os.path.join(folder, LOG)
        rows = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    file, _, variant = line.rstrip("\n").partition("\t")
                    rows.append((os.path.join(folder, file), variant))
        if not rows and os.path.isdir(folder):
            images = [os.path.join(folder, n) for n in os.listdir(folder)
                      if n.lower().endswith((".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff"))]
            rows = [(p, "") for p in sorted(images, key=os.path.getmtime)]
        return rows[-last:] if last else rows

    @staticmethod
    def _pixels(path, width):
        import numpy as np
        image = bpy.data.images.load(path, check_existing=False)
        w, h = image.size
        image.scale(width, round(h * width / w))
        px = np.array(image.pixels[:], dtype=np.float32).reshape(image.size[1], image.size[0], 4)
        bpy.data.images.remove(image)
        return px

    @staticmethod
    def sheet(paths, name="sheet.png", cols=5, width=360, gap=8):
        """Contact sheet in <extension user dir>/render_check (look at it with Read)."""
        import numpy as np
        from ..core import workdir
        tiles = [V._pixels(p, width) for p in paths]
        tile_h = max(t.shape[0] for t in tiles)
        cols = min(cols, len(tiles))
        rows = (len(tiles) + cols - 1) // cols
        W, H = cols * width + (cols + 1) * gap, rows * tile_h + (rows + 1) * gap
        canvas = np.ones((H, W, 4), dtype=np.float32)
        canvas[..., :3] = 0.12
        for n, tile in enumerate(tiles):
            r, c = divmod(n, cols)
            y = H - (r + 1) * (tile_h + gap)      # Blender's pixels start at the bottom
            x = gap + c * (width + gap)
            canvas[y:y + tile.shape[0], x:x + tile.shape[1]] = tile
        folder = os.path.join(workdir(), "render_check")
        os.makedirs(folder, exist_ok=True)
        out = os.path.join(folder, name)
        image = bpy.data.images.new("_sheet", W, H, alpha=False)
        image.pixels[:] = canvas.ravel()
        image.filepath_raw = out
        image.file_format = 'PNG'
        image.save()
        bpy.data.images.remove(image)
        return out


def log_render(path, variant):
    """One line "file<TAB>variant" in varianten.txt next to the image."""
    try:
        with open(os.path.join(os.path.dirname(path), LOG), "a", encoding="utf-8") as f:
            f.write("%s\t%s\n" % (os.path.basename(path), variant))
    except OSError:
        pass

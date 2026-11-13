# SPDX-License-Identifier: GPL-3.0-or-later
"""Material structures: procedural starting points built in Blender, to go on from by hand.

A structure is a small node setup: Texture Coordinate (Object) → Mapping → one node group ("ID | Worn
Metal", …) → Bump → Principled BSDF. The group holds the pattern and offers the few values that matter;
open it (Tab) to change how it is made. Everything is procedural 3D: no UVs, no image files, works on
CAD parts as they come.

    Worn Metal       tumbled or used metal: scratches in all directions, uneven sheen, patina
    Layered Earth    rammed earth, cast stone: layers along Z, grain, pores
    Speckled         recycled plastic, powder coat, terrazzo-like: flecks of two colors, fine grain
    Resin            amber, acetate, tinted glass: clear, the color deepens with the thickness, bands
    Hammered         polished metal with dents
    Dark Anodized    dark metal with fine long scratches and wipe marks

Wear is a layer for any material with a Principled BSDF: scratches, fingerprints and dust, each with
its own amount. The building blocks ("ID | Scratches", "ID | Fingerprints", "ID | Dust") are node groups
of their own and can be used in any node tree.

Sizes are for products of 10 to 30 cm; "Scale" makes a pattern finer (2) or coarser (0.5).
"""

import math

import bpy

from . import matpresets

PREFIX = "ID | "
VERSION = 1
VERSION_KEY = "id_structure_version"
STRUCTURE_KEY = "id_structure"        # material[STRUCTURE_KEY] = kind
NODE_GROUP, NODE_BUMP, NODE_MAPPING = "ID Structure", "ID Bump", "ID Mapping"
NODE_WEAR, NODE_WEAR_BUMP = "ID Wear", "ID Wear Bump"

F, C, V = 'NodeSocketFloat', 'NodeSocketColor', 'NodeSocketVector'


def rgb(r, g, b):
    return (r, g, b, 1.0)


# ------------------------------------------------------------------ a small node builder

class Build:
    """Adds nodes to a tree; every method returns the output socket to go on from."""

    def __init__(self, tree):
        self.tree = tree
        self.count = 0

    def node(self, kind, **props):
        node = self.tree.nodes.new(kind)
        for key, value in props.items():
            setattr(node, key, value)
        node.location = (200 * (self.count % 8), -220 * (self.count // 8))
        self.count += 1
        return node

    def put(self, socket, value):
        """A number, tuple or socket into an input."""
        if value is None:
            return
        if isinstance(value, bpy.types.NodeSocket):
            self.tree.links.new(value, socket)
        else:
            socket.default_value = value

    def math(self, operation, a, b=None, c=None, clamp=False):
        node = self.node('ShaderNodeMath', operation=operation, use_clamp=clamp)
        for index, value in enumerate((a, b, c)):
            self.put(node.inputs[index], value)
        return node.outputs[0]

    def vector(self, operation, a, b=None, scale=None):
        node = self.node('ShaderNodeVectorMath', operation=operation)
        self.put(node.inputs[0], a)
        self.put(node.inputs[1], b)
        self.put(node.inputs[3], scale)
        return node.outputs[1] if operation == 'LENGTH' else node.outputs[0]

    def mapping(self, vector, scale=None, rotation=None):
        node = self.node('ShaderNodeMapping')
        self.put(node.inputs["Vector"], vector)
        self.put(node.inputs["Scale"], scale)
        self.put(node.inputs["Rotation"], rotation)
        return node.outputs[0]

    def noise(self, vector, scale, detail=2.0, roughness=0.5, color=False):
        node = self.node('ShaderNodeTexNoise')
        self.put(node.inputs["Vector"], vector)
        self.put(node.inputs["Scale"], scale)
        node.inputs["Detail"].default_value = detail
        node.inputs["Roughness"].default_value = roughness
        return node.outputs["Color"] if color else node.outputs["Fac"]

    def voronoi(self, vector, scale, feature='F1', smoothness=None):
        node = self.node('ShaderNodeTexVoronoi', feature=feature)
        self.put(node.inputs["Vector"], vector)
        self.put(node.inputs["Scale"], scale)
        if smoothness is not None:
            node.inputs["Smoothness"].default_value = smoothness
        return node

    def smooth(self, value, low, high):
        """0 below `low`, 1 above `high`, soft in between."""
        node = self.node('ShaderNodeMapRange', interpolation_type='SMOOTHSTEP')
        self.put(node.inputs["Value"], value)
        self.put(node.inputs["From Min"], low)
        self.put(node.inputs["From Max"], high)
        return node.outputs["Result"]

    def mix(self, factor, a, b):
        """Colors a and b mixed by factor."""
        node = self.node('ShaderNodeMix', data_type='RGBA')
        self.put(node.inputs[0], factor)
        self.put(node.inputs[6], a)
        self.put(node.inputs[7], b)
        return node.outputs[2]

    def blend(self, factor, a, b):
        """Numbers a and b mixed by factor."""
        node = self.node('ShaderNodeMix', data_type='FLOAT')
        self.put(node.inputs[0], factor)
        self.put(node.inputs[2], a)
        self.put(node.inputs[3], b)
        return node.outputs[0]

    def xyz(self, vector):
        node = self.node('ShaderNodeSeparateXYZ')
        self.put(node.inputs[0], vector)
        return node.outputs

    def group(self, tree, **inputs):
        node = self.node('ShaderNodeGroup', node_tree=tree)
        for name, value in inputs.items():
            self.put(node.inputs[name.replace("_", " ")], value)
        return node


def _group(name, inputs, outputs, build):
    """The node group `name`, built once per file (again when this module's VERSION is newer).
    inputs: (label, socket kind, default, min, max); outputs: (label, socket kind);
    build(b, ins) returns {output label: socket or value}."""
    tree = bpy.data.node_groups.get(PREFIX + name)
    if tree is not None and tree.get(VERSION_KEY) == VERSION:
        return tree
    if tree is None:
        tree = bpy.data.node_groups.new(PREFIX + name, 'ShaderNodeTree')
    else:
        tree.nodes.clear()
        tree.interface.clear()
    for label, kind in outputs:
        tree.interface.new_socket(label, in_out='OUTPUT', socket_type=kind)
    for label, kind, default, low, high in inputs:
        socket = tree.interface.new_socket(label, in_out='INPUT', socket_type=kind)
        if kind == V:
            socket.hide_value = True
            continue
        socket.default_value = default
        if kind == F:
            socket.min_value, socket.max_value = low, high
            if (low, high) == (0.0, 1.0):
                socket.subtype = 'FACTOR'
    b = Build(tree)
    enter, leave = b.node('NodeGroupInput'), b.node('NodeGroupOutput')
    result = build(b, {label: enter.outputs[label] for label, *_rest in inputs})
    for label, value in result.items():
        b.put(leave.inputs[label], value)
    leave.location = (200 * 8, 0)
    tree[VERSION_KEY] = VERSION
    return tree


# ------------------------------------------------------------------ building blocks

def scratches():
    """Fine scratches in three directions. Mask: 1 on a scratch."""
    def build(b, ins):
        v = b.vector('SCALE', ins["Vector"], scale=ins["Scale"])
        mask = None
        for i, turn in enumerate(((0.0, 0.0, 0.3), (0.9, 0.3, 1.5), (-0.8, 0.7, 2.6))):
            stretched = b.mapping(b.mapping(v, rotation=turn), scale=(1.0, 0.05, 1.0))
            line = b.math('ABSOLUTE', b.math('SUBTRACT', b.noise(stretched, 380.0 + 90.0 * i, detail=1.0), 0.5))
            line = b.math('SUBTRACT', 1.0, b.smooth(line, 0.0, 0.012))
            where = b.noise(b.vector('ADD', v, (3.1 * i, 1.7, 0.4 * i)), 22.0, detail=2.0)
            start = b.math('MULTIPLY_ADD', ins["Amount"], -0.32, 0.7)
            layer = b.math('MULTIPLY', line, b.smooth(where, start, b.math('ADD', start, 0.08)))
            mask = layer if mask is None else b.math('MAXIMUM', mask, layer)
        return {"Mask": mask}
    return _group("Scratches", (("Vector", V, None, 0, 0), ("Scale", F, 1.0, 0.01, 100.0), ("Amount", F, 0.5, 0.0, 1.0)),
                  (("Mask", F),), build)


def fingerprints():
    """Oval prints with ridges, here and there. Mask: 1 on a ridge."""
    def build(b, ins):
        v = b.vector('SCALE', ins["Vector"], scale=ins["Scale"])
        cells = b.voronoi(v, 22.0)
        local = b.vector('MULTIPLY', b.vector('SUBTRACT', v, cells.outputs["Position"]), (1.0, 0.72, 1.0))
        distance = b.math('ADD', b.vector('LENGTH', local), b.math('MULTIPLY', b.noise(v, 90.0), 0.003))
        ridges = b.smooth(b.math('SINE', b.math('MULTIPLY', distance, 2 * math.pi / 0.0011)), -0.3, 0.5)
        area = b.math('SUBTRACT', 1.0, b.smooth(distance, 0.007, 0.012))
        there = b.math('LESS_THAN', b.xyz(cells.outputs["Color"])[0], b.math('MULTIPLY', ins["Amount"], 0.75))
        smear = b.math('MULTIPLY_ADD', b.noise(v, 140.0, detail=3.0), 0.7, 0.3)
        mask = b.math('MULTIPLY', b.math('MULTIPLY', ridges, area), b.math('MULTIPLY', there, smear))
        return {"Mask": mask}
    return _group("Fingerprints", (("Vector", V, None, 0, 0), ("Scale", F, 1.0, 0.01, 100.0),
                                   ("Amount", F, 0.4, 0.0, 1.0)), (("Mask", F),), build)


def dust():
    """Dust: more on faces that look up, fine specks everywhere. Mask: 1 = covered."""
    def build(b, ins):
        v = b.vector('SCALE', ins["Vector"], scale=ins["Scale"])
        up = b.smooth(b.xyz(b.node('ShaderNodeNewGeometry').outputs["Normal"])[2], 0.1, 0.9)
        specks = b.smooth(b.noise(v, 900.0, detail=2.0), 0.55, 0.75)
        cover = b.math('MULTIPLY', up, b.smooth(b.noise(v, 9.0, detail=3.0), 0.3, 0.7))
        lying = b.math('MULTIPLY', specks, b.math('MULTIPLY_ADD', up, 0.7, 0.3))
        both = b.math('ADD', b.math('MULTIPLY', cover, 0.55), b.math('MULTIPLY', lying, 0.6))
        return {"Mask": b.math('MULTIPLY', both, ins["Amount"], clamp=True)}
    return _group("Dust", (("Vector", V, None, 0, 0), ("Scale", F, 1.0, 0.01, 100.0), ("Amount", F, 0.3, 0.0, 1.0)),
                  (("Mask", F),), build)


def wear_group():
    """Scratches, fingerprints and dust over any color and roughness."""
    def build(b, ins):
        v = ins["Vector"]
        s = b.math('MULTIPLY', b.group(scratches(), Vector=v, Scale=ins["Scale"], Amount=ins["Scratches"]).outputs[0],
                   b.smooth(ins["Scratches"], 0.0, 0.15))
        f = b.math('MULTIPLY', b.group(fingerprints(), Vector=v, Scale=ins["Scale"],
                                       Amount=ins["Fingerprints"]).outputs[0], b.smooth(ins["Fingerprints"], 0.0, 0.15))
        d = b.group(dust(), Vector=v, Scale=ins["Scale"], Amount=ins["Dust"]).outputs[0]
        rough = b.math('ADD', b.math('MULTIPLY_ADD', s, 0.3, ins["Roughness"]), b.math('MULTIPLY', f, 0.35), clamp=True)
        color = b.mix(b.math('MULTIPLY', s, 0.2), ins["Color"], rgb(1.0, 1.0, 1.0))
        return {"Color": b.mix(b.math('MULTIPLY', d, 0.85), color, ins["Dust Color"]),
                "Roughness": b.blend(d, rough, 0.85),
                "Height": b.math('MULTIPLY_ADD', s, -1.0, b.math('MULTIPLY', d, 0.3))}
    return _group("Wear", (("Vector", V, None, 0, 0), ("Color", C, rgb(0.8, 0.8, 0.8), 0, 0),
                           ("Roughness", F, 0.5, 0.0, 1.0), ("Scale", F, 1.0, 0.01, 100.0),
                           ("Scratches", F, 0.4, 0.0, 1.0), ("Fingerprints", F, 0.3, 0.0, 1.0),
                           ("Dust", F, 0.2, 0.0, 1.0), ("Dust Color", C, rgb(0.55, 0.52, 0.47), 0, 0)),
                  (("Color", C), ("Roughness", F), ("Height", F)), build)


# ------------------------------------------------------------------ the structures

def _worn_metal():
    def build(b, ins):
        v = b.vector('SCALE', ins["Vector"], scale=ins["Scale"])
        patina = b.math('MULTIPLY', b.smooth(b.noise(v, 30.0, detail=5.0, roughness=0.7), 0.45, 0.75), ins["Patina"])
        s = b.group(scratches(), Vector=ins["Vector"], Scale=ins["Scale"], Amount=ins["Scratches"]).outputs[0]
        sheen = b.noise(v, 14.0, detail=4.0, roughness=0.6)
        dark = b.mix(0.6, ins["Color"], rgb(0.06, 0.07, 0.08))
        color = b.mix(b.math('MULTIPLY', s, 0.35), b.mix(b.math('MULTIPLY', patina, 0.7), ins["Color"], dark),
                      rgb(0.9, 0.9, 0.9))
        rough = b.math('ADD', b.math('MULTIPLY_ADD', b.math('SUBTRACT', sheen, 0.5), 0.3, ins["Roughness"]),
                       b.math('ADD', b.math('MULTIPLY', s, 0.2), b.math('MULTIPLY', patina, 0.15)), clamp=True)
        pits = b.math('MULTIPLY', b.noise(v, 160.0, detail=3.0), 0.35)
        return {"Color": color, "Roughness": rough, "Height": b.math('MULTIPLY_ADD', s, -0.6, pits)}
    return _group("Worn Metal", (("Vector", V, None, 0, 0), ("Color", C, rgb(0.62, 0.63, 0.65), 0, 0),
                                 ("Roughness", F, 0.38, 0.0, 1.0), ("Scratches", F, 0.7, 0.0, 1.0),
                                 ("Patina", F, 0.5, 0.0, 1.0), ("Scale", F, 1.0, 0.01, 100.0)),
                  (("Color", C), ("Roughness", F), ("Height", F)), build)


def _layered_earth():
    def build(b, ins):
        v = b.vector('SCALE', ins["Vector"], scale=ins["Scale"])
        wave = b.math('MULTIPLY', b.math('SUBTRACT', b.noise(v, 18.0, detail=3.0), 0.5), 0.6)
        level = b.math('FLOOR', b.math('ADD', b.math('DIVIDE', b.xyz(ins["Vector"])[2], ins["Layer Height"]), wave))
        pick = b.node('ShaderNodeTexWhiteNoise', noise_dimensions='1D')
        b.put(pick.inputs["W"], level)
        tone = b.math('MULTIPLY_ADD', b.math('SUBTRACT', b.noise(v, 260.0, detail=3.0), 0.5), 0.5, pick.outputs["Value"],
                      clamp=True)
        stones = b.voronoi(v, 190.0)
        pebble = b.math('SUBTRACT', 1.0, b.smooth(stones.outputs["Distance"], 0.15, 0.55))
        color = b.mix(b.math('MULTIPLY', pebble, 0.25), b.mix(tone, ins["Color A"], ins["Color B"]), rgb(0.85, 0.8, 0.7))
        pores = b.smooth(b.noise(v, 95.0, detail=2.0), 0.63, 0.7)
        grain = b.math('MULTIPLY', b.noise(v, 750.0, detail=4.0, roughness=0.7), ins["Grain"])
        height = b.math('SUBTRACT', b.math('MULTIPLY_ADD', pebble, 0.5, grain), b.math('MULTIPLY', pores, 1.5))
        return {"Color": color, "Roughness": 0.93, "Height": height}
    return _group("Layered Earth", (("Vector", V, None, 0, 0), ("Color A", C, rgb(0.62, 0.48, 0.3), 0, 0),
                                    ("Color B", C, rgb(0.33, 0.19, 0.1), 0, 0),
                                    ("Layer Height", F, 0.025, 0.001, 10.0), ("Grain", F, 0.7, 0.0, 1.0),
                                    ("Scale", F, 1.0, 0.01, 100.0)),
                  (("Color", C), ("Roughness", F), ("Height", F)), build)


def _speckled():
    def build(b, ins):
        v = b.vector('SCALE', ins["Vector"], scale=ins["Scale"])
        color = ins["Color"]
        for scale, share in ((1500.0, 1.0), (520.0, 0.35)):       # fine flecks, a few bigger ones
            random = b.xyz(b.voronoi(v, b.math('DIVIDE', scale, ins["Fleck Size"])).outputs["Color"])
            dark = b.math('LESS_THAN', random[0], b.math('MULTIPLY', ins["Amount"], 0.45 * share))
            light = b.math('LESS_THAN', random[1], b.math('MULTIPLY', ins["Amount"], 0.35 * share))
            color = b.mix(light, b.mix(dark, color, ins["Fleck Color A"]), ins["Fleck Color B"])
        grain = b.noise(v, 1600.0, detail=2.0)
        rough = b.math('MULTIPLY_ADD', b.math('SUBTRACT', grain, 0.5), 0.15, ins["Roughness"], clamp=True)
        return {"Color": color, "Roughness": rough, "Height": grain}
    return _group("Speckled", (("Vector", V, None, 0, 0), ("Color", C, rgb(0.6, 0.38, 0.28), 0, 0),
                               ("Fleck Color A", C, rgb(0.3, 0.12, 0.07), 0, 0),
                               ("Fleck Color B", C, rgb(0.85, 0.75, 0.65), 0, 0),
                               ("Fleck Size", F, 1.0, 0.1, 20.0), ("Amount", F, 0.6, 0.0, 1.0),
                               ("Roughness", F, 0.6, 0.0, 1.0), ("Scale", F, 1.0, 0.01, 100.0)),
                  (("Color", C), ("Roughness", F), ("Height", F)), build)


def _hammered():
    def build(b, ins):
        v = b.vector('SCALE', ins["Vector"], scale=ins["Scale"])
        pushed = b.vector('ADD', v, b.vector('SCALE', b.noise(v, 60.0, color=True), scale=0.004))
        dents = b.voronoi(pushed, b.math('DIVIDE', 1.0, ins["Dent Size"]), feature='SMOOTH_F1', smoothness=0.7)
        bowl = b.math('POWER', dents.outputs["Distance"], 2.0)
        rough = b.math('MULTIPLY_ADD', b.noise(v, 40.0, detail=3.0), 0.06, ins["Roughness"], clamp=True)
        return {"Color": ins["Color"], "Roughness": rough, "Height": b.math('MULTIPLY', bowl, ins["Depth"])}
    return _group("Hammered", (("Vector", V, None, 0, 0), ("Color", C, rgb(0.92, 0.92, 0.9), 0, 0),
                               ("Roughness", F, 0.05, 0.0, 1.0), ("Dent Size", F, 0.009, 0.0005, 1.0),
                               ("Depth", F, 0.6, 0.0, 2.0), ("Scale", F, 1.0, 0.01, 100.0)),
                  (("Color", C), ("Roughness", F), ("Height", F)), build)


def _dark_anodized():
    def build(b, ins):
        v = b.vector('SCALE', ins["Vector"], scale=ins["Scale"])
        streaks = b.noise(b.mapping(b.mapping(v, rotation=(0.0, 0.0, 0.5)), scale=(1.0, 0.12, 1.0)), 60.0, detail=4.0)
        clouds = b.noise(v, 18.0, detail=4.0, roughness=0.6)
        smudge = b.math('MULTIPLY', b.smooth(b.blend(0.5, streaks, clouds), 0.38, 0.7), ins["Smudges"])
        long_scale = b.math('MULTIPLY', ins["Scale"], 0.4)       # longer, fewer scratches than on worn metal
        s = b.group(scratches(), Vector=ins["Vector"], Scale=long_scale, Amount=ins["Scratches"]).outputs[0]
        lighter = b.mix(0.5, ins["Color"], rgb(0.12, 0.12, 0.13))
        color = b.mix(b.math('MULTIPLY', s, 0.55), b.mix(b.math('MULTIPLY', smudge, 0.5), ins["Color"], lighter),
                      rgb(0.4, 0.4, 0.42))
        rough = b.math('ADD', b.math('MULTIPLY_ADD', b.math('SUBTRACT', smudge, 0.3), 0.35, ins["Roughness"]),
                       b.math('MULTIPLY', s, 0.15), clamp=True)
        return {"Color": color, "Roughness": rough, "Height": b.math('MULTIPLY', s, -0.5)}
    return _group("Dark Anodized", (("Vector", V, None, 0, 0), ("Color", C, rgb(0.012, 0.012, 0.014), 0, 0),
                                    ("Roughness", F, 0.36, 0.0, 1.0), ("Scratches", F, 0.45, 0.0, 1.0),
                                    ("Smudges", F, 0.7, 0.0, 1.0), ("Scale", F, 1.0, 0.01, 100.0)),
                  (("Color", C), ("Roughness", F), ("Height", F)), build)


def _resin():
    def build(b, ins):
        v = b.vector('SCALE', ins["Vector"], scale=ins["Scale"])
        along = b.math('DIVIDE', b.xyz(v)[2], ins["Band Size"])
        drift = b.math('MULTIPLY', b.math('SUBTRACT', b.noise(v, 12.0, detail=2.0), 0.5), 1.2)
        wave = b.math('SINE', b.math('MULTIPLY', b.math('ADD', along, drift), math.pi))
        band = b.math('MULTIPLY', b.smooth(wave, -0.25, 0.25), ins["Bands"])
        return {"Color": b.mix(band, ins["Color"], ins["Band Color"]),
                "Density": b.math('MULTIPLY', ins["Depth"], b.math('MULTIPLY_ADD', band, 1.5, 1.0))}
    return _group("Resin", (("Vector", V, None, 0, 0), ("Color", C, rgb(0.9, 0.32, 0.04), 0, 0),
                            ("Band Color", C, rgb(0.5, 0.07, 0.01), 0, 0), ("Bands", F, 0.6, 0.0, 1.0),
                            ("Band Size", F, 0.02, 0.0005, 10.0), ("Depth", F, 90.0, 0.0, 5000.0),
                            ("Scale", F, 1.0, 0.01, 100.0)),
                  (("Color", C), ("Density", F)), build)


# kind: (name, group builder, Principled inputs, bump distance in m, what it is for)
STRUCTURES = {
    'WORN_METAL': ("Worn Metal", _worn_metal, {"Metallic": 1.0}, 0.0004,
                   "Tumbled or used metal: scratches in all directions, uneven sheen, patina"),
    'LAYERED_EARTH': ("Layered Earth", _layered_earth, {"Specular IOR Level": 0.25}, 0.0025,
                      "Rammed earth, cast stone: layers along Z, grain and pores"),
    'SPECKLED': ("Speckled", _speckled, {}, 0.0003,
                 "Recycled plastic, powder coat: flecks of two colors and a fine grain"),
    'RESIN': ("Resin", _resin, {"Transmission Weight": 1.0, "Roughness": 0.04, "IOR": 1.5,
                                "Base Color": rgb(1.0, 0.97, 0.93)}, 0.0,
              "Amber, acetate, tinted glass: clear, the color deepens with the thickness, soft bands (Cycles)"),
    'HAMMERED': ("Hammered", _hammered, {"Metallic": 1.0}, 0.0012, "Polished metal with hammered dents"),
    'DARK_ANODIZED': ("Dark Anodized", _dark_anodized, {"Metallic": 1.0}, 0.0003,
                      "Dark metal with fine long scratches and wipe marks"),
}
ITEMS = tuple((kind, entry[0], entry[4]) for kind, entry in STRUCTURES.items())


def _coordinates(tree, x):
    coord = tree.nodes.new('ShaderNodeTexCoord')
    coord.location = (x - 220, 0)
    mapping = tree.nodes.new('ShaderNodeMapping')
    mapping.name = NODE_MAPPING
    mapping.location = (x, 0)
    tree.links.new(coord.outputs["Object"], mapping.inputs["Vector"])
    return mapping


def create(kind, name=None):
    """A new material with this structure. Returns the material."""
    if kind not in STRUCTURES:
        raise ValueError("Structure must be one of %s" % ", ".join(STRUCTURES))
    label, builder, values, distance, _text = STRUCTURES[kind]
    material = bpy.data.materials.new(name or label)
    tree = material.node_tree
    shader = matpresets._principled(material)
    if shader is None:
        raise RuntimeError("The new material has no Principled BSDF")
    out = next(n for n in tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial')
    shader.location, out.location = (0, 0), (320, 0)
    for key, value in values.items():
        if key in shader.inputs:
            shader.inputs[key].default_value = value
    mapping = _coordinates(tree, -760)
    group = tree.nodes.new('ShaderNodeGroup')
    group.node_tree = builder()
    group.name, group.label, group.location, group.width = NODE_GROUP, label, (-540, 0), 220
    tree.links.new(mapping.outputs[0], group.inputs["Vector"])
    if kind == 'RESIN':
        volume = tree.nodes.new('ShaderNodeVolumeAbsorption')
        volume.location = (0, -700)
        tree.links.new(group.outputs["Color"], volume.inputs["Color"])
        tree.links.new(group.outputs["Density"], volume.inputs["Density"])
        tree.links.new(volume.outputs[0], out.inputs["Volume"])
    else:
        tree.links.new(group.outputs["Color"], shader.inputs["Base Color"])
        tree.links.new(group.outputs["Roughness"], shader.inputs["Roughness"])
        bump = tree.nodes.new('ShaderNodeBump')
        bump.name, bump.label, bump.location = NODE_BUMP, "Surface Depth", (-260, -420)
        bump.inputs["Distance"].default_value = distance
        tree.links.new(group.outputs["Height"], bump.inputs["Height"])
        tree.links.new(bump.outputs[0], shader.inputs["Normal"])
    color = group.inputs["Color"].default_value if "Color" in group.inputs else group.inputs["Color A"].default_value
    material.diffuse_color = tuple(color)
    material.metallic = values.get("Metallic", 0.0)
    material.roughness = 0.4
    material[STRUCTURE_KEY] = kind
    return material


def set_values(material, values):
    """Sets inputs of the structure group and of the wear layer by their names (case and spaces do not
    matter: "fleck_size", "Fleck Size"). Returns the names that were set."""
    done = []
    wanted = {key.lower().replace("_", " "): value for key, value in values.items()}
    for node in control_nodes(material):
        for socket in node.inputs:
            key = socket.name.lower()
            if key in wanted and not socket.is_linked and hasattr(socket, "default_value"):
                value = wanted[key]
                socket.default_value = (tuple(value) + (1.0,))[:4] if socket.type == 'RGBA' else value
                done.append(socket.name)
    return done


def control_nodes(material):
    """The structure group and the wear layer of a material (their inputs are its controls)."""
    if material is None or material.node_tree is None:
        return []
    nodes = material.node_tree.nodes
    return [n for n in (nodes.get(NODE_GROUP), nodes.get(NODE_WEAR)) if n is not None and n.type == 'GROUP']


# ------------------------------------------------------------------ wear on any material

def has_wear(material):
    return material is not None and material.node_tree is not None and NODE_WEAR in material.node_tree.nodes


def add_wear(material, **values):
    """Puts scratches, fingerprints and dust over the material's color and roughness (what was plugged
    in stays plugged in, one step earlier). values: scratches, fingerprints, dust (0-1), scale."""
    shader = matpresets._principled(material)
    if shader is None:
        raise ValueError("%s has no Principled BSDF" % material.name)
    tree = material.node_tree
    if not has_wear(material):
        x, y = shader.location
        wear = tree.nodes.new('ShaderNodeGroup')
        wear.node_tree = wear_group()
        wear.name, wear.label, wear.location, wear.width = NODE_WEAR, "Wear", (x - 260, y + 80), 200
        mapping = tree.nodes.get(NODE_MAPPING) or _coordinates(tree, x - 520)
        tree.links.new(mapping.outputs[0], wear.inputs["Vector"])
        for name in ("Base Color", "Roughness"):
            target, entry = shader.inputs[name], wear.inputs["Color" if name == "Base Color" else "Roughness"]
            if target.is_linked:
                tree.links.new(target.links[0].from_socket, entry)
            else:
                entry.default_value = target.default_value
            tree.links.new(wear.outputs[entry.name], target)
        bump = tree.nodes.new('ShaderNodeBump')
        bump.name, bump.label, bump.location = NODE_WEAR_BUMP, "Wear Depth", (x - 260, y - 420)
        bump.inputs["Distance"].default_value = 0.0002
        normal = shader.inputs["Normal"]
        if normal.is_linked:
            tree.links.new(normal.links[0].from_socket, bump.inputs["Normal"])
        tree.links.new(wear.outputs["Height"], bump.inputs["Height"])
        tree.links.new(bump.outputs[0], normal)
    if values:
        set_values(material, values)
    return tree.nodes[NODE_WEAR]


def remove_wear(material):
    """Takes the wear layer out; what was plugged in before is plugged in again."""
    if not has_wear(material):
        return
    tree = material.node_tree
    wear, bump = tree.nodes[NODE_WEAR], tree.nodes.get(NODE_WEAR_BUMP)
    shader = matpresets._principled(material)
    if shader is not None:
        for name in ("Base Color", "Roughness"):
            target, entry = shader.inputs[name], wear.inputs["Color" if name == "Base Color" else "Roughness"]
            if entry.is_linked:
                tree.links.new(entry.links[0].from_socket, target)
            else:
                value = entry.default_value
                for link in list(target.links):
                    tree.links.remove(link)
                target.default_value = value
        if bump is not None:
            source = bump.inputs["Normal"].links[0].from_socket if bump.inputs["Normal"].is_linked else None
            for link in list(shader.inputs["Normal"].links):
                tree.links.remove(link)
            if source is not None:
                tree.links.new(source, shader.inputs["Normal"])
    tree.nodes.remove(wear)
    if bump is not None:
        tree.nodes.remove(bump)

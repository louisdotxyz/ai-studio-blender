# SPDX-License-Identifier: GPL-3.0-or-later
"""Writes "Base Materials.blend" (base materials as assets) into an asset library folder.

    Blender -b --factory-startup --python tools/make_base_materials.py -- "<asset library folder>"

The materials are plain Principled BSDF setups (no textures) for every project:
glass, metals, plastics, rubber, paint, fabric. They are marked as assets in the
catalog "Base"; the Material tab of AI Studio shows them as the group
"Base Materials" and copies one into the project when it is used. Set the file in
Preferences › Studio, or put it into one of your asset libraries.
"""

import os
import sys
import uuid

import bpy

FILE_NAME = "Base Materials.blend"
CATALOG = "Base"
# Stable id, so a re-run keeps the catalog
CATALOG_ID = "6f1c2d4e-5a7b-4c8d-9e0f-1a2b3c4d5e6f"

G = lambda v: (v, v, v, 1.0)   # gray in linear values
MATERIALS = {
    # name: Principled inputs
    "Glass clear": {"Base Color": G(1.0), "Transmission Weight": 1.0, "Roughness": 0.0, "IOR": 1.5},
    "Glass frosted": {"Base Color": G(1.0), "Transmission Weight": 1.0, "Roughness": 0.35, "IOR": 1.5},
    "Acrylic clear": {"Base Color": G(1.0), "Transmission Weight": 1.0, "Roughness": 0.02, "IOR": 1.49},
    "Stainless steel brushed": {"Base Color": G(0.62), "Metallic": 1.0, "Roughness": 0.3, "Anisotropic": 0.8},
    "Stainless steel polished": {"Base Color": G(0.62), "Metallic": 1.0, "Roughness": 0.06},
    "Aluminium anodized natural": {"Base Color": G(0.8), "Metallic": 1.0, "Roughness": 0.35},
    "Aluminium anodized black": {"Base Color": G(0.02), "Metallic": 1.0, "Roughness": 0.4},
    "Chrome": {"Base Color": G(0.9), "Metallic": 1.0, "Roughness": 0.02},
    "Brass": {"Base Color": (0.91, 0.67, 0.29, 1.0), "Metallic": 1.0, "Roughness": 0.25},
    "Copper": {"Base Color": (0.93, 0.5, 0.36, 1.0), "Metallic": 1.0, "Roughness": 0.25},
    "Rubber black": {"Base Color": G(0.02), "Roughness": 0.9, "Specular IOR Level": 0.3},
    "ABS black matte": {"Base Color": G(0.02), "Roughness": 0.55},
    "ABS white": {"Base Color": G(0.8), "Roughness": 0.4},
    "Plastic black glossy": {"Base Color": G(0.01), "Roughness": 0.1},
    "Powder coat black": {"Base Color": G(0.015), "Roughness": 0.45},
    "Lacquer white glossy": {"Base Color": G(0.8), "Roughness": 0.15, "Coat Weight": 1.0,
                            "Coat Roughness": 0.03},
    "Speaker cloth black": {"Base Color": G(0.01), "Roughness": 1.0, "Sheen Weight": 1.0,
                                  "Sheen Roughness": 0.4, "Specular IOR Level": 0.2},
    "Acoustic foam grey": {"Base Color": G(0.03), "Roughness": 1.0, "Specular IOR Level": 0.1},
    "Paper cone black": {"Base Color": G(0.02), "Roughness": 0.8, "Specular IOR Level": 0.3},
}


def build(folder):
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    for name, inputs in MATERIALS.items():
        material = bpy.data.materials.new(name)
        material.use_nodes = True
        node = next(n for n in material.node_tree.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled')
        for key, value in inputs.items():
            node.inputs[key].default_value = value
        material.diffuse_color = inputs.get("Base Color", G(0.8))
        material.metallic = inputs.get("Metallic", 0.0)
        material.roughness = inputs.get("Roughness", 0.5)
        material.use_fake_user = True
        material.asset_mark()
        material.asset_data.catalog_id = CATALOG_ID
        material.asset_data.description = "AI Studio – base material"
        material.asset_generate_preview()
    _write_catalog(folder)
    path = os.path.join(folder, FILE_NAME)
    bpy.ops.wm.save_as_mainfile(filepath=path, compress=True)
    print("WROTE", path, len(MATERIALS))


def _write_catalog(folder):
    """blender_assets.cats.txt: add the catalog "Base" if it is missing."""
    path = os.path.join(folder, "blender_assets.cats.txt")
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    if any(line.startswith(CATALOG_ID) for line in lines):
        return
    if not lines:
        lines = ["# This is an Asset Catalog Definition file for Blender.", "", "VERSION 1", ""]
    lines.append("%s:%s:%s" % (CATALOG_ID, CATALOG, CATALOG))
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    argv = sys.argv
    build(os.path.expanduser(argv[argv.index("--") + 1]))

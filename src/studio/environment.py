# SPDX-License-Identifier: GPL-3.0-or-later
"""The Environment tab's own pieces: studio floor / cove, haze, sun from the HDRI, flags and gobos,
solo and light linking, scene presets.

Everything this module creates carries obj["as_studio"] (so it never counts as the product and is
never baked into a room HDRI) and lives in the collection "Studio".
"""

import json
import math
import os

import bmesh
import bpy
from mathutils import Matrix, Vector

from . import background, cameras, hdri, lights, look

STUDIO_KEY = "as_studio"            # obj[STUDIO_KEY] = "floor" | "flag"
FLOOR_NAME = "Studio | Floor"
HAZE_NODE = "AS Haze"
SOLO_KEY = "as_solo"                # scene[SOLO_KEY] = {"light": name, "hidden": {name: hide_render}}
LINK_PREFIX = "Link | "


def _lin(rgb):
    return [lights.srgb_to_linear(c) for c in rgb[:3]]


def studio_collection(scene):
    return cameras.named_collection(scene, "Studio")


def product_objects(scene):
    """The visible meshes that make up the product: not ours, not a modelled room, not hidden."""
    out = []
    for obj in scene.objects:
        if obj.type not in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT'} or obj.hide_render:
            continue
        if obj.get(STUDIO_KEY) or obj.get("as_env") or any(c.name.startswith("ENV |") for c in obj.users_collection):
            continue
        out.append(obj)
    return out


def product_bounds(scene):
    """(min corner, max corner) of the product, or None without one."""
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    for obj in product_objects(scene):
        for corner in obj.bound_box:
            p = obj.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
    return (lo, hi) if lo.x <= hi.x else None


# ------------------------------------------------------------------ floor / cove

def _cove_profile(depth, height, radius, segments=12):
    """(y, z) points from the front edge over the curve up the back wall (+Y is the back)."""
    radius = max(0.0, min(radius, height, depth))
    points = [(-depth / 2, 0.0), (depth / 2 - radius, 0.0)]
    for i in range(1, segments + 1):
        a = -math.pi / 2 + (math.pi / 2) * i / segments
        points.append((depth / 2 - radius + radius * math.cos(a), radius + radius * math.sin(a)))
    if height > radius:
        points.append((depth / 2, height))
    return points


def _build_mesh(mesh, width, profile):
    bm = bmesh.new()
    rows = []
    for x in (-width / 2, width / 2):
        rows.append([bm.verts.new((x, y, z)) for y, z in profile])
    for i in range(len(profile) - 1):
        bm.faces.new((rows[0][i], rows[0][i + 1], rows[1][i + 1], rows[1][i]))
    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    for poly in mesh.polygons:
        poly.use_smooth = True


def _floor_material(rs):
    mat = bpy.data.materials.get(FLOOR_NAME) or bpy.data.materials.new(FLOOR_NAME)
    from .matpresets import _principled
    node = _principled(mat)
    if node is not None:
        node.inputs["Base Color"].default_value = (*_lin(rs.floor_color), 1.0)
        node.inputs["Roughness"].default_value = rs.floor_roughness
    mat.diffuse_color = (*_lin(rs.floor_color), 1.0)
    return mat


def remove_floor():
    obj = bpy.data.objects.get(FLOOR_NAME)
    if obj is not None:
        mesh = obj.data
        bpy.data.objects.remove(obj)
        if mesh is not None and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def build_floor(scene):
    """Builds (or rebuilds) the floor or cove under the product, its back wall facing away from the
    camera. NONE removes it."""
    rs = scene.render_studio
    if rs.floor_mode == 'NONE':
        remove_floor()
        return None
    bounds = product_bounds(scene)
    if bounds is None:
        center, size, ground = scene.cursor.location.copy(), 0.5, scene.cursor.location.z
    else:
        lo, hi = bounds
        center = (lo + hi) / 2
        size = max((hi - lo).length / 2, 0.05)
        ground = lo.z
    width = 2 * size * rs.floor_size
    depth = width * 0.75
    if rs.floor_mode == 'COVE':
        height = width * 0.5
        profile = _cove_profile(depth, height, height * rs.floor_curve)
    else:
        profile = [(-depth / 2, 0.0), (depth / 2, 0.0)]
    obj = bpy.data.objects.get(FLOOR_NAME)
    if obj is None or obj.type != 'MESH':
        remove_floor()
        obj = bpy.data.objects.new(FLOOR_NAME, bpy.data.meshes.new(FLOOR_NAME))
        studio_collection(scene).objects.link(obj)
    obj[STUDIO_KEY] = "floor"
    _build_mesh(obj.data, width, profile)
    # the back wall opposite the camera: local +Y points away from it
    angle = lights.front_angle(scene, center) + math.pi / 2 + math.pi
    obj.matrix_world = Matrix.Translation((center.x, center.y, ground)) @ Matrix.Rotation(angle, 4, 'Z')
    if not obj.data.materials:
        obj.data.materials.append(_floor_material(rs))
    else:
        obj.data.materials[0] = _floor_material(rs)
    obj.is_shadow_catcher = rs.floor_shadow_catcher
    obj.hide_select = False
    return obj


def on_floor(settings, context):
    try:
        build_floor(context.scene)
    except Exception as exc:   # never break a slider drag
        print("ID Toolkit: floor:", exc)


def on_floor_look(settings, context):
    obj = bpy.data.objects.get(FLOOR_NAME)
    if obj is None:
        return
    rs = context.scene.render_studio
    if obj.data.materials:
        obj.data.materials[0] = _floor_material(rs)
    obj.is_shadow_catcher = rs.floor_shadow_catcher


# ------------------------------------------------------------------ haze (World volume)

def _world_output(world):
    if world is None or world.node_tree is None:
        return None
    outputs = [n for n in world.node_tree.nodes if n.bl_idname == 'ShaderNodeOutputWorld']
    return next((n for n in outputs if n.is_active_output), outputs[0] if outputs else None)


def apply_haze(scene):
    """Volume scatter in the whole World: haze that makes light beams visible. 0 removes it."""
    rs = scene.render_studio
    world = scene.world
    out = _world_output(world)
    if out is None:
        return
    nt = world.node_tree
    node = nt.nodes.get(HAZE_NODE)
    if rs.haze_density <= 0:
        if node is not None:
            nt.nodes.remove(node)
        return
    if node is None:
        node = nt.nodes.new('ShaderNodeVolumePrincipled')
        node.name = node.label = HAZE_NODE
        node.location = (out.location.x - 250, out.location.y - 250)
    if not out.inputs["Volume"].is_linked or out.inputs["Volume"].links[0].from_node != node:
        nt.links.new(node.outputs["Volume"], out.inputs["Volume"])
    node.inputs["Density"].default_value = rs.haze_density
    node.inputs["Color"].default_value = (*_lin(rs.haze_color), 1.0)
    node.inputs["Anisotropy"].default_value = 0.4


def on_haze(settings, context):
    apply_haze(context.scene)


# ------------------------------------------------------------------ sun from the HDRI

def _env_image(scene):
    world = scene.world
    if world is None or world.node_tree is None:
        return None
    env = next((n for n in world.node_tree.nodes if n.bl_idname == 'ShaderNodeTexEnvironment' and n.image), None)
    return env.image if env else None


def brightest_direction(image, rotation=0.0, width=256):
    """(direction in world space, linear color) of the brightest spot of an equirectangular image."""
    import numpy as np
    path = bpy.path.abspath(image.filepath)
    copy = bpy.data.images.load(path, check_existing=False) if path and os.path.isfile(path) else image.copy()
    try:
        w, h = copy.size
        if w > width:
            copy.scale(width, max(1, round(h * width / w)))
            w, h = copy.size
        px = np.array(copy.pixels[:], dtype=np.float32).reshape(h, w, copy.channels)
    finally:
        bpy.data.images.remove(copy)
    lum = px[..., 0] * 0.2126 + px[..., 1] * 0.7152 + px[..., 2] * 0.0722
    y, x = np.unravel_index(int(np.argmax(lum)), lum.shape)
    u, v = (x + 0.5) / w, (y + 0.5) / h            # rows start at the bottom
    # Cycles: u = 0.5 - atan2(y, x) / 2π, elevation = π (v - 0.5)
    azimuth, elevation = math.pi - 2 * math.pi * u, (v - 0.5) * math.pi
    tex = Vector((math.cos(elevation) * math.cos(azimuth), math.cos(elevation) * math.sin(azimuth),
                  math.sin(elevation)))
    world = Matrix.Rotation(-rotation, 3, 'Z') @ tex   # the mapping node turns the World by `rotation`
    color = [float(c) for c in px[y, x, :3]]
    return world.normalized(), color


def sun_from_hdri(scene):
    """A sun light where the HDRI is brightest (direction and color), named like the other lights."""
    image = _env_image(scene)
    if image is None:
        raise ValueError("The World has no HDRI image")
    direction, color = brightest_direction(image, hdri.get_rotation(scene.render_studio))
    if direction.z < -0.05:
        raise ValueError("The brightest spot of this HDRI is below the horizon")
    peak = max(color) or 1.0
    name = cameras.scheme_name('LIGHT', 'SUN', "Sun")
    data = bpy.data.lights.new(name, 'SUN')
    data.color = [c / peak for c in color]
    data.energy = lights.SUN_STRENGTH * max(0.5, min(3.0, scene.render_studio.world_strength))
    data.angle = math.radians(1.5)
    obj = bpy.data.objects.new(name, data)
    lights.light_collection(scene).objects.link(obj)
    # a sun shines along its -Z: -Z = -direction
    obj.rotation_euler = direction.to_track_quat('Z', 'Y').to_euler()
    obj.location = scene.cursor.location + direction * 3
    return obj


# ------------------------------------------------------------------ flags and gobos

FLAG_PATTERNS = (
    ('FLAG', "Flag", "A black card that cuts part of the light"),
    ('BLINDS', "Blinds", "Stripes of light and shadow"),
    ('WINDOW', "Window", "A window frame's shadow"),
    ('DAPPLE', "Leaves", "Dappled light through leaves"),
)


def _flag_material(pattern):
    name = "Studio | Gobo " + pattern.title()
    mat = bpy.data.materials.get(name)
    if mat is not None:
        return mat
    mat = bpy.data.materials.new(name)
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    black = nt.nodes.new('ShaderNodeBsdfDiffuse')
    black.inputs["Color"].default_value = (0, 0, 0, 1)
    if pattern == 'FLAG':
        nt.links.new(black.outputs[0], out.inputs["Surface"])
        return mat
    clear = nt.nodes.new('ShaderNodeBsdfTransparent')
    mix = nt.nodes.new('ShaderNodeMixShader')
    coord = nt.nodes.new('ShaderNodeTexCoord')
    ramp = nt.nodes.new('ShaderNodeMath')
    ramp.operation = 'GREATER_THAN'
    if pattern == 'BLINDS':
        tex = nt.nodes.new('ShaderNodeTexWave')
        tex.inputs["Scale"].default_value = 4.0
        nt.links.new(coord.outputs["Generated"], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Fac"], ramp.inputs[0])
        ramp.inputs[1].default_value = 0.5
    elif pattern == 'WINDOW':
        tex = nt.nodes.new('ShaderNodeTexBrick')
        tex.inputs["Scale"].default_value = 2.0
        tex.inputs["Mortar Size"].default_value = 0.06
        tex.offset = 0.0
        tex.squash = 1.0
        nt.links.new(coord.outputs["Generated"], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Fac"], ramp.inputs[0])     # mortar = 1: the frame
        ramp.operation = 'LESS_THAN'
        ramp.inputs[1].default_value = 0.5
    else:
        tex = nt.nodes.new('ShaderNodeTexNoise')
        tex.inputs["Scale"].default_value = 6.0
        tex.inputs["Detail"].default_value = 4.0
        nt.links.new(coord.outputs["Generated"], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Fac"], ramp.inputs[0])
        ramp.inputs[1].default_value = 0.52
    nt.links.new(ramp.outputs[0], mix.inputs["Fac"])        # 1 = transparent (light passes)
    nt.links.new(black.outputs[0], mix.inputs[1])
    nt.links.new(clear.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    return mat


def flag_of(light):
    return next((c for c in light.children if c.get(STUDIO_KEY) == "flag"), None)


def add_flag(scene, light, pattern='FLAG'):
    """A card between the light and the product: blocks (flag) or shapes (gobo) the light. Seen only by
    the light (no camera, no reflections), moves with the light."""
    old = flag_of(light)
    if old is not None:
        remove_flag(light)
    target = lights.light_target(light, scene.render_studio)
    center = target.matrix_world.translation if target is not None and target != light else scene.cursor.location
    distance = max((center - light.matrix_world.translation).length, 0.2)
    size = light.data.size * 1.5 if light.data.type == 'AREA' else distance * 0.4
    mesh = bpy.data.meshes.new("Studio | Flag")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=size / 2)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Studio | %s | %s" % (pattern.title(), light.name), mesh)
    obj[STUDIO_KEY] = "flag"
    obj["as_pattern"] = pattern
    studio_collection(scene).objects.link(obj)
    obj.parent = light
    obj.matrix_parent_inverse = Matrix.Identity(4)
    # in front of the light along its -Z, a third of the way to the product, offset half a card when it is a flag
    obj.location = (size * 0.45 if pattern == 'FLAG' else 0.0, 0.0, -distance * 0.33)
    mesh.materials.append(_flag_material(pattern))
    for attr in ("visible_camera", "visible_diffuse", "visible_glossy", "visible_transmission",
                 "visible_volume_scatter"):
        if hasattr(obj, attr):
            setattr(obj, attr, False)
    obj.visible_shadow = True
    obj.hide_select = False
    return obj


def remove_flag(light):
    obj = flag_of(light)
    if obj is None:
        return
    mesh = obj.data
    bpy.data.objects.remove(obj)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


# ------------------------------------------------------------------ solo / light linking

def solo_light(scene):
    data = scene.get(SOLO_KEY)
    return data.get("light", "") if data else ""


def toggle_solo(scene, light):
    """Only this light renders; again (or solo on another) brings the others back as they were."""
    data = scene.get(SOLO_KEY)
    if data:
        hidden = dict(data.get("hidden", {}))
        for name, value in hidden.items():
            obj = scene.objects.get(name)
            if obj is not None and obj.type == 'LIGHT':
                obj.hide_render = bool(value)
                obj.hide_viewport = False
        was = data.get("light", "")
        del scene[SOLO_KEY]
        if was == light.name:
            return False
    scene[SOLO_KEY] = {"light": light.name,
                       "hidden": {o.name: bool(o.hide_render) for o in lights.scene_lights(scene)}}
    for obj in lights.scene_lights(scene):
        obj.hide_render = obj != light
    return True


def linked_to_product(light):
    coll = light.light_linking.receiver_collection
    return coll is not None and coll.name.startswith(LINK_PREFIX)


def toggle_product_link(scene, light):
    """The light lights only the product (not floor, room or background objects) and back."""
    if linked_to_product(light):
        coll = light.light_linking.receiver_collection
        light.light_linking.receiver_collection = None
        if coll.users == 0:
            bpy.data.collections.remove(coll)
        return False
    name = LINK_PREFIX + light.name
    coll = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    for obj in list(coll.objects):
        coll.objects.unlink(obj)
    for obj in product_objects(scene):     # flat: nested collections are not followed
        coll.objects.link(obj)
    light.light_linking.receiver_collection = coll
    return True


# ------------------------------------------------------------------ scene presets

BUILTIN_SCENES = {
    "Studio White": {
        "lights": "High Key", "floor": {"mode": "COVE", "color": [0.92, 0.92, 0.92], "roughness": 0.6,
                                         "shadow_catcher": False},
        "background": {"mode": "COLOR", "color": [0.96, 0.96, 0.96], "gradient": False},
        "world": {"generate": {"name": "Soft Studio", "resolution": 1024, "sky": {"top": [1, 1, 1], "horizon": [0.95, 0.95, 0.95],
                                                              "ground": [0.6, 0.6, 0.6]}, "strength": 0.6}},
        "haze": 0.0, "look": {"glow": 0.0, "contrast": 0.1, "vignette": 0.0}},
    "Dark Moody": {
        "lights": "3-Point", "floor": {"mode": "COVE", "color": [0.05, 0.05, 0.055], "roughness": 0.3,
                                        "shadow_catcher": False},
        "background": {"mode": "COLOR", "color": [0.02, 0.02, 0.022], "gradient": False},
        "world": {"generate": {"name": "Dark Studio", "resolution": 1024, "sky": {"top": [0.05, 0.05, 0.06], "horizon": [0.02, 0.02, 0.02],
                                                              "ground": [0.0, 0.0, 0.0]}, "strength": 0.3}},
        "haze": 0.0, "look": {"glow": 0.15, "contrast": 0.35, "vignette": 0.3}},
    "Outdoor": {
        "lights": None, "floor": {"mode": "FLOOR", "color": [0.5, 0.48, 0.45], "roughness": 0.8,
                                  "shadow_catcher": True},
        "background": {"mode": "NORMAL"},
        "world": {"generate": {"name": "Sunny Sky", "resolution": 1024, "sky": {"top": [0.35, 0.55, 1.0], "horizon": [0.85, 0.9, 1.0],
                                                            "ground": [0.35, 0.33, 0.3]}, "strength": 1.0,
                               "sun": {"horizontal": -30, "vertical": 40, "size": 1.0, "strength": 60,
                                       "kelvin": 5600}},
                  "atmosphere": True},
        "haze": 0.0, "look": {"glow": 0.05, "contrast": 0.15, "vignette": 0.1}},
}


def _scenes_file():
    from .paths import data_dir
    return os.path.join(data_dir(), "scene_presets.json")


def saved_scenes():
    try:
        with open(_scenes_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def scene_preset(name):
    return saved_scenes().get(name) or BUILTIN_SCENES.get(name)


def capture(scene, name):
    """The whole environment as a preset: lights, World, background, floor, haze, look."""
    rs = scene.render_studio
    preset = {"name": name}
    try:
        preset["lights"] = lights.setup_from_scene(scene, rs.light_orbit_target, name)
    except ValueError:
        preset["lights"] = None
    path = hdri.current_path(scene)
    preset["world"] = ({"hdri": path, "strength": rs.world_strength, "rotation": rs.world_rotation} if path
                       else {"world": scene.world.name if scene.world else ""})
    from . import addons
    if addons.has("psa"):
        preset["world"]["atmosphere"] = addons.atmosphere_on(scene)
    preset["background"] = {"mode": rs.background_mode, "color": list(rs.background_color),
                            "gradient": rs.use_gradient, "top": list(rs.background_color_top)}
    preset["floor"] = {"mode": rs.floor_mode, "color": list(rs.floor_color), "roughness": rs.floor_roughness,
                       "shadow_catcher": rs.floor_shadow_catcher, "size": rs.floor_size, "curve": rs.floor_curve}
    preset["haze"] = rs.haze_density
    preset["look"] = {"glow": rs.look_glow, "contrast": rs.look_contrast, "vignette": rs.look_vignette}
    return preset


def save_scene(scene, name):
    presets = saved_scenes()
    presets[name] = capture(scene, name)
    with open(_scenes_file(), "w", encoding="utf-8") as f:
        json.dump(presets, f, indent=1)
    scene.render_studio.scene_preset = name
    return name


def delete_scene(name):
    presets = saved_scenes()
    if presets.pop(name, None) is not None:
        with open(_scenes_file(), "w", encoding="utf-8") as f:
            json.dump(presets, f, indent=1)


def apply_scene(scene, name):
    """Applies a scene preset; what it does not mention stays as it is. Returns notes for the user."""
    preset = scene_preset(name)
    if preset is None:
        raise ValueError("No scene preset %r" % name)
    rs = scene.render_studio
    notes = []
    spec = preset.get("lights")
    if isinstance(spec, str):
        spec = lights.preset(spec)
    if spec:
        lights.apply_setup(scene, rs.light_orbit_target, lights.parse_setup(spec))
    world = preset.get("world") or {}
    if world.get("hdri"):
        if os.path.isfile(world["hdri"]):
            hdri.apply(scene, world["hdri"], rs)
            rs.world_strength = world.get("strength", rs.world_strength)
            rs.world_rotation = world.get("rotation", rs.world_rotation)
        else:
            notes.append("HDRI not found: %s" % os.path.basename(world["hdri"]))
    elif world.get("generate"):
        from . import api
        api.hdri_setup(world["generate"], scene)
    elif world.get("world") and bpy.data.worlds.get(world["world"]):
        scene.world = bpy.data.worlds[world["world"]]
    from . import addons
    if "atmosphere" in world and addons.has("psa") and world["atmosphere"] != addons.atmosphere_on(scene):
        try:
            if world["atmosphere"]:
                bpy.ops.psa.add_atmosphere()
            else:
                bpy.ops.psa.remove_atmosphere()
        except (RuntimeError, AttributeError) as exc:
            notes.append("Atmosphere: %s" % exc)
    bg = preset.get("background") or {}
    if bg.get("mode"):
        if "color" in bg:
            rs.background_color = bg["color"][:3]
        if "top" in bg:
            rs.background_color_top = bg["top"][:3]
        rs.use_gradient = bool(bg.get("gradient", False))
        rs.background_mode = bg["mode"]
    floor = preset.get("floor") or {}
    if floor:
        rs.floor_color = floor.get("color", list(rs.floor_color))[:3]
        rs.floor_roughness = floor.get("roughness", rs.floor_roughness)
        rs.floor_shadow_catcher = floor.get("shadow_catcher", rs.floor_shadow_catcher)
        rs.floor_size = floor.get("size", rs.floor_size)
        rs.floor_curve = floor.get("curve", rs.floor_curve)
        rs.floor_mode = floor.get("mode", rs.floor_mode)
        build_floor(scene)
    if "haze" in preset:
        rs.haze_density = float(preset["haze"])
    lk = preset.get("look") or {}
    if lk:
        rs.look_glow, rs.look_contrast, rs.look_vignette = (lk.get("glow", 0), lk.get("contrast", 0),
                                                             lk.get("vignette", 0))
        look.apply(scene, rs.look_glow, rs.look_contrast, rs.look_vignette)
    background.sync(scene, force=True)
    rs.scene_preset = name
    return notes

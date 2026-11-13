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
    ('DAPPLE', "Leaves", "Light through leaves that move in the wind"),
    ('BRANCHES', "Tree", "Leaves and branches of a tree, moving in the wind"),
    ('WATER', "Water", "Moving ripples of light, like light reflected off water"),
    ('CLOUDS', "Clouds", "Soft shadows that drift slowly"),
)
MOVING = {'DAPPLE', 'BRANCHES', 'WATER', 'CLOUDS'}
GOBO_PREFIX = "Studio | Gobo "
NODE_TIME, NODE_SPEED, NODE_SCALE = "Time", "Speed", "Scale"


def _value(nt, name, value):
    node = nt.nodes.new('ShaderNodeValue')
    node.name = node.label = name
    node.outputs[0].default_value = value
    return node


def _math(nt, operation, a, b=None, c=None):
    """A Math node; a, b and c are sockets or numbers."""
    node = nt.nodes.new('ShaderNodeMath')
    node.operation = operation
    for index, value in enumerate((a, b, c)):
        if value is None:
            continue
        if isinstance(value, (int, float)):
            node.inputs[index].default_value = value
        else:
            nt.links.new(value, node.inputs[index])
    return node.outputs[0]


def _smooth(nt, value, low, high):
    """0 below `low`, 1 above `high`, soft in between."""
    node = nt.nodes.new('ShaderNodeMapRange')
    node.interpolation_type = 'SMOOTHSTEP'
    nt.links.new(value, node.inputs["Value"])
    node.inputs["From Min"].default_value, node.inputs["From Max"].default_value = low, high
    return node.outputs["Result"]


def _noise(nt, vector, scale, detail, w=None, roughness=0.6):
    node = nt.nodes.new('ShaderNodeTexNoise')
    node.noise_dimensions = '4D' if w is not None else '3D'
    node.inputs["Scale"].default_value, node.inputs["Detail"].default_value = scale, detail
    node.inputs["Roughness"].default_value = roughness
    nt.links.new(vector, node.inputs["Vector"])
    if w is not None:
        nt.links.new(w, node.inputs["W"])
    return node.outputs["Fac"]


def _moving_pattern(nt, pattern, generated):
    """The part of a moving gobo that lets light through (socket, 0..1). The pattern lives in the card's
    0..1 square; Scale makes it finer, Speed moves it (0 = still)."""
    time = _value(nt, NODE_TIME, 0.0)
    speed = _value(nt, NODE_SPEED, 1.0)
    scale = _value(nt, NODE_SCALE, 1.0)
    try:        # seconds since frame 1; a still image is not affected
        curve = nt.driver_add('nodes["%s"].outputs[0].default_value' % NODE_TIME)
        curve.driver.type = 'SCRIPTED'
        curve.driver.expression = "frame / 24"
    except (TypeError, RuntimeError, AttributeError) as exc:
        print("ID Toolkit: gobo:", exc)
    t = _math(nt, 'MULTIPLY', time.outputs[0], speed.outputs[0])
    mapping = nt.nodes.new('ShaderNodeMapping')
    offset = nt.nodes.new('ShaderNodeCombineXYZ')
    nt.links.new(generated, mapping.inputs["Vector"])
    nt.links.new(scale.outputs[0], mapping.inputs["Scale"])
    nt.links.new(offset.outputs[0], mapping.inputs["Location"])
    vector = mapping.outputs["Vector"]
    if pattern == 'CLOUDS':
        nt.links.new(_math(nt, 'MULTIPLY', t, 0.04), offset.inputs["X"])
        clouds = _noise(nt, vector, 1.6, 3.0, w=_math(nt, 'MULTIPLY', t, 0.03), roughness=0.5)
        return _math(nt, 'MAXIMUM', _smooth(nt, clouds, 0.4, 0.62), 0.12)
    if pattern == 'WATER':
        wobble = nt.nodes.new('ShaderNodeTexNoise')
        wobble.noise_dimensions = '4D'
        wobble.inputs["Scale"].default_value = 2.5
        nt.links.new(vector, wobble.inputs["Vector"])
        nt.links.new(_math(nt, 'MULTIPLY', t, 0.25), wobble.inputs["W"])
        push = nt.nodes.new('ShaderNodeVectorMath')        # the noise bends the cells: no straight lines
        push.operation = 'SCALE'
        nt.links.new(wobble.outputs["Color"], push.inputs[0])
        push.inputs[3].default_value = 0.18
        bent = nt.nodes.new('ShaderNodeVectorMath')
        bent.operation = 'ADD'
        nt.links.new(vector, bent.inputs[0])
        nt.links.new(push.outputs[0], bent.inputs[1])
        cells = nt.nodes.new('ShaderNodeTexVoronoi')
        cells.voronoi_dimensions = '4D'
        cells.feature = 'DISTANCE_TO_EDGE'
        cells.inputs["Scale"].default_value = 5.0
        nt.links.new(bent.outputs[0], cells.inputs["Vector"])
        nt.links.new(_math(nt, 'MULTIPLY', t, 0.35), cells.inputs["W"])
        lines = _math(nt, 'SUBTRACT', 1.0, _smooth(nt, cells.outputs["Distance"], 0.0, 0.14))
        return _math(nt, 'MAXIMUM', lines, 0.2)
    # leaves: they sway (the whole pattern moves a little back and forth) and flutter (the pattern changes)
    nt.links.new(_math(nt, 'MULTIPLY', _math(nt, 'SINE', _math(nt, 'MULTIPLY', t, 1.3)), 0.035), offset.inputs["X"])
    nt.links.new(_math(nt, 'MULTIPLY', _math(nt, 'SINE', _math(nt, 'MULTIPLY_ADD', t, 0.9, 1.0)), 0.02),
                 offset.inputs["Y"])
    flutter = _math(nt, 'MULTIPLY', t, 0.12)
    fine = _noise(nt, vector, 6.0, 6.0, w=flutter, roughness=0.65)
    coarse = _noise(nt, vector, 1.7, 2.0, w=_math(nt, 'MULTIPLY', t, 0.04))
    density = _math(nt, 'ADD', _math(nt, 'MULTIPLY', fine, 0.6), _math(nt, 'MULTIPLY', coarse, 0.4))
    light = _smooth(nt, density, 0.49, 0.55)
    if pattern == 'BRANCHES':
        wave = nt.nodes.new('ShaderNodeTexWave')
        wave.wave_type = 'BANDS'
        wave.bands_direction = 'DIAGONAL'
        wave.inputs["Scale"].default_value = 1.1
        wave.inputs["Distortion"].default_value = 7.0
        wave.inputs["Detail"].default_value = 2.0
        wave.inputs["Detail Scale"].default_value = 0.8
        nt.links.new(vector, wave.inputs["Vector"])
        branch = _smooth(nt, wave.outputs["Fac"], 0.9, 0.96)
        light = _math(nt, 'MULTIPLY', light, _math(nt, 'SUBTRACT', 1.0, branch))
    return light


def _flag_material(pattern, owner=""):
    """A flag is one shared black material; every gobo gets its own (its scale and speed are per light)."""
    name = GOBO_PREFIX + pattern.title() + (" | " + owner if owner and pattern != 'FLAG' else "")
    mat = bpy.data.materials.get(name)
    if mat is not None and (pattern == 'FLAG' or mat.users == 0):
        if pattern == 'FLAG':
            return mat
        bpy.data.materials.remove(mat)
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
    if pattern in MOVING:
        nt.links.new(_moving_pattern(nt, pattern, coord.outputs["Generated"]), mix.inputs["Fac"])
    else:
        ramp = nt.nodes.new('ShaderNodeMath')
        ramp.operation = 'GREATER_THAN'
        if pattern == 'BLINDS':
            tex = nt.nodes.new('ShaderNodeTexWave')
            tex.inputs["Scale"].default_value = 4.0
            ramp.inputs[1].default_value = 0.5
        else:
            tex = nt.nodes.new('ShaderNodeTexBrick')
            tex.inputs["Scale"].default_value = 2.0
            tex.inputs["Mortar Size"].default_value = 0.06
            tex.offset = 0.0
            tex.squash = 1.0
            ramp.operation = 'LESS_THAN'        # mortar = 1: the frame
            ramp.inputs[1].default_value = 0.5
        nt.links.new(coord.outputs["Generated"], tex.inputs["Vector"])
        nt.links.new(tex.outputs["Fac"], ramp.inputs[0])
        nt.links.new(ramp.outputs[0], mix.inputs["Fac"])    # 1 = transparent (light passes)
    nt.links.new(black.outputs[0], mix.inputs[1])
    nt.links.new(clear.outputs[0], mix.inputs[2])
    nt.links.new(mix.outputs[0], out.inputs["Surface"])
    return mat


def gobo_nodes(flag):
    """(speed socket, scale socket) of a moving gobo, None for the others."""
    mat = flag.data.materials[0] if flag is not None and flag.data and flag.data.materials else None
    if mat is None or mat.node_tree is None:
        return None, None
    speed, scale = mat.node_tree.nodes.get(NODE_SPEED), mat.node_tree.nodes.get(NODE_SCALE)
    return (speed.outputs[0] if speed else None), (scale.outputs[0] if scale else None)


def gobo_values(flag):
    speed, scale = gobo_nodes(flag)
    return {"speed": speed.default_value if speed else 0.0, "scale": scale.default_value if scale else 1.0}


def set_gobo_values(flag, values):
    speed, scale = gobo_nodes(flag)
    if speed is not None and "speed" in values:
        speed.default_value = max(0.0, float(values["speed"]))
    if scale is not None and "scale" in values:
        scale.default_value = max(0.05, float(values["scale"]))


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
    mesh.materials.append(_flag_material(pattern, light.name))
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
    mats = [m for m in mesh.materials if m is not None and m.name.startswith(GOBO_PREFIX)]
    bpy.data.objects.remove(obj)
    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)
    for mat in mats:
        if mat.users == 0 and mat.name != GOBO_PREFIX + "Flag":
            bpy.data.materials.remove(mat)


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

_STUDIO_PINS = [
    {"horizontal": -45, "vertical": 35, "width": 50, "height": 40, "strength": 10, "softness": 0.35},
    {"horizontal": 60, "vertical": 15, "width": 70, "height": 50, "strength": 2.5, "softness": 0.6},
    {"horizontal": 160, "vertical": 40, "width": 30, "height": 30, "strength": 8, "softness": 0.3},
]

BUILTIN_SCENES = {
    "Studio Softboxes": {
        "lights": None, "floor": {"mode": "COVE", "color": [0.85, 0.85, 0.85], "roughness": 0.5,
                                  "shadow_catcher": False},
        "background": {"mode": "COLOR", "color": [0.9, 0.9, 0.9], "gradient": False},
        "world": {"mode": "STUDIO", "pins": _STUDIO_PINS, "ambient": 0.25},
        "haze": 0.0, "look": {"glow": 0.0, "contrast": 0.1, "vignette": 0.0}},
    "Daylight": {
        "lights": None, "floor": {"mode": "FLOOR", "color": [0.5, 0.48, 0.45], "roughness": 0.8,
                                  "shadow_catcher": True},
        "background": {"mode": "NORMAL"}, "world": {"mode": "SKY", "time": 14.0},
        "haze": 0.0, "look": {"glow": 0.0, "contrast": 0.1, "vignette": 0.0}},
    "Golden Hour": {
        "lights": None, "floor": {"mode": "FLOOR", "color": [0.5, 0.48, 0.45], "roughness": 0.8,
                                  "shadow_catcher": True},
        "background": {"mode": "NORMAL"}, "world": {"mode": "SKY", "time": 17.2},
        "haze": 0.0, "look": {"glow": 0.1, "contrast": 0.15, "vignette": 0.15}},
    "Night": {
        "lights": "3-Point", "floor": {"mode": "FLOOR", "color": [0.1, 0.1, 0.11], "roughness": 0.4,
                                       "shadow_catcher": False},
        "background": {"mode": "NORMAL"}, "world": {"mode": "SKY", "time": 23.0},
        "haze": 0.0, "look": {"glow": 0.2, "contrast": 0.2, "vignette": 0.3}},
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
    preset["world"]["mode"] = rs.world_mode
    if rs.world_mode == 'STUDIO':
        from . import world_modes
        spec = world_modes.studio_spec(scene)
        preset["world"].update(pins=spec["softboxes"], ambient=rs.studio_ambient, ambient_color=list(rs.studio_color),
                               strength=rs.world_strength)
    elif rs.world_mode == 'SKY':
        preset["world"].update(time=rs.sky_time, north=rs.sky_north)
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
    notes = apply_preset(scene, preset)
    scene.render_studio.scene_preset = name
    return notes


def apply_preset(scene, preset):
    """Applies preset data (a scene preset or a look, looks.py). Returns notes for the user."""
    rs = scene.render_studio
    notes = []
    spec = preset.get("lights")
    if isinstance(spec, str):
        spec = lights.preset(spec)
    if spec:
        lights.apply_setup(scene, rs.light_orbit_target, lights.parse_setup(spec))
    world = preset.get("world") or {}
    from . import world_modes
    if world.get("mode") == 'STUDIO':
        if "pins" in world:
            rs.hdri_pins.clear()
            for i, item in enumerate(world["pins"]):
                pin = rs.hdri_pins.add()
                pin["name"] = "Light %d" % (i + 1)
                pin["horizontal"], pin["vertical"] = math.radians(item["horizontal"]), math.radians(item["vertical"])
                pin["width"], pin["height"] = math.radians(item["width"]), math.radians(item["height"])
                pin["strength"], pin["softness"] = item["strength"], item.get("softness", 0.35)
                pin["shape"] = 1 if item.get("shape") == 'ROUND' else 0
                pin["color"] = item.get("color", [1, 1, 1])[:3]
        rs["studio_ambient"] = world.get("ambient", rs.studio_ambient)
        rs["studio_color"] = world.get("ambient_color", list(rs.studio_color))[:3]
        rs.world_mode = 'STUDIO'      # renders the studio and faces the camera
        if "strength" in world:
            rs.world_strength = world["strength"]
    elif world.get("mode") == 'SKY':
        rs["sky_time"] = world.get("time", rs.sky_time)
        rs["sky_north"] = world.get("north", rs.sky_north)
        rs.world_mode = 'SKY'
    elif world.get("hdri") or world.get("generate") or world.get("world"):
        if rs.world_mode != 'IMAGE':
            rs.world_mode = 'IMAGE'   # leaves the sky / studio
    if world.get("mode") in ('STUDIO', 'SKY'):
        pass
    elif world.get("hdri"):
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
    return notes

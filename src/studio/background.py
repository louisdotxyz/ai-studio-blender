# SPDX-License-Identifier: GPL-3.0-or-later
"""The image background: World (internally NORMAL), Transparent or Color.

The setting changes the scene right away:

- Normal: the World (Film > Transparent off). The World strength is shown in
  the panel, a strength of 0 hides an HDRI completely.
- Transparent: Film > Transparent on; renders are saved as PNG with alpha.
- Color: Film > Transparent on plus an Alpha Over in the compositor with the
  color as background. An Alpha Over that already feeds the compositor output
  (e.g. one you built yourself) is used and overwritten, otherwise one named
  "RS Background" is inserted in front of the output. The viewport compositor
  is switched on ("Camera"), so Material Preview and Rendered show the color
  when looking through the camera.

The view transform (AgX, Filmic …) changes colors on screen. The node color is
therefore the value that the view transform turns into the chosen color,
found by running the scene's color management on a few test pixels. Colors a
view transform cannot produce (very saturated ones with AgX) come out as
close as possible. Queue renders are exact: they render transparent and put
the color behind the finished PNG (core.composite_background).
"""

import math
import os
import tempfile

import bpy

PREFIX = "RS Background"
N_ALPHA = PREFIX
TREE_NAME = "Render Studio Compositor"

_cache = {}        # scene name -> state the node color was computed for
_busy = [False]    # no re-entry while computing (save_render causes depsgraph updates)


# ------------------------------------------------------------ Color matching

def _srgb_to_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _is_plain_srgb(scene):
    view, display = scene.view_settings, scene.display_settings
    return (view.view_transform == 'Standard' and view.look in {'None', ''} and view.exposure == 0.0
            and view.gamma == 1.0 and not view.use_curve_mapping and display.display_device == 'sRGB'
            and not getattr(view, "use_white_balance", False))


class _ViewTransform:
    """Applies the scene's color management to linear colors (via a tiny image)."""

    def __init__(self, scene):
        self.scene = scene
        self.path = os.path.join(tempfile.gettempdir(), "render_studio_view_probe.png")
        self.image = bpy.data.images.new("_render_studio_probe", 4, 1, float_buffer=True, alpha=False)

    def close(self):
        bpy.data.images.remove(self.image)
        try:
            os.remove(self.path)
        except OSError:
            pass

    def __call__(self, rows):
        import numpy as np
        px = np.ones((4, 4), np.float32)
        px[:len(rows), :3] = rows
        self.image.pixels.foreach_set(px.ravel())
        settings = self.scene.render.image_settings
        old = (settings.media_type, settings.file_format, settings.color_mode, settings.color_depth)
        try:
            settings.media_type, settings.file_format = 'IMAGE', 'PNG'
            settings.color_mode, settings.color_depth = 'RGB', '16'
            self.image.save_render(self.path, scene=self.scene)
        finally:
            for attr, value in zip(("media_type", "file_format", "color_mode", "color_depth"), old):
                if getattr(settings, attr) != value:
                    setattr(settings, attr, value)
        out = bpy.data.images.load(self.path, check_existing=False)
        try:
            out.colorspace_settings.name = 'Non-Color'   # the stored values
            values = np.empty(16, np.float32)
            out.pixels.foreach_get(values)
        finally:
            bpy.data.images.remove(out)
        return values.reshape(4, 4)[:len(rows), :3]


def linear_for_display(scene, color):
    """Linear color that the scene's view transform shows as `color` (sRGB 0..1)."""
    linear = [_srgb_to_linear(c) for c in color]
    if _is_plain_srgb(scene):
        return tuple(linear)
    import numpy as np
    target = np.asarray(color, np.float64)
    x = np.asarray(linear, np.float64)
    best, best_err = x.copy(), None
    forward = _ViewTransform(scene)

    def error(values):
        return np.abs(target - forward(values[None, :]).astype(np.float64)[0]).max()

    try:
        best_err = error(x)
        for _step in range(12):   # damped Newton steps with a numeric Jacobian
            if best_err < 0.4 / 255:
                break
            h = 1e-3 + 0.02 * np.abs(x)
            rows = np.stack([x] + [x + np.eye(3)[i] * h[i] for i in range(3)])
            f = forward(rows).astype(np.float64)
            jac = np.stack([(f[i + 1] - f[0]) / h[i] for i in range(3)], axis=1)
            try:
                delta = np.linalg.lstsq(jac, target - f[0], rcond=None)[0]
            except np.linalg.LinAlgError:
                break
            improved = False
            for scale in (1.0, 0.5, 0.25, 0.125):
                candidate = np.clip(x + delta * scale, -1.0, 1e4)   # slightly negative helps with AgX
                err = error(candidate)
                if err < best_err:
                    x, best_err, improved = candidate, err, True
                    break
            if not improved:
                break
        best = x
    finally:
        forward.close()
    return tuple(float(v) for v in best)


# ------------------------------------------------------------- Compositor

def compositor_tree(scene):
    """The scene's compositor node tree. Blender 5.3 keeps it in a stack of compositor effects
    (scene.compositor_effects); 5.0-5.2 in scene.compositing_node_group."""
    effects = getattr(scene, "compositor_effects", None)
    if effects is not None:
        active = effects.active
        return active.node_group if active is not None else None
    return scene.compositing_node_group


def set_compositor_tree(scene, tree):
    effects = getattr(scene, "compositor_effects", None)
    if effects is None:
        scene.compositing_node_group = tree
    elif tree is None:
        if effects.active is not None:
            effects.remove(effects.active)
    elif effects.active is not None:
        effects.active.node_group = tree
    else:
        effect = effects.new(tree.name)
        effect.node_group = tree
        effects.active = effect


def _output_node(tree):
    outputs = [n for n in tree.nodes if n.bl_idname == 'NodeGroupOutput']
    return next((n for n in outputs if n.is_active_output), outputs[0] if outputs else None)


def _new_tree():
    tree = bpy.data.node_groups.new(TREE_NAME, 'CompositorNodeTree')
    tree.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
    layers = tree.nodes.new('CompositorNodeRLayers')
    layers.location = (-300, 0)
    out = tree.nodes.new('NodeGroupOutput')
    out.location = (300, 0)
    tree.links.new(layers.outputs["Image"], out.inputs[0])
    return tree


def _render_layers_image(tree):
    layers = next((n for n in tree.nodes if n.bl_idname == 'CompositorNodeRLayers'), None)
    if layers is None:
        layers = tree.nodes.new('CompositorNodeRLayers')
        layers.location = (-300, 0)
    return layers.outputs["Image"]


def alpha_node(scene):
    """The Alpha Over that puts the color behind the image: ours, or one that feeds the output."""
    tree = compositor_tree(scene)
    if tree is None:
        return None
    node = tree.nodes.get(N_ALPHA)
    if node is not None and node.bl_idname == 'CompositorNodeAlphaOver':
        return node
    out = _output_node(tree)
    if out is not None and out.inputs and out.inputs[0].is_linked:
        source = out.inputs[0].links[0].from_node
        if source.bl_idname == 'CompositorNodeAlphaOver':
            return source
    return None


def _ours(tree, name, idname, location):
    node = tree.nodes.get(name)
    if node is None or node.bl_idname != idname:
        if node is not None:
            tree.nodes.remove(node)
        node = tree.nodes.new(idname)
        node.name = name
        node.label = name[len(PREFIX) + 1:]
        node.location = location
    return node


GAMMA = 2.2


def _perceptual(linear):
    return tuple(math.copysign(abs(v) ** (1.0 / GAMMA), v) for v in linear)


def _set(socket, value):
    current = socket.default_value
    try:
        same = all(abs(a - b) < 1e-7 for a, b in zip(current, value))
    except TypeError:
        same = abs(current - value) < 1e-7
    if not same:
        socket.default_value = value


def ensure_color(scene, bottom_linear, top_linear=None, grain_size=2.0, grain_amount=0.0):
    """Builds or updates the Alpha Over with the background: a color or a vertical
    gradient (bottom → top) plus monochrome grain. Returns the Alpha Over."""
    tree = compositor_tree(scene)
    if tree is None:
        tree = _new_tree()
        set_compositor_tree(scene, tree)
    node = alpha_node(scene)
    if node is None:
        out = _output_node(tree)
        if out is None:
            if not any(i.item_type == 'SOCKET' and i.in_out == 'OUTPUT' for i in tree.interface.items_tree):
                tree.interface.new_socket("Image", in_out='OUTPUT', socket_type='NodeSocketColor')
            out = tree.nodes.new('NodeGroupOutput')
            out.location = (300, 0)
        into = out.inputs[0].links[0] if out.inputs[0].is_linked else None
        source = into.from_socket if into is not None else _render_layers_image(tree)
        node = tree.nodes.new('CompositorNodeAlphaOver')
        node.name = node.label = N_ALPHA
        node.location = (out.location.x - 220, out.location.y)
        tree.links.new(source, node.inputs["Foreground"])
        tree.links.new(node.outputs["Image"], out.inputs[0])

    x, y = node.location.x - 1400, node.location.y - 300
    image = node.inputs["Foreground"].links[0].from_socket if node.inputs["Foreground"].is_linked \
        else _render_layers_image(tree)
    coords = _ours(tree, PREFIX + " Coordinates", 'CompositorNodeImageCoordinates', (x, y))
    height = _ours(tree, PREFIX + " Height", 'ShaderNodeSeparateXYZ', (x + 220, y + 100))
    bottom = _ours(tree, PREFIX + " Bottom", 'CompositorNodeRGB', (x + 220, y - 60))
    top = _ours(tree, PREFIX + " Top", 'CompositorNodeRGB', (x + 220, y - 260))
    gradient = _ours(tree, PREFIX + " Gradient", 'ShaderNodeMix', (x + 440, y))
    scale = _ours(tree, PREFIX + " Grain Scale", 'ShaderNodeVectorMath', (x + 220, y - 460))
    snap = _ours(tree, PREFIX + " Grain Pixels", 'ShaderNodeVectorMath', (x + 440, y - 460))
    noise = _ours(tree, PREFIX + " Grain", 'ShaderNodeTexWhiteNoise', (x + 660, y - 460))
    amount = _ours(tree, PREFIX + " Grain Amount", 'ShaderNodeMapRange', (x + 880, y - 460))
    gray = _ours(tree, PREFIX + " Grain Gray", 'ShaderNodeCombineXYZ', (x + 1100, y - 460))
    # The gradient runs in a perceptual space (value^(1/2.2), sign kept), back to linear here:
    sign = _ours(tree, PREFIX + " Sign", 'ShaderNodeVectorMath', (x + 660, y + 150))
    absolute = _ours(tree, PREFIX + " Abs", 'ShaderNodeVectorMath', (x + 660, y))
    power = _ours(tree, PREFIX + " To Linear", 'ShaderNodeVectorMath', (x + 880, y))
    signed = _ours(tree, PREFIX + " Signed", 'ShaderNodeVectorMath', (x + 1100, y + 150))
    add = _ours(tree, PREFIX + " Add Grain", 'ShaderNodeVectorMath', (x + 1320, y))
    gradient.data_type = 'RGBA'
    scale.operation, snap.operation, add.operation = 'DIVIDE', 'FLOOR', 'ADD'
    sign.operation, absolute.operation, power.operation, signed.operation = 'SIGN', 'ABSOLUTE', 'POWER', 'MULTIPLY'
    _set(power.inputs[1], (GAMMA, GAMMA, GAMMA))
    noise.noise_dimensions = '3D'
    links = tree.links
    links.new(image, coords.inputs["Image"])
    links.new(coords.outputs["Normalized"], height.inputs[0])
    links.new(height.outputs["Y"], gradient.inputs[0])
    links.new(bottom.outputs[0], gradient.inputs[6])
    links.new(top.outputs[0], gradient.inputs[7])
    links.new(coords.outputs["Pixel"], scale.inputs[0])
    links.new(scale.outputs["Vector"], snap.inputs[0])
    links.new(snap.outputs["Vector"], noise.inputs["Vector"])
    links.new(noise.outputs["Value"], amount.inputs["Value"])
    for i in range(3):
        links.new(amount.outputs["Result"], gray.inputs[i])
    links.new(gradient.outputs[2], sign.inputs[0])
    links.new(gradient.outputs[2], absolute.inputs[0])
    links.new(absolute.outputs["Vector"], power.inputs[0])
    links.new(power.outputs["Vector"], signed.inputs[0])
    links.new(sign.outputs["Vector"], signed.inputs[1])
    links.new(signed.outputs["Vector"], add.inputs[0])
    links.new(gray.outputs[0], add.inputs[1])
    background = node.inputs["Background"]
    if not background.is_linked or background.links[0].from_node != add:
        links.new(add.outputs["Vector"], background)

    top_linear = top_linear or bottom_linear
    _set(bottom.outputs[0], (*_perceptual(bottom_linear), 1.0))
    _set(top.outputs[0], (*_perceptual(top_linear), 1.0))
    size = max(1.0, float(grain_size))
    _set(scale.inputs[1], (size, size, size))
    # Grain in linear values, scaled with the brightness so it looks even on dark and light colors.
    level = max(0.02, sum(bottom_linear + top_linear) / 6.0)
    spread = float(grain_amount) * level
    _set(amount.inputs["To Min"], -spread)
    _set(amount.inputs["To Max"], spread)
    if not scene.render.use_compositing:
        scene.render.use_compositing = True
    return node


def remove_color(scene):
    """Takes our Alpha Over out again (an Alpha Over of your own stays)."""
    tree = compositor_tree(scene)
    if tree is None:
        return
    node = tree.nodes.get(N_ALPHA)
    if node is not None:
        source = node.inputs["Foreground"].links[0].from_socket if node.inputs["Foreground"].is_linked else None
        targets = [link.to_socket for link in node.outputs["Image"].links]
        tree.nodes.remove(node)
        if source is not None:
            for socket in targets:
                tree.links.new(source, socket)
    # 0.5 left more nodes ("RS Background …")
    for extra in [n for n in tree.nodes if n.name.startswith(PREFIX)]:
        tree.nodes.remove(extra)
    if tree.name.startswith(TREE_NAME) and {n.bl_idname for n in tree.nodes} <= {
            'CompositorNodeRLayers', 'NodeGroupOutput'}:
        set_compositor_tree(scene, None)
        if tree.users == 0:
            bpy.data.node_groups.remove(tree)


def set_viewport_compositor(on):
    """Viewport compositor in all 3D views: 'CAMERA' for Color, off otherwise
    (only views that we switched or that show the camera setting)."""
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type != 'VIEW_3D':
                    continue
                shading = space.shading
                if on and shading.use_compositor == 'DISABLED':
                    shading.use_compositor = 'CAMERA'
                elif not on and shading.use_compositor == 'CAMERA':
                    shading.use_compositor = 'DISABLED'


def show_world_in_viewports():
    """Normal: the viewports show the scene's World (not the built-in studio HDRI)."""
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    shading = space.shading
                    if not shading.use_scene_world_render:
                        shading.use_scene_world_render = True
                    if not shading.use_scene_world:
                        shading.use_scene_world = True


def world_problems(scene, space=None):
    """Why the World is not visible as background, as short texts (empty = fine)."""
    problems = []
    world = scene.world
    if world is None:
        return ["The scene has no World"]
    if scene.render.film_transparent:
        problems.append("Film > Transparent is on")
    visibility = getattr(world, "cycles_visibility", None)
    if visibility is not None and not visibility.camera:
        problems.append("World is hidden from the camera (Ray Visibility)")
    node = world_background_node(scene)
    if node is not None and node.inputs["Strength"].default_value == 0.0 and not node.inputs["Strength"].is_linked:
        problems.append("World Strength is 0")
    shading = getattr(space, "shading", None) if space is not None else None
    if shading is not None:
        if shading.type == 'MATERIAL' and not shading.use_scene_world:
            problems.append("Material Preview shows the studio HDRI")
        elif shading.type == 'RENDERED' and not shading.use_scene_world_render:
            problems.append("Rendered view shows the studio HDRI")
        if shading.use_compositor != 'DISABLED' and compositor_tree(scene) is not None:
            problems.append("Viewport compositor is on")
    return problems


def fix_world(scene):
    """Makes the World visible as background again."""
    if scene.render.film_transparent:
        scene.render.film_transparent = False
    world = scene.world
    if world is not None:
        visibility = getattr(world, "cycles_visibility", None)
        if visibility is not None and not visibility.camera:
            visibility.camera = True
        node = world_background_node(scene)
        if node is not None and node.inputs["Strength"].default_value == 0.0:
            node.inputs["Strength"].default_value = 1.0
    for screen in bpy.data.screens:
        for area in screen.areas:
            for space in area.spaces:
                if space.type == 'VIEW_3D' and space.shading.use_compositor != 'DISABLED':
                    space.shading.use_compositor = 'DISABLED'
    show_world_in_viewports()


# ---------------------------------------------------------- World strength

def world_background_node(scene):
    """The Background node that feeds the World output (the one with the HDRI)."""
    world = scene.world
    if world is None or world.node_tree is None:
        return None
    outputs = [n for n in world.node_tree.nodes if n.bl_idname == 'ShaderNodeOutputWorld']
    out = next((n for n in outputs if n.is_active_output), outputs[0] if outputs else None)
    if out is None or not out.inputs["Surface"].is_linked:
        return None
    node = out.inputs["Surface"].links[0].from_node
    return node if node.bl_idname == 'ShaderNodeBackground' else None


def get_world_strength(settings):
    node = world_background_node(settings.id_data)
    return node.inputs["Strength"].default_value if node else 1.0


def set_world_strength(settings, value):
    node = world_background_node(settings.id_data)
    if node is not None:
        node.inputs["Strength"].default_value = value


# -------------------------------------------------------------------- Sync

def _state(scene):
    rs = scene.render_studio
    view, display = scene.view_settings, scene.display_settings
    node = alpha_node(scene)
    return (rs.background_mode, tuple(round(c, 5) for c in rs.background_color),
            rs.use_gradient, tuple(round(c, 5) for c in rs.background_color_top),
            round(rs.noise_size, 3), round(rs.noise_intensity, 4),
            compositor_tree(scene).name if compositor_tree(scene) else None,
            node.name if node else None,
            view.view_transform, view.look, round(view.exposure, 4), round(view.gamma, 4),
            view.use_curve_mapping, display.display_device)


def sync(scene, force=False, viewport=False):
    """Applies the background setting to the scene. Cheap when nothing changed."""
    if _busy[0]:
        return
    state = _state(scene)
    if not force and _cache.get(scene.name) == state:
        return
    _busy[0] = True
    try:
        _cache[scene.name] = state   # first, so updates during the work do not start it again
        mode = scene.render_studio.background_mode
        transparent = mode != 'NORMAL'
        if scene.render.film_transparent != transparent:
            scene.render.film_transparent = transparent
        if mode == 'COLOR':
            rs = scene.render_studio
            bottom = linear_for_display(scene, tuple(rs.background_color))
            top = linear_for_display(scene, tuple(rs.background_color_top)) if rs.use_gradient else bottom
            ensure_color(scene, bottom, top, rs.noise_size, rs.noise_intensity)
        else:
            remove_color(scene)
        remove_world_nodes()
        if viewport:
            set_viewport_compositor(mode == 'COLOR')
            if mode == 'NORMAL':
                show_world_in_viewports()
        _cache[scene.name] = _state(scene)
    finally:
        _busy[0] = False


# ------------------------------------------------- Clean-up of version 0.6

def remove_world_nodes():
    """0.6 put the color into the World ("RS Background Mix"). Take it out again."""
    for world in bpy.data.worlds:
        tree = world.node_tree
        mix = tree.nodes.get(PREFIX + " Mix") if tree else None
        if mix is None:
            continue
        source = mix.inputs[1].links[0].from_socket if mix.inputs[1].is_linked else None
        targets = [link.to_socket for link in mix.outputs[0].links]
        for node in [n for n in tree.nodes if n.name.startswith(PREFIX)]:
            tree.nodes.remove(node)
        if source is not None:
            for socket in targets:
                tree.links.new(source, socket)


# Property updates (RS_Settings) -----------------------------------------------

def on_mode(settings, _context):
    sync(settings.id_data, force=True, viewport=True)


def on_color(settings, _context):
    sync(settings.id_data, force=True)


# ------------------------------------------------------------------ Register

@bpy.app.handlers.persistent
def _on_load_post(*_args):
    _cache.clear()
    for scene in bpy.data.scenes:
        try:
            sync(scene, force=True)
        except Exception as ex:
            print("Render Studio: background:", ex)
    # The viewport compositor only where a scene uses Color.
    try:
        set_viewport_compositor(any(s.render_studio.background_mode == 'COLOR' for s in bpy.data.scenes))
    except Exception as ex:
        print("Render Studio: viewport compositor:", ex)


def _later():
    _on_load_post()
    return None


def register():
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)
    bpy.app.timers.register(_later, first_interval=0.5)


def unregister():
    if bpy.app.timers.is_registered(_later):
        bpy.app.timers.unregister(_later)
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)

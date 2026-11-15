# SPDX-License-Identifier: GPL-3.0-or-later
"""Look: the finish of the image (Camera tab).

Exposure and white balance are Blender's own (Color Management of the scene). Saturation, glow, contrast
and vignette are nodes of our own ("Studio Look …") right in front of the compositor output, after everything
else (also the background color). A neutral value takes an effect out again; with all of them neutral
nothing of ours is left in the tree. (Render Raw does not run in the Blender 5 compositor.)
"""

import bpy
from bpy.props import StringProperty

from . import background

PREFIX = "Studio Look"
GLOW, CONTRAST, VIGNETTE = PREFIX + " Glow", PREFIX + " Contrast", PREFIX + " Vignette"
SATURATION = PREFIX + " Saturation"

PRESETS = {
    "Clean": (0.0, 0.0, 0.0),
    "Soft Glow": (0.35, 0.05, 0.15),
    "Punchy": (0.15, 0.3, 0.25),
    "Dramatic": (0.45, 0.45, 0.5),
}


OLD_PREFIXES = ("AS Look",)        # before 3.0


def _is_ours(node):
    return node.name.startswith((PREFIX,) + OLD_PREFIXES)


def _ours(tree):
    return [n for n in tree.nodes if _is_ours(n)]


def _remove(tree):
    """Take our nodes out and connect what fed them straight to where they went (also old or doubled
    ones, "Studio Look Glow.001"); the output is never left without an image."""
    ours = set(_ours(tree))
    if ours:
        source = None
        for link in tree.links:      # the image that comes into our chain (not the vignette's side input)
            if link.to_node in ours and link.from_node not in ours and link.to_socket.type == 'RGBA':
                if link.to_socket.name in ("Image", "A") or source is None:
                    source = link.from_socket
        targets = [l.to_socket for l in tree.links if l.from_node in ours and l.to_node not in ours]
        for node in ours:
            tree.nodes.remove(node)
        if source is not None:
            for socket in targets:
                tree.links.new(source, socket)
    background.ensure_output(tree)


def _node(tree, name, idname, x, y):
    node = tree.nodes.new(idname)
    node.name = name
    node.label = name[len(PREFIX) + 1:]
    node.location = (x, y)
    return node


def apply(scene, glow, contrast, vignette, saturation=1.0):
    """Build the nodes for these strengths (0-1 each; saturation 1 = as rendered)."""
    saturated = abs(saturation - 1.0) > 0.005
    tree = background.compositor_tree(scene)
    if tree is None:
        if not (glow or contrast or vignette or saturated):
            return
        tree = background._new_tree()
        background.set_compositor_tree(scene, tree)
    _remove(tree)
    if not (glow or contrast or vignette or saturated):
        return
    out = background._output_node(tree)
    if out is None:
        return
    into = out.inputs[0].links[0].from_socket if out.inputs[0].is_linked else background._render_layers_image(tree)
    x, y = out.location.x, out.location.y
    out.location.x += 900
    image = into
    if saturated:
        node = _node(tree, SATURATION, 'CompositorNodeHueSat', x, y)
        if "Saturation" in node.inputs:
            node.inputs["Saturation"].default_value = saturation
        else:       # before the node's values became sockets
            node.color_saturation = saturation
        tree.links.new(image, node.inputs["Image"])
        image = node.outputs["Image"]
        x += 220
    if glow:
        node = _node(tree, GLOW, 'CompositorNodeGlare', x, y)
        node.inputs["Type"].default_value = 'Fog Glow'
        node.inputs["Threshold"].default_value = 0.8
        node.inputs["Strength"].default_value = 0.6 * glow
        node.inputs["Size"].default_value = 0.6
        tree.links.new(image, node.inputs["Image"])
        image = node.outputs["Image"]
        x += 220
    if contrast:
        node = _node(tree, CONTRAST, 'CompositorNodeBrightContrast', x, y)
        node.inputs["Contrast"].default_value = 12.0 * contrast
        tree.links.new(image, node.inputs["Image"])
        image = node.outputs["Image"]
        x += 220
    if vignette:
        # Distance from the image center (aspect-correct) → darkening towards the corners.
        coords = _node(tree, VIGNETTE + " Coordinates", 'CompositorNodeImageCoordinates', x - 200, y - 300)
        tree.links.new(into, coords.inputs["Image"])
        length = _node(tree, VIGNETTE + " Distance", 'ShaderNodeVectorMath', x, y - 300)
        length.operation = 'LENGTH'
        tree.links.new(coords.outputs["Uniform"], length.inputs[0])
        mask = _node(tree, VIGNETTE + " Falloff", 'ShaderNodeMapRange', x + 200, y - 300)
        mask.interpolation_type = 'SMOOTHERSTEP'
        mask.inputs["From Min"].default_value = 0.55
        mask.inputs["From Max"].default_value = 1.5
        mask.inputs["To Min"].default_value = 1.0
        mask.inputs["To Max"].default_value = 1.0 - 0.8 * vignette
        tree.links.new(length.outputs["Value"], mask.inputs["Value"])
        mix = _node(tree, VIGNETTE + " Mix", 'ShaderNodeMix', x + 420, y)
        mix.data_type = 'RGBA'
        mix.blend_type = 'MULTIPLY'
        mix.inputs[0].default_value = 1.0
        tree.links.new(image, mix.inputs[6])
        tree.links.new(mask.outputs["Result"], mix.inputs[7])
        image = mix.outputs[2]
    tree.links.new(image, out.inputs[0])
    background.ensure_output(tree)
    if not scene.render.use_compositing:
        scene.render.use_compositing = True


def on_change(settings, context):
    scene = settings.id_data
    apply(scene, settings.look_glow, settings.look_contrast, settings.look_vignette, settings.look_saturation)


class STUDIO_OT_look_preset(bpy.types.Operator):
    bl_idname = "studio.look_preset"
    bl_label = "Look Preset"
    bl_description = "Set glow, contrast and vignette for the compositor"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        glow, contrast, vignette = PRESETS.get(props.name, (0, 0, 0))
        return "Glow %d%%, contrast %d%%, vignette %d%%" % (glow * 100, contrast * 100, vignette * 100)

    def execute(self, context):
        rq = context.scene.studio
        rq.look_glow, rq.look_contrast, rq.look_vignette = PRESETS[self.name]
        return {'FINISHED'}


class STUDIO_OT_look_reset(bpy.types.Operator):
    bl_idname = "studio.look_reset"
    bl_label = "Reset Look"
    bl_description = "Exposure, white balance, saturation, glow, contrast and vignette back to neutral"
    bl_options = {'UNDO', 'INTERNAL'}

    def execute(self, context):
        scene = context.scene
        rq = scene.studio
        view = scene.view_settings
        view.exposure = 0.0
        if hasattr(view, "use_white_balance"):
            view.use_white_balance = False
        rq.look_saturation = 1.0
        rq.look_glow, rq.look_contrast, rq.look_vignette = PRESETS["Clean"]
        return {'FINISHED'}


def draw(layout, scene, live=None, menu=None):
    """Look: exposure, white balance and saturation first (the basics), then the presets with glow,
    contrast and vignette. live: the viewport shows the look (None: no switch). menu: the saved looks."""
    from .ui_common import section
    rq = scene.studio
    view = scene.view_settings
    box, head, is_open = section(layout, "look", "Look", 'NODE_COMPOSITING')
    if menu:
        head.menu(menu, text="", icon='PRESET')
    if live is not None:
        head.operator("studio.look_live", text="", icon='HIDE_OFF' if live else 'HIDE_ON', depress=live)
    head.operator(STUDIO_OT_look_reset.bl_idname, text="", icon='LOOP_BACK')
    from . import keys
    keys.button(head, 'LOOK')
    if not is_open:
        return
    col = box.column(align=True)
    col.prop(view, "exposure", text="Exposure", slider=True)
    if hasattr(view, "use_white_balance"):
        row = col.row(align=True)
        sub = row.row(align=True)
        sub.active = view.use_white_balance
        sub.prop(view, "white_balance_temperature", text="White Balance", slider=True)
        row.prop(view, "use_white_balance", text="", icon='CHECKBOX_HLT' if view.use_white_balance else 'CHECKBOX_DEHLT')
        if view.use_white_balance:
            col.prop(view, "white_balance_tint", text="Tint", slider=True)
    col.prop(rq, "look_saturation", slider=True)
    row = box.row(align=True)
    current = (round(rq.look_glow, 2), round(rq.look_contrast, 2), round(rq.look_vignette, 2))
    for name, values in PRESETS.items():
        row.operator(STUDIO_OT_look_preset.bl_idname, text=name, depress=current == values).name = name
    col = box.column(align=True)
    col.prop(rq, "look_glow", slider=True)
    col.prop(rq, "look_contrast", slider=True)
    col.prop(rq, "look_vignette", slider=True)
    draw_effects(box, scene)


# Nodes every compositor has (not effects)
_PLAIN = {'NodeGroupInput', 'NodeGroupOutput', 'CompositorNodeComposite', 'CompositorNodeViewer',
          'CompositorNodeRLayers', 'NodeReroute', 'NodeFrame'}


def effects(scene):
    """The compositor's other effects (Glare, Lens Distortion … e.g. added by Claude), not ours."""
    tree = background.compositor_tree(scene)
    if tree is None:
        return []
    return [n for n in tree.nodes if n.bl_idname not in _PLAIN
            and not n.name.startswith((PREFIX, background.PREFIX))]


# The few values worth a slider per kind of effect (the rest: in the compositor)
MAIN = {
    'CompositorNodeGlare': ("Type", "Threshold", "Strength", "Size"),
    'CompositorNodeBrightContrast': ("Bright", "Contrast"),
    'CompositorNodeHueSat': ("Hue", "Saturation", "Value"),
    'CompositorNodeLensdist': ("Distortion", "Dispersion"),
    'CompositorNodeBlur': ("Size",),
    'CompositorNodeDefocus': ("Size",),
    'CompositorNodeColorBalance': ("Factor",),
    'CompositorNodeExposure': ("Exposure",),
    'CompositorNodeGamma': ("Gamma",),
    'CompositorNodeFilter': ("Factor",),
}


def draw_effects(layout, scene):
    """Every other effect in the compositor: on/off and its two or three main values; all the rest
    in the compositor (the button on the right)."""
    nodes = effects(scene)
    layout.separator(factor=0.5)
    head = layout.row(align=True)
    head.label(text="Effects" if nodes else "Effects: none", icon='SHADERFX')
    head.operator("studio.open_compositor", text="", icon='NODETREE')
    for node in nodes:
        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(node, "mute", text="", icon='HIDE_ON' if node.mute else 'HIDE_OFF', invert_checkbox=True,
                 emboss=False)
        row.label(text=node.label or node.name, translate=False)
        body = col.column(align=True)
        body.active = not node.mute
        names = MAIN.get(node.bl_idname)
        shown = 0
        for socket in node.inputs:
            if socket.is_linked or not socket.enabled or not hasattr(socket, "default_value"):
                continue
            if names is not None and socket.name not in names:
                continue
            if names is None and (socket.type not in {'VALUE', 'INT'} or shown >= 2):
                continue
            body.prop(socket, "default_value", text=socket.name)
            shown += 1


class STUDIO_OT_open_compositor(bpy.types.Operator):
    bl_idname = "studio.open_compositor"
    bl_label = "Open Compositor"
    bl_description = "All effects and their values in Blender's compositor (a new window)"

    def execute(self, context):
        bpy.ops.wm.window_new()
        window = context.window_manager.windows[-1]
        area = window.screen.areas[0]
        area.type = 'NODE_EDITOR'
        area.ui_type = 'CompositorNodeTree'
        return {'FINISHED'}

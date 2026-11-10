# SPDX-License-Identifier: GPL-3.0-or-later
"""Look: glow, contrast and vignette in the Blender 5 compositor (Render Raw does not run there).

Three nodes of our own ("AS Glow", "AS Contrast", "AS Vignette …") right in front of the
compositor output, after everything else (also the background color). A value of 0 takes the
effect out again; with all three at 0 nothing of ours is left in the tree.
"""

import bpy
from bpy.props import StringProperty

from . import background

PREFIX = "AS Look"
GLOW, CONTRAST, VIGNETTE = PREFIX + " Glow", PREFIX + " Contrast", PREFIX + " Vignette"

PRESETS = {
    "Clean": (0.0, 0.0, 0.0),
    "Soft Glow": (0.35, 0.05, 0.15),
    "Punchy": (0.15, 0.3, 0.25),
    "Dramatic": (0.45, 0.45, 0.5),
}


def _ours(tree):
    return [n for n in tree.nodes if n.name.startswith(PREFIX)]


def _remove(tree):
    """Take our nodes out and connect what fed them straight to where they went."""
    first = tree.nodes.get(GLOW) or tree.nodes.get(CONTRAST) or tree.nodes.get(VIGNETTE + " Mix")
    last_out = None
    for name in (VIGNETTE + " Mix", CONTRAST, GLOW):
        node = tree.nodes.get(name)
        if node is not None:
            last_out = node
            break
    source = None
    if first is not None:
        socket = first.inputs["Image"] if first.bl_idname != 'ShaderNodeMix' else first.inputs[6]
        if socket.is_linked:
            source = socket.links[0].from_socket
    targets = [l.to_socket for l in last_out.outputs[0].links] if last_out is not None else []
    if last_out is not None and last_out.bl_idname == 'ShaderNodeMix':
        targets = [l.to_socket for l in last_out.outputs[2].links]
    for node in _ours(tree):
        tree.nodes.remove(node)
    if source is not None:
        for socket in targets:
            tree.links.new(source, socket)


def _node(tree, name, idname, x, y):
    node = tree.nodes.new(idname)
    node.name = name
    node.label = name[len(PREFIX) + 1:]
    node.location = (x, y)
    return node


def apply(scene, glow, contrast, vignette):
    """Build the nodes for these strengths (0-1 each)."""
    tree = background.compositor_tree(scene)
    if tree is None:
        if not (glow or contrast or vignette):
            return
        tree = background._new_tree()
        background.set_compositor_tree(scene, tree)
    _remove(tree)
    if not (glow or contrast or vignette):
        return
    out = background._output_node(tree)
    if out is None:
        return
    into = out.inputs[0].links[0].from_socket if out.inputs[0].is_linked else background._render_layers_image(tree)
    x, y = out.location.x, out.location.y
    out.location.x += 900
    image = into
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
    if not scene.render.use_compositing:
        scene.render.use_compositing = True


def on_change(settings, context):
    scene = settings.id_data
    apply(scene, settings.look_glow, settings.look_contrast, settings.look_vignette)


class RS_OT_look_preset(bpy.types.Operator):
    bl_idname = "render_studio.look_preset"
    bl_label = "Look Preset"
    bl_description = "Set glow, contrast and vignette for the compositor"
    bl_options = {'UNDO', 'INTERNAL'}

    name: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        glow, contrast, vignette = PRESETS.get(props.name, (0, 0, 0))
        return "Glow %d%%, contrast %d%%, vignette %d%%" % (glow * 100, contrast * 100, vignette * 100)

    def execute(self, context):
        rq = context.scene.render_studio
        rq.look_glow, rq.look_contrast, rq.look_vignette = PRESETS[self.name]
        return {'FINISHED'}


def draw(layout, scene):
    rq = scene.render_studio
    box = layout.box()
    box.label(text="Look (Compositor)", icon='NODE_COMPOSITING')
    row = box.row(align=True)
    current = (round(rq.look_glow, 2), round(rq.look_contrast, 2), round(rq.look_vignette, 2))
    for name, values in PRESETS.items():
        row.operator(RS_OT_look_preset.bl_idname, text=name, depress=current == values).name = name
    col = box.column(align=True)
    col.prop(rq, "look_glow", slider=True)
    col.prop(rq, "look_contrast", slider=True)
    col.prop(rq, "look_vignette", slider=True)

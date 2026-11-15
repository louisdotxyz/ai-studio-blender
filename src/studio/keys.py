# SPDX-License-Identifier: GPL-3.0-or-later
"""Keyframes for the studio's settings. The fields of the tabs are partly the add-on's own helpers
(orbit angles, the look sliders …), which Blender cannot key directly; the ◆ button of a section keys
what they really change, at the current frame, so films (also in their own process) play it back:

- ORBIT: the camera on its pivot (orbit.rig: angle, height, distance → an arc between keys), lens or
  ortho scale, focus distance;
- LIGHT: the open light: place, turn, brightness, color, size;
- LOOK: exposure, white balance and every value of the compositor's nodes (the look and the effects);
- WORLD: the World's node values (strength, rotation …).

Fields that show Blender's own values (focal length, material values …) key with I over the field as
everywhere in Blender."""

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator

from . import background, cameras, orbit

WHAT = (('ORBIT', "Camera", ""), ('LIGHT', "Light", ""), ('LOOK', "Look", ""), ('WORLD', "World", ""))


def _key(owner, path, frame):
    try:
        return bool(owner.keyframe_insert(path, frame=frame))
    except (TypeError, RuntimeError):
        return False


def _key_nodes(tree, frame, only=None):
    count = 0
    if tree is None:
        return 0
    for node in tree.nodes:
        if only is not None and not only(node):
            continue
        for socket in node.inputs:
            if not socket.is_linked and hasattr(socket, "default_value") and socket.type in {'VALUE', 'INT', 'RGBA', 'VECTOR', 'BOOLEAN'}:
                count += _key(socket, "default_value", frame)
    return count


def key_camera(scene, camera, frame):
    distance = orbit.camera_orbit(scene.studio, camera)[2]
    pivot = orbit.rig(camera, distance)
    count = _key(pivot, "location", frame) + _key(pivot, "rotation_euler", frame) + _key(camera, "location", frame)
    data = camera.data
    count += _key(data, "ortho_scale" if data.type == 'ORTHO' else "lens", frame)
    if data.dof.use_dof:
        count += _key(data.dof, "focus_distance", frame) + _key(data.dof, "aperture_fstop", frame)
    return count


def key_light(obj, frame):
    count = sum(_key(obj, path, frame) for path in ("location", "rotation_euler", "scale"))
    data = obj.data
    for path in ("energy", "color", "size", "size_y", "spot_size", "spot_blend", "shadow_soft_size"):
        if hasattr(data, path):
            count += _key(data, path, frame)
    return count


def key_look(scene, frame):
    view = scene.view_settings
    count = _key(view, "exposure", frame)
    if getattr(view, "use_white_balance", False):
        count += _key(view, "white_balance_temperature", frame) + _key(view, "white_balance_tint", frame)
    return count + _key_nodes(background.compositor_tree(scene), frame)


def key_world(scene, frame):
    world = scene.world
    return _key_nodes(world.node_tree if world is not None and world.use_nodes else None, frame)


class STUDIO_OT_key(Operator):
    bl_idname = "studio.key"
    bl_label = "Keyframe"
    bl_options = {'UNDO', 'INTERNAL'}

    what: EnumProperty(items=WHAT, options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return {'ORBIT': "Keyframe at this frame: the camera on its orbit (between keys it moves on an arc around "
                         "the product), lens and focus",
                'LIGHT': "Keyframe at this frame: the open light's place, turn, brightness, color and size",
                'LOOK': "Keyframe at this frame: exposure, white balance and every look and effect value",
                'WORLD': "Keyframe at this frame: the World's values (strength, rotation …)"}.get(props.what, "")

    def execute(self, context):
        scene = context.scene
        frame = scene.frame_current
        if self.what == 'ORBIT':
            camera = cameras.active_camera(scene)
            if camera is None:
                self.report({'WARNING'}, "No camera")
                return {'CANCELLED'}
            count = key_camera(scene, camera, frame)
        elif self.what == 'LIGHT':
            from . import light_items
            item = light_items.active_item(scene)
            if item is None or item[0] == 'PIN':
                self.report({'WARNING'}, "Open a light first (lights in the World: key the World)")
                return {'CANCELLED'}
            count = key_light(item[1], frame)
        elif self.what == 'LOOK':
            count = key_look(scene, frame)
        else:
            count = key_world(scene, frame)
        self.report({'INFO'}, "Frame %d: %d keys" % (frame, count))
        return {'FINISHED'}


def button(layout, what):
    layout.operator(STUDIO_OT_key.bl_idname, text="", icon='DECORATE_KEYFRAME', emboss=False).what = what


classes = (STUDIO_OT_key,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

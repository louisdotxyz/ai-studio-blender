# SPDX-License-Identifier: GPL-3.0-or-later
"""The explosion view: groups of parts fly apart and back, driven by one number.

- A group (from the selection, or by collection / name) gets a handle, an empty "Griff | <group>" at the
  group's centre in the collection Object › Explosion. Each part follows its handle through a Child Of
  constraint ("Studio Explode") whose influence is the scene property "Explosion" (0 … 1, a driver
  with a simple expression: no Python needed in the render). At 0 the parts are exactly where CAD put
  them; moving or turning a handle only shows at Explosion > 0, the assembly stays whole.
- Keys: scene["Explosion"] like any value; "In this section" keys 0 → 1 at the section's start and back
  to 0 after its end.
- Arrange: a group flat to the camera or upright (its main axes from the vertices), groups in a row
  in front of the camera (equal gaps, one centre height).
- Parts hidden only in the explosion (a driver on their visibility), the CAD placement kept on each part
  (studio_cad_matrix) to warn when a part was moved directly and to put it back.
- Hooks: after a part got a new origin its hooks point into the void: realign_hooks() sets their
  inverse matrix again (what "Reset" does in the modifier).
"""

import bpy
from bpy.props import EnumProperty, StringProperty
from mathutils import Matrix, Vector

from . import cameras

PROP = "Explosion"                    # the scene property that drives everything
CONSTRAINT = "Studio Explode"
HANDLE_PREFIX = "Griff | "
GROUP_KEY = "studio_explode_group"    # on parts and handles: the group's name
CAD_KEY = "studio_cad_matrix"         # on parts: where CAD put them (16 floats, world)
HIDE_KEY = "studio_explode_hide"
COLLECTION = "Explosion"


# ------------------------------------------------------------------ the driving value

def ensure_prop(scene):
    if PROP not in scene.keys():
        scene[PROP] = 0.0
    ui = scene.id_properties_ui(PROP)
    ui.update(min=0.0, max=1.0, soft_min=0.0, soft_max=1.0, description="How far the explosion view is open "
              "(0 = assembled as in CAD, 1 = open)")
    return scene[PROP]


def set_value(scene, value):
    """Set the explosion and let the drivers follow at once (frame_set evaluates them)."""
    ensure_prop(scene)
    scene[PROP] = float(value)
    scene.update_tag()
    scene.frame_set(scene.frame_current)


def _drive(owner, path, scene, expression="var"):
    """A driver on owner.path from scene["Explosion"] (simple expression: no Python needed)."""
    owner.driver_remove(path)
    fc = owner.driver_add(path)
    drv = fc.driver
    drv.type = 'SCRIPTED'
    drv.expression = expression
    var = drv.variables.new()
    var.name = "var"
    var.type = 'SINGLE_PROP'
    var.targets[0].id_type = 'SCENE'
    var.targets[0].id = scene
    var.targets[0].data_path = '["%s"]' % PROP
    return fc


# ------------------------------------------------------------------ groups

def _collection(scene):
    parent = cameras.object_collection(scene)
    coll = parent.children.get(COLLECTION) or bpy.data.collections.get(COLLECTION)
    if coll is None:
        coll = bpy.data.collections.new(COLLECTION)
    if coll.name not in parent.children:
        parent.children.link(coll)
    return coll


def _store_cad(obj):
    if CAD_KEY not in obj.keys():
        obj[CAD_KEY] = [v for row in obj.matrix_world for v in row]


def cad_matrix(obj):
    m = obj.get(CAD_KEY)
    return Matrix([m[0:4], m[4:8], m[8:12], m[12:16]]) if m is not None and len(m) == 16 else None


def _center(objects):
    points = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
    if not points:
        return Vector()
    lo = Vector((min(p.x for p in points), min(p.y for p in points), min(p.z for p in points)))
    hi = Vector((max(p.x for p in points), max(p.y for p in points), max(p.z for p in points)))
    return (lo + hi) / 2


def handle_of(name):
    return bpy.data.objects.get(HANDLE_PREFIX + name)


def groups(scene):
    """{group name: [parts]} of the scene."""
    out = {}
    for obj in scene.objects:
        name = obj.get(GROUP_KEY)
        if name and not obj.name.startswith(HANDLE_PREFIX):
            out.setdefault(name, []).append(obj)
    return out


def make_group(scene, name, objects):
    """A group with its handle; the parts follow it at Explosion > 0. Returns the handle."""
    objects = [o for o in objects if o.type not in {'CAMERA', 'LIGHT'} and not o.name.startswith(HANDLE_PREFIX)]
    if not objects:
        raise ValueError("No parts for the group")
    ensure_prop(scene)
    explosion = scene[PROP]
    set_value(scene, 0.0)                    # set up in the assembled state
    handle = handle_of(name)
    if handle is None:
        handle = bpy.data.objects.new(HANDLE_PREFIX + name, None)
        handle.empty_display_type = 'ARROWS'
        _collection(scene).objects.link(handle)
    handle.empty_display_size = max(0.02, max(max(o.dimensions) for o in objects) * 0.4)
    handle[GROUP_KEY] = name
    handle.matrix_world = Matrix.Translation(_center(objects))
    bpy.context.view_layer.update()
    for obj in objects:
        _store_cad(obj)
        obj[GROUP_KEY] = name
        con = obj.constraints.get(CONSTRAINT) or obj.constraints.new('CHILD_OF')
        con.name = CONSTRAINT
        con.target = handle
        con.set_inverse_pending = False
        con.inverse_matrix = handle.matrix_world.inverted()   # at the handle's start: no change
        _drive(con, "influence", scene)
    set_value(scene, explosion)
    return handle


def remove_group(scene, name):
    """The parts are plain again (exactly where CAD put them), the handle goes."""
    for obj in groups(scene).get(name, []):
        con = obj.constraints.get(CONSTRAINT)
        if con is not None:
            con.driver_remove("influence")
            obj.constraints.remove(con)
        obj.pop(GROUP_KEY, None)
    handle = handle_of(name)
    if handle is not None:
        bpy.data.objects.remove(handle)


def name_for(objects):
    """A group's name from its parts: their common collection, else the first part's name."""
    colls = {c.name for o in objects for c in o.users_collection}
    shared = [c for c in colls if all(c in {x.name for x in o.users_collection} for o in objects)]
    shared = [c for c in shared if c not in cameras.COLLECTIONS]
    return shared[0] if shared else objects[0].name.split(".")[0]


# ------------------------------------------------------------------ keys

def key_section(scene, start, end, open_frames=24):
    """In a section: closed at its start, open after `open_frames`, closed again right after its end."""
    ensure_prop(scene)
    keep = scene[PROP]
    for frame, value in ((start, 0.0), (min(end, start + open_frames), 1.0)):
        scene[PROP] = value
        scene.keyframe_insert('["%s"]' % PROP, frame=frame)
    if end + 1 <= scene.frame_end:
        scene[PROP] = 1.0
        scene.keyframe_insert('["%s"]' % PROP, frame=end)
        scene[PROP] = 0.0
        scene.keyframe_insert('["%s"]' % PROP, frame=end + 1)
        from .motion import fcurves_of
        for fc in fcurves_of(scene):
            if fc.data_path == '["%s"]' % PROP:
                for p in fc.keyframe_points:
                    if abs(p.co.x - end) < 0.5:
                        p.interpolation = 'CONSTANT'     # back at once at the section's end
    scene[PROP] = keep


# ------------------------------------------------------------------ arranging

def _points(objects, limit=4000):
    pts = []
    for obj in objects:
        if obj.type != 'MESH' or obj.data is None:
            pts += [obj.matrix_world @ Vector(c) for c in obj.bound_box]
            continue
        verts = obj.data.vertices
        step = max(1, len(verts) // max(1, limit // max(1, len(objects))))
        mw = obj.matrix_world
        pts += [mw @ verts[i].co for i in range(0, len(verts), step)]
    return pts


def main_axes(objects):
    """The group's main axes (largest spread first) from its vertices (PCA), as unit vectors."""
    pts = _points(objects)
    if len(pts) < 3:
        return Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
    n = len(pts)
    mean = sum(pts, Vector()) / n
    cov = [[0.0] * 3 for _ in range(3)]
    for p in pts:
        d = p - mean
        for i in range(3):
            for j in range(3):
                cov[i][j] += d[i] * d[j] / n
    try:
        import numpy
        values, vectors = numpy.linalg.eigh(numpy.array(cov))
        order = list(reversed(numpy.argsort(values)))
        axes = [Vector(vectors[:, k]).normalized() for k in order]
    except ImportError:
        axes = [Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))]
    return axes[0], axes[1], axes[0].cross(axes[1]).normalized()


def _turn_handle(handle, rotation):
    """Turn a handle about its own point (its group turns along in the explosion)."""
    loc = handle.matrix_world.translation.copy()
    handle.matrix_world = Matrix.Translation(loc) @ rotation.to_4x4() @ Matrix.Translation(-loc) @ handle.matrix_world


def face_camera(scene, name, camera, upright=False):
    """The group flat to the camera (its thinnest axis looks at it); upright: its longest axis up in the
    picture as well."""
    parts = groups(scene).get(name, [])
    handle = handle_of(name)
    if not parts or handle is None or camera is None:
        raise ValueError("No group %s or no camera" % name)
    keep = scene[PROP]
    set_value(scene, 1.0)                    # as it is in the explosion now
    longest, _middle, thin = main_axes(parts)
    view = camera.matrix_world.to_3x3()
    toward = view.col[2].normalized()        # from the product to the camera
    up = view.col[1].normalized()
    if thin.dot(toward) < 0:
        thin = -thin
    turn = thin.rotation_difference(toward).to_matrix()
    if upright:
        turned = (turn @ longest).normalized()
        flat = (turned - toward * turned.dot(toward)).normalized()
        if flat.dot(up) < 0:
            flat = -flat
        turn = flat.rotation_difference(up).to_matrix() @ turn
    _turn_handle(handle, turn)
    set_value(scene, keep)


def row(scene, names, camera, gap=0.0):
    """The groups side by side in front of the camera: equal gaps, one centre height, in the order given
    (from left), around where they are now."""
    if camera is None or not names:
        raise ValueError("No camera or no groups")
    keep = scene[PROP]
    set_value(scene, 1.0)
    right = camera.matrix_world.to_3x3().col[0].normalized()
    up = camera.matrix_world.to_3x3().col[1].normalized()
    found = groups(scene)
    boxes = []
    for name in names:
        parts = found.get(name, [])
        pts = _points(parts, 1500)
        if not pts:
            continue
        xs = [p.dot(right) for p in pts]
        boxes.append((name, min(xs), max(xs), _center(parts)))
    if not boxes:
        set_value(scene, keep)
        return 0
    widths = [hi - lo for _n, lo, hi, _c in boxes]
    gap = gap or (sum(widths) / len(widths)) * 0.25
    total = sum(widths) + gap * (len(boxes) - 1)
    middle = sum((c for *_r, c in boxes), Vector()) / len(boxes)
    height = middle.dot(up)
    x = middle.dot(right) - total / 2
    for (name, lo, hi, center), width in zip(boxes, widths):
        target_x = x + width / 2
        now_x = (lo + hi) / 2
        shift = right * (target_x - now_x) + up * (height - center.dot(up))
        handle = handle_of(name)
        handle.matrix_world = Matrix.Translation(shift) @ handle.matrix_world
        x += width + gap
    set_value(scene, keep)
    return len(boxes)


# ------------------------------------------------------------------ hiding, protection, hooks

def hide_in_explosion(scene, objects, hide=True):
    """Parts that disappear while the explosion is open (and are back when it closes)."""
    for obj in objects:
        for path in ("hide_render", "hide_viewport"):
            obj.driver_remove(path)
        if hide:
            for path in ("hide_render", "hide_viewport"):
                _drive(obj, path, scene, "var > 0.01")
            obj[HIDE_KEY] = True
        else:
            obj.pop(HIDE_KEY, None)
            obj.hide_render = obj.hide_viewport = False


_moved_seen = [0.0, []]


def moved_cached(scene):
    import time
    if time.monotonic() - _moved_seen[0] > 1.0:
        _moved_seen[:] = [time.monotonic(), moved_parts(scene)]
    return _moved_seen[1]


def moved_parts(scene, tolerance=1e-4):
    """Parts that are not where CAD put them although the explosion is closed (moved directly, not by
    their handle)."""
    if float(scene.get(PROP, 0.0)) > 1e-6:
        return []
    out = []
    for obj in scene.objects:
        cad = cad_matrix(obj)
        if cad is None:
            continue
        diff = max(abs(a - b) for ra, rb in zip(obj.matrix_world, cad) for a, b in zip(ra, rb))
        if diff > tolerance:
            out.append(obj)
    return out


def restore_cad(objects):
    """Parts back where CAD put them (with the explosion closed)."""
    done = 0
    for obj in objects:
        cad = cad_matrix(obj)
        if cad is None:
            continue
        con = obj.constraints.get(CONSTRAINT)
        mute = con.mute if con else None
        if con:
            con.mute = True
        bpy.context.view_layer.update()
        obj.matrix_world = cad
        if con:
            con.mute = mute
        done += 1
    return done


def realign_hooks(objects):
    """Hook modifiers that point into the void (the part or its hook target got a new origin): their
    inverse matrix is set again, so the hooked points stay where they are now."""
    count = 0
    for obj in objects:
        for mod in getattr(obj, "modifiers", ()):
            if mod.type == 'HOOK' and mod.object is not None:
                target = mod.object.matrix_world
                if mod.subtarget and mod.object.type == 'ARMATURE':
                    bone = mod.object.pose.bones.get(mod.subtarget)
                    if bone is not None:
                        target = mod.object.matrix_world @ bone.matrix
                mod.matrix_inverse = target.inverted() @ obj.matrix_world
                count += 1
    return count


# ------------------------------------------------------------------ operators and drawing

ACTIONS = (('GROUP', "Group", "The selected parts as a group with a handle (named after their collection)"),
        ('REMOVE', "Remove", "This group's parts are plain again, its handle goes"),
        ('SELECT', "Handle", "Select the group's handle: move and turn it with the explosion open"),
        ('FLAT', "Flat to Camera", "The group turns so its flat side looks at the camera"),
        ('UPRIGHT', "Upright", "Flat to the camera with its longest side up"),
        ('ROW', "Row", "The selected groups (or all) side by side in front of the camera, equal gaps"),
        ('KEY', "In This Section", "Keys: closed at the section's start, open after 24 frames, closed again after "
                                   "its end"),
        ('HIDE', "Hide When Open", "The selected parts disappear while the explosion is open"),
        ('SHOW', "Always Show", "The selected parts stay visible in the explosion"),
        ('RESTORE', "CAD Placement", "Parts moved directly (not by their handle) go back where CAD put them"),
        ('HOOKS', "Realign Hooks", "Hooks of the selected parts point at their targets again (after a new origin)"))


class STUDIO_OT_explode(bpy.types.Operator):
    bl_idname = "studio.explode"
    bl_label = "Explosion"
    bl_options = {'UNDO', 'INTERNAL'}

    action: EnumProperty(items=ACTIONS, options={'HIDDEN', 'SKIP_SAVE'})
    group: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return next(i[2] for i in ACTIONS if i[0] == props.action)

    def execute(self, context):
        scene = context.scene
        selected = list(context.selected_objects)
        camera = cameras.active_camera(scene)
        try:
            if self.action == 'GROUP':
                name = name_for(selected) if selected else ""
                handle = make_group(scene, name, selected)
                self.report({'INFO'}, "Group %s: move %s with the explosion open" % (name, handle.name))
            elif self.action == 'REMOVE':
                remove_group(scene, self.group)
            elif self.action == 'SELECT':
                handle = handle_of(self.group)
                for obj in selected:
                    obj.select_set(False)
                handle.select_set(True)
                context.view_layer.objects.active = handle
                if float(scene.get(PROP, 0.0)) < 0.5:
                    set_value(scene, 1.0)
            elif self.action in {'FLAT', 'UPRIGHT'}:
                face_camera(scene, self.group, camera, upright=self.action == 'UPRIGHT')
            elif self.action == 'ROW':
                names = [n for n in groups(scene) if handle_of(n) in selected] or list(groups(scene))
                self.report({'INFO'}, "%d groups in a row" % row(scene, names, camera))
            elif self.action == 'KEY':
                from .motion import current_range
                start, end, _name = current_range(scene)
                key_section(scene, start, end)
            elif self.action in {'HIDE', 'SHOW'}:
                hide_in_explosion(scene, selected, self.action == 'HIDE')
            elif self.action == 'RESTORE':
                self.report({'INFO'}, "%d parts back in their CAD placement" % restore_cad(moved_parts(scene)))
            elif self.action == 'HOOKS':
                self.report({'INFO'}, "%d hooks realigned" % realign_hooks(selected or scene.objects))
        except (ValueError, KeyError, AttributeError) as ex:
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


def draw(layout, scene):
    """Animation tab · Explosion."""
    found = groups(scene)
    row_ = layout.row(align=True)
    if PROP in scene.keys():
        row_.prop(scene, '["%s"]' % PROP, text="Explosion", slider=True)
        row_.operator(STUDIO_OT_explode.bl_idname, text="", icon='DECORATE_KEYFRAME').action = 'KEY'
    layout.operator(STUDIO_OT_explode.bl_idname, text="Group from Selection", icon='ADD').action = 'GROUP'
    for name in sorted(found):
        r = layout.row(align=True)
        op = r.operator(STUDIO_OT_explode.bl_idname, text="%s (%d)" % (name, len(found[name])), icon='EMPTY_ARROWS',
                        translate=False)
        op.action, op.group = 'SELECT', name
        for action, icon in (('FLAT', 'VIEW_CAMERA'), ('UPRIGHT', 'EMPTY_SINGLE_ARROW'), ('REMOVE', 'X')):
            op = r.operator(STUDIO_OT_explode.bl_idname, text="", icon=icon, emboss=action != 'REMOVE')
            op.action, op.group = action, name
    if found:
        r = layout.row(align=True)
        r.operator(STUDIO_OT_explode.bl_idname, text="Row", icon='ALIGN_CENTER').action = 'ROW'
        r.operator(STUDIO_OT_explode.bl_idname, text="Hide When Open", icon='HIDE_ON').action = 'HIDE'
        r.operator(STUDIO_OT_explode.bl_idname, text="", icon='HIDE_OFF').action = 'SHOW'
    moved = moved_cached(scene) if found else []
    if moved:
        warn = layout.row(align=True)
        warn.alert = True
        warn.label(text="%d parts moved directly (not by a handle)" % len(moved), icon='ERROR')
        warn.operator(STUDIO_OT_explode.bl_idname, text="CAD Placement").action = 'RESTORE'
    layout.operator(STUDIO_OT_explode.bl_idname, text="Realign Hooks", icon='HOOK').action = 'HOOKS'


classes = (STUDIO_OT_explode,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

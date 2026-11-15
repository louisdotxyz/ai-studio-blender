# SPDX-License-Identifier: GPL-3.0-or-later
"""Shortcuts: ⌘R renders an image, ⌘E the film's sections (both Low Quality first, High Quality once nothing
changed), ⇧⌘R an image in Low Quality, ⇧⌘E views the newest image."""

import bpy


_keymaps = []


# Editor keymaps that use ⌘R / ⇧⌘R themselves on macOS (Blender turns their Ctrl
# shortcuts into Cmd ones): Node Editor (Shading tab), Image Editor, Edit Mode
# loop cut, Sculpt, Pose, Armature, Curve, Sequencer. Editor keymaps are handled
# before the Window keymap, so the shortcuts are added to them as well; add-on
# items go in front of Blender's own items of the same keymap. Plain Ctrl+R
# (e.g. loop cut) keeps working.
EDITOR_KEYMAPS = (
    ("Node Editor", 'NODE_EDITOR'),
    ("Image Generic", 'IMAGE_EDITOR'),
    ("Sequencer", 'SEQUENCE_EDITOR'),
    ("Mesh", 'EMPTY'),
    ("Sculpt", 'EMPTY'),
    ("Pose", 'EMPTY'),
    ("Armature", 'EMPTY'),
    ("Curve", 'EMPTY'),
)


# (operator, key, modifier keys with ⌘, operator properties, label)
SHORTCUTS = (
    ("studio.add", 'R', {}, {"mode": 'STILL'}, "Render Image"),
    ("studio.add", 'E', {}, {"mode": 'ANIM'}, "Render Film"),
    ("studio.add", 'R', {"shift": True}, {"mode": 'STILL', "quality": 'LOW'},
     "Render Image, Low Quality"),
    ("studio.show_render", 'E', {"shift": True}, {}, "View Render"),
)
_KEYS = {key for _i, key, _m, _p, _l in SHORTCUTS}


_SHORTCUT_IDS = {idname for idname, *_rest in SHORTCUTS}


def _shortcut_keymaps():
    """(name, space_type) of every keymap that gets the shortcuts: Window, the list
    above and any other keymap of Blender's keyconfig that uses ⌘R (other add-ons)."""
    found = [("Window", 'EMPTY')] + list(EDITOR_KEYMAPS)
    wm = bpy.context.window_manager
    for kc in (wm.keyconfigs.default, wm.keyconfigs.user):
        for km in (kc.keymaps if kc else ()):
            if km.is_modal or km.region_type != 'WINDOW' or (km.name, km.space_type) in found:
                continue
            if any(k.type in _KEYS and k.oskey and k.idname not in _SHORTCUT_IDS
                   for k in km.keymap_items):
                found.append((km.name, km.space_type))
    return found


def register_keymaps():
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:
        return
    for name, space_type in _shortcut_keymaps():
        km = kc.keymaps.new(name=name, space_type=space_type)
        for idname, key, modifiers, props, _label in SHORTCUTS:
            kmi = km.keymap_items.new(idname, key, 'PRESS', oskey=True, **modifiers)
            for key, value in props.items():
                setattr(kmi.properties, key, value)
            _keymaps.append((km, kmi))
    # Stop the queue (only does something while it runs, otherwise the key passes on).
    km = kc.keymaps.new(name="Window", space_type='EMPTY')
    _keymaps.append((km, km.keymap_items.new('studio.cancel', 'ESC', 'PRESS', shift=True)))


def unregister_keymaps():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except Exception:
            pass
    _keymaps.clear()


def _user_keymap_item(idname, props=None):
    """The Window shortcut in the user keyconfig. Blender saves changes to it with the Preferences."""
    def matches(k):
        return k.idname == idname and all(getattr(k.properties, key, None) == value
                                          for key, value in (props or {}).items())
    wm = bpy.context.window_manager
    for kc in (wm.keyconfigs.user, wm.keyconfigs.addon):
        km = kc.keymaps.get("Window") if kc else None
        kmi = km and next((k for k in km.keymap_items if matches(k)), None)
        if kmi is not None:
            return kmi
    return None

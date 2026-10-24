# SPDX-License-Identifier: GPL-3.0-or-later
"""Keeps the Render tab of the sidebar for Render Studio.

The Render Raw extension puts its panels (Color Management …) into a sidebar
tab that is also called "Render". Those panels are moved into their own tab
"Render Raw" (re-registered with another bl_category, children first) and moved
back when Render Studio is disabled.
"""

import bpy

TAB = "Render"
NEW_TAB = "Render Raw"
_moved = []   # panel classes we changed, parents first


def _registered_panels():
    seen, todo = [], list(bpy.types.Panel.__subclasses__())
    while todo:
        cls = todo.pop()
        if cls in seen:
            continue
        seen.append(cls)
        todo.extend(cls.__subclasses__())
    return [c for c in seen if getattr(c, "is_registered", False)]


def _is_render_raw(cls):
    return "render_raw" in (cls.__module__ or "").lower().replace("-", "_").replace(" ", "_")


def foreign_panels():
    """Render Raw's sidebar panels in the Render tab, parents before children."""
    panels = _registered_panels()
    top = [c for c in panels if _is_render_raw(c) and getattr(c, "bl_space_type", "") == 'VIEW_3D'
           and getattr(c, "bl_region_type", "") == 'UI' and getattr(c, "bl_category", "") == TAB
           and not getattr(c, "bl_parent_id", "")]
    result, ids = list(top), {getattr(c, "bl_idname", c.__name__) for c in top}
    grew = True
    while grew:
        grew = False
        for c in panels:
            if c not in result and getattr(c, "bl_parent_id", "") in ids:
                result.append(c)
                ids.add(getattr(c, "bl_idname", c.__name__))
                grew = True
    return result


def _reregister(classes, category):
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    for cls in classes:
        cls.bl_category = category
        bpy.utils.register_class(cls)


def move_render_raw():
    """Returns the number of moved panels."""
    classes = foreign_panels()
    if not classes:
        return 0
    try:
        _reregister(classes, NEW_TAB)
    except Exception as ex:
        print("Render Studio: could not move the Render Raw panels:", ex)
        return 0
    _moved.extend(c for c in classes if c not in _moved)
    return len(classes)


def restore():
    classes = [c for c in _moved if getattr(c, "is_registered", False)]
    _moved.clear()
    try:
        if classes:
            _reregister(classes, TAB)
    except Exception as ex:
        print("Render Studio: could not move the Render Raw panels back:", ex)


def _later():
    move_render_raw()
    return None


@bpy.app.handlers.persistent
def _on_load_post(*_args):
    move_render_raw()   # Render Raw may have been enabled in the meantime


def register():
    # After all add-ons are enabled (Render Raw may come after Render Studio).
    bpy.app.timers.register(_later, first_interval=1.0)
    if _on_load_post not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load_post)


def unregister():
    if bpy.app.timers.is_registered(_later):
        bpy.app.timers.unregister(_later)
    if _on_load_post in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load_post)
    restore()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared pieces of the Studio tabs: tab order, the sidebar panel base, cameras of the queue."""

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

from . import cameras, runner, sync
from .props import FORMAT_PRESETS
from .runner import RUN


# ----------------------------------------------------------------- Operators

def ensure_uids(rq):
    """Every entry needs its own uid (older files have none yet)."""
    seen = set()
    for item in rq.items:
        if item.uid <= 0 or item.uid in seen:
            item.uid = rq.next_uid
            rq.next_uid += 1
        seen.add(item.uid)


def _look_through(context, camera):
    RUN.set_scene_camera(context.scene, camera)
    cameras.seed(context.scene)
    space = context.space_data
    if space is not None and space.type == 'VIEW_3D' and space.region_3d is not None:
        space.region_3d.view_perspective = 'CAMERA'
    # Fit the frame even if only the view changes (no depsgraph update).
    sync.schedule_fit()


def _user_resolution(scene, cam):
    """Resolution `cam` renders with, from the user's settings (not the running render's)."""
    if sync.has_own_format(cam):
        return sync.camera_resolution(cam)
    rq = scene.render_studio
    active = RUN.original("camera", scene.camera)
    if sync.has_own_format(active) and rq.base_resolution_x and rq.base_resolution_y:
        return (rq.base_resolution_x, rq.base_resolution_y)
    return (RUN.original("render.resolution_x", scene.render.resolution_x),
            RUN.original("render.resolution_y", scene.render.resolution_y))


def active_camera(scene):
    """The user's active camera, also while the queue renders a temporary copy."""
    cam = RUN.original("camera", scene.camera)
    if cam is not None and runner.TEMP_KEY in cam:
        cam = bpy.data.objects.get(cam[runner.TEMP_KEY])
    return cam


def _select_only(context, obj):
    view_layer = context.view_layer
    if obj.name not in view_layer.objects:
        return
    for other in context.selected_objects:
        other.select_set(False)
    obj.select_set(True)
    view_layer.objects.active = obj


def _report_value_error(op, func):
    try:
        return func()
    except ValueError as ex:
        op.report({'ERROR'}, str(ex))
        return None


SCALE_PRESETS = (0.5, 1, 2, 4)


# ------------------------------------------------------------------------ UI

def format_label(width, height):
    """Name of the preset, otherwise "1234×567"."""
    for label, _long, w, h in FORMAT_PRESETS:
        if (width, height) == (w, h):
            return label
    return "%d×%d" % (width, height)


# Order of the sidebar tabs: Blender sorts panels by bl_order, and panels
# without a header go before all others (rna_ui.cc). The tabs appear in the
# order of their first panel (area.cc). One headerless panel with a low number
# per tab puts "Render", "Light" and "Queue" right behind other headerless tabs
# such as "Claude" and in front of Item, Tool and View.
RENDER_TAB_ORDER = 1


LIGHT_TAB_ORDER = 2


MATERIAL_TAB_ORDER = 3


QUEUE_TAB_ORDER = 4


def _unpin(panel):
    """A pinned panel is shown in every tab. Ours have no header to unpin them,
    so a pin (e.g. from an older version) is cleared. UI state, not ID data."""
    try:
        if panel.use_pin:
            panel.use_pin = False
    except (AttributeError, TypeError):
        pass


STUDIO_TABS = (
    ('RENDER', "Camera", "Camera, format and image", 'CAMERA_DATA', 0),
    ('LIGHT', "Environment", "Scene presets, lights, World, background and floor", 'WORLD', 1),
    ('MATERIAL', "Material", "Materials of the active object", 'MATERIAL', 2),
    ('QUEUE', "Queue", "Render queue", 'RENDER_STILL', 3),
)


def studio_tab(context):
    return getattr(context.window_manager, "render_studio_tab", 'RENDER')


class _SidebarPanel:
    """All of Render Studio lives in one sidebar tab "Studio"; the switcher on top
    (RS_PT_tabs) picks which panel is shown."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Studio"
    studio_tab = 'RENDER'

    @classmethod
    def poll(cls, context):
        return studio_tab(context) == cls.studio_tab


def _item_progress_text():
    """"45 % · 25 s" for the image that renders now ("Draft", "Starting Blender" before)."""
    if RUN.render_progress < 0.005 and RUN.status not in {"", "rendering"}:
        return RUN.status.capitalize()
    text = "%d %%" % round(RUN.render_progress * 100)
    left = RUN.current_left()
    return text + (" · " + runner.format_time(left) if left is not None else "")


def _queue_progress_text():
    """"6/28 · 18 min left" for the whole queue."""
    if RUN.current and RUN.current.get("preview"):
        return "Preview for Claude · %d %%" % round(RUN.render_progress * 100)
    text = "%d/%d" % (RUN.position, RUN.total)
    left = RUN.total_left()
    return text + (" · %s left" % runner.format_time(left) if left is not None else "")

# SPDX-License-Identifier: GPL-3.0-or-later
"""Per-camera resolution.

A camera can have its own resolution. When it becomes the active camera, a
`depsgraph_update_post` handler switches the scene resolution to it, so the
camera frame in the viewport matches too. Cameras without their own resolution
get the scene's resolution ("base") back. Changes to the resolution while a
camera with its own resolution is active are stored in that camera.

The camera frame is also re-fitted in every 3D viewport that looks through the
camera whenever the camera or the resolution changes.
"""

import bpy


def has_own_format(obj):
    return obj is not None and obj.type == 'CAMERA' and obj.render_studio_camera.use_resolution


def scene_resolution(scene):
    return (scene.render.resolution_x, scene.render.resolution_y)


def set_scene_resolution(scene, resolution):
    if scene_resolution(scene) != tuple(resolution):
        scene.render.resolution_x, scene.render.resolution_y = resolution
        from . import render_size
        render_size.follow(scene)        # the chosen render size (1K/2K/4K) stays


def camera_resolution(obj):
    settings = obj.render_studio_camera
    return (settings.resolution_x, settings.resolution_y)


def base_resolution(scene):
    """Resolution for cameras without their own resolution."""
    if not has_own_format(scene.camera):
        return scene_resolution(scene)
    rq = scene.render_studio
    if rq.base_resolution_x and rq.base_resolution_y:
        return (rq.base_resolution_x, rq.base_resolution_y)
    return scene_resolution(scene)


def effective_resolution(scene, obj):
    """Resolution `obj` is rendered with."""
    if has_own_format(obj):
        return camera_resolution(obj)
    return base_resolution(scene)


def remember_base(scene, resolution):
    rq = scene.render_studio
    rq.base_resolution_x, rq.base_resolution_y = resolution


def sync_scene(scene):
    rq = scene.render_studio
    cam = scene.camera
    if cam != rq.last_camera:
        # Camera switched. If the old one had no resolution of its own, the
        # current resolution is the scene resolution.
        if not has_own_format(rq.last_camera):
            remember_base(scene, scene_resolution(scene))
        rq.last_camera = cam
        if has_own_format(cam):
            set_scene_resolution(scene, camera_resolution(cam))
        elif rq.base_resolution_x and rq.base_resolution_y:
            set_scene_resolution(scene, (rq.base_resolution_x, rq.base_resolution_y))
    elif has_own_format(cam) and camera_resolution(cam) != scene_resolution(scene):
        # Resolution was changed elsewhere (Image panel, Output properties).
        settings = cam.render_studio_camera
        settings.resolution_x, settings.resolution_y = scene_resolution(scene)


# ------------------------------------------------------------ Fit the frame

_fit_pending = False
_last_view = {}   # scene name -> (camera, width, height, pixel aspect)


def fit_camera_views(force=False):
    """Like Home in camera view (Frame Camera Bounds), for every viewport that is
    looking through the camera right now, but fitted into the part of the viewport
    that header, toolbar, sidebar and navigation gizmo leave free."""
    wm = bpy.context.window_manager
    if wm is None:
        return 0
    fitted = 0
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            rv3d = area.spaces.active.region_3d
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if rv3d is None or region is None or rv3d.view_perspective != 'CAMERA':
                continue
            if not force and not frame_settings()[0]:
                continue
            fit_into_visible(area, region, rv3d, bpy.context.scene)
            area.tag_redraw()
            fitted += 1
    return fitted


# ------------------------------------------- Visible part of the viewport
# With Region Overlap the sidebar (N), the toolbar (T) and the headers lie on
# top of the viewport, the navigation gizmo sits in its top right corner. The
# camera frame is centered and fitted in what is left, with a margin.

def frame_settings():
    """(auto, size 0..1, shift x, shift y in fractions of the free part, avoid gizmo)
    from the preferences (popover in the Render tab)."""
    from .runner import prefs
    p = prefs()
    if p is None:
        return True, 0.5, 0.0, 0.0, False
    # The gizmo option is gone: the smaller default size (50 %) leaves room for it anyway.
    return p.frame_auto, p.frame_size / 100.0, p.frame_shift_x / 100.0, p.frame_shift_y / 100.0, False


def _zoom_factor(camzoom):
    """BKE_screen_view3d_zoom_to_fac."""
    return ((2 ** 0.5) + camzoom / 50.0) ** 2 / 4.0


def _camzoom(factor):
    return (2.0 * factor ** 0.5 - 2 ** 0.5) * 50.0


def _ui_scale():
    system = bpy.context.preferences.system
    return (system.ui_scale or 1.0) * (getattr(system, "pixel_size", 1.0) or 1.0)


def gizmo_width(space):
    """Pixels the navigation gizmo (or the mini axis) takes at the right edge."""
    if space is None or not getattr(space, "show_gizmo", False) or not getattr(space, "show_gizmo_navigate", False):
        return 0
    view = bpy.context.preferences.view
    kind = getattr(view, "mini_axis_type", 'GIZMO')
    if kind == 'GIZMO':
        size = getattr(view, "gizmo_size_navigate_v3d", 80) + 16
    elif kind == 'MINIMAL':
        size = 2 * getattr(view, "mini_axis_size", 25) + 16
    else:
        return 0
    return round(size * _ui_scale())


def covered(area, region):
    """(left, right, bottom, top) pixels of the viewport covered by toolbar, sidebar
    and headers."""
    left = right = bottom = top = 0
    x0, x1 = region.x, region.x + region.width
    y0, y1 = region.y, region.y + region.height
    for other in area.regions:
        if other.width <= 1 or other.height <= 1 or other == region:
            continue
        if other.x + other.width <= x0 or other.x >= x1 or other.y + other.height <= y0 or other.y >= y1:
            continue   # next to the viewport, not on top of it
        if other.type in {'UI', 'TOOLS'}:
            if other.x + other.width / 2 > (x0 + x1) / 2:
                right = max(right, x1 - other.x)
            else:
                left = max(left, other.x + other.width - x0)
        elif other.type in {'HEADER', 'TOOL_HEADER', 'ASSET_SHELF', 'ASSET_SHELF_HEADER', 'FOOTER'}:
            if other.y + other.height / 2 > (y0 + y1) / 2:
                top = max(top, y1 - other.y)
            else:
                bottom = max(bottom, other.y + other.height - y0)
    return left, right, bottom, top


def frame_size(scene, winx, winy, zoomfac):
    """Size of the camera frame on screen in pixels (ED_view3d_calc_camera_border):
    the frame and the view share the camera's sensor; at zoom factor 1 the sensor
    spans the region along the fitted axis."""
    render = scene.render
    rx = render.resolution_x * render.pixel_aspect_x
    ry = render.resolution_y * render.pixel_aspect_y
    cam = scene.camera
    fit = cam.data.sensor_fit if cam is not None and cam.type == 'CAMERA' else 'AUTO'
    if fit == 'AUTO':
        frame_h, view_h = rx >= ry, winx >= winy
    else:
        frame_h = view_h = fit == 'HORIZONTAL'
    frame_w = 1.0 if frame_h else rx / ry            # in sensor sizes
    view_w = 1.0 if view_h else winx / winy
    width = zoomfac * winx * frame_w / view_w
    return width, width * ry / rx


def centered_offset(area, region, rv3d):
    """view_camera_offset that puts the frame in the middle of the visible part
    (plus the shift from the settings)."""
    left, right, bottom, top = covered(area, region)
    if region.width <= 0 or region.height <= 0:
        return 0.0, 0.0
    _auto, _size, shift_x, shift_y, _gizmo = frame_settings()
    zoom = _zoom_factor(rv3d.view_camera_zoom)
    # pixels the frame center should move right / up
    move_x = (left - right) / 2.0 + shift_x * (region.width - left - right)
    move_y = (bottom - top) / 2.0 + shift_y * (region.height - bottom - top)
    # camera.cc: the view moves by width (height) * 2 * offset * zoom factor pixels
    return -move_x / (2.0 * region.width * zoom), -move_y / (2.0 * region.height * zoom)


def fit_into_visible(area, region, rv3d, scene):
    """Zoom so the frame fills the set share of the free part of the viewport, then center it.
    The navigation gizmo makes the free part narrower on both sides, so the frame stays
    centered."""
    left, right, bottom, top = covered(area, region)
    _auto, size, _sx, _sy, avoid_gizmo = frame_settings()
    free_w = region.width - left - right
    free_h = region.height - bottom - top
    if avoid_gizmo:
        free_w -= 2 * gizmo_width(area.spaces.active)
    if free_w > 50 and free_h > 50 and scene.render.resolution_x and scene.render.resolution_y:
        width, height = frame_size(scene, region.width, region.height, 1.0)
        factor = size * min(free_w / width, free_h / height)
        rv3d.view_camera_zoom = max(-30.0, min(600.0, _camzoom(factor)))
    rv3d.view_camera_offset = centered_offset(area, region, rv3d)


def _fit_now():
    global _fit_pending
    _fit_pending = False
    try:
        fit_camera_views()
    except Exception as ex:
        print("Render Studio: fitting the camera frame failed:", ex)
    return None


def schedule_fit():
    """Fit the frame after the current update (not inside the depsgraph handler
    itself, operators are off limits there)."""
    global _fit_pending
    if _fit_pending or bpy.app.background:
        return
    _fit_pending = True
    bpy.app.timers.register(_fit_now, first_interval=0.0)


def view_state(scene):
    r = scene.render
    return (scene.camera, r.resolution_x, r.resolution_y, r.pixel_aspect_x, r.pixel_aspect_y)


def check_view_changed(scene):
    state = view_state(scene)
    if _last_view.get(scene.name) != state:
        _last_view[scene.name] = state
        schedule_fit()


def _queue_busy():
    from .runner import RUN
    return RUN.preparing


@bpy.app.handlers.persistent
def on_depsgraph_update(scene, _depsgraph=None):
    if _queue_busy():
        return
    try:
        sync_scene(scene)
        check_view_changed(scene)
        from . import background
        background.sync(scene)   # e.g. another World or view transform: update the color
    except Exception as ex:  # nie den Depsgraph-Update blockieren
        print("Render Studio: Format-Sync fehlgeschlagen:", ex)


# Update callbacks of the camera properties ----------------------------------

def on_use_resolution(settings, context):
    obj = settings.id_data
    scene = context.scene
    if settings.use_resolution:
        if scene.camera == obj:
            # The current resolution is the scene resolution, the camera starts with it.
            remember_base(scene, scene_resolution(scene))
            settings.resolution_x, settings.resolution_y = scene_resolution(scene)
        else:
            settings.resolution_x, settings.resolution_y = base_resolution(scene)
    elif scene.camera == obj:
        rq = scene.render_studio
        if rq.base_resolution_x and rq.base_resolution_y:
            set_scene_resolution(scene, (rq.base_resolution_x, rq.base_resolution_y))


def on_camera_resolution(settings, context):
    obj = settings.id_data
    scene = context.scene
    if settings.use_resolution and scene.camera == obj and not _queue_busy():
        set_scene_resolution(scene, (settings.resolution_x, settings.resolution_y))


def center_camera_frames():
    """Keeps the camera frame (passepartout) centered in the visible part of every viewport
    that looks through the camera (sidebar and toolbar taken into account). Returns how many
    views were moved."""
    wm = bpy.context.window_manager
    moved = 0
    if wm is None or not frame_settings()[0]:
        return 0
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            region_3d = area.spaces.active.region_3d
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region_3d is None or region is None or region_3d.view_perspective != 'CAMERA':
                continue
            target = centered_offset(area, region, region_3d)
            offset = region_3d.view_camera_offset
            if abs(offset[0] - target[0]) > 1e-5 or abs(offset[1] - target[1]) > 1e-5:
                region_3d.view_camera_offset = target
                area.tag_redraw()
                moved += 1
    return moved


def _center_timer():
    try:
        center_camera_frames()
    except Exception as ex:
        print("Render Studio: centering the camera frame:", ex)
    return 0.25


def register():
    if not bpy.app.timers.is_registered(_center_timer):
        bpy.app.timers.register(_center_timer, first_interval=1.0, persistent=True)
    handlers = bpy.app.handlers.depsgraph_update_post
    if on_depsgraph_update not in handlers:
        handlers.append(on_depsgraph_update)


def unregister():
    if bpy.app.timers.is_registered(_center_timer):
        bpy.app.timers.unregister(_center_timer)
    if bpy.app.timers.is_registered(_fit_now):
        bpy.app.timers.unregister(_fit_now)
    handlers = bpy.app.handlers.depsgraph_update_post
    if on_depsgraph_update in handlers:
        handlers.remove(on_depsgraph_update)

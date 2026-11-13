# SPDX-License-Identifier: GPL-3.0-or-later
"""The World in three ways (Environment tab › World): Image, Studio, Sky.

- Image: an HDRI file (hdri.py), as before.
- Studio: an environment painted from lights ("pins": softboxes with place, size, brightness, softness),
  the way HDRI light editors work. The pins live in the scene's settings; every change renders a small
  HDR file again (numpy, a fraction of a second) and reloads it. A pin can be placed by clicking on the
  product (light_paint.py): the reflection lands where you click.
- Sky: a sky by time of day. With Physical Starlight & Atmosphere installed its atmosphere is used (sun,
  clouds, stars at night); without it a simple generated sky with a sun.
"""

import math
import os

import bpy

from . import core, hdri, hdri_gen, lights, runner

LIVE_RESOLUTION = 1024     # while values change
LATITUDE = math.radians(47.0)
_pending = {"scene": "", "sky": False}

DEFAULT_PINS = (
    ("Key", -45, 35, 50, 40, 10.0, 0.35, 'RECT'),
    ("Fill", 60, 15, 70, 50, 2.5, 0.6, 'RECT'),
    ("Rim", 160, 40, 30, 30, 8.0, 0.3, 'RECT'),
)


def _folder():
    p = runner.prefs()
    root = hdri.folder_path(p.hdri_folder if p else "")
    folder = os.path.join(root, "Studio")
    os.makedirs(folder, exist_ok=True)
    return folder


def _file(scene, kind):
    name = core.safe_name(core.project_name(name=scene.render_studio.output_name) or "Scene")
    return os.path.join(_folder(), "%s %s.hdr" % (name, kind))


def _write(scene, spec, kind):
    """Renders the spec into this scene's own file (overwritten every time) and uses it as the World."""
    rs = scene.render_studio
    path = _file(scene, kind)
    hdri_gen.save_hdr(hdri_gen.render_pixels(hdri_gen.parse_world(spec)), path)
    strength = hdri.get_strength(rs) if hdri.current_path(scene) else 1.0
    world = hdri.apply(scene, path, rs)
    hdri.set_strength(rs, strength)
    env = world.node_tree.nodes.get(hdri.NODE_ENV)
    if env is not None and env.image is not None:
        env.image.reload()
    return path


# ------------------------------------------------------------------ Studio

def ensure_pins(scene):
    rs = scene.render_studio
    if len(rs.hdri_pins):
        return
    for name, h, v, w, hgt, strength, soft, shape in DEFAULT_PINS:
        pin = rs.hdri_pins.add()
        pin["name"] = name
        pin["horizontal"], pin["vertical"] = math.radians(h), math.radians(v)
        pin["width"], pin["height"] = math.radians(w), math.radians(hgt)
        pin["strength"], pin["softness"] = strength, soft
        pin["shape"] = 0 if shape == 'RECT' else 1
    rs.hdri_pin_index = -1


def studio_spec(scene, resolution=LIVE_RESOLUTION):
    rs = scene.render_studio
    color = list(rs.studio_color)
    return {
        "name": "Studio", "resolution": resolution, "strength": rs.studio_ambient,
        "sky": {"top": color, "horizon": color, "ground": [c * 0.5 for c in color]},
        "softboxes": [{"horizontal": math.degrees(p.horizontal), "vertical": math.degrees(p.vertical),
                       "width": math.degrees(p.width), "height": math.degrees(p.height),
                       "strength": p.strength, "color": list(p.color), "shape": p.shape, "softness": p.softness}
                      for p in list(rs.hdri_pins)[:16] if p.on],
    }


def apply_studio(scene, resolution=LIVE_RESOLUTION):
    return _write(scene, studio_spec(scene, resolution), "Studio")


def face_camera(scene):
    """Turn the generated World so its front (pin at 0°) is where the camera is."""
    rs = scene.render_studio
    center, _radius = lights.subject(scene, rs.light_orbit_target)
    hdri.set_rotation(rs, hdri_gen.FRONT - lights.front_angle(scene, center))


def pin_direction(scene, pin):
    """World direction a pin shines from (unit vector, from the product towards the light)."""
    from mathutils import Vector
    a = hdri_gen.FRONT + pin.horizontal - hdri.get_rotation(scene.render_studio)
    return Vector((math.cos(pin.vertical) * math.cos(a), math.cos(pin.vertical) * math.sin(a), math.sin(pin.vertical)))


def set_pin_direction(scene, pin, direction):
    d = direction.normalized()
    world_angle = math.atan2(d.y, d.x)
    h = world_angle - hdri_gen.FRONT + hdri.get_rotation(scene.render_studio)
    pin.horizontal = (h + math.pi) % (2 * math.pi) - math.pi
    pin.vertical = math.asin(max(-1.0, min(1.0, d.z)))


# ------------------------------------------------------------------ Sky

def sun_position(hours, latitude=LATITUDE):
    """(elevation, compass azimuth) in radians at the equinox: 6 h east, 12 h south, 18 h west."""
    hour_angle = math.radians((hours - 12.0) * 15.0)
    elevation = math.asin(max(-1.0, min(1.0, math.cos(latitude) * math.cos(hour_angle))))
    azimuth = math.atan2(math.sin(hour_angle), math.cos(hour_angle) * math.sin(latitude)) + math.pi
    return elevation, azimuth % (2 * math.pi)


def _mix(a, b, t):
    return [x + (y - x) * t for x, y in zip(a, b)]


def sky_spec(scene, resolution=LIVE_RESOLUTION):
    """A simple sky for this time of day (used without the atmosphere add-on)."""
    rs = scene.render_studio
    elevation, azimuth = sun_position(rs.sky_time)
    day = max(0.0, min(1.0, math.degrees(elevation) / 25.0))             # 0 at the horizon, 1 from 25° up
    dusk = max(0.0, min(1.0, (math.degrees(elevation) + 8.0) / 8.0))      # 0 = night, 1 = sun at the horizon
    night_top, night_hor = [0.004, 0.006, 0.02], [0.01, 0.015, 0.04]
    low_top, low_hor = [0.2, 0.3, 0.6], [1.0, 0.55, 0.25]
    day_top, day_hor = [0.25, 0.45, 1.0], [0.8, 0.88, 1.0]
    top = _mix(_mix(night_top, low_top, dusk), day_top, day)
    horizon = _mix(_mix(night_hor, low_hor, dusk), day_hor, day)
    spec = {"name": "Sky", "resolution": resolution, "strength": 0.05 + 0.95 * dusk * (0.4 + 0.6 * day),
            "sky": {"top": top, "horizon": horizon, "ground": [c * 0.35 for c in horizon]}}
    if elevation > math.radians(-1.0):
        # compass (0 = +Y, clockwise) → math angle → generator's horizontal (0 = FRONT)
        world_angle = math.pi / 2 - (azimuth + rs.sky_north)
        spec["sun"] = {"horizontal": math.degrees(world_angle - hdri_gen.FRONT), "vertical": math.degrees(elevation),
                       "size": 1.2, "strength": 20 + 180 * day, "kelvin": 2200 + 3600 * day}
    return spec


def atmosphere_available():
    from . import addons
    return addons.has("psa") and hasattr(bpy.ops, "psa")


def apply_sky(scene):
    """The sky for scene.render_studio.sky_time. Returns "atmosphere" or "generated"."""
    from . import addons
    rs = scene.render_studio
    elevation, azimuth = sun_position(rs.sky_time)
    if atmosphere_available() and scene.world is not None:
        try:
            if not addons.atmosphere_on(scene):
                bpy.ops.psa.add_atmosphere()
            settings = scene.world.psa_atmosphere_settings
            settings.elevation = elevation
            settings.azimuth = azimuth + rs.sky_north
            return "atmosphere"
        except (RuntimeError, AttributeError) as exc:
            print("ID Toolkit: atmosphere:", exc)
    _write(scene, sky_spec(scene), "Sky")
    hdri.set_rotation(rs, 0.0)
    return "generated"


def leave_sky(scene):
    from . import addons
    if atmosphere_available() and addons.atmosphere_on(scene):
        try:
            bpy.ops.psa.remove_atmosphere()
        except (RuntimeError, AttributeError) as exc:
            print("ID Toolkit: atmosphere:", exc)


# ------------------------------------------------------------------ changes

def _apply_pending():
    name, sky = _pending["scene"], _pending["sky"]
    _pending["scene"], _pending["sky"] = "", False
    scene = bpy.data.scenes.get(name)
    if scene is None:
        return None
    try:
        mode = scene.render_studio.world_mode
        if mode == 'STUDIO':
            apply_studio(scene)
        elif mode == 'SKY' and sky:
            apply_sky(scene)
    except Exception as exc:   # a slider must never raise
        print("ID Toolkit: world:", exc)
    return None


def _schedule(scene, sky=False):
    _pending["scene"] = scene.name
    _pending["sky"] = _pending["sky"] or sky
    if bpy.app.background:      # tests and headless runs: right away
        _apply_pending()
    elif not bpy.app.timers.is_registered(_apply_pending):
        bpy.app.timers.register(_apply_pending, first_interval=0.15)


def on_change(scene):
    """A pin or the ambient changed."""
    if isinstance(scene, bpy.types.Scene) and scene.render_studio.world_mode == 'STUDIO':
        _schedule(scene)


def on_sky(scene):
    if scene.render_studio.world_mode == 'SKY':
        _schedule(scene, sky=True)


def on_mode(scene):
    rs = scene.render_studio
    try:
        if rs.world_mode != 'SKY':
            leave_sky(scene)
        if rs.world_mode == 'STUDIO':
            ensure_pins(scene)
            apply_studio(scene)
            face_camera(scene)
        elif rs.world_mode == 'SKY':
            apply_sky(scene)
    except Exception as exc:
        print("ID Toolkit: world mode:", exc)


def unregister():
    if bpy.app.timers.is_registered(_apply_pending):
        bpy.app.timers.unregister(_apply_pending)

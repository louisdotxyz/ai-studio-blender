# SPDX-License-Identifier: GPL-3.0-or-later
"""Render quality: High and Low Quality, separately for stills and animations (Render tab).

Four profiles per scene (Scene.studio.q_still_high, q_still_low, q_anim_high, q_anim_low). Each starts from a
preset – Quick, Medium, High, Max – and every value can be changed afterwards (the preset then says
"Custom"). A render applies its profile on the worker's copy (core.prepare_job): your scene's own Cycles
settings are not changed. Resolution is a share of the render size (1K/2K/4K), not part of the presets.

Files from before: High Quality takes over the scene's Cycles settings as they were, Low Quality starts
from Quick at the Low resolution share of the preferences.
"""

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty
from bpy.types import PropertyGroup

PRESETS = {
    'QUICK':  dict(samples=32,   adaptive=True, threshold=0.1,   bounces=4,  diffuse=2, glossy=2, transmission=4,
                   clamp=5.0,  denoise=True, simplify=True,  eevee_samples=16),
    'MEDIUM': dict(samples=128,  adaptive=True, threshold=0.05,  bounces=8,  diffuse=3, glossy=3, transmission=6,
                   clamp=10.0, denoise=True, simplify=False, eevee_samples=64),
    'HIGH':   dict(samples=512,  adaptive=True, threshold=0.01,  bounces=12, diffuse=4, glossy=4, transmission=12,
                   clamp=10.0, denoise=True, simplify=False, eevee_samples=128),
    'MAX':    dict(samples=2048, adaptive=True, threshold=0.005, bounces=32, diffuse=8, glossy=8, transmission=32,
                   clamp=0.0,  denoise=True, simplify=False, eevee_samples=512),
}
PRESET_ITEMS = (
    ('QUICK', "Quick", "32 samples, 4 bounces, simplified: a fast look, a bit noisy and flat"),
    ('MEDIUM', "Medium", "128 samples, 8 bounces: good for checking and most animations"),
    ('HIGH', "High", "512 samples, 12 bounces: final stills"),
    ('MAX', "Max", "2048 samples, 32 bounces, no clamping: glass, caustics, big prints – slow"),
    ('CUSTOM', "Custom", "Values changed by hand"),
)
DEFAULTS = {"q_still_high": ('HIGH', 100), "q_still_low": ('QUICK', 50),
            "q_anim_high": ('MEDIUM', 100), "q_anim_low": ('QUICK', 50)}

_applying = [False]


def _custom(self, _context):
    if not _applying[0] and self.preset != 'CUSTOM':
        _applying[0] = True
        try:
            self.preset = 'CUSTOM'
        finally:
            _applying[0] = False


def _on_preset(self, _context):
    if not _applying[0]:
        apply_preset(self, self.preset)


def apply_preset(profile, name):
    values = PRESETS.get(name)
    _applying[0] = True
    try:
        if values:
            for key, value in values.items():
                setattr(profile, key, value)
        if profile.preset != name:
            profile.preset = name
    finally:
        _applying[0] = False


class STUDIO_Quality(PropertyGroup):
    """One render quality (still or animation, High or Low)."""
    preset: EnumProperty(name="Preset", items=PRESET_ITEMS, default='HIGH', update=_on_preset)
    samples: IntProperty(name="Samples", description="Rays per pixel: more = less noise, slower",
                         min=1, max=65536, default=512, update=_custom)
    adaptive: BoolProperty(name="Noise Threshold", description="Stop a pixel early once it is clean enough",
                           default=True, update=_custom)
    threshold: FloatProperty(name="Noise Threshold", description="Lower = cleaner and slower",
                             min=0.0001, max=1.0, precision=4, step=0.1, default=0.01, update=_custom)
    time_limit: FloatProperty(name="Time Limit", description="Stop after this many seconds per image (0 = no limit)",
                              min=0.0, subtype='TIME_ABSOLUTE', default=0.0, update=_custom)
    bounces: IntProperty(name="Light Bounces", min=0, max=1024, default=12, update=_custom)
    diffuse: IntProperty(name="Diffuse", min=0, max=1024, default=4, update=_custom)
    glossy: IntProperty(name="Glossy", min=0, max=1024, default=4, update=_custom)
    transmission: IntProperty(name="Glass", min=0, max=1024, default=12, update=_custom)
    clamp: FloatProperty(name="Clamp Indirect", description="Cuts bright noise speckles (0 = off, most accurate)",
                         min=0.0, default=10.0, update=_custom)
    denoise: BoolProperty(name="Denoise", default=True, update=_custom)
    simplify: BoolProperty(name="Simplify", description="Subdivision at most 1, textures at most 1024 px",
                           default=False, update=_custom)
    eevee_samples: IntProperty(name="Samples", description="EEVEE samples", min=1, max=65536, default=128,
                               update=_custom)
    motion_blur: BoolProperty(name="Motion Blur", default=False)
    resolution: IntProperty(name="Resolution", description="Share of the render size (1K / 2K / 4K)",
                            subtype='PERCENTAGE', min=1, max=100, default=100)


def key_of(animation, quality):
    return "q_%s_%s" % ("anim" if animation else "still", "low" if quality == 'LOW' else "high")


def profile(scene, animation, quality):
    return getattr(scene.studio, key_of(animation, quality))


def _detect(profile_):
    for name, values in PRESETS.items():
        if all(abs(getattr(profile_, k) - v) < 1e-6 if isinstance(v, float) else getattr(profile_, k) == v
               for k, v in values.items() if k not in ("eevee_samples", "simplify")):
            return name
    return 'CUSTOM'


def ensure(scene):
    """Fills the four profiles once: High from the scene's Cycles settings, Low from Quick."""
    rq = scene.studio
    if rq.quality_ready:
        return
    from .runner import prefs
    p = prefs()
    low_res = p.low_resolution if p is not None else 50
    for key, (name, res) in DEFAULTS.items():
        prof = getattr(rq, key)
        apply_preset(prof, name)
        prof.resolution = low_res if key.endswith("_low") else res
    cy = getattr(scene, "cycles", None)
    if cy is not None and scene.render.engine == 'CYCLES':
        for key in ("q_still_high", "q_anim_high"):
            prof = getattr(rq, key)
            _applying[0] = True
            try:
                prof.samples = cy.samples
                prof.adaptive = cy.use_adaptive_sampling
                prof.threshold = max(0.0001, cy.adaptive_threshold)
                prof.time_limit = cy.time_limit
                prof.bounces, prof.diffuse = cy.max_bounces, cy.diffuse_bounces
                prof.glossy, prof.transmission = cy.glossy_bounces, cy.transmission_bounces
                prof.clamp = cy.sample_clamp_indirect
                prof.denoise = cy.use_denoising
            finally:
                _applying[0] = False
            prof.preset = _detect(prof)
    rq.q_anim_high.motion_blur = rq.q_anim_low.motion_blur = scene.render.use_motion_blur
    rq.quality_ready = True


def apply(ov, scene, animation, quality):
    """The profile onto the render's overrides (core.prepare_job)."""
    if not scene.studio.quality_ready:
        ensure(scene)
    prof = profile(scene, animation, quality)
    if prof.resolution != 100:
        percent = ov.get(scene, "render.resolution_percentage")
        ov.set(scene, "render.resolution_percentage", max(1, round(percent * prof.resolution / 100)))
    engine = scene.render.engine
    if engine == 'CYCLES':
        for path, value in (("cycles.samples", prof.samples), ("cycles.use_adaptive_sampling", prof.adaptive),
                            ("cycles.adaptive_threshold", prof.threshold), ("cycles.time_limit", prof.time_limit),
                            ("cycles.max_bounces", prof.bounces), ("cycles.diffuse_bounces", prof.diffuse),
                            ("cycles.glossy_bounces", prof.glossy),
                            ("cycles.transmission_bounces", prof.transmission),
                            ("cycles.sample_clamp_indirect", prof.clamp), ("cycles.use_denoising", prof.denoise),
                            ("render.use_motion_blur", prof.motion_blur)):
            ov.set(scene, path, value)
        if prof.bounces <= 4:
            ov.set(scene, "cycles.caustics_reflective", False)
            ov.set(scene, "cycles.caustics_refractive", False)
        if prof.simplify:
            ov.set(scene, "render.use_simplify", True)
            ov.set(scene, "render.simplify_subdivision_render", min(1, ov.get(scene, "render.simplify_subdivision_render")))
            if ov.get(scene, "cycles.texture_limit_render") in {'OFF', '2048', '4096', '8192'}:
                ov.set(scene, "cycles.texture_limit_render", '1024')
    elif engine.startswith('BLENDER_EEVEE'):
        ov.set(scene, "eevee.taa_render_samples", prof.eevee_samples)
        ov.set(scene, "render.use_motion_blur", prof.motion_blur)


VALUES = ("preset", "samples", "adaptive", "threshold", "time_limit", "bounces", "diffuse", "glossy", "transmission",
          "clamp", "denoise", "simplify", "eevee_samples", "motion_blur", "resolution")


def capture(scene):
    return {key: {v: getattr(getattr(scene.studio, key), v) for v in VALUES} for key in DEFAULTS}


def restore(scene, data):
    _applying[0] = True
    try:
        for key, values in (data or {}).items():
            prof = getattr(scene.studio, key, None)
            for v, value in (values or {}).items():
                if prof is not None and hasattr(prof, v):
                    try:
                        setattr(prof, v, value)
                    except (TypeError, ValueError):
                        pass
    finally:
        _applying[0] = False


def _ensure_all():
    for scene in bpy.data.scenes:
        try:
            ensure(scene)
        except Exception as ex:
            print("ID Toolkit: render quality:", ex)
    return None


@bpy.app.handlers.persistent
def _on_load(_dummy=None):
    if not bpy.app.timers.is_registered(_ensure_all):
        bpy.app.timers.register(_ensure_all, first_interval=0.5)


def schedule_ensure():
    _on_load()


def register():
    if _on_load not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_on_load)
    _on_load()


def unregister():
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    if bpy.app.timers.is_registered(_ensure_all):
        bpy.app.timers.unregister(_ensure_all)

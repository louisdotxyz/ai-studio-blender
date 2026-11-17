# SPDX-License-Identifier: GPL-3.0-or-later
"""Editing the film in the Video Editor, on the strips as they are now: your own moves, trims and cuts stay.

- Transition (cut, cross, dip to black or white) and hold of a section: changed in place; the strips
  after it move along (ripple), with their keys.
- A clip's look (brightness, contrast, saturation, warmth): strip modifiers, kept on the strip;
  the film's look: an adjustment strip over everything. Letterbox bars, fades from or to black or
  white, a freeze frame at a frame, titles.
- Everything here is a normal step of Blender's undo.
"""

import os
import re

import bpy
from bpy.props import FloatProperty

from . import cut
from .cut import KIND_KEY, SECTION_KEY, _get, _strips, _tag

OVERLAYS = {"LOOK", "BARS", "MUSIC"}          # over the whole film: they follow its length, never ripple
SIDE_KEY = "studio_side"                      # fades: IN / OUT
LOOK_MOD, COLOR_MOD = "Studio Look", "Studio Color"
TOP = 6                                       # fades, titles, the look and bars start on this channel


# ------------------------------------------------------------------ helpers

def _sed(scene):
    """The film's strips (in its Video Editor scene, made if missing)."""
    return cut.editor_create(scene).sequence_editor


def _top_level(scene):
    sed = cut._sed_of(scene)
    return list(_strips(sed)) if sed is not None else []


def _bound(strip):
    """An effect that follows its inputs (a cross): it moves by itself."""
    return getattr(strip, "input_1", None) is not None


def _content(scene):
    return [s for s in _top_level(scene) if _get(s, KIND_KEY) not in OVERLAYS and not s.mute]


def content_range(scene):
    found = _content(scene)
    if not found:
        return None
    return min(s.frame_final_start for s in found), max(s.frame_final_end for s in found)


def _keys_of(scene, strip):
    bag = cut._channelbag(strip.id_data)
    if bag is None:
        return []
    mark = '["%s"]' % strip.name
    return [fc for fc in bag.fcurves if fc.data_path.startswith("sequence_editor.") and mark in fc.data_path]


def _shift_keys(scene, strip, delta):
    for fc in _keys_of(scene, strip):
        for point in fc.keyframe_points:
            point.co.x += delta
            point.handle_left.x += delta
            point.handle_right.x += delta
        fc.update()


def remove(scene, strip):
    """A strip and its keys."""
    bag = cut._channelbag(scene)
    for fc in _keys_of(scene, strip):
        try:
            bag.fcurves.remove(fc)
        except (RuntimeError, ReferenceError):   # gone already (a fade removed with its strip)
            pass
    try:
        _strips(_sed(scene)).remove(strip)
    except (RuntimeError, ReferenceError):
        pass


def ripple(scene, at, delta, skip=()):
    """Every strip that starts at `at` or later moves by `delta` frames (with its keys); effects that
    follow their inputs move along by themselves, the overlays stretch to the film's length."""
    if not delta:
        return
    skip = set(s.name for s in skip)
    moving = [s for s in _top_level(scene) if s.frame_final_start >= at and s.name not in skip
              and not _bound(s) and _get(s, KIND_KEY) not in OVERLAYS]
    moving.sort(key=lambda s: s.frame_final_start, reverse=delta > 0)   # never onto a neighbour
    for strip in moving:
        strip.frame_start += delta
        _shift_keys(scene, strip, delta)
    fit_overlays(scene)


def fit_overlays(scene):
    """The film's look and the letterbox bars over the whole film."""
    span = content_range(scene)
    if span is None:
        return
    a, b = span
    for strip in _top_level(scene):
        if _get(strip, KIND_KEY) in {"LOOK", "BARS"}:
            try:
                strip.frame_final_end = max(b, strip.frame_final_start + 1)
                strip.frame_final_start = a
                strip.frame_final_end = b
            except (AttributeError, RuntimeError):
                pass


def _free_channel(scene, start, end, lowest=TOP):
    """The lowest channel from `lowest` up with nothing between start and end."""
    channel = lowest
    while any(s.channel == channel and s.frame_final_start < end and start < s.frame_final_end
              for s in _top_level(scene)):
        channel += 1
    return channel


def _ours(scene, kind, section=None):
    return [s for s in _top_level(scene) if _get(s, KIND_KEY) == kind
            and (section is None or _get(s, SECTION_KEY) == section)]


def hold_strip(scene, section):
    found = _ours(scene, "HOLD", section)
    return max(found, key=lambda s: s.frame_final_end) if found else None


def section_end(scene, section):
    """The last strip of a section (its hold, else the section strip)."""
    return hold_strip(scene, section) or cut.section_strip(scene, section)


def _next_section(scene, section):
    names = [n for n, _s, _e, _m in cut.sections_of(scene)]
    if section not in names:
        return None
    later = names[names.index(section) + 1:]
    return next((n for n in later if cut.section_strip(scene, n) is not None), None)


def element_path(strip, frame):
    """The image file a strip shows at `frame` (image strips), or ""."""
    elements = list(getattr(strip, "elements", []))
    if not elements:
        return ""
    index = max(0, min(len(elements) - 1, int(frame - strip.frame_start)))
    return os.path.join(bpy.path.abspath(strip.directory), elements[index].filename)


def frame_of(strip, frame):
    """The 3D frame an image strip shows at `frame` (from its file name: <section>_0042.png), or None."""
    path = element_path(strip, frame)
    found = re.search(r"_(\d+)\.\w+$", path)
    return int(found.group(1)) if found else None


def _still(scene, source, path, channel, start, length, name, kind, section):
    """One image standing still (a hold, a freeze frame) with the look of `source`."""
    strip = cut._new_image(_strips(_sed(scene)), name, path, channel, start, 'ORIGINAL')
    strip.frame_final_duration = length
    if hasattr(source, "transform"):
        strip.transform.scale_x, strip.transform.scale_y = source.transform.scale_x, source.transform.scale_y
    copy_look(source, strip)
    _tag(strip, kind, section)
    return strip


# ------------------------------------------------------------------ transitions and holds

def clear_transition(scene, section):
    """The section's transition out (cross or dip) goes; a cross's overlap opens again."""
    for strip in _ours(scene, "CROSS", section) + _ours(scene, "DIP", section):
        remove(scene, strip)
    end, nxt = section_end(scene, section), cut.section_strip(scene, _next_section(scene, section) or "")
    if end is None or nxt is None:
        return
    overlap = end.frame_final_end - nxt.frame_final_start
    if overlap > 0:
        ripple(scene, nxt.frame_final_start, overlap, skip=(end,))
        try:
            nxt.channel = end.channel
        except (RuntimeError, AttributeError):
            pass


def _dip(scene, kind, frames, at, owner):
    half = max(1, frames // 2)
    channel = _free_channel(scene, at - half, at + half, 4)
    color = cut._effect(_strips(_sed(scene)), "%s dip" % owner, 'COLOR', channel, at - half, 2 * half)
    color.color = (1.0, 1.0, 1.0) if kind == 'WHITE' else (0.0, 0.0, 0.0)
    color.blend_type = 'ALPHA_OVER'
    _tag(color, "DIP", owner)
    for frame, alpha in ((at - half, 0.0), (at, 1.0), (at + half, 0.0)):
        color.blend_alpha = alpha
        color.keyframe_insert("blend_alpha", frame=frame)
    return color


def apply_transition(scene, section, kind, frames):
    """The transition into the next section, on the strips as they are."""
    end, nxt = section_end(scene, section), cut.section_strip(scene, _next_section(scene, section) or "")
    if end is None or nxt is None or kind == 'CUT':
        return None
    if kind == 'CROSS':
        frames = max(1, min(frames, end.frame_final_duration, nxt.frame_final_duration - 1))
        if nxt.channel == end.channel:
            nxt.channel = end.channel + 1 if end.channel == 1 else 1
        ripple(scene, nxt.frame_final_start, -frames, skip=(end,))
        fx = cut._effect(_strips(_sed(scene)), "%s ×" % section, 'CROSS', max(end.channel, nxt.channel) + 1,
                         nxt.frame_final_start, frames, input1=end, input2=nxt)
        _tag(fx, "CROSS", section)
        return fx
    return _dip(scene, kind, frames, nxt.frame_final_start, section)


def set_hold(scene, section, frames):
    """The section's last image stands still for `frames` after it; what follows moves along."""
    strip = cut.section_strip(scene, section)
    if strip is None:
        return None
    clear_transition(scene, section)
    old = hold_strip(scene, section)
    main = max((s for s in _ours(scene, "SECTION", section)), key=lambda s: s.frame_final_end)
    base = main.frame_final_end
    before = old.frame_final_duration if old is not None else 0
    if old is not None:
        remove(scene, old)
    ripple(scene, base, frames - before, skip=(main,))
    made = None
    if frames > 0:
        path = element_path(main, base - 1)
        if path:
            made = _still(scene, main, path, main.channel, base, frames, "%s hold" % section, "HOLD", section)
    item = cut.settings(scene, section)
    apply_transition(scene, section, item.transition, item.frames)
    return made


def apply_section(scene, section):
    """The section's settings (hold, transition) on the strips as they are."""
    item = cut.settings(scene, section)
    set_hold(scene, section, item.hold)


def freeze(scene, frame, frames):
    """The image at `frame` stands still for `frames`; the rest of the clip and everything after it moves
    along. Works on frame strips (images); returns the new still strip."""
    found = [s for s in _top_level(scene) if s.type == 'IMAGE' and not s.mute
             and s.frame_final_start <= frame < s.frame_final_end]
    if not found:
        raise ValueError("No frame strip at frame %d" % frame)
    strip = max(found, key=lambda s: s.channel)
    section = _get(strip, SECTION_KEY)
    if section:
        clear_transition(scene, section)
    elements = list(strip.elements)
    index = int(frame - strip.frame_start)
    old_end = strip.frame_final_end
    ripple(scene, frame + 1, frames, skip=(strip,))
    strip.frame_final_end = frame + 1
    path = element_path(strip, frame)
    still = _still(scene, strip, path, strip.channel, frame + 1, frames, "%s freeze %d" % (strip.name, frame),
                   "FREEZE", section)
    if index + 1 < len(elements) and old_end > frame + 1:
        directory = bpy.path.abspath(strip.directory)
        rest = cut._image(_strips(_sed(scene)), "%s %d" % (strip.name, frame + 1),
                          [os.path.join(directory, e.filename) for e in elements[index + 1:]],
                          strip.channel, frame + 1 + frames, fit='ORIGINAL')
        rest.frame_final_end = old_end + frames
        if hasattr(strip, "transform"):
            rest.transform.scale_x, rest.transform.scale_y = strip.transform.scale_x, strip.transform.scale_y
        copy_look(strip, rest)
        if section:
            _tag(rest, "SECTION", section)
    if section:
        item = cut.settings(scene, section)
        apply_transition(scene, section, item.transition, item.frames)
    return still


# ------------------------------------------------------------------ fades

def fade(scene, side, frames, color='BLACK', strip=None):
    """A fade in from (side IN) or out to (OUT) black or white over `frames` of a clip, or of the whole
    film without a clip. frames 0: the fade goes. Returns the colour strip or None."""
    owner = strip.name if strip is not None else "Film"
    for old in _ours(scene, "FADE", owner):
        if _get(old, SIDE_KEY) == side:
            remove(scene, old)
    if frames <= 0:
        return None
    span = (strip.frame_final_start, strip.frame_final_end) if strip is not None else content_range(scene)
    if span is None:
        raise ValueError("Nothing in the Video Editor yet")
    frames = min(frames, span[1] - span[0])
    start = span[0] if side == 'IN' else span[1] - frames
    channel = _free_channel(scene, start, start + frames, max(TOP, (strip.channel + 1) if strip else TOP))
    fx = cut._effect(_strips(_sed(scene)), "%s fade %s" % (owner, side.lower()), 'COLOR', channel, start, frames)
    fx.color = (1.0, 1.0, 1.0) if color == 'WHITE' else (0.0, 0.0, 0.0)
    fx.blend_type = 'ALPHA_OVER'
    _tag(fx, "FADE", owner)
    fx[SIDE_KEY] = side
    keys = ((start, 1.0), (start + frames, 0.0)) if side == 'IN' else ((start, 0.0), (start + frames, 1.0))
    for frame, alpha in keys:
        fx.blend_alpha = alpha
        fx.keyframe_insert("blend_alpha", frame=frame)
    return fx


# ------------------------------------------------------------------ the look

_STRIP = getattr(bpy.types, "Strip", None) or getattr(bpy.types, "Sequence")
LOOK_PROPS = ("studio_brightness", "studio_contrast", "studio_saturation", "studio_warmth", "studio_exposure",
              "studio_shadows", "studio_highlights", "studio_tint")
NEUTRAL = {"studio_saturation": 1.0}


def _modifier(strip, name, kind, create):
    mods = getattr(strip, "modifiers", None)
    if mods is None:
        return None
    mod = mods.get(name)
    if mod is None and create:
        try:
            mod = mods.new(name=name, type=kind)
        except (TypeError, RuntimeError):
            return None
    return mod


def _apply_look(strip):
    cut.quiet(strip)                                 # no read-ahead while the modifiers change
    b, c = strip.studio_brightness, strip.studio_contrast
    mod = _modifier(strip, LOOK_MOD, 'BRIGHT_CONTRAST', bool(b or c))
    if mod is not None:
        mod.bright, mod.contrast = b, c
    if hasattr(strip, "color_saturation"):
        strip.color_saturation = strip.studio_saturation
    w, t = strip.studio_warmth, strip.studio_tint                 # colour: warmer/cooler, magenta/green
    e, lo, hi = strip.studio_exposure, strip.studio_shadows, strip.studio_highlights   # light
    mod = _modifier(strip, COLOR_MOD, 'COLOR_BALANCE', bool(w or t or e or lo or hi))
    if mod is not None:
        light = (2.0 ** e) * (1.0 + 0.5 * hi)                  # exposure in stops, highlights on top
        mod.color_balance.gain = (light * (1.0 + 0.12 * w + 0.05 * t), light * (1.0 + 0.02 * w - 0.10 * t),
                                  light * (1.0 - 0.12 * w + 0.05 * t))
        lift = 1.0 + 0.25 * lo                                  # shadows up (+) or down (−)
        mod.color_balance.lift = (lift, lift, lift)


def _look_changed(strip, _context):
    try:
        _apply_look(strip)
    except (AttributeError, RuntimeError, TypeError) as ex:
        print("AI Studio: look:", ex)


def set_look(strip, brightness=None, contrast=None, saturation=None, warmth=None, exposure=None, shadows=None,
             highlights=None, tint=None):
    for prop, value in zip(LOOK_PROPS, (brightness, contrast, saturation, warmth, exposure, shadows, highlights,
                                        tint)):
        if value is not None:
            setattr(strip, prop, value)
    return look_of(strip)


def look_of(strip):
    return {p[len("studio_"):]: round(getattr(strip, p), 3) for p in LOOK_PROPS}


def copy_look(source, target):
    if source is None or target is None:
        return
    for prop in LOOK_PROPS:
        if getattr(target, prop) != getattr(source, prop):
            setattr(target, prop, getattr(source, prop))


def reset_look(strip):
    for prop in LOOK_PROPS:
        setattr(strip, prop, NEUTRAL.get(prop, 0.0))


def clips(scene):
    """Our section strips, holds and freezes and your own image and movie strips (what a look applies to)."""
    return [s for s in _top_level(scene) if s.type in {'IMAGE', 'MOVIE'} and _get(s, KIND_KEY) not in OVERLAYS]


def look_to_all(scene, source):
    for strip in clips(scene):
        if strip != source:
            copy_look(source, strip)


def film_look(scene, create=True):
    """The adjustment strip over the whole film (its look applies to everything under it), or None."""
    found = _ours(scene, "LOOK")
    if found or not create:
        return found[0] if found else None
    span = content_range(scene)
    if span is None:
        raise ValueError("Nothing in the Video Editor yet")
    channel = _free_channel(scene, span[0], span[1], TOP + 2)
    strip = cut._effect(_strips(_sed(scene)), "Film Look", 'ADJUSTMENT', channel, span[0], span[1] - span[0])
    _tag(strip, "LOOK")
    return strip


RATIOS = (('0', "None", "No bars"), ('2.39', "2.39 : 1", "Cinemascope"), ('2.0', "2 : 1", "Univisium"),
          ('1.85', "1.85 : 1", "Widescreen cinema"))


def letterbox(scene, ratio):
    """Black bars top and bottom for the aspect `ratio` (0: none)."""
    for strip in _ours(scene, "BARS"):
        remove(scene, strip)
    ratio = float(ratio or 0)
    render = scene.render
    width, height = render.resolution_x, render.resolution_y
    if ratio <= 0 or width / ratio >= height:
        return []
    span = content_range(scene)
    if span is None:
        raise ValueError("Nothing in the Video Editor yet")
    bar = (height - width / ratio) / 2
    made = []
    channel = _free_channel(scene, span[0], span[1], TOP + 3)
    for sign, name in ((1, "Letterbox top"), (-1, "Letterbox bottom")):
        strip = cut._effect(_strips(_sed(scene)), name, 'COLOR', channel, span[0], span[1] - span[0])
        strip.color = (0.0, 0.0, 0.0)
        strip.blend_type = 'ALPHA_OVER'
        strip.transform.scale_y = bar / height
        strip.transform.offset_y = sign * (height - bar) / 2
        _tag(strip, "BARS")
        strip["studio_ratio"] = ratio
        made.append(strip)
        channel += 1
    return made


def letterbox_ratio(scene):
    found = _ours(scene, "BARS")
    return float(_get(found[0], "studio_ratio", 0)) if found else 0.0


def title(scene, text, frame=None, length=48, place='CENTER', size=0.07):
    """A text strip at `frame` (default: the playhead): centred or as a lower third."""
    frame = cut.playhead(scene) if frame is None else frame
    channel = _free_channel(scene, frame, frame + length, TOP)
    strip = cut._effect(_strips(_sed(scene)), "Title %s" % text[:20], 'TEXT', channel, frame, length)
    strip.text = text
    strip.font_size = max(8, int(scene.render.resolution_y * size))
    strip.location = (0.5, 0.5) if place == 'CENTER' else (0.5, 0.14)
    if hasattr(strip, "use_shadow"):
        strip.use_shadow = place != 'CENTER'
    _tag(strip, "TITLE")
    return strip


def state(scene):
    """What the Video Editor shows, for Claude: playhead, the active strip, the strips in order."""
    sed = cut._sed_of(scene)
    active = sed.active_strip if sed is not None else None
    strips = []
    for s in sorted(_top_level(scene), key=lambda s: (s.frame_final_start, s.channel)):
        entry = {"name": s.name, "type": s.type, "channel": s.channel, "frames": [s.frame_final_start,
                                                                             s.frame_final_end - 1]}
        if _get(s, KIND_KEY):
            entry["kind"] = _get(s, KIND_KEY)
        if _get(s, SECTION_KEY):
            entry["section"] = _get(s, SECTION_KEY)
        if s.type in {'IMAGE', 'MOVIE', 'ADJUSTMENT'} and any(look_of(s)[k] != v for k, v in
                                                             (("brightness", 0), ("contrast", 0),
                                                              ("saturation", 1), ("warmth", 0))):
            entry["look"] = look_of(s)
        strips.append(entry)
    here = cut.playhead(scene)
    out = {"scene": scene.name, "playhead": here, "strips": strips,
           "active": active.name if active is not None else None}
    if active is not None and active.type == 'IMAGE':
        out["active_3d_frame"] = frame_of(active, here)
    return out


def context_line(scene):
    """A short line about the Video Editor for every message from the cut chat: where the playhead is
    (which section, which 3D frame, how far rendered), what is selected. The user usually means exactly
    that frame or clip ("here the light is too dark")."""
    sed = cut._sed_of(scene)
    active = sed.active_strip if sed is not None else None
    here = cut.playhead(scene)
    sections = {n: (a, b) for n, a, b, _m in cut.sections_of(scene)}
    parts = ["Video Editor of %s (its own scene: its playhead is not the 3D scene's frame), playhead frame %d"
             % (scene.name, here)]
    under = next((s for s in _top_level(scene) if s.type == 'IMAGE' and cut._get(s, cut.SECTION_KEY)
                  and s.frame_final_start <= here < s.frame_final_end and not s.mute), None)
    if under is not None:
        section = cut._get(under, cut.SECTION_KEY)
        shown = frame_of(under, here)
        text = "under the playhead: section %s" % section
        if shown is not None:
            text += ", 3D frame %d (api.section_render(%r, frames=(%d, %d)) renders just it)" % (
                shown, section, shown, shown)
        if section in sections:
            st = cut.frame_status(section, *sections[section])
            text += ", %d/%d frames rendered, %s" % (st["done"], st["total"], {
                'FRESH': "up to date", 'RENDERING': "renders now", 'PENDING': "waits / changed since",
                'OPEN': "not all rendered"}[cut.section_state(scene, section, st)])
        parts.append(text)
    picked = [s for s in _top_level(scene) if s.select and s.type in {'IMAGE', 'MOVIE'}]
    if active is not None and active not in picked:
        picked.insert(0, active)
    if picked:
        words = []
        for strip in picked[:6]:
            section = cut._get(strip, cut.SECTION_KEY)
            word = section or strip.name
            if section in sections:
                word += " (3D frames %d-%d, cut frames %d-%d)" % (sections[section][0], sections[section][1],
                                                                  strip.frame_final_start, strip.frame_final_end - 1)
            words.append(word)
        parts.append("selected: " + ", ".join(words))
    span = content_range(scene)
    if span:
        parts.append("film frames %d-%d" % (span[0], span[1] - 1))
    return "; ".join(parts) + ". Details: api.cut_state()."


def register():
    _STRIP.studio_brightness = FloatProperty(name="Brightness", default=0.0, min=-100.0, max=100.0, soft_min=-40.0,
                                             soft_max=40.0, update=_look_changed)
    _STRIP.studio_contrast = FloatProperty(name="Contrast", default=0.0, min=-100.0, max=100.0, soft_min=-50.0,
                                           soft_max=50.0, update=_look_changed)
    _STRIP.studio_saturation = FloatProperty(name="Saturation", default=1.0, min=0.0, max=20.0, soft_max=2.0,
                                             update=_look_changed)
    _STRIP.studio_warmth = FloatProperty(name="Warmth", description="Cooler (−) or warmer (+)", default=0.0,
                                         min=-1.0, max=1.0, update=_look_changed)
    _STRIP.studio_tint = FloatProperty(name="Tint", description="Greener (−) or more magenta (+)", default=0.0,
                                       min=-1.0, max=1.0, update=_look_changed)
    _STRIP.studio_exposure = FloatProperty(name="Exposure", description="Brighter or darker in stops (+1: twice as "
                                           "bright)", default=0.0, min=-4.0, max=4.0, soft_min=-2.0, soft_max=2.0,
                                           update=_look_changed)
    _STRIP.studio_shadows = FloatProperty(name="Shadows", description="The dark parts lighter (+) or deeper (−)",
                                          default=0.0, min=-1.0, max=1.0, update=_look_changed)
    _STRIP.studio_highlights = FloatProperty(name="Highlights", description="The bright parts brighter (+) or "
                                             "softer (−)", default=0.0, min=-1.0, max=1.0, update=_look_changed)


def unregister():
    for prop in LOOK_PROPS:
        if hasattr(_STRIP, prop):
            delattr(_STRIP, prop)

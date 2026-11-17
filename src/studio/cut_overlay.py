# SPDX-License-Identifier: GPL-3.0-or-later
"""A thin band along the bottom of each section clip in the Video Editor, frame by frame:

  green   rendered in High Quality (new)        orange  Low Quality
  grey    an older frame that is being rendered again
  blue    renders now (not here yet)            red     missing (nothing renders it)
  violet  a draft or the preview image stands in

Worked out at most every 2 s per section (the timeline draws often); drawn with the GPU module."""

import json
import os
import time

import bpy

from . import cut

COLORS = {
    'HIGH': (0.30, 0.80, 0.36, 1.0),
    'LOW': (0.95, 0.60, 0.15, 1.0),
    'OLD': (0.55, 0.55, 0.58, 1.0),
    'RENDERING': (0.25, 0.55, 1.0, 1.0),
    'MISSING': (0.85, 0.22, 0.22, 1.0),
    'DRAFT': (0.62, 0.42, 0.85, 1.0),
}
LABELS = (('HIGH', "HQ"), ('LOW', "LQ"), ('OLD', "old"), ('RENDERING', "renders"), ('MISSING', "missing"),
          ('DRAFT', "draft"))
_cache = {}
_handle = [None]


def _pending(section, folder):
    """{frame: since} of the frames a farm job or a film of this Mac renders now (since: when it was sent)."""
    from . import film, renderfarm
    out = {}
    wanted = os.path.normcase(folder)
    for job in renderfarm.active():
        if job.get("kind") == 'ANIM' and os.path.normcase(job.get("folder", "")) == wanted:
            since = job.get("sent_at") or job.get("added") or 0.0
            for f in range(job["start"], job["end"] + 1):
                out[f] = since
    for entry, st, kind in film.films_here():
        if kind in {'RENDERING', 'ENCODING', 'STUCK'} and os.path.normcase(entry.get("folder", "")) == wanted \
                and not entry.get("draft"):
            try:
                with open(os.path.join(entry["dir"], "job.json"), encoding="utf-8") as f:
                    frames = json.load(f).get("frames") or []
            except (OSError, ValueError, KeyError):
                frames = []
            if len(frames) == 2:
                for f in range(int(frames[0]), int(frames[1]) + 1):
                    out.setdefault(f, entry.get("added", 0.0))
    return out


def states(section, start, end):
    """Per frame of the section: 'HIGH' 'LOW' 'OLD' 'RENDERING' 'MISSING' 'DRAFT'."""
    key = (section, start, end)
    hit = _cache.get(key)
    now = time.monotonic()
    if hit and now - hit[0] < 2.0:
        return hit[1]
    st = cut.frame_status(section, start, end)
    pending = _pending(section, st["folder"])
    out = []
    for f, code, name in zip(range(start, end + 1), st["codes"], st["names"]):
        since = pending.get(f)
        if code in {'HIGH', 'LOW'}:
            if since is not None:
                try:
                    fresh = os.path.getmtime(os.path.join(st["folder"], name)) >= since - 1
                except OSError:
                    fresh = False
                out.append(code if fresh else 'OLD')
            else:
                out.append(code)
        elif since is not None:
            out.append('RENDERING')
        elif code in {'DRAFT', 'POSTER'}:
            out.append('DRAFT')
        else:
            out.append('MISSING')
    _cache[key] = (now, out)
    if len(_cache) > 64:
        _cache.clear()
    return out


def summary(section, start, end):
    """{state: count} of a section (the Studio tab's line)."""
    out = {}
    for s in states(section, start, end):
        out[s] = out.get(s, 0) + 1
    return out


def _runs(strip, per_frame, start):
    """(x0, x1, state) runs of the strip's visible frames."""
    a, b = int(strip.frame_final_start), int(strip.frame_final_end)
    runs = []
    for x in range(a, b):
        index = x - int(strip.frame_start)
        state = per_frame[index] if 0 <= index < len(per_frame) else None
        if state is None:
            continue
        if runs and runs[-1][2] == state and runs[-1][1] == x:
            runs[-1][1] = x + 1
        else:
            runs.append([x, x + 1, state])
    return runs


def _draw():
    context = bpy.context
    space = context.space_data
    if space is None or getattr(space, "view_type", "") not in {'SEQUENCER', 'SEQUENCER_PREVIEW'}:
        return
    try:
        scene = cut.film_scene(getattr(context, "sequencer_scene", None) or context.scene)
        sections = {n: (s, e) for n, s, e, _m in cut.sections_of(scene)}
        strips = [s for s in cut.ours(scene) if s.type == 'IMAGE' and cut._get(s, cut.KIND_KEY) == "SECTION"]
    except (AttributeError, RuntimeError, ReferenceError):
        return
    import gpu
    from gpu_extras.batch import batch_for_shader
    by_color = {}
    for strip in strips:
        section = cut._get(strip, cut.SECTION_KEY)
        span = sections.get(section)
        if span is None or len(strip.elements) != span[1] - span[0] + 1:
            continue
        y0 = strip.channel + 0.06
        y1 = strip.channel + 0.20
        for x0, x1, state in _runs(strip, states(section, span[0], span[1]), span[0]):
            quads = by_color.setdefault(state, [])
            quads.append((x0, y0, x1, y1))
    if not by_color:
        return
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    for state, quads in by_color.items():
        verts, index = [], []
        for x0, y0, x1, y1 in quads:
            n = len(verts)
            verts += [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            index += [(n, n + 1, n + 2), (n, n + 2, n + 3)]
        batch = batch_for_shader(shader, 'TRIS', {"pos": verts}, indices=index)
        shader.uniform_float("color", COLORS[state])
        batch.draw(shader)
    gpu.state.blend_set('NONE')


def register():
    if _handle[0] is None and not bpy.app.background:
        _handle[0] = bpy.types.SpaceSequenceEditor.draw_handler_add(_draw, (), 'WINDOW', 'POST_VIEW')


def unregister():
    if _handle[0] is not None:
        bpy.types.SpaceSequenceEditor.draw_handler_remove(_handle[0], 'WINDOW')
        _handle[0] = None

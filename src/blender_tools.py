# SPDX-License-Identifier: GPL-3.0-or-later
"""Things Claude can do in Blender. All functions run on the main thread."""

import contextlib
import io
import math
import os
import tempfile
import traceback

import bmesh
import bpy
import mathutils

from .i18n import tr


# ---------------------------------------------------------------------------
# Context helpers

def find_view3d():
    """Return (window, area, region) of the largest 3D viewport, or None."""
    best = None
    wm = bpy.context.window_manager
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region is None:
                continue
            if best is None or area.width * area.height > best[1].width * best[1].height:
                best = (window, area, region)
    return best


def view3d_override():
    found = find_view3d()
    if not found:
        return {}
    window, area, region = found
    return {"window": window, "screen": window.screen, "area": area, "region": region}


@contextlib.contextmanager
def viewport_context():
    override = view3d_override()
    if override:
        with bpy.context.temp_override(**override):
            yield
    else:
        yield


# ---------------------------------------------------------------------------
# Scene description

def _fmt(values, digits=3):
    return "(" + ", ".join(f"{v:.{digits}g}" for v in values) + ")"


def _rot_deg(obj):
    if obj.rotation_mode == 'QUATERNION':
        euler = obj.rotation_quaternion.to_euler()
    elif obj.rotation_mode == 'AXIS_ANGLE':
        euler = mathutils.Quaternion(obj.rotation_axis_angle[1:], obj.rotation_axis_angle[0]).to_euler()
    else:
        euler = obj.rotation_euler
    return _fmt([math.degrees(a) for a in euler])


def _view_layer():
    return getattr(bpy.context, "view_layer", None) or bpy.context.scene.view_layers[0]


def _object_line(obj, view_layer):
    parts = [f"- {obj.name} [{obj.type}]"]
    parts.append(f"loc {_fmt(obj.location)}")
    if any(abs(a) > 1e-6 for a in obj.rotation_euler) or obj.rotation_mode != 'XYZ':
        parts.append(f"rot° {_rot_deg(obj)}")
    if any(abs(s - 1) > 1e-6 for s in obj.scale):
        parts.append(f"scale {_fmt(obj.scale)}")
    if obj.type in {'MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'CURVES', 'POINTCLOUD', 'VOLUME'}:
        parts.append(f"dims {_fmt(obj.dimensions)}")
    if obj.parent:
        parts.append(f"parent {obj.parent.name}")
    if obj.modifiers:
        parts.append("mods: " + ", ".join(f"{m.name}({m.type})" for m in obj.modifiers))
    mats = [s.material.name for s in obj.material_slots if s.material]
    if mats:
        parts.append("mats: " + ", ".join(mats))
    flags = []
    if obj.hide_get(view_layer=view_layer):
        flags.append("hidden")
    if obj.hide_render:
        flags.append("no-render")
    if obj.select_get(view_layer=view_layer):
        flags.append("selected")
    if flags:
        parts.append("[" + ", ".join(flags) + "]")
    return " | ".join(parts)


def scene_summary(max_objects=80):
    header, objects, total = scene_summary_parts(max_objects)
    return "\n".join(header + [line for _name, line in objects] + _more_line(total, max_objects))


def _more_line(total, max_objects):
    if total > max_objects:
        return [f"… and {total - max_objects} more (use get_object_info for details)"]
    return []


def scene_summary_parts(max_objects=80):
    """(header lines, [(object name, object line)], object count) – lets callers send only changes."""
    scene = bpy.context.scene
    view_layer = _view_layer()
    unit = scene.unit_settings
    active = view_layer.objects.active
    selected = [o.name for o in view_layer.objects if o.select_get(view_layer=view_layer)]

    lines = [
        f"Blender {bpy.app.version_string} | File: {bpy.data.filepath or '(unsaved)'}",
        f"Scene '{scene.name}' | Engine {scene.render.engine} | "
        f"Resolution {scene.render.resolution_x}x{scene.render.resolution_y} | "
        f"Frame {scene.frame_current} ({scene.frame_start}-{scene.frame_end}) | "
        f"Units {unit.system} (scale {unit.scale_length:g}, {unit.length_unit})",
        f"Mode: {bpy.context.mode} | Active: {active.name if active else '-'} | "
        f"Selected: {', '.join(selected[:20]) or '-'}" + (" …" if len(selected) > 20 else ""),
        f"Camera: {scene.camera.name if scene.camera else '-'} | "
        f"World: {scene.world.name if scene.world else '-'}",
    ]

    if active and active.type == 'MESH' and active.data:
        me = active.data
        lines.append(f"Active mesh '{me.name}': {len(me.vertices)} Verts, {len(me.edges)} Edges, {len(me.polygons)} Faces")

    cols = [c for c in scene.collection.children_recursive]
    if cols:
        lines.append("Collections: " + ", ".join(
            f"{c.name}({len(c.objects)})" + ("[hidden]" if c.hide_viewport else "") for c in cols[:30]))

    objects = list(scene.objects)
    lines.append(f"Objects ({len(objects)}):")
    return lines, [(obj.name, _object_line(obj, view_layer)) for obj in objects[:max_objects]], len(objects)


def _rna_settings(struct, skip=("name", "type", "rna_type", "bl_rna")):
    out = []
    for prop in struct.bl_rna.properties:
        pid = prop.identifier
        if pid in skip or prop.type in {'POINTER', 'COLLECTION'}:
            continue
        try:
            value = getattr(struct, pid)
        except AttributeError:
            continue
        if prop.type == 'FLOAT' and getattr(prop, "array_length", 0):
            value = _fmt(value)
        elif prop.type == 'FLOAT':
            value = f"{value:.4g}"
        elif hasattr(value, "__len__") and not isinstance(value, str):
            value = list(value)
        out.append(f"{pid}={value}")
    return ", ".join(out)


def object_info(name):
    obj = bpy.data.objects.get(name)
    if obj is None:
        close = [o.name for o in bpy.data.objects if name.lower() in o.name.lower()][:10]
        return None, f"No object named '{name}'." + (f" Similar: {', '.join(close)}" if close else "")

    lines = [
        f"Object '{obj.name}' [{obj.type}] data='{obj.data.name if obj.data else '-'}'",
        f"location {_fmt(obj.location)} | rotation° {_rot_deg(obj)} ({obj.rotation_mode}) | scale {_fmt(obj.scale)}",
        f"dimensions {_fmt(obj.dimensions)} | world location {_fmt(obj.matrix_world.translation)}",
        f"parent: {obj.parent.name if obj.parent else '-'} | children: {', '.join(c.name for c in obj.children) or '-'}",
        f"collections: {', '.join(c.name for c in obj.users_collection)}",
        f"display: {obj.display_type} | hide_viewport {obj.hide_viewport} | hide_render {obj.hide_render}",
    ]

    if obj.type == 'MESH':
        me = obj.data
        lines.append(f"Mesh: {len(me.vertices)} verts, {len(me.edges)} edges, {len(me.polygons)} faces, "
                     f"UV maps: {', '.join(uv.name for uv in me.uv_layers) or '-'}, "
                     f"shade smooth: {any(p.use_smooth for p in me.polygons[:200])}")
    elif obj.type == 'CAMERA':
        cam = obj.data
        lines.append(f"Camera: {cam.type}, lens {cam.lens:.3g}mm, sensor {cam.sensor_width:.3g}mm, "
                     f"clip {cam.clip_start:.3g}-{cam.clip_end:.3g}, DoF {cam.dof.use_dof}")
    elif obj.type == 'LIGHT':
        light = obj.data
        lines.append(f"Light: {light.type}, energy {light.energy:.4g}, color {_fmt(light.color)}")
    elif obj.type == 'FONT':
        lines.append(f"Text: {obj.data.body!r}")

    if obj.modifiers:
        lines.append("Modifier:")
        for mod in obj.modifiers:
            lines.append(f"  - {mod.name} ({mod.type}, viewport={mod.show_viewport}): {_rna_settings(mod)[:600]}")

    if obj.material_slots:
        lines.append("Materialien:")
        for i, slot in enumerate(obj.material_slots):
            mat = slot.material
            if not mat:
                lines.append(f"  - Slot {i}: empty")
                continue
            desc = f"  - Slot {i}: {mat.name}"
            if mat.node_tree:
                nodes = mat.node_tree.nodes
                desc += " nodes: " + ", ".join(f"{n.name}[{n.bl_idname}]" for n in nodes[:15])
                bsdf = next((n for n in nodes if n.type == 'BSDF_PRINCIPLED'), None)
                if bsdf:
                    vals = []
                    for key in ("Base Color", "Metallic", "Roughness", "Alpha", "Emission Strength"):
                        sock = bsdf.inputs.get(key)
                        if sock is None:
                            continue
                        if sock.is_linked:
                            vals.append(f"{key}=<linked>")
                        else:
                            v = sock.default_value
                            vals.append(f"{key}={_fmt(v) if hasattr(v, '__len__') else f'{v:.3g}'}")
                    desc += " | Principled: " + ", ".join(vals)
            lines.append(desc)

    props = [k for k in obj.keys() if not k.startswith("_")]
    if props:
        lines.append("Custom Properties: " + ", ".join(f"{k}={obj[k]!r}"[:80] for k in props[:20]))

    if obj.animation_data and obj.animation_data.action:
        lines.append(f"Animation: Action '{obj.animation_data.action.name}'")
    return obj, "\n".join(lines)


# ---------------------------------------------------------------------------
# Code execution

UNDO_MARKER = "_claude_step"  # scene ID property; "_" hides it in the UI


# Values the model's code keeps between execute_python calls of one chat (see _keep).
_kept = {"chat": None, "values": {}}
_kept_live = {"values": {}}     # the same for the Live Connection, independent of the chat


def forget_values():
    """A new or another chat: start with an empty namespace."""
    _kept.update(chat=None, values={})


def _plain(value, depth=0):
    """(True, copy) for values that are safe to keep across undo steps: numbers, strings,
    unwrapped mathutils values, numpy arrays and lists/dicts of them. Blender data
    (objects, materials …) is not: undo frees it, a kept reference could crash Blender."""
    if value is None or isinstance(value, (bool, int, float, complex, str, bytes)):
        return True, value
    if isinstance(value, (mathutils.Vector, mathutils.Matrix, mathutils.Euler, mathutils.Quaternion,
                          mathutils.Color)):
        return True, value.copy()
    if type(value).__module__ == "numpy":
        return True, value
    if depth < 4 and isinstance(value, (list, tuple, set)):
        items = [_plain(v, depth + 1) for v in value]
        if all(ok for ok, _v in items):
            return True, type(value)(v for _ok, v in items)
        return False, None
    if depth < 4 and isinstance(value, dict):
        items = [(_plain(k, depth + 1), _plain(v, depth + 1)) for k, v in value.items()]
        if all(k[0] and v[0] for k, v in items):
            return True, {k[1]: v[1] for k, v in items}
    return False, None


def _keep(namespace, base, store=None):
    """After a call: keep the model's plain values and functions for the next call."""
    import types
    kept, dropped = {}, []
    for name, value in namespace.items():
        if name.startswith("__") or name in base or isinstance(value, types.ModuleType):
            continue
        if isinstance(value, (types.FunctionType, type)):
            kept[name] = value
            continue
        ok, copy = _plain(value)
        if ok:
            kept[name] = copy
        else:
            dropped.append(name)
    (_kept if store is None else store)["values"] = kept
    return dropped


def _base_namespace():
    """Names every call starts with: Blender modules, common imports, skill helpers."""
    import json
    import os
    import random
    import re
    import sys
    import time
    namespace = {
        "__name__": "__claude__",
        "bpy": bpy, "bmesh": bmesh, "mathutils": mathutils, "math": math,
        "os": os, "sys": sys, "re": re, "json": json, "time": time, "random": random,
        "Vector": mathutils.Vector, "Matrix": mathutils.Matrix, "Euler": mathutils.Euler,
        "Quaternion": mathutils.Quaternion, "Color": mathutils.Color,
        "C": bpy.context, "D": bpy.data,
    }
    try:
        import numpy
        namespace["np"] = numpy
    except ImportError:
        pass
    return namespace


def execute_python(code, undo_message="Claude", step=None, max_output=6000, chat=None, live=False):
    """Run code, return (output_text, ok).

    ``step`` is written to the scene as an undo marker before the undo push, so
    the step can later be undone safely (see ``undo_claude_steps``). Plain values and
    functions the code defines stay for the next call of the same ``chat``
    (``live``: for the next call over the Live Connection).
    """
    namespace = _base_namespace()
    try:   # skill helpers: T (checks), M (photo match), V (variants), api (Render Studio)
        from . import skilltools
        namespace.update(skilltools.namespace())
    except Exception as exc:
        print("Claude: skill helpers not available:", exc)
    base = set(namespace)
    if chat is not None and _kept["chat"] != chat:
        forget_values()
        _kept["chat"] = chat
    namespace.update((_kept_live if live else _kept)["values"])
    buffer = io.StringIO()
    ok = True
    wm = bpy.context.window_manager
    windows_before = {w.as_pointer() for w in wm.windows}
    view = bpy.context.preferences.view
    render_display = view.render_display_type
    view.render_display_type = 'NONE'  # renders started by the code must not open a window
    try:
        compiled = compile(code, "<claude>", "exec")
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            with viewport_context():
                namespace["C"] = bpy.context
                exec(compiled, namespace)
    except Exception as exc:
        ok = False
        # Hide this module's own frame, show only the frames of Claude's code.
        tb = exc.__traceback__.tb_next or exc.__traceback__
        buffer.write("\n" + "".join(traceback.format_exception(type(exc), exc, tb)))
    finally:
        view.render_display_type = render_display
        closed = _close_new_windows(windows_before)
        if closed:
            buffer.write(f"\n[Note: the code opened {closed} new window(s); they were closed. Do not open "
                         "windows, editors or dialogs – change data directly.]")
        # One undo step for whatever happened (also for partial changes on error).
        try:
            if step is not None:
                bpy.context.scene[UNDO_MARKER] = step
            with viewport_context():
                bpy.ops.ed.undo_push(message=undo_message[:60])
        except Exception:
            pass
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()

    if live:
        _keep(namespace, base, _kept_live)
    elif chat is not None:
        _keep(namespace, base)
    output = buffer.getvalue().strip()
    if len(output) > max_output:  # long prints eat context; keep the end, where errors are
        output = "… (output truncated)\n" + output[-max_output:]
    if not output:
        output = "OK (no output)" if ok else "Error"
    return output, ok


def _close_new_windows(before):
    """Close Blender windows opened by Claude's code (e.g. window_new, INVOKE_DEFAULT renders)."""
    closed = 0
    for window in list(bpy.context.window_manager.windows):
        if window.as_pointer() in before:
            continue
        try:
            with bpy.context.temp_override(window=window):
                bpy.ops.wm.window_close()
            closed += 1
        except Exception:
            traceback.print_exc()
    return closed


def undo_claude_steps(first_step):
    """Undo Claude's steps back to before ``first_step``.

    Undoes one step at a time and checks the scene marker. If an undo did not
    change the marker, it was a step of the user: that one is redone and the
    function stops. Returns (undone_count, message).
    """
    undone = 0
    for _ in range(200):
        marker = bpy.context.scene.get(UNDO_MARKER, 0)
        if marker < first_step:
            return undone, ""
        with viewport_context():
            if bpy.ops.ed.undo.poll():
                bpy.ops.ed.undo()
            else:
                return undone, tr("Undo is not possible right now.")
        if bpy.context.scene.get(UNDO_MARKER, 0) == marker:
            with viewport_context():
                bpy.ops.ed.redo()
            return undone, tr("You changed something yourself afterwards. Undo your own changes first "
                              "with Ctrl+Z, or use Edit › Undo History.")
        undone += 1
    return undone, ""


def redo_claude_steps(target_step):
    """Redo until the scene marker reaches ``target_step`` (after the steps were undone).
    Returns a message if redo is not possible (e.g. something new was done since)."""
    for _ in range(200):
        marker = bpy.context.scene.get(UNDO_MARKER, 0)
        if marker >= target_step:
            return ""
        with viewport_context():
            if not bpy.ops.ed.redo.poll():
                return tr("Nothing to redo: later versions were replaced by newer changes.")
            bpy.ops.ed.redo()
        if bpy.context.scene.get(UNDO_MARKER, 0) == marker and not bpy.ops.ed.redo.poll():
            return tr("Nothing to redo: later versions were replaced by newer changes.")
    return ""


def selection_info(max_objects=5, max_chars=1200):
    """Details of the selected objects, or '' if nothing is selected."""
    view_layer = _view_layer()
    selected = [o for o in view_layer.objects if o.select_get(view_layer=view_layer)]
    if not selected:
        return ""
    active = view_layer.objects.active
    selected.sort(key=lambda o: o != active)  # active object first
    parts = []
    for obj in selected[:max_objects]:
        _obj, text = object_info(obj.name)
        parts.append(text[:max_chars])
    if len(selected) > max_objects:
        parts.append(f"… and {len(selected) - max_objects} more selected objects: "
                     + ", ".join(o.name for o in selected[max_objects:max_objects + 30]))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Render preview

# ---------------------------------------------------------------------------
# Viewport screenshot

def capture_viewport(max_size=1280):
    """Screenshot of the largest 3D viewport (without the sidebar). Returns PNG bytes."""
    found = find_view3d()
    if not found:
        raise RuntimeError(tr("No 3D viewport found"))
    window, area, region = found

    path = os.path.join(tempfile.gettempdir(), f"claude_viewport_{os.getpid()}.png")
    with bpy.context.temp_override(window=window, screen=window.screen, area=area, region=region):
        bpy.ops.screen.screenshot_area(filepath=path)

    try:
        _crop_and_scale(path, area, max_size)
    except Exception:
        traceback.print_exc()  # fall back to the unmodified screenshot

    with open(path, "rb") as f:
        data = f.read()
    try:
        os.remove(path)
    except OSError:
        pass
    return data


def image_file_png(path, max_size=1280):
    """An image file (PNG/JPEG) as PNG bytes, scaled down to max_size (longest side)."""
    path = os.path.abspath(os.path.expanduser(path))
    if not path.lower().endswith((".png", ".jpg", ".jpeg")) or not os.path.isfile(path):
        raise ValueError("No PNG or JPEG file: " + path)
    image = bpy.data.images.load(path, check_existing=False)
    out = os.path.join(tempfile.gettempdir(), f"claude_image_{os.getpid()}.png")
    try:
        width, height = image.size
        longest = max(width, height, 1)
        if longest > max_size:
            image.scale(max(1, round(width * max_size / longest)), max(1, round(height * max_size / longest)))
        image.filepath_raw = out
        image.file_format = 'PNG'
        image.save()
    finally:
        bpy.data.images.remove(image)
    with open(out, "rb") as f:
        data = f.read()
    os.remove(out)
    return data


def _crop_and_scale(path, area, max_size):
    import numpy as np

    src = bpy.data.images.load(path, check_existing=False)
    try:
        width, height = src.size
        if width == 0 or height == 0:
            return
        scale_x = width / max(area.width, 1)

        # Cut off the sidebar (N panel) on the right – it is drawn over the viewport.
        right = width
        for reg in area.regions:
            if reg.type == 'UI' and reg.width > 1:
                rel_x = reg.x - area.x
                if rel_x > area.width / 2:
                    right = min(right, int(rel_x * scale_x))
        right = max(right, 16)

        new_w, new_h = right, height
        factor = min(1.0, max_size / max(new_w, new_h))
        if right == width and factor >= 1.0:
            return

        pixels = np.empty(width * height * 4, dtype=np.float32)
        src.pixels.foreach_get(pixels)
        pixels = pixels.reshape(height, width, 4)[:, :right, :].copy()
        pixels[:, :, 3] = 1.0

        out = bpy.data.images.new("claude_screenshot", new_w, new_h, alpha=False)
        try:
            out.pixels.foreach_set(pixels.ravel())
            if factor < 1.0:
                out.scale(max(1, int(new_w * factor)), max(1, int(new_h * factor)))
            out.filepath_raw = path
            out.file_format = 'PNG'
            out.save()
        finally:
            bpy.data.images.remove(out)
    finally:
        bpy.data.images.remove(src)

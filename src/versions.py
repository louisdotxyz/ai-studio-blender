# SPDX-License-Identifier: GPL-3.0-or-later
"""Versions: every code step of the model is a version; the timeline moves through them with Blender's
undo/redo. Also code approval."""

import re

import bpy

from . import blender_tools, mcp_server
from .i18n import tr
from . import core


# ---------------------------------------------------------------------------
# Versions: every code step of the model is a version; the timeline moves
# through them with Blender's undo/redo (the scene marker tells where we are).

_VERSION_LINE = re.compile(r"^[ \t]*Version:[ \t]*(.+?)[ \t]*$", re.M | re.I)


def turns():
    """The chat split into turns: a user message and everything until the next one.
    Answers to ask_user (a user message right after a "question") stay in the turn.
    [{"start", "end" (exclusive), "tools": [msg], "steps": [int], "word": str, "last_assistant": index}]"""
    out, cur = [], None
    for i, m in enumerate(core.messages):
        if m["role"] == "user" and not (i and core.messages[i - 1]["role"] == "question"):
            if cur:
                cur["end"] = i
            cur = {"start": i, "end": len(core.messages), "tools": [], "steps": [], "word": "",
                   "last_assistant": -1}
            out.append(cur)
            continue
        if cur is None:
            cur = {"start": i, "end": len(core.messages), "tools": [], "steps": [], "word": "", "last_assistant": -1}
            out.append(cur)
        if m["role"] == "tool":
            cur["tools"].append(m)
            if m.get("step"):
                cur["steps"].append(m["step"])
        elif m["role"] == "assistant":
            cur["last_assistant"] = i
            cur["word"] = m.get("version") or cur["word"]
    for t in out:
        if not t["word"] and t["tools"]:
            t["word"] = _fallback_word(t["tools"])
    return out


def _fallback_word(tools):
    """A one-word name when the model gave none: the first longer word of the first code step."""
    for m in tools:
        if m.get("code"):
            for word in re.findall(r"[\wÄÖÜäöüß-]{4,}", m["text"]):
                return word[:1].upper() + word[1:]
    return tr("Change")


def _take_version_word():
    """Moves "Version: <word>" lines of the answers into the messages (not shown as text)."""
    for msg in core.messages:
        if msg["role"] != "assistant" or "Version:" not in msg["text"] and "version:" not in msg["text"]:
            continue
        found = _VERSION_LINE.findall(msg["text"])
        if found:
            msg["version"] = found[-1].strip(" .\"'")[:20]
            msg["text"] = _VERSION_LINE.sub("", msg["text"]).rstrip()


def versions():
    """One version per turn that changed the scene: [{"step": last step of the turn, "text": word}]."""
    return [{"step": max(t["steps"]), "text": t["word"]} for t in turns() if t["steps"]]


SUMMARY_KINDS = (  # (key, icon, label, tools, keywords in code descriptions)
    ("material", 'MATERIAL', "Material", ("studio_base_material",), r"materi|beiz|farb|colou?r|textur|shader|rauheit|rough|glanz|holz"),
    ("camera", 'CAMERA_DATA', "Camera", ("studio_camera",), r"kamera|camera|\bcam\b|brennweite|focal"),
    ("light", 'LIGHT', "Light", ("studio_light_setup",), r"licht|light|lampe|schatten|shadow"),
    ("world", 'WORLD', "World", ("studio_hdri_setup", "studio_environment_bake"), r"hdri|world|umgebung|himmel|sky"),
    ("render", 'RENDER_STILL', "Render", ("render_preview", "studio_render_queue"), r"render"),
    ("look", 'HIDE_OFF', "Looked", ("get_viewport_screenshot", "get_scene_info", "get_object_info",
                                    "studio_context"), r"$^"),
    ("object", 'MESH_DATA', "Objects", (), r""),
)


def summarize(tools):
    """Counts per area for the icon row under an answer: [(icon, label, count)], steps, undone?"""
    counts = {}
    for m in tools:
        name, text = m.get("tool", ""), m["text"].lower()
        for key, _icon, _label, names, pattern in SUMMARY_KINDS:
            if name in names or (m.get("code") and pattern and re.search(pattern, text)) \
                    or (key == "object" and m.get("code")):
                counts[key] = counts.get(key, 0) + 1
                break
    rows = [(icon, tr(label), counts[key]) for key, icon, label, _n, _p in SUMMARY_KINDS if key in counts]
    steps = sum(1 for m in tools if m.get("step"))
    undone = any(m.get("undone") for m in tools)
    return rows, steps, undone


def current_version():
    try:
        return int(bpy.context.scene.get(blender_tools.UNDO_MARKER, 0))
    except Exception:
        return 0


def goto_version(step):
    """Move the scene to the state right after ``step`` (0 = before the first step)."""
    if core.is_busy():
        return tr("Wait until the model is done.")
    if bpy.context.mode != 'OBJECT':
        return tr("Undo only works in Object Mode.")
    current = current_version()
    problem = ""
    if step < current:
        _count, problem = blender_tools.undo_claude_steps(step + 1)
    elif step > current:
        problem = blender_tools.redo_claude_steps(step)
    now = current_version()
    for m in core.messages:
        if m["role"] == "tool" and m.get("step"):
            m["undone"] = m["step"] > now
    core.history_rev += 1
    if now != current:
        names = ", ".join(m["text"] for m in core.messages if m["role"] == "tool" and m.get("step") == now) or "–"
        core.notes.append(f"The user moved the scene to version {now} ({names}); later steps are undone"
                     if now < current else f"The user restored the scene to version {now} ({names}).")
    core.mark_dirty()
    return problem


def approve(index, accept):
    if not 0 <= index < len(core.pending):
        return
    task = core.pending.pop(index)
    if task.done:
        return
    if accept:
        task.finish(core._run_live_code(task) if task.live else core._run_code_task(task))
    else:
        core.add_message("info", tr("Rejected: {}").format(task.description or tr("Code")))
        task.finish(mcp_server.text_result(
            "The user rejected this code. Do not run it again; ask what they want instead.", True))
    core.mark_dirty()

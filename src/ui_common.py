# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidebar helpers shared by the chat panel and its operators."""

import os
import time

import bpy

from . import attachments as att_mod
from . import core
from .i18n import tr
from . import ui


_ICON_IDS = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------------------------------------------------------------------------
# Helpers

def att_icon(path, kind=None):
    """kwargs for layout.label: image thumbnail if possible, else a type icon."""
    ext = os.path.splitext(path or "")[1].lower()
    is_image = kind == "image" or ext in att_mod.IMAGE_TYPES
    if is_image and ui._previews is not None and os.path.isfile(path):
        try:
            preview = ui._previews[path] if path in ui._previews else ui._previews.load(path, path, 'IMAGE')
            return {"icon_value": preview.icon_id}
        except Exception:
            pass
    return {"icon": att_mod.KIND_ICONS.get(kind or ("image" if is_image else "file"), 'FILE_BLANK')}


def _prop_with_placeholder(layout, data, prop, placeholder):
    try:
        layout.prop(data, prop, text="", placeholder=placeholder)
    except TypeError:  # Blender versions without placeholder support
        layout.prop(data, prop, text="")


def _format_reset(ts):
    lt = time.localtime(ts)
    if ts - time.time() < 20 * 3600:
        return time.strftime("%H:%M", lt)
    return f"{tr(WEEKDAYS[lt.tm_wday])} {time.strftime('%H:%M', lt)}"


SKILLS_PER_ROW = 10
# UI rows besides the chat: title row, input box (prompt, model row, padding), status line, margins
RESERVED_ROWS = 9


def _usage_windows():
    """[(label, fraction_left, reset_ts)] from the last rate limit info."""
    windows = (core.usage or {}).get("unifiedWindows") or {}
    out = []
    for key, label in (("five_hour", tr("Session")), ("seven_day", tr("Week"))):
        window = windows.get(key)
        if not isinstance(window, dict):
            continue
        used = float(window.get("utilization") or 0.0)
        used = used / 100 if used > 1.5 else used  # accept fraction or percent
        reset = window.get("resetsAt")
        if reset and time.time() > reset:
            used, reset = 0.0, None
        out.append((label, max(0.0, min(1.0, 1.0 - used)), reset))
    return out

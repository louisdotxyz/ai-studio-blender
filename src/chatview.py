# SPDX-License-Identifier: GPL-3.0-or-later
"""Chat history drawn as plain labels in a fixed-height area.

- Text is wrapped by its real pixel width (blf), so lines are filled evenly.
- The area always has the same height; it scrolls with mouse wheel/trackpad
  through add-on keymap items in "View2D Buttons List" (the sidebar keymap).
  A permanent modal operator is avoided on purpose: it would block autosave.
"""

import re

import blf
import bpy
from bpy.props import FloatProperty, IntProperty

from . import core
from .i18n import tr
from .preferences import get_prefs

UNIT = 20  # UI_UNIT_Y at scale 1.0
_BULLET = re.compile(r"(•|-|\*|\d+[.)])\s+")

# Scroll state shared by all sidebars: offset = lines hidden below the view.
_scroll = {"offset": 0, "target": 0, "count": 0, "rows": 20, "accum": 0.0}
_cache = {"base_sig": None, "base": [], "tail_sig": None, "tail": []}
_keymaps = []


def ui_scale(context):
    return context.preferences.system.ui_scale or 1.0


# ---------------------------------------------------------------------------
# Wrapping by pixel width

def _setup_font(context):
    try:
        points = context.preferences.ui_styles[0].widget.points
    except (AttributeError, IndexError):
        points = 11
    blf.size(0, points * ui_scale(context))


def _width(text):
    return blf.dimensions(0, text)[0]


def wrap(text, max_px):
    lines = []
    for para in text.split("\n"):
        if not para.strip():
            if lines and lines[-1] != "":
                lines.append("")
            continue
        stripped = para.lstrip()
        indent = para[: len(para) - len(stripped)][:8]
        bullet = _BULLET.match(stripped)
        hang = indent + " " * (len(bullet.group(0)) if bullet else 0)
        cur = indent
        for word in stripped.split(" "):
            candidate = cur + word if not cur.strip() else cur + " " + word
            if _width(candidate) <= max_px:
                cur = candidate
                continue
            if cur.strip():
                lines.append(cur)
                cur = hang + word
            else:
                cur = candidate
            while _width(cur) > max_px and len(cur) > len(hang) + 1:  # very long word
                cut = len(cur) - 1
                while cut > len(hang) + 1 and _width(cur[:cut]) > max_px:
                    cut -= 1
                lines.append(cur[:cut])
                cur = hang + cur[cut:]
        lines.append(cur)
    while lines and lines[-1] == "":
        lines.pop()
    return lines


# ---------------------------------------------------------------------------
# Display lines: (kind, text, first, last, msg_index, path)

def _spacer():
    return ("spacer", "", False, False, -1, "")


def _message_lines(width, icon_width):
    out = []
    msgs = core.messages
    if not msgs:
        if not core.is_busy():
            out.append(("hint", tr("How can I help?"), True, False, -1, ""))
            out.append(_spacer())
            for line in wrap(tr("{} sees your scene and viewport and can control Blender directly. "
                                "Attach images and files with the upload button or drop them onto this sidebar."
                                ).format(core.model_label()), width):
                out.append(("hint", line, False, False, -1, ""))
        return out

    prefs = get_prefs()
    start = max(0, len(msgs) - (prefs.max_display if prefs else 100))
    turns = core.turns()
    turn_end = {t["end"] - 1: t for t in turns}
    last_turn = turns[-1] if turns else None
    prev = None
    for i in range(start, len(msgs)):
        msg = msgs[i]
        role = msg["role"]
        if role == "render":
            continue   # renders of older chats: they are in the Queue tab now
        if role != "tool":
            if out and (role in ("user", "assistant", "question") or prev in ("user", "assistant")):
                out.append(_spacer())
            prev = role
            if role == "user":
                if msg.get("skill"):
                    out.append(("skill", msg["skill"], False, False, i, ""))
                for att in msg.get("attachments") or []:
                    out.append(("att", att["name"], False, False, i, att.get("path", "")))
                lines = wrap(msg["text"], width * 0.78 - UNIT)
            elif role == "assistant":
                lines = wrap(core.plain_text(msg["text"]), width)
            else:  # question / error / info
                lines = wrap(msg["text"], icon_width)
            for n, line in enumerate(lines):
                out.append((role, line, n == 0, n == len(lines) - 1, i, ""))
        turn = turn_end.get(i)
        if turn is None:
            continue
        running = turn is last_turn and core.is_busy()
        if turn["tools"] and not running:
            # What the answer did, as one icon row (click to see the single steps)
            first = msgs[turn["start"]]
            out.append(("summary", "", False, False, turn["start"], ""))
            if first.get("expanded"):
                for m in turn["tools"]:
                    line = wrap(m["text"], icon_width - UNIT)[:1] or [""]
                    out.append(("tool", line[0], m.get("undone", False), False, turn["start"], ""))
        stats = msgs[turn["last_assistant"]].get("stats") if turn["last_assistant"] >= 0 else None
        if stats and not running:
            out.append(("stats", stats, False, False, i, ""))
        if turn is not last_turn:
            out.append(("rule", "", False, False, i, ""))
        prev = None
    return out


def _tail_lines(width, icon_width):
    out = []
    if core.streaming_text.strip():
        out.append(_spacer())
        for line in wrap(core.plain_text(core.streaming_text), width):
            out.append(("stream", line, False, False, -1, ""))
    for msg in core.queued:
        out.append(_spacer())
        for att in msg.get("attachments") or []:
            out.append(("queued", att["name"], False, False, -1, ""))
        for line in wrap(msg["text"], width * 0.8):
            out.append(("queued", line, False, False, -1, ""))
    if core.is_busy() and not core.pending and not core.questions:
        out.append(_spacer())
        status = f"{core.model_label()} · {core.activity_text()} · {core.elapsed()}s"
        if core.queued:
            status += " · " + tr("your message is next")
        out.append(("status", wrap(status, icon_width)[0], True, True, -1, ""))
    return out


def get_lines(context):
    scale = ui_scale(context)
    width = max(80.0, context.region.width - 4.4 * UNIT * scale)  # sidebar tabs, box padding
    icon_width = width - UNIT * scale
    msgs = core.messages
    last = msgs[-1] if msgs else None
    prefs = get_prefs()
    base_sig = (len(msgs), len(last["text"]) if last else 0, width, scale, core.history_rev,
                core.session_id, core.is_busy() and not msgs, prefs.max_display if prefs else 0)
    tail_sig = (len(core.streaming_text), len(core.queued), core.elapsed(), core.is_busy(),
                len(core.pending), len(core.questions), width, scale)
    if base_sig != _cache["base_sig"] or tail_sig != _cache["tail_sig"]:
        _setup_font(context)
        if base_sig != _cache["base_sig"]:
            _cache["base"] = _message_lines(width, icon_width)
            _cache["base_sig"] = base_sig
        _cache["tail"] = _tail_lines(width, icon_width)
        _cache["tail_sig"] = tail_sig
    return _cache["base"] + _cache["tail"]


# ---------------------------------------------------------------------------
# Drawing

def _height(line):
    kind = line[0]
    if kind in ("spacer", "rule"):
        return 0.5
    if kind == "rule_strong":
        return 0.8
    if kind == "assistant":
        return 1.1
    if kind == "user":
        return 1.0 + (0.3 if line[2] else 0) + (0.3 if line[3] else 0)  # box padding
    return 1.0


def draw_chat(layout, context, rows, att_icon):
    lines = get_lines(context)
    count = len(lines)

    # Keep the view still when new lines arrive while scrolled up.
    if _scroll["offset"] and count > _scroll["count"]:
        _scroll["offset"] += count - _scroll["count"]
        _scroll["target"] += count - _scroll["count"]
    _scroll["count"] = count
    _scroll["rows"] = rows

    # Clamp the offset so the top of the chat stays reachable but not beyond.
    total = sum(_height(l) for l in lines)
    if total <= rows:
        _scroll["offset"] = _scroll["target"] = 0
    else:
        # Lines that fit when scrolled to the very top (one row is the "Newest" button).
        fit_top = 0
        h = 0.0
        for line in lines:
            if h + _height(line) > rows - 1:
                break
            h += _height(line)
            fit_top += 1
        max_off = max(0, count - fit_top)
        _scroll["offset"] = min(_scroll["offset"], max_off)
        _scroll["target"] = min(_scroll["target"], max_off)

    offset = _scroll["offset"]
    budget = rows - (1 if offset else 0)
    end = count - offset
    visible, used = [], 0.0
    for line in reversed(lines[:end]):
        if used + _height(line) > budget:
            break
        visible.append(line)
        used += _height(line)
    visible.reverse()

    box = layout.box()   # dark box: readable over the viewport
    scrollable = total > rows
    if scrollable:
        split = box.split(factor=0.955, align=True)
        col = split.column(align=True)
        _draw_scrollbar(split.column(align=True), count, end - len(visible), end, budget)
    else:
        col = box.column(align=True)
    i = 0
    while i < len(visible):
        line = visible[i]
        if line[0] in ("user", "att", "skill"):
            # Your message: one right-aligned box per message (attachments and skills inside).
            j, idx = i, line[4]
            while j < len(visible) and visible[j][0] in ("user", "att", "skill") and visible[j][4] == idx:
                j += 1
            row = col.row()
            row.alignment = 'RIGHT'
            box = row.box().column(align=True)
            for l in visible[i:j]:
                _draw_line(box, l, att_icon)
            i = j
            continue
        _draw_line(col, line, att_icon)
        i += 1

    # Pad to the fixed height.
    rest = budget - used
    while rest >= 1:
        col.label(text="")
        rest -= 1
    if rest > 0.01:
        pad = col.row()
        pad.scale_y = rest
        pad.label(text="")
    if offset:
        row = col.row()
        row.alignment = 'RIGHT'
        row.operator("claude.chat_scroll_bottom", text="Newest", icon='TRIA_DOWN', emboss=False)


def _draw_scrollbar(col, count, first, end, rows):
    """A plain scrollbar right of the chat: one grey thumb for the part you see; clicking the
    empty track above or below it pages up or down."""
    col.scale_x = 0.4
    total = max(count, 1)
    top, bottom = first / total, end / total
    page = bottom - top
    thumb = max(1.0, rows * page)
    above = max(0.0, min(rows - thumb, rows * top))
    below = max(0.0, rows - above - thumb)
    if above >= 0.1:
        cell = col.row(align=True)
        cell.scale_y = above
        cell.operator("claude.chat_scroll_to", text="", emboss=False).fraction = max(0.0, top - page / 2)
    cell = col.row(align=True)
    cell.scale_y = thumb
    cell.operator("claude.chat_scroll_to", text="").fraction = (top + bottom) / 2
    if below >= 0.1:
        cell = col.row(align=True)
        cell.scale_y = below
        cell.operator("claude.chat_scroll_to", text="", emboss=False).fraction = min(1.0, bottom + page / 2)


def _draw_line(col, line, att_icon):
    kind, text, first, last, msg_index, path = line
    row = col.row(align=True)

    def label(target, **kw):  # chat text is content, not UI: never translate it
        target.label(translate=False, **kw)

    if kind == "spacer":
        row.scale_y = 0.5
        label(row, text="")
    elif kind in ("user", "queued"):
        row.alignment = 'RIGHT'
        row.active = kind == "user"
        label(row, text=text)
        if kind == "user" and last:
            op = row.operator("claude.edit_message", text="", icon='GREASEPENCIL', emboss=False)
            op.index = msg_index
    elif kind == "att":
        row.alignment = 'RIGHT'
        label(row, text=text, **att_icon(path))
    elif kind in ("assistant", "stream"):
        row.scale_y = 1.1  # a little more line spacing for the answers
        label(row, text=text)
        if kind == "assistant" and last:
            op = row.operator("claude.copy_message", text="", icon='COPYDOWN', emboss=False)
            op.index = msg_index
    elif kind == "stats":
        row.alignment = 'RIGHT'
        row.active = False
        row.scale_y = 0.9
        label(row, text=text)
    elif kind == "rule":
        col.separator(type='LINE')
    elif kind == "rule_strong":
        col.separator(factor=0.3)
        col.separator(type='LINE')
        col.separator(type='LINE')
        col.separator(factor=0.3)
    elif kind == "question":
        label(row, text=text, icon='QUESTION' if first else 'BLANK1')
    elif kind == "tool":   # a single step of an expanded summary
        row.active = False
        row.separator(factor=2.0)
        label(row, text=text + ("  " + tr("(undone)") if first else ""), icon='DOT')
    elif kind == "summary":
        _draw_summary(row, msg_index)
    elif kind == "skill":
        row.alignment = 'RIGHT'
        row.active = False
        label(row, text=text, icon='SHADERFX')
    elif kind == "error":
        row.alert = True
        label(row, text=text, icon='ERROR' if first else 'BLANK1')
    elif kind == "status":
        row.active = False
        label(row, text=text, icon='SORTTIME')
    elif kind == "hint":
        row.active = not first
        label(row, text=text, icon='LIGHT_SUN' if first else 'NONE')
    else:  # info
        row.active = False
        label(row, text=text, icon='INFO' if first else 'BLANK1')


def _draw_summary(row, start):
    """Icon row: what the answer changed (Material 2 · Camera 1 · …), click to expand."""
    turn = next((t for t in core.turns() if t["start"] == start), None)
    if turn is None:
        return
    rows, steps, undone = core.summarize(turn["tools"])
    expanded = core.messages[start].get("expanded", False)
    row.active = not undone
    op = row.operator("claude.toggle_summary", text="", emboss=False,
                      icon='DISCLOSURE_TRI_DOWN' if expanded else 'DISCLOSURE_TRI_RIGHT')
    op.index = start
    icons = row.row(align=True)
    for icon, text, count in rows:
        icons.label(text="%s %d" % (text, count) if count > 1 else text, icon=icon, translate=False)
    right = row.row()
    right.alignment = 'RIGHT'
    right.active = False
    word = turn["word"] or ""
    label_text = word + (" · " if word and steps else "") + (tr("{} steps").format(steps) if steps else "")
    if undone:
        label_text += " · " + tr("undone")
    right.label(text=label_text, translate=False)


# ---------------------------------------------------------------------------
# Scrolling

SCROLL_TICK = 1 / 60


def scroll(lines, smooth=False):
    """Move the chat by `lines` (positive = older). Smooth: glide there over a few frames."""
    target = max(0, (_scroll["target"] if smooth else _scroll["offset"]) + lines)
    _scroll["target"] = target
    if not smooth or lines <= -10**6 or abs(target - _scroll["offset"]) > 200:
        _scroll["offset"] = target
        core.mark_dirty()
        return
    if not bpy.app.timers.is_registered(_glide):
        bpy.app.timers.register(_glide, first_interval=0.0)


def scroll_to_fraction(fraction):
    """Jump so that `fraction` (0 = oldest, 1 = newest) of the chat is in the middle."""
    count, rows = _scroll["count"], _scroll["rows"]
    middle = fraction * count
    scroll(max(0, round(count - middle - rows / 2)) - _scroll["target"], smooth=True)


def _glide():
    diff = _scroll["target"] - _scroll["offset"]
    if diff == 0:
        return None
    step = max(1, round(abs(diff) * 0.35))   # ease out: fast first, slow at the end
    _scroll["offset"] += step if diff > 0 else -step
    core.mark_dirty()
    core._tag_redraw()
    return SCROLL_TICK


def _in_claude_sidebar(context):
    area, region = context.area, context.region
    if not (area and region and area.type == 'VIEW_3D' and region.type == 'UI'):
        return False
    try:
        return region.active_panel_category == "Claude"
    except (AttributeError, TypeError):
        return True


def _mouse_over_chat(context, event):
    region = context.region
    scale = ui_scale(context)
    _x, view_y = region.view2d.region_to_view(event.mouse_region_x, event.mouse_region_y)
    # Panel content starts at view y = 0 and grows downwards: top row + chat rows.
    chat_bottom = -(2.2 + _scroll["rows"]) * UNIT * scale  # top row + box padding + chat
    return view_y >= chat_bottom


class CLAUDE_OT_chat_scroll(bpy.types.Operator):
    bl_idname = "claude.chat_scroll"
    bl_label = "Scroll Chat"
    bl_options = {'INTERNAL'}

    lines: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        return _in_claude_sidebar(context)

    def invoke(self, context, event):
        if not _mouse_over_chat(context, event):
            return {'PASS_THROUGH'}
        if event.type == 'TRACKPADPAN':
            # Same direction as View2D panning: positive = towards older messages.
            _scroll["accum"] += (event.mouse_prev_y - event.mouse_y) / (UNIT * ui_scale(context))
            steps = int(_scroll["accum"])
            _scroll["accum"] -= steps
        else:
            steps = self.lines
        if steps:
            scroll(steps, smooth=True)
            context.region.tag_redraw()
        return {'FINISHED'}


class CLAUDE_OT_chat_scroll_bottom(bpy.types.Operator):
    bl_idname = "claude.chat_scroll_bottom"
    bl_label = "Jump to Newest Messages"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        scroll(-_scroll["offset"], smooth=True)
        return {'FINISHED'}


class CLAUDE_OT_chat_scroll_to(bpy.types.Operator):
    bl_idname = "claude.chat_scroll_to"
    bl_label = "Scroll To"
    bl_description = "Jump to this part of the chat"
    bl_options = {'INTERNAL'}

    fraction: FloatProperty(default=1.0)

    def execute(self, context):
        scroll_to_fraction(self.fraction)
        return {'FINISHED'}


class CLAUDE_OT_toggle_summary(bpy.types.Operator):
    bl_idname = "claude.toggle_summary"
    bl_label = "Steps"
    bl_description = "Show or hide the single steps of this answer"
    bl_options = {'INTERNAL'}

    index: IntProperty(default=0)

    def execute(self, context):
        if 0 <= self.index < len(core.messages):
            msg = core.messages[self.index]
            msg["expanded"] = not msg.get("expanded", False)
            core.history_rev += 1
            core.mark_dirty()
        return {'FINISHED'}


classes = (CLAUDE_OT_chat_scroll, CLAUDE_OT_chat_scroll_bottom, CLAUDE_OT_chat_scroll_to,
           CLAUDE_OT_toggle_summary)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:  # background mode
        return
    km = kc.keymaps.new(name="View2D Buttons List", space_type='EMPTY', region_type='WINDOW')
    for event_type, value, lines in (("WHEELUPMOUSE", 'PRESS', 2),     # fine: 2 lines per notch
                                     ("WHEELDOWNMOUSE", 'PRESS', -2),
                                     ("TRACKPADPAN", 'ANY', 0)):
        kmi = km.keymap_items.new(CLAUDE_OT_chat_scroll.bl_idname, event_type, value)
        kmi.properties.lines = lines
        _keymaps.append((km, kmi))


def unregister():
    for km, kmi in _keymaps:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _keymaps.clear()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

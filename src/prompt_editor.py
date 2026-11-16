# SPDX-License-Identifier: GPL-3.0-or-later
"""Long messages: the message box of the chat is one line, so a long message opens in a window of its own
(Blender's Text Editor: several lines, word wrap, arrow keys, Cmd+A …). The window and the message box show
the same text (kept in step twice a second while the window is open); "Send" in the window's header sends
it, "Done" closes the window and leaves the text in the message box."""

import bpy
from bpy.types import Operator

from . import core

TEXT_NAME = "Claude Message"
SYNC = 0.5
_last = [None]


def _text():
    return bpy.data.texts.get(TEXT_NAME)


def _windows_showing(text):
    wm = bpy.context.window_manager
    found = []
    for window in (wm.windows if wm else ()):
        for area in window.screen.areas:
            if area.type == 'TEXT_EDITOR' and getattr(area.spaces.active, "text", None) == text:
                found.append((window, area))
    return found


def _set_prompt(value):
    from . import ui_menus
    wm = bpy.context.window_manager
    ui_menus._clearing = True        # not a message being confirmed: never sends by itself
    try:
        wm.claude_prompt = value
    finally:
        ui_menus._clearing = False
    ui_menus.save_draft(value)
    core.mark_dirty()


def _sync():
    """Window ⇄ message box, whichever changed. Stops when no window shows the text any more."""
    text = _text()
    if text is None or not _windows_showing(text):
        _last[0] = None
        return None
    wm = bpy.context.window_manager
    body = text.as_string()
    if body != _last[0]:
        if body != wm.claude_prompt:
            _set_prompt(body)
        _last[0] = body
    elif wm.claude_prompt != _last[0]:
        text.from_string(wm.claude_prompt)
        _last[0] = wm.claude_prompt
    return SYNC


def _close(text):
    for window, _area in _windows_showing(text):
        try:
            with bpy.context.temp_override(window=window):
                bpy.ops.wm.window_close()
        except RuntimeError:
            pass


class CLAUDE_OT_edit_prompt(Operator):
    bl_idname = "claude.edit_prompt"
    bl_label = "Write in a Window"
    bl_description = ("Write the message in a window of its own: several lines, word wrap, arrow keys. "
                      "Send it from the window's header")

    def execute(self, context):
        text = _text() or bpy.data.texts.new(TEXT_NAME)
        text.from_string(context.window_manager.claude_prompt)
        _last[0] = text.as_string()
        if not _windows_showing(text):
            area = context.area
            before = set(w.as_pointer() for w in context.window_manager.windows)
            try:
                with context.temp_override(area=area):
                    bpy.ops.screen.area_dupli('INVOKE_DEFAULT')
            except RuntimeError as ex:
                self.report({'WARNING'}, str(ex))
                return {'CANCELLED'}
            new = [w for w in context.window_manager.windows if w.as_pointer() not in before]
            if not new:
                return {'CANCELLED'}
            target = new[-1].screen.areas[0]
            target.ui_type = 'TEXT_EDITOR'
            space = target.spaces.active
            space.text = text
            space.show_word_wrap = True
            space.show_line_numbers = False
            space.show_syntax_highlight = False
            try:
                lines = text.as_string().split("\n")
                text.cursor_set(len(lines) - 1, character=len(lines[-1]))
            except (AttributeError, TypeError):
                pass
        if not bpy.app.timers.is_registered(_sync):
            bpy.app.timers.register(_sync, first_interval=SYNC)
        return {'FINISHED'}


class CLAUDE_OT_prompt_window(Operator):
    bl_idname = "claude.prompt_window"
    bl_label = "Message Window"
    bl_options = {'INTERNAL'}

    action: bpy.props.EnumProperty(items=(('SEND', "Send", "Send the message to Claude and close the window"),
                                          ('DONE', "Done", "Close the window; the text stays in the message box")),
                                   options={'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        return {"SEND": "Send the message to Claude and close the window",
                "DONE": "Close the window; the text stays in the message box"}.get(props.action, "")

    def execute(self, context):
        text = _text()
        if text is None:
            return {'CANCELLED'}
        body = text.as_string()
        _set_prompt(body)
        if self.action == 'SEND':
            if not body.strip() and not core.attachments:
                self.report({'WARNING'}, "Write a message first")
                return {'CANCELLED'}
            from . import ui_menus
            ui_menus._send_prompt(context.window_manager)
        _last[0] = None
        _close(text)
        bpy.app.timers.register(_remove_text, first_interval=0.2)   # not saved into the .blend
        return {'FINISHED'}


def _remove_text():
    text = _text()
    if text is not None and not _windows_showing(text):
        bpy.data.texts.remove(text)
    return None


def _header(self, context):
    space = context.space_data
    if getattr(getattr(space, "text", None), "name", "") != TEXT_NAME:
        return
    row = self.layout.row(align=True)
    row.scale_x = 1.3
    row.operator(CLAUDE_OT_prompt_window.bl_idname, text="Send", icon='EVENT_RETURN').action = 'SEND'
    row.operator(CLAUDE_OT_prompt_window.bl_idname, text="Done", icon='CHECKMARK').action = 'DONE'


classes = (CLAUDE_OT_edit_prompt, CLAUDE_OT_prompt_window)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TEXT_HT_header.append(_header)


def unregister():
    for timer in (_sync, _remove_text):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    bpy.types.TEXT_HT_header.remove(_header)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

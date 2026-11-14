# SPDX-License-Identifier: GPL-3.0-or-later
"""Menus, the file drop handler and the input box's Enter handling."""

import bpy

from . import attachments as att_mod
from . import chatview, core, lmstudio, skills
from .preferences import get_prefs


# ---------------------------------------------------------------------------
# Menus

class CLAUDE_MT_skills(bpy.types.Menu):
    bl_idname = "CLAUDE_MT_skills"
    bl_label = "Skills"

    def draw(self, context):
        layout = self.layout
        for skill in skills.list_skills(core.workdir()):
            layout.operator("claude.use_skill", text=skill["title"], translate=False).name = skill["name"]
        layout.separator()
        layout.operator("claude.open_path", text="Edit Skills…", icon='FILE_FOLDER').target = "skills"


class CLAUDE_MT_local_models(bpy.types.Menu):
    bl_idname = "CLAUDE_MT_local_models"
    bl_label = "LM Studio"

    def draw(self, context):
        layout = self.layout
        prefs = get_prefs()
        models = lmstudio.list_models()
        if not models:
            layout.label(text="No LM Studio models found", icon='ERROR')
        for m in models:
            current = prefs is not None and prefs.local_model == m["key"]
            text = m["name"] + (f"  ({m['size']})" if m["size"] else "")
            layout.operator("claude.set_local_model", text=text, translate=False,
                            icon='RADIOBUT_ON' if current else 'RADIOBUT_OFF').key = m["key"]
        layout.separator()
        layout.operator("claude.refresh_local_models", icon='FILE_REFRESH')
        layout.operator("claude.open_models_folder", text="Models Folder…", icon='FILE_FOLDER')


class CLAUDE_MT_history(bpy.types.Menu):
    bl_idname = "CLAUDE_MT_history"
    bl_label = "Chats"

    def draw(self, context):
        layout = self.layout
        chats = core.list_chats()
        if not chats:
            layout.label(text="No chats yet")
        for chat in chats[:30]:
            title = chat.get("title") or "Chat"
            title = title if len(title) <= 45 else title[:44] + "…"
            current = chat.get("id") == core.session_id
            op = layout.operator("claude.load_chat", text=title, translate=False,
                                 icon='RADIOBUT_ON' if current else 'RADIOBUT_OFF')
            op.chat_id = chat.get("id", "")
        if core.session_id:
            layout.separator()
            layout.operator("claude.compact_chat", icon='SORTSIZE')
            layout.operator("claude.delete_chat", text="Delete This Chat", icon='TRASH')
        layout.separator()
        row = layout.row()
        row.enabled = bool(bpy.data.filepath)
        row.operator("claude.open_path", text="Project Memory…", icon='BOOKMARKS').target = "project_memory"
        layout.operator("claude.open_path", text="Global Memory…", icon='WORLD').target = "global_memory"
        layout.operator("claude.open_lessons", icon='LIGHT')


# ---------------------------------------------------------------------------
# Drag & drop onto the Claude sidebar

class CLAUDE_FH_attach(bpy.types.FileHandler):
    bl_idname = "CLAUDE_FH_attach"
    bl_label = "Attach to Claude"
    bl_import_operator = "claude.attach_files"
    bl_file_extensions = ";".join(att_mod.ALL_EXT)

    @classmethod
    def poll_drop(cls, context):
        area = getattr(context, "area", None)
        region = getattr(context, "region", None)
        return bool(
            area and area.type == 'VIEW_3D' and region and region.type == 'UI'
            and getattr(region, "active_panel_category", "Claude") == "Claude"
        )


# ---------------------------------------------------------------------------
# Prompt field: Enter sends, clicking elsewhere keeps the text
# Blender applies a text field the same way on Enter and on a click outside.
# The window's last key/mouse event tells them apart: an operator invoked with
# INVOKE_DEFAULT receives it, so it can check for RET.

_clearing = False


class CLAUDE_OT_prompt_confirmed(bpy.types.Operator):
    bl_idname = "claude.prompt_confirmed"
    bl_label = "Prompt Confirmed"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        if event is not None and event.type in {'RET', 'NUMPAD_ENTER'}:
            _send_prompt(context.window_manager)
        return {'FINISHED'}

    def execute(self, context):
        return {'CANCELLED'}  # no event: never send


def _clear_prompt(wm):
    """Empties the message box without it counting as a new message."""
    global _clearing
    _clearing = True
    try:
        wm.claude_prompt = ""
    finally:
        _clearing = False
    core.mark_dirty()


def _send_prompt(wm):
    if core.queue_prompt(wm.claude_prompt):
        _clear_prompt(wm)
        chatview.scroll(-10**9)  # jump to the newest message


def _prompt_update(self, context):
    if _clearing or not self.claude_prompt.strip():
        return
    try:
        bpy.ops.claude.prompt_confirmed('INVOKE_DEFAULT')
    except RuntimeError:
        pass

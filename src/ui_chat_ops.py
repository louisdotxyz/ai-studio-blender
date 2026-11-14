# SPDX-License-Identifier: GPL-3.0-or-later
"""Chat operators: send, stop, chats, approvals, answers, attachments, undo."""

import os

import bpy
from bpy.props import BoolProperty, BoolVectorProperty, CollectionProperty, IntProperty, StringProperty

from . import attachments as att_mod
from . import chatview, core
from .i18n import tip, tr
from .preferences import get_prefs
from . import ui_menus
from .ui_common import _format_reset, _usage_windows


# ---------------------------------------------------------------------------
# Operators

class CLAUDE_OT_send(bpy.types.Operator):
    bl_idname = "claude.send"
    bl_label = "Send"
    bl_description = "Send the message, also while Claude is working"

    def execute(self, context):
        wm = context.window_manager
        if not wm.claude_prompt.strip() and not core.attachments:
            self.report({'WARNING'}, tr("Write a message first"))
            return {'CANCELLED'}
        ui_menus._send_prompt(wm)
        return {'FINISHED'}


class CLAUDE_OT_queue_chat(bpy.types.Operator):
    bl_idname = "claude.queue_chat"
    bl_label = "Queue as New Chat"
    bl_description = ("Send this message as a new chat that starts when the current one is done. "
                      "It keeps the checked skills and the attached files")

    def execute(self, context):
        wm = context.window_manager
        if not wm.claude_prompt.strip() and not core.attachments:
            self.report({'WARNING'}, tr("Write a message first"))
            return {'CANCELLED'}
        if core.queue_chat(wm.claude_prompt):
            ui_menus._clear_prompt(wm)
        return {'FINISHED'}


class CLAUDE_OT_queued_chat_remove(bpy.types.Operator):
    bl_idname = "claude.queued_chat_remove"
    bl_label = "Remove from Queue"
    bl_description = "Take this message out of the queue"
    bl_options = {'INTERNAL'}

    chat_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        core.remove_queued_chat(self.chat_id)
        return {'FINISHED'}


class CLAUDE_OT_queued_chat_edit(bpy.types.Operator):
    bl_idname = "claude.queued_chat_edit"
    bl_label = "Edit Waiting Chat"
    bl_description = "Change this message before it starts: its text, files and skills"
    bl_options = {'INTERNAL'}

    chat_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    text: StringProperty(name="Message", options={'SKIP_SAVE'})
    keep_files: BoolVectorProperty(size=32, default=[True] * 32, options={'SKIP_SAVE'})
    keep_skills: BoolVectorProperty(size=32, default=[True] * 32, options={'SKIP_SAVE'})

    def invoke(self, context, event):
        entry = core.queued_chat(self.chat_id)
        if entry is None:
            return {'CANCELLED'}
        self.text = entry.get("text", "")
        core.hold_queued_chat(self.chat_id)
        return context.window_manager.invoke_props_dialog(self, width=520, confirm_text=tr("Save"))

    def draw(self, context):
        layout = self.layout
        entry = core.queued_chat(self.chat_id)
        layout.prop(self, "text", text="")
        if entry is None:
            layout.label(text=tr("Already started"), icon='INFO')
            return
        files = (entry.get("attachments") or [])[:32]
        skills = (entry.get("skills") or [])[:32]
        if files or skills:
            col = layout.column(align=True)
            for i, att in enumerate(files):
                col.prop(self, "keep_files", index=i, text=att.get("name") or os.path.basename(att.get("path", "")),
                         icon='FILE_IMAGE' if att.get("kind") == "image" else 'FILE', translate=False)
            for i, name in enumerate(skills):
                col.prop(self, "keep_skills", index=i, text=name, icon='BOOKMARKS', translate=False)

    def execute(self, context):
        core.hold_queued_chat(None)
        if not core.edit_queued_chat(self.chat_id, self.text, list(self.keep_files), list(self.keep_skills)):
            self.report({'WARNING'}, tr("This chat has already started"))
            return {'CANCELLED'}
        return {'FINISHED'}

    def cancel(self, context):
        core.hold_queued_chat(None)


class CLAUDE_OT_queued_chat_move(bpy.types.Operator):
    bl_idname = "claude.queued_chat_move"
    bl_label = "Move Up"
    bl_description = "Start this message earlier"
    bl_options = {'INTERNAL'}

    chat_id: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    step: IntProperty(default=-1, options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        core.move_queued_chat(self.chat_id, self.step)
        return {'FINISHED'}


class CLAUDE_OT_chat_queue_pause(bpy.types.Operator):
    bl_idname = "claude.chat_queue_pause"
    bl_label = "Pause Queue"
    bl_description = "Pause or continue the queued chats"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        core.set_chat_queue_paused(not core.chat_queue_paused[0])
        return {'FINISHED'}


class CLAUDE_OT_stop(bpy.types.Operator):
    bl_idname = "claude.stop"
    bl_label = "Stop"
    bl_description = "Stop Claude"

    def execute(self, context):
        core.stop()
        return {'FINISHED'}


class CLAUDE_OT_new_chat(bpy.types.Operator):
    bl_idname = "claude.new_chat"
    bl_label = "New Chat"
    bl_description = "Start a new chat (the current one stays in the history)"

    @classmethod
    def poll(cls, context):
        return not core.is_busy()

    def execute(self, context):
        core.new_chat()
        chatview.scroll(-10**9)
        return {'FINISHED'}


class CLAUDE_OT_load_chat(bpy.types.Operator):
    bl_idname = "claude.load_chat"
    bl_label = "Open Chat"
    bl_description = "Open this chat and continue it"

    chat_id: StringProperty(options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        return not core.is_busy()

    def execute(self, context):
        core.load_chat(self.chat_id)
        chatview.scroll(-10**9)
        return {'FINISHED'}


class CLAUDE_OT_delete_chat(bpy.types.Operator):
    bl_idname = "claude.delete_chat"
    bl_label = "Delete Chat"
    bl_description = "Delete the current chat from the history"

    @classmethod
    def poll(cls, context):
        return not core.is_busy() and core.session_id is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        core.delete_chat(core.session_id)
        return {'FINISHED'}


class CLAUDE_OT_compact_chat(bpy.types.Operator):
    bl_idname = "claude.compact_chat"
    bl_label = "Summarize Chat"
    bl_description = "Summarize older parts of this chat so it uses less context (faster, avoids context errors)"

    @classmethod
    def description(cls, context, properties):
        fraction = core.context_fraction()
        head = tr("Context of this chat: {}% used").format(round(fraction * 100)) + "\n" if fraction else ""
        return head + tip(cls.bl_description)

    @classmethod
    def poll(cls, context):
        return not core.is_busy() and core.session_id is not None

    def execute(self, context):
        core.compact()
        return {'FINISHED'}


class CLAUDE_OT_approve(bpy.types.Operator):
    bl_idname = "claude.approve"
    bl_label = "Run Code"
    bl_description = "Run the code (undo with Ctrl+Z) or reject it"

    index: IntProperty(default=0)
    accept: BoolProperty(default=True)

    def execute(self, context):
        core.approve(self.index, self.accept)
        return {'FINISHED'}


class CLAUDE_OT_show_code(bpy.types.Operator):
    bl_idname = "claude.show_code"
    bl_label = "Show Full Code"
    bl_description = "Open the code in the Text Editor (text 'Claude_Code.py')"

    index: IntProperty(default=0)

    def execute(self, context):
        if not 0 <= self.index < len(core.pending):
            return {'CANCELLED'}
        task = core.pending[self.index]
        text = bpy.data.texts.get("Claude_Code.py") or bpy.data.texts.new("Claude_Code.py")
        text.clear()
        text.write(f"# {task.description}\n{task.code}\n")
        for area in context.screen.areas:
            if area.type == 'TEXT_EDITOR':
                area.spaces.active.text = text
                return {'FINISHED'}
        bpy.ops.wm.window_new()
        area = bpy.context.window_manager.windows[-1].screen.areas[0]
        area.type = 'TEXT_EDITOR'
        area.spaces.active.text = text
        return {'FINISHED'}


class CLAUDE_OT_answer(bpy.types.Operator):
    bl_idname = "claude.answer"
    bl_label = "Answer"
    bl_description = "Send this answer to Claude"

    index: IntProperty(default=0)
    option: IntProperty(default=-1)   # -1 = send the multi-selection

    def execute(self, context):
        if not 0 <= self.index < len(core.questions):
            return {'CANCELLED'}
        task = core.questions[self.index]
        if self.option >= 0 and task.multi_select:
            core.toggle_option(self.index, self.option)
            return {'FINISHED'}
        if self.option >= 0:
            answer = task.options[self.option]
        else:
            answer = ", ".join(task.options[i] for i in sorted(task.selected))
        core.answer_question(self.index, answer)
        return {'FINISHED'}


class CLAUDE_OT_copy_message(bpy.types.Operator):
    bl_idname = "claude.copy_message"
    bl_label = "Copy"
    bl_description = "Copy the answer to the clipboard"

    index: IntProperty(default=-1)

    def execute(self, context):
        if not 0 <= self.index < len(core.messages):
            return {'CANCELLED'}
        context.window_manager.clipboard = core.messages[self.index]["text"]
        self.report({'INFO'}, tr("Copied"))
        return {'FINISHED'}


class CLAUDE_OT_attach_files(bpy.types.Operator):
    bl_idname = "claude.attach_files"
    bl_label = "Upload"
    bl_description = "Attach images, PDFs, text or 3D files to the message (opens the folder from the settings)"

    filepath: StringProperty(subtype='FILE_PATH', options={'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE'})
    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={'SKIP_SAVE'})

    def invoke(self, context, event):
        if self.directory and len(self.files):  # drag & drop
            return self.execute(context)
        prefs = get_prefs()
        start = os.path.expanduser(bpy.path.abspath(prefs.attach_folder if prefs else "") or "~/Downloads")
        if os.path.isdir(start):
            self.directory = start.rstrip(os.sep) + os.sep
            self.filepath = start.rstrip(os.sep) + os.sep
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        paths = [os.path.join(self.directory, f.name) for f in self.files if f.name]
        if not paths and self.filepath:
            paths = [self.filepath]
        added = 0
        for path in paths:
            try:
                core.add_attachment(path)
                added += 1
            except Exception as exc:
                self.report({'WARNING'}, str(exc))
        return {'FINISHED'} if added else {'CANCELLED'}


class CLAUDE_OT_paste(bpy.types.Operator):
    bl_idname = "claude.paste_clipboard"
    bl_label = "Paste from Clipboard"
    bl_description = "Attach an image or a copied file from the clipboard"

    def execute(self, context):
        try:
            paths = att_mod.paste_from_clipboard(os.path.join(core.workdir(), "attachments"))
        except Exception as exc:
            self.report({'WARNING'}, str(exc))
            return {'CANCELLED'}
        for path in paths:
            core.add_attachment(path)
        return {'FINISHED'}


class CLAUDE_OT_remove_attachment(bpy.types.Operator):
    bl_idname = "claude.remove_attachment"
    bl_label = "Remove"
    bl_description = "Remove the attachment"

    index: IntProperty(default=0)

    def execute(self, context):
        core.remove_attachment(self.index)
        return {'FINISHED'}


class CLAUDE_OT_usage_info(bpy.types.Operator):
    bl_idname = "claude.usage_info"
    bl_label = "Usage"
    bl_options = {'INTERNAL'}

    @classmethod
    def description(cls, context, properties):
        lines = [tr("{}: {}% left").format(label, round(left * 100))
                 + (tr(", resets {}").format(_format_reset(reset)) if reset else "")
                 for label, left, reset in _usage_windows()]
        lines.append(tip("Updated with every answer from Claude"))
        return "\n".join(lines)

    def execute(self, context):
        return {'FINISHED'}


class CLAUDE_OT_undo_steps(bpy.types.Operator):
    bl_idname = "claude.undo_steps"
    bl_label = "Undo"
    bl_description = "Undo these changes by Claude (and everything Claude changed after them)"

    first_step: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        return not core.is_busy()

    def execute(self, context):
        problem = core.undo_from(self.first_step)
        if problem:
            self.report({'WARNING'}, problem)
        return {'FINISHED'}

# SPDX-License-Identifier: GPL-3.0-or-later
"""Files and models: open paths, local models, backups, crash recovery, editing messages."""

import os
import time

import bpy
from bpy.props import IntProperty, StringProperty

from . import autosave, core, lmstudio, memory, skills
from .i18n import tip, tr
from .preferences import get_prefs
from . import ui_menus


class CLAUDE_OT_open_path(bpy.types.Operator):
    bl_idname = "claude.open_path"
    bl_label = "Open"
    bl_description = "Open in the file browser or default editor"

    target: StringProperty(options={'SKIP_SAVE'})  # skills / project_memory / global_memory

    @classmethod
    def description(cls, context, properties):
        return tip({"skills": "Open the skills folder: edit skills as Markdown files or add new ones",
                    "project_memory": "Open this project's memory (Markdown next to the .blend file)",
                    "global_memory": "Open the memory for all projects (Markdown)"}.get(properties.target, ""))

    def execute(self, context):
        workdir = core.workdir()
        if self.target == "skills":
            path = skills.skills_dir(workdir)
        elif self.target == "project_memory":
            path = memory.project_path()
            if path is None:
                self.report({'WARNING'}, tr("Save the .blend file first"))
                return {'CANCELLED'}
            memory.ensure_file(path, memory.PROJECT_HEADER.format(name=os.path.basename(bpy.data.filepath)))
        else:
            path = memory.ensure_file(memory.global_path(workdir), memory.GLOBAL_HEADER)
        bpy.ops.wm.path_open(filepath=path)
        return {'FINISHED'}


class CLAUDE_OT_set_local_model(bpy.types.Operator):
    bl_idname = "claude.set_local_model"
    bl_label = "Use Local Model"
    bl_description = "Use this LM Studio model (loaded automatically when you send)"

    key: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        prefs = get_prefs()
        if prefs:
            prefs.local_model = self.key
            context.preferences.is_dirty = True
            if prefs.backend == 'LOCAL':
                lmstudio.preload(self.key, prefs.local_context)
        core.mark_dirty()
        return {'FINISHED'}


class CLAUDE_OT_refresh_local_models(bpy.types.Operator):
    bl_idname = "claude.refresh_local_models"
    bl_label = "Refresh Model List"
    bl_description = "Read the list of models from LM Studio again"

    def execute(self, context):
        if not lmstudio.list_models(refresh=True):
            self.report({'WARNING'}, tr("No LM Studio models found (~/.lmstudio/models)"))
        core.mark_dirty()
        return {'FINISHED'}


class CLAUDE_OT_local_load(bpy.types.Operator):
    bl_idname = "claude.local_load"
    bl_label = "Load Model"
    bl_description = "Load the chosen model in LM Studio now, so the first answer comes faster"

    def execute(self, context):
        prefs = get_prefs()
        if prefs and prefs.local_model:
            lmstudio.preload(prefs.local_model, prefs.local_context)
        return {'FINISHED'}


class CLAUDE_OT_local_unload(bpy.types.Operator):
    bl_idname = "claude.local_unload"
    bl_label = "Unload Model"
    bl_description = "Unload the model from LM Studio to free its memory (loaded again with the next message)"

    @classmethod
    def poll(cls, context):
        return not core.is_busy()

    def execute(self, context):
        prefs = get_prefs()
        if prefs and prefs.local_model:
            lmstudio.unload_async(prefs.local_model)
        return {'FINISHED'}


class CLAUDE_OT_autosave_now(bpy.types.Operator):
    bl_idname = "claude.autosave_now"
    bl_label = "Save Backup Now"
    bl_description = "Auto save: click to save a backup right now"

    @classmethod
    def description(cls, context, properties):
        st = autosave.state
        if st["error"]:
            return tr("Auto save failed: {}").format(st["error"][:200])
        if st["path"]:
            return tr("Last backup {} ({}): {}").format(
                time.strftime("%H:%M", time.localtime(st["last"])), st["reason"],
                os.path.basename(st["path"])) + "\n" + tip(cls.bl_description)
        return tip(cls.bl_description)

    def execute(self, context):
        path = autosave.save_now("manual", force=True)
        if path:
            self.report({'INFO'}, tr("Saved: {}").format(path))
            return {'FINISHED'}
        self.report({'WARNING'}, autosave.state["error"] or tr("Not saved (a render is running)"))
        return {'CANCELLED'}


class CLAUDE_OT_open_backups(bpy.types.Operator):
    bl_idname = "claude.open_backups"
    bl_label = "Open Backups"
    bl_description = "Open the folder with the auto save copies (File › Open to restore one)"

    def execute(self, context):
        folder = autosave.backup_folder()
        os.makedirs(folder, exist_ok=True)
        bpy.ops.wm.path_open(filepath=folder)
        return {'FINISHED'}


class CLAUDE_OT_open_file(bpy.types.Operator):
    bl_idname = "claude.open_file"
    bl_label = "Open"
    bl_description = "Open the file in the default app"

    path: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        if not os.path.exists(self.path):
            self.report({'WARNING'}, tr("File not found"))
            return {'CANCELLED'}
        bpy.ops.wm.path_open(filepath=self.path)
        return {'FINISHED'}


class CLAUDE_OT_version_goto(bpy.types.Operator):
    bl_idname = "claude.version_goto"
    bl_label = "Go to Version"
    bl_description = "Move the scene back or forward to this version (the model's steps are undone or redone)"

    step: IntProperty(default=0)

    @classmethod
    def poll(cls, context):
        return not core.is_busy()

    def execute(self, context):
        problem = core.goto_version(self.step)
        if problem:
            self.report({'WARNING'}, problem)
        return {'FINISHED'}


class CLAUDE_OT_crash_open_backup(bpy.types.Operator):
    bl_idname = "claude.crash_open_backup"
    bl_label = "Open Last Backup"
    bl_description = "Open the last auto save copy from before the crash (the current file is replaced in Blender)"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        crash = autosave.last_crash or {}
        path = crash.get("backup")
        if not path or not os.path.isfile(path):
            self.report({'WARNING'}, tr("No backup found"))
            return {'CANCELLED'}
        autosave.dismiss_crash()
        bpy.ops.wm.open_mainfile(filepath=path)
        return {'FINISHED'}


class CLAUDE_OT_save_as_original(bpy.types.Operator):
    bl_idname = "claude.save_as_original"
    bl_label = "Save as Original"
    bl_description = ("Save the open file as the main file <name>_now.blend. "
                      "The file that was there becomes a backup in autosave, nothing is deleted")

    @classmethod
    def poll(cls, context):
        return autosave.original_path() is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title=tr("Make this the main file?"),
            message=tr("Saves as {}, the previous version becomes a backup").format(
                os.path.basename(autosave.original_path() or "")),
            confirm_text=tr("Save as Main File"))

    def execute(self, context):
        try:
            path = autosave.make_main()
        except Exception as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        if not path:
            return {'CANCELLED'}
        self.report({'INFO'}, tr("Saved: {}").format(path))
        return {'FINISHED'}


class CLAUDE_OT_make_project_folder(bpy.types.Operator):
    bl_idname = "claude.make_project_folder"
    bl_label = "Make Project Folder"
    bl_description = ("Give this file its own folder <name>/ with autosave, assets and archiv and save it "
                      "there as <name>_now.blend. Its backups and project memory move along, nothing is deleted")

    @classmethod
    def poll(cls, context):
        return autosave.project_folder_target() is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(
            self, event, title=tr("Make a project folder?"),
            message=tr("Saves the file as {}/{}_now.blend; backups and memory move along").format(
                os.path.basename(autosave.project_folder_target() or ""),
                autosave.project_name(bpy.data.filepath)),
            confirm_text=tr("Make Project Folder"))

    def execute(self, context):
        try:
            path = autosave.move_to_project_folder()
        except Exception as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        self.report({'INFO'}, tr("Saved: {}").format(path))
        return {'FINISHED'}


class CLAUDE_OT_open_crash_log(bpy.types.Operator):
    bl_idname = "claude.open_crash_log"
    bl_label = "Crash Details"
    bl_description = ("Open the crash trace, the newest freeze report and the render queue logs "
                      "(Claude can read them too: just say that Blender froze)")
    bl_options = {'INTERNAL'}

    def execute(self, context):
        freezes = autosave.freeze_reports()[:1]
        for path in [os.path.join(core.workdir(), "crash_trace.txt"),
                     os.path.join(core.workdir(), "studio", "queue.log"),
                     os.path.join(core.workdir(), "studio", "jobs", "worker.log")] + freezes:
            if os.path.exists(path):
                bpy.ops.wm.path_open(filepath=path)
        return {'FINISHED'}


class CLAUDE_OT_crash_dismiss(bpy.types.Operator):
    bl_idname = "claude.crash_dismiss"
    bl_label = "Dismiss"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        autosave.dismiss_crash()
        core.mark_dirty()
        return {'FINISHED'}


class CLAUDE_OT_open_lessons(bpy.types.Operator):
    bl_idname = "claude.open_lessons"
    bl_label = "Lessons…"
    bl_description = "Open the lessons the models learned from their errors (Obsidian project folder)"

    def execute(self, context):
        path = core.lessons_root()
        os.makedirs(path, exist_ok=True)
        bpy.ops.wm.path_open(filepath=path)
        return {'FINISHED'}


class CLAUDE_OT_open_models_folder(bpy.types.Operator):
    bl_idname = "claude.open_models_folder"
    bl_label = "Open Models Folder"
    bl_description = "Open the LM Studio models folder (~/.lmstudio/models)"

    def execute(self, context):
        bpy.ops.wm.path_open(filepath=lmstudio.MODELS_DIR)
        return {'FINISHED'}


class CLAUDE_OT_edit_message(bpy.types.Operator):
    bl_idname = "claude.edit_message"
    bl_label = "Edit"
    bl_description = "Load this message into the input field to edit and resend it"

    index: IntProperty(default=-1)

    def execute(self, context):
        if not 0 <= self.index < len(core.messages):
            return {'CANCELLED'}
        msg = core.messages[self.index]
        ui_menus._clearing = True  # loading text for editing must not send it
        try:
            context.window_manager.claude_prompt = msg["text"]
        finally:
            ui_menus._clearing = False
        for att in msg.get("attachments") or []:
            if os.path.isfile(att.get("path", "")):
                core.add_attachment(att["path"])
        if msg.get("skill"):
            titles = [t.strip() for t in msg["skill"].split(",")]
            core.active_skills[:] = [s["name"] for s in skills.list_skills(core.workdir())
                                     if s["title"] in titles]
        core.mark_dirty()
        return {'FINISHED'}

# SPDX-License-Identifier: GPL-3.0-or-later
"""The Claude sidebar panels: settings popover and the chat panel."""

import os
import time

import bpy

from . import autosave, chatview, core, lmstudio, setup_flow, skills
from .i18n import tr
from .preferences import get_prefs
from .ui_common import (
    RESERVED_ROWS, SKILLS_PER_ROW, _ICON_IDS, _format_reset, _prop_with_placeholder, _usage_windows,
    att_icon
)
from .ui_skills import skill_operator


# ---------------------------------------------------------------------------
# Panel

class CLAUDE_PT_settings(bpy.types.Panel):
    """Gear menu of the Claude tab: what is sent, chat view, auto save, learning."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Chat Settings"
    bl_ui_units_x = 13

    def draw(self, context):
        layout = self.layout
        prefs = get_prefs()
        if prefs is None:
            return
        box = layout.box()
        box.label(text="Sent With Every Message", icon='EXPORT')
        col = box.column(align=True)
        col.prop(prefs, "send_scene_context", toggle=True, icon='SCENE_DATA')
        col.prop(prefs, "auto_screenshot", toggle=True, icon='RESTRICT_VIEW_OFF')
        col.prop(prefs, "send_selection", toggle=True, icon='RESTRICT_SELECT_OFF')

        box = layout.box()
        box.label(text="Claude or Local Model", icon='WORLD')
        box.row(align=True).prop(prefs, "backend", expand=True)

        box = layout.box()
        box.label(text="New Chats Start With", icon='OUTLINER_DATA_LIGHTPROBE')
        row = box.row(align=True)
        row.prop(prefs, "default_model", text="")
        sub = row.row(align=True)
        sub.enabled = prefs.default_model != 'LOCAL'
        sub.prop(prefs, "default_effort", text="")

        box = layout.box()
        box.label(text="Chat", icon='TEXT')
        col = box.column(align=True)
        col.prop(prefs, "show_stats", toggle=True, icon='INFO')
        row = col.row(align=True)
        row.prop(prefs, "auto_compact", text="Auto Summarize", toggle=True, icon='SORTSIZE')
        sub = row.row(align=True)
        sub.active = prefs.auto_compact
        sub.prop(prefs, "compact_at", text="")
        col.prop(prefs, "learn_from_errors", toggle=True, icon='LIGHT')

        box = layout.box()
        box.label(text="Auto Save", icon='FILE_TICK')
        col = box.column(align=True)
        col.prop(prefs, "autosave", toggle=True, icon='CHECKMARK')
        sub = col.column(align=True)
        sub.active = prefs.autosave
        sub.prop(prefs, "autosave_minutes")
        sub.prop(prefs, "autosave_before_risky", toggle=True, icon='SCRIPT')
        layout.operator("preferences.addon_show", text="All Settings…", icon='PREFERENCES').module = __package__


def _wrap_text(text, width):
    import textwrap
    return textwrap.wrap(text, width) or [text]


def _setup_steps_open(context, prefs, state=None):
    """Online access, Claude Code, login: until the chat worked once, or when one goes missing."""
    if prefs is None:
        return False
    if context.window_manager.claude_show_setup or setup_flow.running():
        return True
    if prefs.backend == 'LOCAL':
        return False
    state = state or core.setup_state()
    return (not prefs.setup_done or not state["cli"] or state["online"] != 'ON'
            or state["login"] is False)


def _setup_questions_open(context, prefs):
    return prefs is not None and (not prefs.setup_asked or context.window_manager.claude_show_setup)


def _setup_visible(context, prefs):
    """The setup card: its steps until the chat works, its questions until they were answered,
    everything again from the ?."""
    return _setup_steps_open(context, prefs) or _setup_questions_open(context, prefs)


class CLAUDE_PT_chat(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Claude"
    bl_label = "Claude"
    bl_options = {'HIDE_HEADER'}

    def draw(self, context):
        layout = self.layout
        prefs = get_prefs()
        scale = chatview.ui_scale(context)
        unit = chatview.UNIT * scale

        # Top: chat history + new chat, right aligned.
        # Top: [chat title ........] [history] [+]
        top = layout.row()
        _prop_with_placeholder(top, context.window_manager, "claude_chat_title", tr("New Chat"))
        top.menu("CLAUDE_MT_history", text="", icon='RECOVER_LAST')
        top.operator("claude.new_chat", text="", icon='ADD')
        top.popover("CLAUDE_PT_settings", text="", icon='PREFERENCES')
        top.popover("CLAUDE_PT_help", text="", icon='QUESTION')

        # Chat area: fills what the cards, the input and the status line leave free.
        skill_rows = len(skills.list_skills(core.workdir())) // SKILLS_PER_ROW + 1
        reserved = RESERVED_ROWS + len(core.attachments) + skill_rows + self._cards_height()
        if prefs is not None and prefs.backend == 'LOCAL':
            reserved += 1   # local model status has two lines
        rows = int(max(6, context.region.height / unit - reserved))
        chatview.draw_chat(layout, context, rows, att_icon)

        self._draw_crash(layout, context)
        self._draw_timeline(layout, context)
        self._draw_setup(layout, context, prefs)
        self._draw_login(layout)
        self._draw_actions(layout)
        self._draw_live(layout)
        self._draw_render(layout)
        self._draw_questions(layout, context)
        self._draw_pending(layout, context)
        self._draw_input(layout, context, prefs)
        # One status line (context, usage, last backup) on a box, readable over any viewport.
        info = layout.box()
        info.scale_y = 0.9
        self._draw_status(info, prefs)

    @staticmethod
    def _cards_height():
        h = 0
        if autosave.last_crash:
            h += 4
        if autosave.project_folder_target():
            h += 2.5 if autosave.in_copy() else 1.8
        elif autosave.original_path():
            h += 2.5 if autosave.in_copy() else 1.2
        if core.versions():
            h += 1.4
        prefs = get_prefs()
        if _setup_visible(bpy.context, prefs):
            h += 2.5
            h += 7 if _setup_steps_open(bpy.context, prefs) else 0
            h += 8 if _setup_questions_open(bpy.context, prefs) else 0
        elif core.login_needed:
            h += 2
        if core.live_status():
            h += 1
        from .studio import runner
        if runner.RUN.busy:
            h += 1
        if core.update_needed or core.local_down:
            h += 2
        from . import updates
        if updates.state["available"] and updates.mode() == 'ASK':
            h += 1.5
        for task in core.questions:
            h += 4 + len(task.options) + (1 if task.multi_select else 0)
        for task in core.pending:
            h += 5 + min(10, len(task.code.splitlines())) * 0.7
        return h

    # -- crash notice and version timeline ---------------------------------

    def _draw_crash(self, layout, context):
        original = autosave.original_path()
        target = autosave.project_folder_target()
        if target:
            # Older files without their own folder: one click moves them into <name>/.
            box = layout.box()
            if autosave.in_copy():
                row = box.row()
                row.alert = True
                row.label(text=tr("You are working in a backup copy"), icon='FILE_BACKUP')
            box.operator("claude.make_project_folder", icon='NEWFOLDER',
                         text=tr("Make project folder {}/").format(os.path.basename(target)))
        elif original and autosave.in_copy():
            box = layout.box()
            row = box.row()
            row.alert = True
            row.label(text=tr("You are working in a backup copy"), icon='FILE_BACKUP')
            box.operator("claude.save_as_original", icon='FILE_TICK',
                         text=tr("Save as {}").format(os.path.basename(original)))
        elif original:
            # The main file should be called <name>_now.blend (older versions go to archiv/).
            layout.operator("claude.save_as_original", icon='FILE_TICK',
                            text=tr("Save as {}").format(os.path.basename(original)))
        crash = autosave.last_crash
        if not crash:
            return
        box = layout.box()
        box.alert = True
        box.label(text=tr("Blender did not quit cleanly last time (crash?)"), icon='ERROR')
        box.alert = False
        col = box.column(align=True)
        col.active = False
        if crash.get("step"):
            col.label(text=tr("Last step: {}").format(crash["step"])[:70], translate=False)
        backup = crash.get("backup")
        if backup:
            col.label(text=tr("Backup: {}").format(os.path.basename(backup)), translate=False)
        row = box.row(align=True)
        if backup and os.path.isfile(backup):
            row.operator("claude.crash_open_backup", icon='FILE_BACKUP')
        row.operator("claude.open_crash_log", text="", icon='TEXT')
        row.operator("claude.open_backups", text="", icon='FILE_FOLDER')
        row.operator("claude.crash_dismiss", text="", icon='X')

    def _draw_timeline(self, layout, context):
        """◀  Beize 3/7  ▶ – one version per answer, moved through with undo/redo."""
        versions = core.versions()
        if not versions:
            return
        current = core.current_version()
        steps = [0] + [v["step"] for v in versions]
        pos = max(i for i, s in enumerate(steps) if s <= current) if current else 0
        row = layout.row(align=True)
        row.scale_y = 1.1
        back = row.row(align=True)
        back.enabled = pos > 0
        back.operator("claude.version_goto", text="", icon='TRIA_LEFT').step = steps[max(0, pos - 1)]
        name = next((v["text"] for v in versions if v["step"] == steps[pos]), tr("Start"))
        mid = row.row(align=True)
        mid.alignment = 'CENTER'
        mid.label(text=f"{name}  {pos}/{len(versions)}", icon='TIME', translate=False)
        fwd = row.row(align=True)
        fwd.enabled = pos < len(steps) - 1
        fwd.operator("claude.version_goto", text="", icon='TRIA_RIGHT').step = steps[min(len(steps) - 1, pos + 1)]

    # -- questions from Claude -------------------------------------------

    def _draw_questions(self, layout, context):
        if not core.questions:
            return
        chatview._setup_font(context)
        width = max(80.0, context.region.width - 4.4 * chatview.UNIT * chatview.ui_scale(context))
        for i, task in enumerate(core.questions):
            box = layout.box()
            col = box.column(align=True)
            col.label(text=tr("{} asks").format(core.model_label()), icon='QUESTION', translate=False)
            for line in chatview.wrap(task.question, width):
                col.label(text=line, translate=False)
            col.separator()
            opts = box.column(align=True)
            opts.scale_y = 1.2
            for j, option in enumerate(task.options):
                selected = j in task.selected
                icon = ('CHECKBOX_HLT' if selected else 'CHECKBOX_DEHLT') if task.multi_select else 'NONE'
                op = opts.operator("claude.answer", text=option, icon=icon, depress=selected, translate=False)
                op.index, op.option = i, j
            if task.multi_select:
                row = box.row()
                row.scale_y = 1.2
                row.enabled = bool(task.selected)
                op = row.operator("claude.answer", text="Answer", icon='CHECKMARK')
                op.index, op.option = i, -1
            hint = box.row()
            hint.active = False
            hint.label(text="or type your own answer below")

    # -- code approval ----------------------------------------------------

    def _draw_setup(self, layout, context, prefs):
        if not _setup_visible(context, prefs):
            return
        state = core.setup_state()
        box = layout.box()
        box.label(text=tr("Setup"), icon='TOOL_SETTINGS')
        if _setup_steps_open(context, prefs, state):
            self._draw_setup_steps(box, prefs, state)
        if _setup_questions_open(context, prefs):
            self._draw_setup_questions(box, context, prefs)
        if prefs.live_connection and state["live_desktop"]:
            # The last step happens in the Claude app (its names as of app 5.12, see README)
            sub = box.column(align=True)
            sub.separator(factor=0.5)
            sub.label(text=tr("Last step, in the Claude app:"), icon='INFO', translate=False)
            for line in _wrap_text(tr("Set the permission switch under the message box to Auto: the Blender "
                                      "tools then run without asking each time (or Customize › Connectors › "
                                      "ai-studio › Tool permissions › Always allow)."), 52):
                row = sub.row()
                row.active = False
                row.label(text=line, translate=False)
        from .studio import render_setup
        box.separator(factor=0.5)
        render_setup.draw(box, context.scene)
        box.operator("claude.setup_done", text=tr("Done"), icon='CHECKMARK')

    @staticmethod
    def _draw_setup_steps(box, prefs, state):
        progress = setup_flow.status()
        if progress:
            text, factor = progress
            row = box.row(align=True)
            if factor is not None:
                row.progress(factor=factor, type='BAR', text=text)
            else:
                row.label(text=text, icon='TIME', translate=False)
            row.operator("claude.setup_cancel", text="", icon='X')
        elif setup_flow.missing():
            row = box.row()
            row.scale_y = 1.4
            row.operator("claude.setup_all", text=tr("Set Up Everything"), icon='PLAY')
        col = box.column(align=True)
        col.enabled = not progress

        def line(ok, text, op=None, op_text=""):
            row = col.row(align=True)
            row.label(text=text, icon='CHECKMARK' if ok else ('TIME' if ok is None else 'ERROR'), translate=False)
            if op and not ok:
                row.operator(op, text=op_text)
            return row

        online = state["online"]
        if online == 'LOCKED':
            line(False, tr("Offline: Blender was started with --offline-mode"))
        else:
            line(online == 'ON', tr("Online access on") if online == 'ON' else tr("Online access off"),
                 "claude.allow_online", tr("Allow"))
        line(bool(state["cli"]), tr("Claude Code installed") if state["cli"] else tr("Claude Code missing"),
             "claude.install_cli", tr("Install"))
        if state["cli"]:
            line(state["login"], {True: tr("Logged in"), False: tr("Not logged in"),
                                  None: tr("Checking the login…")}[state["login"]], "claude.login", tr("Log in"))
        from .studio import tab_material
        has_materials = bool(tab_material.base_library_file())
        line(has_materials, tr("Base materials ready") if has_materials else tr("Base materials missing"),
             "claude.make_base_materials", tr("Create"))
        row = col.row(align=True)
        row.prop(prefs, "live_connection", text=tr("Live Connection for Cowork / Claude Code"))
        sub = row.row(align=True)
        sub.enabled = prefs.live_connection
        sub.operator("claude.copy_live_connection", text="", icon='COPYDOWN')
        if prefs.live_connection:
            if state["cli"]:
                row = line(state["live_code"], tr("Claude Code connected") if state["live_code"]
                           else tr("Claude Code: not connected"))
                if not state["live_code"]:
                    row.operator("claude.connect_client", text=tr("Connect")).target = 'CODE'
            if state["live_desktop"] is not None:
                row = line(state["live_desktop"], tr("Claude app (Cowork) connected") if state["live_desktop"]
                           else tr("Claude app (Cowork): not connected"))
                if not state["live_desktop"]:
                    row.operator("claude.connect_client", text=tr("Connect")).target = 'DESKTOP'

    @staticmethod
    def _draw_setup_questions(box, context, prefs):
        """How the user wants it (neutral until answered): one row per choice."""
        from .studio import runner as studio_runner
        studio = studio_runner.prefs(context)
        box.separator(factor=0.5)
        col = box.column(align=True)

        def choice(label, options):
            row = col.row(align=True)
            split = row.split(factor=0.34, align=True)
            split.label(text=label, translate=False)
            buttons = split.row(align=True)
            for text, key, value, active in options:   # tr() already translated (or not)
                op = buttons.operator("claude.setup_choice", text=text, depress=active, translate=False)
                op.key, op.value = key, value

        if not context.window_manager.claude_show_setup:
            # Only what is really the user's to decide; the rest has good defaults (Preferences, or the ?)
            choice(tr("Claude's code"), [(tr("Ask first"), "code", "ask", not prefs.auto_approve),
                                         (tr("Run directly"), "code", "auto", prefs.auto_approve)])
            hint = col.row()
            hint.active = False
            hint.label(text=tr("Run directly: Claude changes the scene without asking each time (Undo works)"),
                       translate=False)
            return
        if studio is not None:
            folder = bool(studio.output_root)
            choice(tr("Renders"), [(tr("Next to the file"), "renders", "next", not folder),
                                   (tr("One folder"), "renders", "folder", folder)])
        choice(tr("Backups"), [(tr("Every 5 min"), "backups", "on", prefs.autosave),
                               (tr("Off"), "backups", "off", not prefs.autosave)])
        choice(tr("New files"), [(tr("Own folder"), "folders", "on", prefs.project_folders),
                                 (tr("Just the file"), "folders", "off", not prefs.project_folders)])
        choice(tr("Claude's code"), [(tr("Ask first"), "code", "ask", not prefs.auto_approve),
                                     (tr("Run directly"), "code", "auto", prefs.auto_approve)])
        choice(tr("Updates"), [(tr("Automatic"), "updates", "AUTO", prefs.updates == 'AUTO'),
                               (tr("Ask"), "updates", "ASK", prefs.updates == 'ASK')])
        row = col.row(align=True)
        split = row.split(factor=0.34, align=True)
        split.label(text=tr("Model"), translate=False)
        split.prop(prefs, "default_model", text="")

    def _draw_actions(self, layout):
        """Errors that one click can fix, and a new version (Updates: Ask)."""
        from . import updates
        if updates.state["available"] and updates.mode() == 'ASK':
            box = layout.box()
            row = box.row(align=True)
            row.label(text=tr("AI Studio {} is available").format(updates.state["available"]), icon='IMPORT',
                      translate=False)
            row.operator("claude.install_update", text=tr("Install"))
        if core.update_needed and not core.is_local():
            box = layout.box()
            box.label(text=tr("Claude Code is too old for this add-on"), icon='ERROR')
            box.operator("claude.update_cli", text=tr("Update"), icon='FILE_REFRESH')
        if core.local_down and core.is_local():
            box = layout.box()
            box.label(text=tr("LM Studio does not answer"), icon='ERROR')
            box.operator("claude.start_lmstudio", text=tr("Start LM Studio"), icon='PLAY')

    @staticmethod
    def _draw_render(layout):
        """A render runs (the queue, or a preview for Claude): its progress, also in the chat."""
        from .studio import runner
        from .studio.ui_common import _queue_progress_text
        if not runner.RUN.busy:
            return
        row = layout.row(align=True)
        text = _queue_progress_text()
        row.progress(factor=runner.RUN.list_progress, type='BAR',
                     text=text if text.startswith("Preview") else "Render " + text)
        row.operator("render_studio.cancel", text="", icon='X')

    def _draw_live(self, layout):
        text = core.live_status()
        if text:
            row = layout.row()
            row.active = False
            row.label(text=text, icon='LINKED', translate=False)

    def _draw_login(self, layout):
        if not core.login_needed or core.is_local() or _setup_visible(bpy.context, get_prefs()):
            return   # the setup card has its own Log in button
        box = layout.box()
        box.label(text=tr("Logged out of Claude"), icon='ERROR')
        box.operator("claude.login", text=tr("Log in"), icon='URL')

    def _draw_pending(self, layout, context):
        if not core.pending:
            return
        chatview._setup_font(context)
        width = max(80.0, context.region.width - 4.4 * chatview.UNIT * chatview.ui_scale(context))
        for i, task in enumerate(core.pending):
            box = layout.box()
            col = box.column(align=True)
            who = "Cowork" if task.live else core.model_label()
            col.label(text=tr("{} wants to run code").format(who), icon='SCRIPT', translate=False)
            for line in chatview.wrap(task.description or tr("(no description)"), width):
                col.label(text=line, translate=False)
            code = box.column(align=True)
            code.scale_y = 0.7
            code.active = False
            code_lines = task.code.splitlines()
            for line in code_lines[:10]:
                code.label(text=line.replace("\t", "    ") or " ", translate=False)
            if len(code_lines) > 10:
                code.label(text=tr("… {} more lines").format(len(code_lines) - 10), translate=False)
            row = box.row(align=True)
            row.scale_y = 1.3
            op = row.operator("claude.approve", text="Run", icon='CHECKMARK')
            op.index, op.accept = i, True
            op = row.operator("claude.approve", text="Reject", icon='X')
            op.index, op.accept = i, False
            row.operator("claude.show_code", text="", icon='TEXT').index = i

    # -- input bar --------------------------------------------------------

    def _draw_input(self, layout, context, prefs):
        busy = core.is_busy()
        box = layout.box()
        col = box.column(align=True)

        chips = False
        wm = context.window_manager
        # Skills, always visible: one row of icons (name and text in the tooltip),
        # "+" creates a new one; checked skills are highlighted.
        items = [s for s in skills.list_skills(core.workdir()) if s["in_chat"]]
        for start in range(0, len(items) + 1, SKILLS_PER_ROW):
            row = col.row(align=True)
            for skill in items[start:start + SKILLS_PER_ROW]:
                icon = skill["icon"] if skill["icon"] in _ICON_IDS else 'SHADERFX'
                op_id = skill_operator(skill)
                if op_id:   # its own operator: the tooltip shows the skill's title, then its text
                    row.operator(op_id, text="", depress=skill["name"] in core.active_skills, icon=icon)
                else:
                    row.operator("claude.use_skill", text="", depress=skill["name"] in core.active_skills,
                                 icon=icon).name = skill["name"]
            if start + SKILLS_PER_ROW > len(items):
                row.operator("claude.new_skill", text="", icon='ADD')
        chips = True
        for i, att in enumerate(core.attachments):
            row = col.row(align=True)
            row.label(text=att["name"], translate=False, **att_icon(att["path"], att["kind"]))
            row.operator("claude.remove_attachment", text="", icon='X', emboss=False).index = i
            chips = True
        selected = [o.name for o in getattr(context, "selected_objects", None) or []]
        if selected and (prefs is None or prefs.send_selection):
            wm = context.window_manager
            row = col.row(align=True)
            row.active = wm.claude_use_selection
            text = tr("Selection: {}").format(selected[0]) + (f" +{len(selected) - 1}" if len(selected) > 1 else "")
            row.prop(wm, "claude_use_selection", text=text, emboss=False, translate=False,
                     icon='RESTRICT_SELECT_OFF' if wm.claude_use_selection else 'RESTRICT_SELECT_ON')
            chips = True
        if chips:
            col.separator()

        if core.questions:
            placeholder = tr("Your own answer…")
        elif busy:
            placeholder = tr("Add a message while it works…")
        elif core.messages:
            placeholder = tr("Reply…")
        else:
            placeholder = tr("How can I help?")

        # Chats waiting for the current one: their first words, skills and files (click: edit); move up,
        # remove; pause all
        if core.chat_queue:
            queue_box = col.box()
            head = queue_box.row(align=True)
            paused = core.chat_queue_paused[0]
            head.label(text=tr("Next chats ({})").format(len(core.chat_queue)) + (
                "  ·  " + tr("paused") if paused else ""), icon='NEXT_KEYFRAME', translate=False)
            head.operator("claude.chat_queue_pause", text="", emboss=False,
                          icon='PLAY' if paused else 'PAUSE')
            lines = queue_box.column(align=True)
            for index, entry in enumerate(core.chat_queue):
                line = lines.row(align=True)
                words = " ".join((entry.get("text") or tr("(see attachment)")).split())
                extra = []
                if entry.get("skills"):
                    extra.append("%d %s" % (len(entry["skills"]), tr("skills") if len(entry["skills"]) > 1 else tr("skill")))
                if entry.get("attachments"):
                    extra.append("%d %s" % (len(entry["attachments"]), tr("files") if len(entry["attachments"]) > 1 else tr("file")))
                text = (words[:46] + "…" if len(words) > 47 else words) + ("  ·  " + ", ".join(extra) if extra else "")
                edit = line.row(align=True)
                edit.alignment = 'LEFT'
                edit.operator("claude.queued_chat_edit", text=text, icon='GREASEPENCIL', emboss=False,
                              translate=False).chat_id = entry["id"]
                up = line.row(align=True)
                up.alignment = 'RIGHT'
                up.enabled = index > 0
                up.operator("claude.queued_chat_move", text="", icon='TRIA_UP', emboss=False).chat_id = entry["id"]
                up.operator("claude.queued_chat_remove", text="", icon='X', emboss=False).chat_id = entry["id"]

        # [ prompt ................ ] [send] [queue as a new chat] [stop]
        row = col.row(align=True)
        row.scale_y = 1.4
        _prop_with_placeholder(row, context.window_manager, "claude_prompt", placeholder)
        row.operator("claude.send", text="", icon='EVENT_RETURN')
        row.operator("claude.queue_chat", text="", icon='NEXT_KEYFRAME')
        if busy:
            row.operator("claude.stop", text="", icon='SNAP_FACE')

        # [Upload]  [Model] [Effort] [Auto]   (Claude or Local: in the settings)
        col.separator(factor=0.6)
        bar = col.row(align=False)
        tools = bar.row(align=True)
        tools.scale_y = 1.25
        tools.operator("claude.attach_files", text="Upload", icon='EXPORT')
        if prefs:
            opts = bar.row(align=True)
            opts.scale_y = 1.25
            if prefs.backend == 'LOCAL':
                name = lmstudio.display_name(prefs.local_model) if prefs.local_model else tr("Choose Model")
                opts.menu("CLAUDE_MT_local_models", text=name, translate=False)
                opts.prop(prefs, "local_effort", text="", icon_only=True)
            else:
                opts.prop(prefs, "model", text="")
                opts.prop(prefs, "effort", text="")
            opts.prop(prefs, "auto_approve", text="Auto", toggle=True,
                      icon='CHECKBOX_HLT' if prefs.auto_approve else 'CHECKBOX_DEHLT')

    # -- usage ------------------------------------------------------------

    def _draw_status(self, layout, prefs):
        """'Ctx 45% · 97% · Week 89%   ✓ 18:46  [folder]' – details in the tooltips."""
        if prefs and prefs.backend == 'LOCAL':
            col = layout.column(align=True)
            self._draw_context(col, prefs)
            self._draw_local_status(col, prefs)
            self._draw_autosave(col, prefs)
            return
        # Short texts so they always fit; the details are in the tooltips. No red: a hint, not an alarm.
        row = layout.row(align=True)
        row.alignment = 'CENTER'
        fraction = core.context_fraction() if core.session_id else 0
        if fraction:
            limit = (prefs.compact_at / 100) if prefs and prefs.auto_compact else 0.95
            sub = row.row(align=True)
            sub.active = fraction >= limit * 0.8
            sub.operator("claude.compact_chat", text="%d%%" % round(fraction * 100), icon='TEXT',
                         emboss=False, translate=False)
        windows = _usage_windows()
        if (core.usage or {}).get("status") == "rejected":
            row.operator("claude.usage_info", text=tr("Limit"), icon='ERROR', emboss=False, translate=False)
        elif windows:
            low = min(left for _label, left, _reset in windows)
            sub = row.row(align=True)
            sub.active = low < 0.15
            text = " · ".join("%d%%" % round(left * 100) for _label, left, _reset in windows)
            sub.operator("claude.usage_info", text=text, icon='TIME', emboss=False, translate=False)
        if prefs is not None and prefs.autosave:
            st = autosave.state
            save = row.row(align=True)   # save now and the backup folder side by side
            if st["error"]:
                save.operator("claude.autosave_now", text=tr("Save failed"), icon='ERROR', emboss=False)
            else:
                sub = save.row(align=True)
                sub.active = bool(st["path"])
                text = time.strftime("%H:%M", time.localtime(st["last"])) if st["path"] else ""
                sub.operator("claude.autosave_now", text=text, icon='FILE_TICK', emboss=False, translate=False)
            save.operator("claude.open_backups", text="", icon='FILE_FOLDER', emboss=False)

    def _draw_usage(self, layout):
        prefs = get_prefs()
        self._draw_context(layout, prefs)
        if prefs and prefs.backend == 'LOCAL':
            self._draw_local_status(layout, prefs)
            return
        windows = _usage_windows()
        if not windows:
            row = layout.row()
            row.alignment = 'CENTER'
            row.active = False
            row.label(text="Usage appears after the first answer")
            return
        if (core.usage or {}).get("status") == "rejected":
            row = layout.row()
            row.alignment = 'CENTER'
            row.alert = True
            text = tr("Limit reached")
            reset = (core.usage or {}).get("resetsAt")
            if reset:
                text += tr(" – available again {}").format(_format_reset(reset))
            row.operator("claude.usage_info", text=text, emboss=False, translate=False)
            return
        col = layout.column(align=True)
        for label, left, reset in windows:
            row = col.row()
            row.alignment = 'CENTER'
            row.alert = left < 0.15
            row.active = left < 0.15
            text = tr("{}: {}% left").format(label, round(left * 100))
            if reset:
                text += " · " + tr("reset {}").format(_format_reset(reset))
            row.operator("claude.usage_info", text=text, emboss=False, translate=False)

    @staticmethod
    def _draw_autosave(layout, prefs):
        if prefs is None or not prefs.autosave:
            return
        st = autosave.state
        row = layout.row()
        row.alignment = 'CENTER'
        if st["error"]:
            row.alert = True
            text = tr("Auto save failed: {}").format(st["error"][:60])
        elif st["path"]:
            reasons = {"regular": tr("regular"), "before code": tr("before code"),
                       "before render": tr("before render"), "memory low": tr("memory low"),
                       "before environment bake": tr("before render"), "manual": tr("manual")}
            text = tr("Saved {} · {}").format(time.strftime("%H:%M", time.localtime(st["last"])),
                                              reasons.get(st["reason"], st["reason"]))
            row.active = st["reason"] != "memory low"
            row.alert = st["reason"] == "memory low"
        else:
            row.active = False
            text = tr("Auto save every {} min").format(prefs.autosave_minutes)
        row.operator("claude.autosave_now", text=text, icon='FILE_TICK', emboss=False, translate=False)
        row.operator("claude.open_backups", text="", icon='FILE_FOLDER', emboss=False)

    @staticmethod
    def _draw_local_status(layout, prefs):
        """'● Gemma loaded · 64k context  [x]' and 'RAM 17.0 of 32 GB' (polled from LM Studio)."""
        col = layout.column(align=True)
        row = col.row(align=True)
        row.alignment = 'CENTER'
        st, live = lmstudio.status, lmstudio.live
        name = lmstudio.display_name(prefs.local_model) if prefs.local_model else tr("No model chosen")
        loaded = prefs.local_model in live["loaded"]
        if st["model"] == prefs.local_model and lmstudio.busy():
            secs = int(time.monotonic() - st["since"])
            row.label(text=tr("{}: loading… {}s").format(name, secs), icon='SORTTIME', translate=False)
        elif st["model"] == prefs.local_model and st["state"] == "error":
            row.alert = True
            row.label(text=tr("LM Studio: {}").format(st["message"][:80]), icon='ERROR', translate=False)
        elif live["server"] is False:
            row.active = False
            row.label(text=tr("LM Studio server is off – starts with the first message"), icon='RADIOBUT_OFF',
                      translate=False)
        elif loaded:
            text = tr("Loaded · {}k").format(live["loaded"][prefs.local_model] // 1024)
            if live["ram"] and lmstudio.TOTAL_RAM:
                text += " · " + tr("{:.1f}/{:.0f} GB").format(live["ram"] / lmstudio.GB,
                                                                   lmstudio.TOTAL_RAM / lmstudio.GB)
                row.alert = live["ram"] > 0.8 * lmstudio.TOTAL_RAM
            row.label(text=text, icon='RADIOBUT_ON', translate=False)
            row.operator("claude.local_unload", text="", icon='X', emboss=False)
        elif prefs.local_model:
            row.active = False
            row.label(text=tr("Not loaded"), icon='RADIOBUT_OFF', translate=False)
            row.operator("claude.local_load", text="", icon='IMPORT', emboss=False)

    @staticmethod
    def _draw_context(layout, prefs):
        """Context use of this chat: 'Context 45% · summarizes at 70%'."""
        fraction = core.context_fraction()
        if not fraction or not core.session_id:
            return
        row = layout.row()
        row.alignment = 'CENTER'
        limit = (prefs.compact_at / 100) if prefs and prefs.auto_compact else 0.95
        row.alert = fraction >= limit
        row.active = fraction >= limit * 0.8
        text = tr("Context {}%").format(round(fraction * 100))
        if prefs and prefs.auto_compact:
            text += " " + tr("(auto {}%)").format(prefs.compact_at)
        if core.lessons_count:
            text += " · " + tr("{} lessons").format(core.lessons_count)
        row.operator("claude.compact_chat", text=text, emboss=False, translate=False)

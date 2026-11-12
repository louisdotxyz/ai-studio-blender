# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidebar (3D Viewport > N > Claude): chat, questions, input bar, usage."""

import os
import re
import time

import bpy
import bpy.utils.previews
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty

from . import attachments as att_mod
from . import autosave, chatview, claude_cli, core, lmstudio, memory, setup_flow, skills
from .i18n import tip, tr
from .preferences import get_prefs

_previews = None
_ICON_IDS = set(bpy.types.UILayout.bl_rna.functions["label"].parameters["icon"].enum_items.keys())
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ---------------------------------------------------------------------------
# Helpers

def att_icon(path, kind=None):
    """kwargs for layout.label: image thumbnail if possible, else a type icon."""
    ext = os.path.splitext(path or "")[1].lower()
    is_image = kind == "image" or ext in att_mod.IMAGE_TYPES
    if is_image and _previews is not None and os.path.isfile(path):
        try:
            preview = _previews[path] if path in _previews else _previews.load(path, path, 'IMAGE')
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
        _send_prompt(wm)
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


# One small operator per skill (registered from a timer, never while drawing): Blender's tooltip
# shows the operator's label big and its description below, so "SORT" and then what it does.
_skill_ops = {}            # skill name -> (class, title, description)
_skill_sync_pending = [False]


def _toggle_skill(name):
    if name in core.active_skills:
        core.active_skills.remove(name)
    else:
        core.active_skills.append(name)
    core.mark_dirty()


def _skill_execute(self, context):
    _toggle_skill(self.skill_name)
    return {'FINISHED'}


def skill_operator(skill):
    """bl_idname of the skill's own operator, or "" (then scheduled, the generic one is used)."""
    entry = _skill_ops.get(skill["name"])
    if entry and entry[1] == skill["title"] and entry[2] == skill["description"]:
        return entry[0].bl_idname
    if not _skill_sync_pending[0]:
        _skill_sync_pending[0] = True
        bpy.app.timers.register(sync_skill_operators, first_interval=0.0)
    return ""


def sync_skill_operators():
    _skill_sync_pending[0] = False
    wanted = {s["name"]: s for s in skills.list_skills(core.workdir())}
    for name in list(_skill_ops):
        cls, title, description = _skill_ops[name]
        skill = wanted.get(name)
        if skill is None or skill["title"] != title or skill["description"] != description:
            try:
                bpy.utils.unregister_class(cls)
            except RuntimeError:
                pass
            del _skill_ops[name]
    for name, skill in wanted.items():
        if name in _skill_ops:
            continue
        ident = re.sub(r"[^a-z0-9_]", "_", name.lower())[:40] or "skill"
        cls = type("CLAUDE_OT_skill_" + ident, (bpy.types.Operator,), {
            "bl_idname": "claude.skill_" + ident,
            "bl_label": skill["title"],
            "bl_description": skill["description"] or tip("Check or uncheck this skill"),
            "bl_options": {'INTERNAL'},
            "skill_name": name,
            "execute": _skill_execute,
        })
        try:
            bpy.utils.register_class(cls)
        except (RuntimeError, ValueError) as ex:
            print("Claude: skill button for %s: %s" % (name, ex))
            continue
        _skill_ops[name] = (cls, skill["title"], skill["description"])
    tag = getattr(core, "_tag_redraw", None)
    if tag:
        tag()
    return None


def _unregister_skill_operators():
    for cls, _title, _description in _skill_ops.values():
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _skill_ops.clear()


class CLAUDE_OT_use_skill(bpy.types.Operator):
    bl_idname = "claude.use_skill"
    bl_label = "Use Skill"
    bl_description = "Check or uncheck this skill (checked skills apply to all your messages)"

    name: StringProperty(options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        if not properties.name:
            return tip("Remove skill")
        skill = skills.find(core.workdir(), properties.name) or {}
        text = skill.get("description") or tip(cls.bl_description)
        title = skill.get("title") or properties.name
        state = tip("on") if properties.name in core.active_skills else tip("off")
        return "%s (%s): %s" % (title, state, text)

    def execute(self, context):
        if not self.name:
            core.active_skills.clear()
        elif self.name in core.active_skills:
            core.active_skills.remove(self.name)
        else:
            core.active_skills.append(self.name)
        core.mark_dirty()
        return {'FINISHED'}


class CLAUDE_OT_new_skill(bpy.types.Operator):
    bl_idname = "claude.new_skill"
    bl_label = "New Skill"
    bl_description = "Create a new skill: reusable instructions you can check for your messages"

    title: StringProperty(name="Name", options={'SKIP_SAVE'})
    description: StringProperty(name="Short Description", options={'SKIP_SAVE'})
    body: StringProperty(name="Instructions", options={'SKIP_SAVE'},
                         description="What the model should do when this skill is checked")
    open_file: BoolProperty(name="Edit Longer Text in the Editor", default=False, options={'SKIP_SAVE'})
    icon: EnumProperty(name="Icon", items=[(i, "", i.replace("_", " ").title(), i, n)
                                           for n, i in enumerate(skills.ICON_CHOICES)],
                       options={'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "title")
        col.row(align=True).prop(self, "icon", expand=True, icon_only=True)
        col.prop(self, "description")
        col.prop(self, "body")
        col.prop(self, "open_file")

    def execute(self, context):
        if not self.title.strip():
            self.report({'WARNING'}, tr("Give the skill a name"))
            return {'CANCELLED'}
        icon = self.icon if self.icon != 'SHADERFX' else ""
        name = skills.create(core.workdir(), self.title, self.description, self.body, icon)
        if name not in core.active_skills:
            core.active_skills.append(name)
        if self.open_file:
            bpy.ops.wm.path_open(filepath=os.path.join(skills.skills_dir(core.workdir()), name + ".md"))
        core.mark_dirty()
        return {'FINISHED'}


class CLAUDE_OT_login(bpy.types.Operator):
    bl_idname = "claude.login"
    bl_label = "Log in"
    bl_description = "Log in to Claude again: opens a terminal with 'claude auth login', which opens the browser"

    def execute(self, context):
        return {'FINISHED'} if core.open_login() else {'CANCELLED'}


class CLAUDE_OT_allow_online(bpy.types.Operator):
    bl_idname = "claude.allow_online"
    bl_label = "Allow Online Access"
    bl_description = ("Turns on Preferences › System › Network › Allow Online Access. Claude Code needs the "
                      "internet; a new Blender starts with it off")

    def execute(self, context):
        if not core.allow_online():
            self.report({'ERROR'}, tr("Blender was started with --offline-mode: start it without it"))
            return {'CANCELLED'}
        self.report({'INFO'}, tr("Online access is on"))
        return {'FINISHED'}


class CLAUDE_OT_install_cli(bpy.types.Operator):
    bl_idname = "claude.install_cli"
    bl_label = "Install Claude Code"
    bl_description = ("Installs Claude Code with its official installer (claude.ai/install.sh) in the background, "
                      "then opens the login")

    def execute(self, context):
        return {'FINISHED'} if setup_flow.start(("online", "install", "login")) else {'CANCELLED'}


class CLAUDE_OT_install_update(bpy.types.Operator):
    bl_idname = "claude.install_update"
    bl_label = "Install Update"
    bl_description = "Install the new version of AI Studio now (Blender loads it again right away)"

    def execute(self, context):
        from . import updates
        if updates.busy():
            self.report({'WARNING'}, tr("Wait until the answer or the render is done"))
            return {'CANCELLED'}
        return {'FINISHED'} if updates.install() else {'CANCELLED'}


class CLAUDE_OT_setup_all(bpy.types.Operator):
    bl_idname = "claude.setup_all"
    bl_label = "Set Up Everything"
    bl_description = ("Does every open step of the setup: allows online access, installs Claude Code, opens the "
                      "login, creates the base materials and connects Claude Code and the Claude app to this Blender")

    def execute(self, context):
        return {'FINISHED'} if setup_flow.start() else {'CANCELLED'}


class CLAUDE_OT_setup_cancel(bpy.types.Operator):
    bl_idname = "claude.setup_cancel"
    bl_label = "Cancel Setup"
    bl_description = "Stop the setup (a download that was started is thrown away)"

    def execute(self, context):
        setup_flow.cancel()
        return {'FINISHED'}


class CLAUDE_OT_update_cli(bpy.types.Operator):
    bl_idname = "claude.update_cli"
    bl_label = "Update Claude Code"
    bl_description = "Opens a terminal with 'claude update'"

    def execute(self, context):
        core.open_update()
        return {'FINISHED'}


class CLAUDE_OT_start_lmstudio(bpy.types.Operator):
    bl_idname = "claude.start_lmstudio"
    bl_label = "Start LM Studio"
    bl_description = "Start LM Studio's server ('lms server start'), then send the message again"

    def execute(self, context):
        core.start_lmstudio()
        return {'FINISHED'}


class CLAUDE_OT_make_base_materials(bpy.types.Operator):
    bl_idname = "claude.make_base_materials"
    bl_label = "Create Base Materials"
    bl_description = ("Writes 'Base Materials.blend' (glass, metals, plastics, rubber, paint, fabric) into your "
                      "first asset library (or the add-on's folder) and uses it in the Material tab")

    def execute(self, context):
        return {'FINISHED'} if setup_flow.start(("materials",)) else {'CANCELLED'}


class CLAUDE_OT_copy_live_connection(bpy.types.Operator):
    bl_idname = "claude.copy_live_connection"
    bl_label = "Copy Connection"
    bl_description = ("Copy instructions for another Claude session (Cowork, Claude Code): how to reach this "
                      "Blender over the Live Connection. Paste them into that session")

    def execute(self, context):
        from . import live
        context.window_manager.clipboard = live.connection_text()
        self.report({'INFO'}, tr("Copied: paste it into the other Claude session"))
        return {'FINISHED'}


class CLAUDE_OT_connect_client(bpy.types.Operator):
    bl_idname = "claude.connect_client"
    bl_label = "Connect"
    bl_description = ("Give this Claude app the 'ai-studio' tools (an MCP server of this add-on), so its chats "
                      "can see and change the open Blender over the Live Connection")

    target: EnumProperty(items=[('CODE', "Claude Code", ""), ('DESKTOP', "Claude Desktop", "")],
                         options={'SKIP_SAVE'})

    def execute(self, context):
        from . import live_clients
        try:
            if self.target == 'DESKTOP':
                live_clients.connect_desktop()
                self.report({'INFO'}, tr("Connected: quit and reopen the Claude app once"))
            else:
                prefs = get_prefs()
                path = claude_cli.find_claude(prefs.claude_path if prefs else "")
                if not path:
                    self.report({'ERROR'}, tr("Claude Code missing"))
                    return {'CANCELLED'}
                live_clients.connect_code(path)
                self.report({'INFO'}, tr("Connected: new Claude Code sessions have the ai-studio tools"))
        except (OSError, RuntimeError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        core._setup_cache["state"] = None
        return {'FINISHED'}


class CLAUDE_OT_setup_done(bpy.types.Operator):
    bl_idname = "claude.setup_done"
    bl_label = "Done"
    bl_description = "Hide the setup card (the ? at the top shows it again)"

    def execute(self, context):
        prefs = get_prefs()
        if prefs is not None:
            prefs.setup_asked = True
            state = core.setup_state()
            if state["cli"] and state["online"] == 'ON' and state["login"] is not False:
                prefs.setup_done = True
            context.preferences.is_dirty = True
        context.window_manager.claude_show_setup = False
        return {'FINISHED'}


class CLAUDE_OT_setup_choice(bpy.types.Operator):
    bl_idname = "claude.setup_choice"
    bl_label = "Choose"
    bl_description = "How you would like it (all of it can be changed later in the preferences)"
    bl_options = {'INTERNAL'}

    key: StringProperty(options={'SKIP_SAVE'})
    value: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        from .studio import core as studio_core, runner as studio_runner
        prefs = get_prefs()
        studio = studio_runner.prefs(context)
        if prefs is None:
            return {'CANCELLED'}
        if self.key == "renders" and studio is not None:
            studio.output_root = "" if self.value == "next" else studio_core.UNSAVED_ROOT
        elif self.key == "backups":
            prefs.autosave = self.value == "on"
        elif self.key == "folders":
            prefs.project_folders = self.value == "on"
        elif self.key == "code":
            prefs.auto_approve = self.value == "auto"
        elif self.key == "updates":
            prefs.updates = self.value
        context.preferences.is_dirty = True
        core._setup_cache["state"] = None
        return {'FINISHED'}


class CLAUDE_OT_example(bpy.types.Operator):
    bl_idname = "claude.example"
    bl_label = "Example"
    bl_description = "Put this example into the message field"

    text: StringProperty(options={'SKIP_SAVE'})

    def execute(self, context):
        context.window_manager.claude_prompt = self.text
        return {'FINISHED'}


EXAMPLES = (
    "Sort the scene into collections and give the parts clear names",
    "Make a soft studio light from the front left and render a preview",
    "Give the housing the base material 'Aluminium anodized' and show me three colors",
    "Import the newest GLB from Downloads and take the materials over from the old version",
)


class CLAUDE_PT_help(bpy.types.Panel):
    """The ? at the top of the chat: shortcuts, clicks worth knowing, example prompts, setup."""
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "Help"
    bl_ui_units_x = 16

    def draw(self, context):
        layout = self.layout
        box = layout.box()
        box.label(text="Try", icon='LIGHT')
        col = box.column(align=True)
        for text in EXAMPLES:
            col.operator("claude.example", text=tr(text), emboss=False, icon='RIGHTARROW_THIN').text = tr(text)
        box = layout.box()
        box.label(text="Good to Know", icon='INFO')
        col = box.column(align=True)
        for line in ("⌘R: render High Quality, ⇧⌘R: Low Quality, ⇧⌘E: view the render",
                     "Material library: click switches all objects with the current material",
                     "Shift-click: only the selection · Ctrl-click: select its objects",
                     "Right-click a material: favorite, rename, delete",
                     "Replaced objects go into the archive collection (Archive), you delete them",
                     "Claude's helper objects go into 'temp' and are removed after the answer"):
            col.label(text=tr(line))
        layout.prop(context.window_manager, "claude_show_setup", text="Show Setup", toggle=True, icon='TOOL_SETTINGS')


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
        global _clearing
        _clearing = True  # loading text for editing must not send it
        try:
            context.window_manager.claude_prompt = msg["text"]
        finally:
            _clearing = False
        for att in msg.get("attachments") or []:
            if os.path.isfile(att.get("path", "")):
                core.add_attachment(att["path"])
        if msg.get("skill"):
            titles = [t.strip() for t in msg["skill"].split(",")]
            core.active_skills[:] = [s["name"] for s in skills.list_skills(core.workdir())
                                     if s["title"] in titles]
        core.mark_dirty()
        return {'FINISHED'}


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


def _send_prompt(wm):
    global _clearing
    if core.queue_prompt(wm.claude_prompt):
        _clearing = True
        try:
            wm.claude_prompt = ""
        finally:
            _clearing = False
        chatview.scroll(-10**9)  # jump to the newest message
        core.mark_dirty()


def _prompt_update(self, context):
    if _clearing or not self.claude_prompt.strip():
        return
    try:
        bpy.ops.claude.prompt_confirmed('INVOKE_DEFAULT')
    except RuntimeError:
        pass


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

        # [ prompt ................ ] [send] [stop]
        row = col.row(align=True)
        row.scale_y = 1.4
        _prop_with_placeholder(row, context.window_manager, "claude_prompt", placeholder)
        row.operator("claude.send", text="", icon='EVENT_RETURN')
        if busy:
            row.operator("claude.stop", text="", icon='SNAP_FACE')

        # [Upload] [Skills]  [Cloud|Local] [Model] [Effort] [Auto]
        col.separator(factor=0.6)
        bar = col.row(align=False)
        tools = bar.row(align=True)
        tools.operator("claude.attach_files", text="", icon='EXPORT')
        if prefs:
            opts = bar.row(align=True)
            opts.prop(prefs, "backend", expand=True, icon_only=True)
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


classes = (
    CLAUDE_OT_send,
    CLAUDE_OT_stop,
    CLAUDE_OT_new_chat,
    CLAUDE_OT_load_chat,
    CLAUDE_OT_delete_chat,
    CLAUDE_OT_compact_chat,
    CLAUDE_OT_approve,
    CLAUDE_OT_show_code,
    CLAUDE_OT_answer,
    CLAUDE_OT_copy_message,
    CLAUDE_OT_edit_message,
    CLAUDE_OT_prompt_confirmed,
    CLAUDE_OT_attach_files,
    CLAUDE_OT_paste,
    CLAUDE_OT_remove_attachment,
    CLAUDE_OT_usage_info,
    CLAUDE_OT_undo_steps,
    CLAUDE_OT_use_skill,
    CLAUDE_OT_new_skill,
    CLAUDE_OT_login,
    CLAUDE_OT_allow_online,
    CLAUDE_OT_connect_client,
    CLAUDE_OT_install_cli,
    CLAUDE_OT_update_cli,
    CLAUDE_OT_start_lmstudio,
    CLAUDE_OT_make_base_materials,
    CLAUDE_OT_copy_live_connection,
    CLAUDE_OT_install_update,
    CLAUDE_OT_setup_all,
    CLAUDE_OT_setup_choice,
    CLAUDE_OT_setup_cancel,
    CLAUDE_OT_setup_done,
    CLAUDE_OT_example,
    CLAUDE_PT_help,
    CLAUDE_OT_open_path,
    CLAUDE_OT_set_local_model,
    CLAUDE_OT_refresh_local_models,
    CLAUDE_OT_open_models_folder,
    CLAUDE_OT_local_load,
    CLAUDE_OT_local_unload,
    CLAUDE_OT_open_lessons,
    CLAUDE_OT_open_file,
    CLAUDE_OT_version_goto,
    CLAUDE_OT_crash_open_backup,
    CLAUDE_OT_crash_dismiss,
    CLAUDE_OT_save_as_original,
    CLAUDE_OT_open_crash_log,
    CLAUDE_OT_make_project_folder,
    CLAUDE_OT_autosave_now,
    CLAUDE_OT_open_backups,
    CLAUDE_MT_skills,
    CLAUDE_MT_local_models,
    CLAUDE_MT_history,
    CLAUDE_FH_attach,
    CLAUDE_PT_settings,
    CLAUDE_PT_chat,
)


def register():
    global _previews
    _previews = bpy.utils.previews.new()
    for cls in classes:
        bpy.utils.register_class(cls)
    chatview.register()
    bpy.types.WindowManager.claude_chat_title = StringProperty(
        name="Chat Name",
        description="Name of this chat – click to rename it",
        get=lambda self: core.current_title(),
        set=lambda self, value: core.set_current_title(value),
        options={'SKIP_SAVE'},
    )
    bpy.types.WindowManager.claude_show_setup = BoolProperty(
        name="Show Setup", description="Show the setup card in the chat", default=False, options={'SKIP_SAVE'})
    bpy.types.WindowManager.claude_use_selection = BoolProperty(
        name="Send Selection",
        description="Send details of the selected objects (click to turn on/off)",
        default=True,
    )
    bpy.types.WindowManager.claude_prompt = StringProperty(
        name="Message",
        description="Message to Claude – Enter sends, clicking elsewhere keeps the text",
        default="",
        update=_prompt_update,
        options={'SKIP_SAVE'},
    )


def unregister():
    global _previews
    if bpy.app.timers.is_registered(sync_skill_operators):
        bpy.app.timers.unregister(sync_skill_operators)
    _unregister_skill_operators()
    del bpy.types.WindowManager.claude_prompt
    del bpy.types.WindowManager.claude_use_selection
    del bpy.types.WindowManager.claude_show_setup
    del bpy.types.WindowManager.claude_chat_title
    chatview.unregister()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
        _previews = None

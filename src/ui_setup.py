# SPDX-License-Identifier: GPL-3.0-or-later
"""Setup card and help: login, online access, Claude Code, connections, examples."""

import bpy
from bpy.props import EnumProperty, StringProperty

from . import claude_cli, core, setup_flow
from .i18n import tr
from .preferences import get_prefs


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

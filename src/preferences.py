# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty, PointerProperty, StringProperty

from .studio.props import RS_Preferences

MODELS = [
    ('sonnet', "Sonnet", "Latest Sonnet model – fast and strong (default)"),
    ('opus', "Opus", "Latest Opus model – the most capable"),
    ('fable', "Fable", "Latest Fable model"),
    ('haiku', "Haiku", "Latest Haiku model – the fastest"),
]

EFFORTS = [
    ('low', "Low", "Quick answers, little thinking"),
    ('medium', "Medium", "Balanced (default for Sonnet)"),
    ('high', "High", "More thorough (default for Opus)"),
    ('xhigh', "Extra High", "Even more thorough"),
    ('max', "Max", "Maximum thinking – slower, uses more of your usage"),
]

BACKENDS = [
    ('CLOUD', "Claude", "Claude models in the cloud (your Claude subscription)", 'WORLD', 0),
    ('LOCAL', "Local", "Local models from LM Studio (~/.lmstudio/models), run on this computer", 'DESKTOP', 1),
]

# Effort that is selected automatically when switching to a model.
MODEL_DEFAULT_EFFORT = {'sonnet': 'medium', 'opus': 'high', 'fable': 'high', 'haiku': 'low'}


def _model_changed(self, context):
    effort = MODEL_DEFAULT_EFFORT.get(self.model)
    if effort and self.effort != effort:
        self.effort = effort


def _backend_changed(self, context):
    if self.backend == 'LOCAL' and self.local_model:
        from . import lmstudio
        lmstudio.preload(self.local_model, self.local_context)


def _live_connection_changed(self, context):
    from . import live
    live.sync()


def get_prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


class ClaudeAssistantPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    studio: PointerProperty(type=RS_Preferences)   # Render Studio's settings (Studio tab)
    show_stats: BoolProperty(
        name="Answer Statistics",
        description="Show duration, tokens and usage share under every answer",
        default=True,
    )
    autosave: BoolProperty(
        name="Auto Save",
        description="Save regularly and right before risky moments (model code, renders, low memory)",
        default=True,
    )
    autosave_mode: EnumProperty(
        name="Auto Save Mode",
        items=[('COPY', "Backup Copies", "Copies in the backup folder next to the file – your file itself "
                                         "is not touched"),
               ('FILE', "Save the File", "Saves the .blend itself (the previous version stays as .blend1)")],
        default='COPY',
    )
    autosave_minutes: IntProperty(
        name="Every",
        description="Minutes between regular saves (only when something changed)",
        default=5, min=1, max=60,
    )
    autosave_keep: IntProperty(
        name="Keep Copies",
        description="Number of backup copies kept per file (the oldest are deleted)",
        default=5, min=1, max=100,
    )
    # Files: one project folder per .blend (autosave.py)
    project_folders: BoolProperty(
        name="Project Folder per File",
        description="A new file gets its own folder <name>/ with the backup, assets and archive folders "
                    "when it is saved the first time",
        default=True,
    )
    backup_dir: StringProperty(name="Backups", description="Folder for the backup copies", default="backups")
    assets_dir: StringProperty(name="Assets", description="Folder for textures, HDRIs … of the project",
                               default="assets")
    archive_dir: StringProperty(name="Archive", description="Folder for your own milestones (never written "
                                "by the add-on)", default="archive")
    main_suffix: StringProperty(
        name="Main File Suffix",
        description="Appended to the main file's name, e.g. '_now' → Chair_now.blend (empty: Chair.blend)",
        default="")
    backup_stamp: EnumProperty(
        name="Backup Names",
        items=[('YMD', "Chair_2026-10-24_12-58", "Date first, sorts by name"),
               ('HMD', "Chair_12-58_24.10.26", "Time first, short")],
        default='YMD')
    attach_folder: StringProperty(
        name="Attach From", description="Folder the attach button opens first", subtype='DIR_PATH',
        default="~/Downloads")
    settings_version: IntProperty(options={'HIDDEN'})   # see settings.py
    prefs_tab: EnumProperty(
        name="Section",
        items=[('CHAT', "Chat", "Model, what is sent, chat behaviour", 'COMMUNITY', 0),
               ('FILES', "Files", "Project folders and backups", 'FILE_FOLDER', 1),
               ('RENDERS', "Renders", "Where renders go, Low Quality", 'RENDER_STILL', 2),
               ('STUDIO', "Studio", "HDRIs, base materials, shortcuts", 'SCENE', 3),
               ('LOCAL', "Local", "Local models with LM Studio", 'DESKTOP', 4),
               ('LEARN', "Learning", "Lessons and memory", 'LIGHT', 5)],
        default='CHAT', options={'SKIP_SAVE'})
    autosave_before_risky: BoolProperty(
        name="Before Code and Renders",
        description="Also save right before the model runs code and before renders "
                    "(at most every 2 minutes) – the moments in which Blender most often crashes",
        default=True,
    )
    autosave_on_memory: BoolProperty(
        name="When Memory Runs Low",
        description="Save immediately when macOS reports memory pressure (often shortly before a crash)",
        default=True,
    )
    backend: EnumProperty(name="Cloud / Local", items=BACKENDS, default='CLOUD', update=_backend_changed)
    model: EnumProperty(name="Model", items=MODELS, default='sonnet', update=_model_changed)
    local_model: StringProperty(
        name="Local Model",
        description="LM Studio model key, e.g. qwen/qwen3.8-27b",
        default="",
    )
    local_context: IntProperty(
        name="Local Context Length",
        description="Context length for loading a local model. 32k fits a chat with a few screenshots "
                    "and keeps memory use and prompt processing low; older parts are summarized automatically",
        default=32768, min=16384, max=262144, step=4096,
    )
    auto_compact: BoolProperty(
        name="Auto Summarize Long Chats",
        description="When a chat fills the model's context, summarize older parts automatically "
                    "(keeps answers fast and avoids context errors)",
        default=True,
    )
    compact_at: IntProperty(
        name="Summarize At",
        description="Context usage at which the chat is summarized",
        default=70, min=40, max=95, subtype='PERCENTAGE',
    )
    local_effort: EnumProperty(
        name="Local Effort",
        items=[('off', "Fast", "No thinking: answers right away (recommended for local models)", 'FF', 0),
               ('medium', "Think", "The model reasons before answering: better for tricky tasks, "
                                   "but often 1-2 minutes slower", 'LIGHT', 1),
               ('high', "Think More", "Like Think, asks for more effort (LM Studio hardly makes a difference)",
                'LIGHT_SUN', 2)],
        default='off',
    )
    learn_from_errors: BoolProperty(
        name="Learn From Errors",
        description="After fixing an error, the model saves the problem and solution (one page per lesson, "
                    "separate for each model) and gets its own lessons in future chats",
        default=True,
    )
    lessons_dir: StringProperty(
        name="Lessons Folder",
        description="Where the lessons are kept, e.g. a folder in your Obsidian vault "
                    "(empty: the add-on's own folder)",
        subtype='DIR_PATH',
        default="",
    )
    local_send_screenshot: BoolProperty(
        name="Screenshot With Every Message (Local)",
        description="Attach a viewport screenshot to every message in local mode. Off: the model takes "
                    "a screenshot itself when it needs one (faster)",
        default=False,
    )
    effort: EnumProperty(name="Effort", items=EFFORTS, default='medium')

    claude_path: StringProperty(
        name="Claude CLI Path",
        description="Leave empty to search automatically (~/.local/bin, ~/.claude/local, Homebrew, npm, nvm)",
        subtype='FILE_PATH',
        default="",
    )
    auto_approve: BoolProperty(
        name="Run Code Without Asking",
        description="Auto mode: Claude runs code directly without asking every time (undo with Ctrl+Z)",
        default=False,
    )
    live_connection: BoolProperty(
        name="Live Connection (External Claude Sessions)",
        description="Let other Claude sessions on this computer (Cowork, blender-mcp) work in the open Blender. "
                    "Port and token are in ~/.config/ai-studio/live.json. "
                    "'Run Code Without Asking' applies to them too",
        default=True,
        update=_live_connection_changed,
    )
    live_port: IntProperty(
        name="Port", description="Fixed port for the Live Connection (0 = a free one each start). "
        "Takes effect when Blender starts", default=0, min=0, max=65535,
    )
    setup_done: BoolProperty(
        name="Setup Done", description="The setup card in the chat was closed", default=False,
    )
    send_scene_context: BoolProperty(
        name="Send Scene Info",
        description="Attach a description of the scene to every message",
        default=True,
    )
    auto_screenshot: BoolProperty(
        name="Screenshot With Every Message",
        description="Attach a viewport screenshot to every message. Off: the model takes one itself when it "
                    "needs to see the scene (saves about 800 tokens per message)",
        default=False,
    )
    send_selection: BoolProperty(
        name="Send Selection",
        description="Send details of the selected objects, so 'this' is clear",
        default=True,
    )
    screenshot_max_size: IntProperty(
        name="Max Image Size",
        description="Longest side of screenshots and attached images in pixels",
        default=1568, min=256, max=4096,
    )
    max_display: IntProperty(
        name="Shown Messages",
        description="How many of the latest messages are shown in the chat",
        default=100, min=4, max=500,
    )
    custom_instructions: StringProperty(
        name="Custom Instructions",
        description="Additional instructions Claude receives with every message",
        default="",
    )
    extra_args: StringProperty(
        name="Extra CLI Arguments",
        description="Appended to the claude command (advanced)",
        default="",
    )

    def draw(self, context):
        layout = self.layout
        if not bpy.app.online_access:
            box = layout.box()
            box.alert = True
            box.label(text="Online access is disabled: Claude Code needs the internet.", icon='ERROR')
            if bpy.app.online_access_override:
                box.label(text="Blender was started with --offline-mode: start it without it.")
            else:
                box.operator("claude.allow_online", icon='URL')
        layout.row().prop(self, "prefs_tab", expand=True)
        box = layout.box()
        getattr(self, "_draw_" + self.prefs_tab.lower())(box, context)

    def _draw_chat(self, layout, context):
        from . import claude_cli
        from .i18n import tr
        col = layout.column()
        col.use_property_split = True
        col.prop(self, "claude_path")
        found = claude_cli.find_claude(self.claude_path)
        row = col.row()
        if found:
            row.label(text=tr("Found: {}").format(found), icon='CHECKMARK', translate=False)
        else:
            row.label(text="Claude CLI not found – see the README", icon='ERROR')
        col.separator()
        sub = col.column(heading="With Every Message")
        sub.prop(self, "send_scene_context", text="Scene Info")
        sub.prop(self, "send_selection", text="Selection")
        sub.prop(self, "auto_screenshot", text="Viewport Screenshot")
        col.prop(self, "screenshot_max_size")
        col.prop(self, "attach_folder")
        col.separator()
        col.prop(self, "auto_approve")
        col.prop(self, "live_connection")
        sub = col.row(align=True)
        sub.active = self.live_connection
        sub.prop(self, "live_port")
        sub.operator("claude.copy_live_connection", text="", icon='COPYDOWN')
        col.prop(self, "show_stats")
        row = col.row(heading="Auto Summarize")
        row.prop(self, "auto_compact", text="")
        sub = row.row()
        sub.active = self.auto_compact
        sub.prop(self, "compact_at", text="At")
        col.prop(self, "max_display")
        col.separator()
        col.prop(self, "custom_instructions")
        col.prop(self, "extra_args")

    def _draw_files(self, layout, context):
        col = layout.column()
        col.use_property_split = True
        col.prop(self, "project_folders")
        sub = col.column(heading="Folder Names", align=True)
        sub.prop(self, "backup_dir")
        sub.prop(self, "assets_dir")
        sub.prop(self, "archive_dir")
        col.prop(self, "main_suffix")
        col.prop(self, "backup_stamp")
        col.separator()
        col.prop(self, "autosave")
        sub = col.column()
        sub.active = self.autosave
        sub.prop(self, "autosave_mode")
        sub.prop(self, "autosave_minutes")
        sub.prop(self, "autosave_keep")
        sub.prop(self, "autosave_before_risky")
        sub.prop(self, "autosave_on_memory")

    def _draw_renders(self, layout, context):
        self.studio.draw_renders(layout)

    def _draw_studio(self, layout, context):
        self.studio.draw_studio(layout)

    def _draw_local(self, layout, context):
        col = layout.column()
        col.use_property_split = True
        col.label(text="Local models run with LM Studio (models in ~/.lmstudio/models).", icon='INFO')
        col.prop(self, "local_model")
        col.prop(self, "local_context")
        col.prop(self, "local_effort")
        col.prop(self, "local_send_screenshot")

    def _draw_learn(self, layout, context):
        col = layout.column()
        col.use_property_split = True
        col.prop(self, "learn_from_errors")
        sub = col.column()
        sub.active = self.learn_from_errors
        sub.prop(self, "lessons_dir")


def register():
    bpy.utils.register_class(ClaudeAssistantPreferences)


def unregister():
    bpy.utils.unregister_class(ClaudeAssistantPreferences)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Sidebar (3D Viewport > N > Claude): registers the chat panel and its operators.

The parts live in their own modules: ui_common (helpers), ui_chat_ops (chat operators), ui_skills
(skill row), ui_setup (setup card, help), ui_files (files, backups, local models), ui_menus (menus,
drop handler, Enter in the input box) and ui_panel (the panels).
"""

import bpy
import bpy.utils.previews
from bpy.props import BoolProperty, StringProperty

from . import chatview, core

# Moved into their own modules; re-exported so `ui.<name>` keeps working.
from .ui_common import (
    RESERVED_ROWS, SKILLS_PER_ROW, WEEKDAYS, _ICON_IDS, _format_reset, _prop_with_placeholder,
    _usage_windows, att_icon
)  # noqa: F401
from .ui_chat_ops import (
    CLAUDE_OT_answer, CLAUDE_OT_approve, CLAUDE_OT_attach_files, CLAUDE_OT_compact_chat,
    CLAUDE_OT_copy_message, CLAUDE_OT_delete_chat, CLAUDE_OT_load_chat, CLAUDE_OT_new_chat, CLAUDE_OT_paste,
    CLAUDE_OT_remove_attachment, CLAUDE_OT_send, CLAUDE_OT_show_code, CLAUDE_OT_stop, CLAUDE_OT_undo_steps,
    CLAUDE_OT_usage_info
)  # noqa: F401
from .ui_skills import (
    CLAUDE_OT_new_skill, CLAUDE_OT_use_skill, _restore_skills, _save_skills, _skill_execute, _skill_ops,
    _skill_sync_pending, _toggle_skill, _unregister_skill_operators, skill_operator, sync_skill_operators
)  # noqa: F401
from .ui_setup import (
    CLAUDE_OT_allow_online, CLAUDE_OT_connect_client, CLAUDE_OT_copy_live_connection, CLAUDE_OT_example,
    CLAUDE_OT_install_cli, CLAUDE_OT_install_update, CLAUDE_OT_login, CLAUDE_OT_make_base_materials,
    CLAUDE_OT_setup_all, CLAUDE_OT_setup_cancel, CLAUDE_OT_setup_choice, CLAUDE_OT_setup_done,
    CLAUDE_OT_start_lmstudio, CLAUDE_OT_update_cli, CLAUDE_PT_help, EXAMPLES
)  # noqa: F401
from .ui_files import (
    CLAUDE_OT_autosave_now, CLAUDE_OT_crash_dismiss, CLAUDE_OT_crash_open_backup, CLAUDE_OT_edit_message,
    CLAUDE_OT_local_load, CLAUDE_OT_local_unload, CLAUDE_OT_make_project_folder, CLAUDE_OT_open_backups,
    CLAUDE_OT_open_crash_log, CLAUDE_OT_open_file, CLAUDE_OT_open_lessons, CLAUDE_OT_open_models_folder,
    CLAUDE_OT_open_path, CLAUDE_OT_refresh_local_models, CLAUDE_OT_save_as_original,
    CLAUDE_OT_set_local_model, CLAUDE_OT_version_goto
)  # noqa: F401
from .ui_menus import (
    CLAUDE_FH_attach, CLAUDE_MT_history, CLAUDE_MT_local_models, CLAUDE_MT_skills,
    CLAUDE_OT_prompt_confirmed, _clearing, _prompt_update, _send_prompt
)  # noqa: F401
from .ui_panel import (
    CLAUDE_PT_chat, CLAUDE_PT_settings, _setup_questions_open, _setup_steps_open, _setup_visible, _wrap_text
)  # noqa: F401

_previews = None


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
    if not bpy.app.timers.is_registered(_restore_skills):
        bpy.app.timers.register(_restore_skills, first_interval=1.0)
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
    if bpy.app.timers.is_registered(_restore_skills):
        bpy.app.timers.unregister(_restore_skills)
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

# SPDX-License-Identifier: GPL-3.0-or-later
"""AI Studio – AI chat and render studio in the 3D viewport sidebar.

Unofficial community add-on, not affiliated with or endorsed by Anthropic.
Two sidebar tabs: "Claude" (the chat) and "Studio" (cameras, light, materials,
render queue – formerly the stand-alone "Render Studio" extension).

Architecture:
- ``claude_cli``   : runs the local ``claude`` CLI (stream-json) in a thread.
- ``mcp_server``   : small MCP server (HTTP on 127.0.0.1 + token) through which
                     the model calls tools inside Blender.
- ``core``         : chat state, main-thread timer, tool dispatch, history.
- ``blender_tools``: scene description, code execution, screenshots, renders, undo.
- ``chatview``     : the fixed-height chat area and its scrolling.
- ``ui``           : chat sidebar panel, menus and operators.
- ``live``         : Live Connection for external Claude sessions (/live on that server).
- ``lmstudio``     : local models through LM Studio (Claude Code with ANTHROPIC_BASE_URL).
- ``autosave``     : regular backups and saves right before risky moments.
- ``attachments``, ``skills``, ``memory``, ``lessons``: the matching features.
- ``studio``       : Render Studio (sub-package); its ``claude_tools`` offers the
                     studio_* tools to the chat.
- ``i18n``         : German translation of the UI.
"""

from . import autosave, core, i18n, live, migrate, preferences, settings, setup_flow, studio, ui, updates


def register():
    moved = migrate.before_register()   # the old id 'claude_assistant' must be off first
    i18n.register()
    studio.register_prefs()   # the extension's preferences hold Studio's settings
    preferences.register()
    prefs = preferences.get_prefs()
    i18n.apply(bool(prefs and (prefs.language == 'DE' or prefs.german_interface)))
    ui.register()
    core.register()
    setup_flow.register()
    updates.register()
    live.register()
    autosave.register()
    studio.register()
    settings.register()
    migrate.after_register(moved)


def unregister():
    settings.unregister()
    studio.unregister()
    autosave.unregister()
    live.unregister()
    updates.unregister()
    setup_flow.unregister()
    core.unregister()
    ui.unregister()
    preferences.unregister()
    studio.unregister_prefs()
    i18n.unregister()

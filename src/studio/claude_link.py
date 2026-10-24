# SPDX-License-Identifier: GPL-3.0-or-later
"""Link between the Studio tabs and the Claude chat.

Each tab (Render, Light, Material) has a prompt line: the wish goes to the chat with the
skills bound to that tab (front matter "studio_tab", see skills.py); the tab shows that
Claude is working and the first line of the answer, the discussion goes on in the chat.
"""


def assistant_core():
    """The chat's core module (same extension since Studio and the chat were combined)."""
    from .. import core
    return core


def available():
    return assistant_core() is not None


def show_chat(region):
    """Switch the sidebar to the Claude tab."""
    if region is not None and getattr(region, "type", "") == 'UI':
        try:
            region.active_panel_category = "Claude"
        except Exception:
            pass

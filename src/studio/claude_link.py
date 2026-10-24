# SPDX-License-Identifier: GPL-3.0-or-later
"""Link between the Studio tabs and the Claude chat.

The skills for the Studio tabs (camera, light, hdri, material, render) ship with
the add-on (skills_default/) and are installed once into the skills folder like
all other skills (skills.py); after that they belong to the user and are never
rewritten. Their front matter "studio_tab" binds them to a Studio tab: the chat
uses them while you come from that tab (see last_tab).
"""

import time

# The Studio tab you were in last (set while the Studio panel is shown); the chat adds
# that tab's skills to your messages until you dismiss them or go to another tab.
last_tab = {"tab": "", "time": 0.0, "dismissed": False}


def note_tab(tab):
    """Called by the Studio panel's draw: you are looking at `tab`."""
    if last_tab["tab"] != tab:
        last_tab["dismissed"] = False
    last_tab.update(tab=tab, time=time.time())


def studio_skills(workdir):
    """The skills bound to the Studio tab you came from (empty if dismissed)."""
    if not last_tab["tab"] or last_tab["dismissed"]:
        return []
    from .. import skills
    return [s for s in skills.list_skills(workdir) if s.get("studio_tab") == last_tab["tab"]]


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

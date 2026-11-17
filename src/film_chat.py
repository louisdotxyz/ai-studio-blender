# SPDX-License-Identifier: GPL-3.0-or-later
"""The cut chat: in the Video Editor the chat is its own conversation ("Schnitt"), one per file and scene.

When the window shows a Video Editor and no 3D view (the Video Editing workspace), the chat switches to
the scene's cut chat; back in a workspace with a 3D view it switches back to the chat that was open
there. Messages from the cut chat carry a line about the Video Editor (playhead, active strip, which 3D
frame it shows) and the CUT skill comes along by itself."""

import bpy

from . import core

CHAT_KEY = "studio_cut_chat"       # on the scene: the id of its cut chat
TITLE = "Schnitt"
_state = {"mode": None, "main": None, "fresh": None}


def _window():
    wm = bpy.context.window_manager
    window = bpy.context.window
    return window or (wm.windows[0] if wm and wm.windows else None)


def video_mode(window=None):
    """The window shows the Video Editor and no 3D view."""
    window = window or _window()
    if window is None:
        return False
    types = {a.type for a in window.screen.areas}
    return 'SEQUENCE_EDITOR' in types and 'VIEW_3D' not in types


def _scene(window):
    from .studio import cut
    space = window.workspace
    return cut.film_scene(getattr(space, "sequencer_scene", None) or window.scene)


def _cut_chat_id(scene):
    try:
        return scene.get(CHAT_KEY, "") if scene is not None else ""
    except (TypeError, AttributeError):
        return ""


def in_cut_chat(scene=None):
    window = _window()
    scene = scene or (_scene(window) if window else None)
    cid = _cut_chat_id(scene)
    return bool(core.session_id and core.session_id == cid) or (
        not core.session_id and _state["fresh"] is not None)


def _switch(window):
    scene = _scene(window)
    if video_mode(window):
        if in_cut_chat(scene):
            return
        _state["main"] = core.session_id
        cid = _cut_chat_id(scene)
        if cid and any(c.get("id") == cid for c in core.list_chats()):
            core.load_chat(cid)
            _state["fresh"] = None
        else:
            core.new_chat()
            core.pending_title = "%s · %s" % (TITLE, scene.name)
            _state["fresh"] = scene.name
    else:
        if not in_cut_chat(scene):
            return
        main = _state["main"]
        if main and any(c.get("id") == main for c in core.list_chats()):
            core.load_chat(main)
        else:
            core.new_chat()
        _state["fresh"] = None


def _remember():
    """The cut chat got its id with its first answer: the scene keeps it."""
    name = _state["fresh"]
    if name and core.session_id:
        scene = bpy.data.scenes.get(name)
        if scene is not None:
            try:
                scene[CHAT_KEY] = core.session_id
            except (TypeError, AttributeError):
                pass
        _state["fresh"] = None


def _tick():
    try:
        _remember()
        window = _window()
        if window is not None and not core.is_busy():
            mode = video_mode(window)
            if mode != _state["mode"]:
                if _state["mode"] is not None:       # not on start: the open chat stays
                    _switch(window)
                _state["mode"] = mode
    except Exception as ex:                          # never stop the timer
        print("AI Studio: cut chat:", ex)
    return 0.5


def context_line():
    """For a message from the cut chat: what the Video Editor shows now, or ""."""
    window = _window()
    if window is None or not video_mode(window):
        return ""
    try:
        from .studio import film_edit
        return film_edit.context_line(_scene(window))
    except Exception:
        return ""


def where():
    """(section, cut frame, 3D frame, selected sections) of the Video Editor now, or None outside it."""
    window = _window()
    if window is None or not video_mode(window):
        return None
    try:
        from .studio import cut, film_edit
        scene = _scene(window)
        here = cut.playhead(scene)
        under = next((s for s in film_edit._top_level(scene) if s.type == 'IMAGE' and cut._get(s, cut.SECTION_KEY)
                      and s.frame_final_start <= here < s.frame_final_end and not s.mute), None)
        section = cut._get(under, cut.SECTION_KEY) if under is not None else ""
        shown = film_edit.frame_of(under, here) if under is not None else None
        picked = [cut._get(s, cut.SECTION_KEY) or s.name for s in film_edit._top_level(scene)
                  if s.select and s.type in {'IMAGE', 'MOVIE'}]
        return section, here, shown, picked
    except Exception:
        return None


def frame_tag():
    """'(F160 · 02 Orbit)' for a message written in the cut chat: what the user means by 'here'."""
    found = where()
    if found is None:
        return ""
    section, here, shown, _picked = found
    return "(F%d%s) " % (shown if shown is not None else here, " · " + section if section else "")


def register():
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=1.0, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)

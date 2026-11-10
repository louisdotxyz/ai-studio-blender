# SPDX-License-Identifier: GPL-3.0-or-later
"""Live Connection: external Claude sessions (Cowork, blender-mcp) reach the open Blender.

Runs on the chat's HTTP server (mcp_server, path /live) with its own token. Port and token are
in ~/.config/ai-studio/live.json (only readable by the user); the Blender opened last owns it.
The requests are handled in core._dispatch_live on the main thread.
"""

import atexit
import json
import os
import secrets
import tempfile

import bpy
from bpy.app.handlers import persistent

from . import mcp_server
from .preferences import get_prefs

LIVE_FILE = os.path.expanduser("~/.config/ai-studio/live.json")


def _blend_file():
    return getattr(bpy.data, "filepath", "")   # bpy.data is restricted while add-ons register


def _owner():
    """The pid in live.json, None without a readable file."""
    try:
        with open(LIVE_FILE, encoding="utf-8") as f:
            return json.load(f).get("pid")
    except (OSError, ValueError, AttributeError):
        return None


def _write():
    os.makedirs(os.path.dirname(LIVE_FILE), mode=0o700, exist_ok=True)
    data = {"port": mcp_server.port(), "token": mcp_server.live_token(), "pid": os.getpid(),
            "file": mcp_server.live_state["file"], "version": mcp_server.live_state["version"]}
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(LIVE_FILE), prefix=".live-")  # mode 0600
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, LIVE_FILE)


def _remove_file():
    if _owner() == os.getpid():
        try:
            os.remove(LIVE_FILE)
        except OSError:
            pass


def is_running():
    return mcp_server.live_token() is not None


def start():
    if is_running():
        return
    prefs = get_prefs()
    port = prefs.live_port if prefs is not None else 0
    try:
        mcp_server.start(port)
    except OSError:          # the fixed port is taken: any free one
        mcp_server.start()
    mcp_server.live_state.update(file=_blend_file(), version=bpy.app.version_string)
    mcp_server.set_live_token(secrets.token_urlsafe(32))
    _write()
    atexit.register(_remove_file)


def stop():
    if not is_running():
        return
    mcp_server.set_live_token(None)
    _remove_file()
    atexit.unregister(_remove_file)


def connection_text():
    """Instructions for another Claude session, for the clipboard."""
    return ("Blender is open with AI Studio's Live Connection. Read ~/.config/ai-studio/live.json "
            "(port, token), then send POST http://127.0.0.1:<port>/live with the header "
            "'Authorization: Bearer <token>' and a JSON body: {\"op\": \"ping\"}, {\"op\": \"info\"} "
            "(scene overview), {\"op\": \"object\", \"name\": \"…\"}, {\"op\": \"python\", \"code\": \"…\", "
            "\"description\": \"…\"} (bpy, bmesh, mathutils, math imported; print() returns output; one undo "
            "step; the user may have to approve), {\"op\": \"screenshot\", \"max_size\": 1280} (PNG as base64). "
            "Answers are JSON with ok and output/error/text/png. Never save the .blend file; move objects "
            "you replace into the archive with api.archive(objects) instead of deleting them.")


def sync():
    """Start or stop to match the preference."""
    prefs = get_prefs()
    if prefs is None or prefs.live_connection:
        start()
    else:
        stop()


@persistent
def _file_changed(_arg=None, _arg2=None):
    if not is_running():
        return
    mcp_server.live_state["file"] = _blend_file()
    if _owner() in (os.getpid(), None):   # another Blender opened later keeps the file
        _write()


def register():
    bpy.app.handlers.load_post.append(_file_changed)
    bpy.app.handlers.save_post.append(_file_changed)    # "Save As" changes the file too
    if not bpy.app.background:   # background Blenders (renders, scripts) must not take the file over
        sync()
        from . import claude_cli, live_clients
        prefs = get_prefs()
        live_clients.refresh(claude_cli.find_claude(prefs.claude_path if prefs else ""))


def unregister():
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.save_post):
        if _file_changed in handlers:
            handlers.remove(_file_changed)
    stop()

# SPDX-License-Identifier: GPL-3.0-or-later
"""Live Connection: external Claude sessions (Cowork, blender-mcp) reach the open Blender.

Runs on the chat's HTTP server (mcp_server, path /live) with its own token. Every open Blender
writes ~/.config/ai-studio/live/<pid>.json (port, token, file, when the user last worked in it);
~/.config/ai-studio/live.json is the one the user worked in last (older clients read only it).
Several Blenders: live_mcp picks the one asked for by file, else the last one worked in.
The requests are handled in core._dispatch_live on the main thread.
"""

import atexit
import glob
import json
import os
import secrets
import tempfile
import time

import bpy
from bpy.app.handlers import persistent

from . import mcp_server
from .preferences import get_prefs

LIVE_FILE = os.path.expanduser("~/.config/ai-studio/live.json")
_active = [0.0]          # when this Blender last told the others it is the one being worked in


def _blend_file():
    return getattr(bpy.data, "filepath", "")   # bpy.data is restricted while add-ons register


def _owner():
    """The pid in live.json, None without a readable file."""
    try:
        with open(LIVE_FILE, encoding="utf-8") as f:
            return json.load(f).get("pid")
    except (OSError, ValueError, AttributeError):
        return None


def _data():
    return {"port": mcp_server.port(), "token": mcp_server.live_token(), "pid": os.getpid(),
            "file": mcp_server.live_state["file"], "version": mcp_server.live_state["version"],
            "active": _active[0] or time.time()}


def _dump(path, data):
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".live-")  # mode 0600
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _instances_dir():
    return os.path.join(os.path.dirname(LIVE_FILE), "live")


def _mine():
    return os.path.join(_instances_dir(), "%d.json" % os.getpid())


def _alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError, TypeError):
        return False


def instances():
    """[data] of every open Blender with the Live Connection, the one worked in last first."""
    found = []
    for path in glob.glob(os.path.join(_instances_dir(), "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if _alive(data.get("pid")):
            found.append(data)
        else:
            try:
                os.remove(path)            # left behind by a crash
            except OSError:
                pass
    return sorted(found, key=lambda d: -float(d.get("active") or 0))


def _write():
    data = _data()
    _dump(_mine(), data)
    _dump(LIVE_FILE, data)


def _remove_file():
    try:
        os.remove(_mine())
    except OSError:
        pass
    if _owner() == os.getpid():          # another Blender still open: it gets live.json
        rest = [d for d in instances() if d.get("pid") != os.getpid()]
        try:
            if rest:
                _dump(LIVE_FILE, rest[0])
            else:
                os.remove(LIVE_FILE)
        except OSError:
            pass


def touch():
    """The user works in this Blender: it becomes the one other Claude apps talk to (every 2 s at most)."""
    if not is_running():
        return
    now = time.time()
    if now - _active[0] < 2.0:
        return
    _active[0] = now
    try:
        _dump(_mine(), _data())
        if _owner() != os.getpid():
            _dump(LIVE_FILE, _data())
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
    _active[0] = time.time()             # opening or saving a file: working in this Blender
    _write()


@persistent
def _worked_in(_scene=None, _depsgraph=None):
    touch()


def register():
    bpy.app.handlers.load_post.append(_file_changed)
    bpy.app.handlers.save_post.append(_file_changed)    # "Save As" changes the file too
    bpy.app.handlers.depsgraph_update_post.append(_worked_in)   # any change: the user works here
    # background Blenders (renders, scripts) must not take the file over; nor a second window opened for tests
    if not bpy.app.background and not os.environ.get("AI_STUDIO_NO_LIVE"):
        sync()
        from . import claude_cli, live_clients
        prefs = get_prefs()
        live_clients.refresh(claude_cli.find_claude(prefs.claude_path if prefs else ""))


def unregister():
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.save_post):
        if _file_changed in handlers:
            handlers.remove(_file_changed)
    if _worked_in in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_worked_in)
    stop()

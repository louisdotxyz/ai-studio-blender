# SPDX-License-Identifier: GPL-3.0-or-later
"""Chat history: saving, loading, naming and deleting chats (in the extension's user directory). The chat
state itself lives in core."""

import os
import re
import time
import traceback

from .preferences import get_prefs
from . import core


# ---------------------------------------------------------------------------
# Chat history (saved in the extension's user directory)

def list_chats():
    if core._chats is None:
        core._chats = core._read_json("chats.json", [])
        if not isinstance(core._chats, list):
            core._chats = []
    return core._chats


def _write_chats(change):
    """Read the saved chats again, apply change(list) → list and write them back under a lock: a
    second Blender open at the same time keeps its chats (each used to write its own whole list)."""
    lock = None
    try:
        import fcntl
        lock = open(os.path.join(core.workdir(), ".chats.lock"), "w")
        fcntl.flock(lock, fcntl.LOCK_EX)
    except (ImportError, OSError):
        pass
    try:
        stored = core._read_json("chats.json", [])
        merged = change([c for c in stored if isinstance(c, dict)] if isinstance(stored, list) else [])
        core._write_json("chats.json", merged)
        core._chats = merged
    finally:
        if lock is not None:
            lock.close()


TITLE_WORDS = 5
TITLE_CHARS = 32


def short_title(text, taken=()):
    """A few words of the first message ("Kamera frontal stellen"), a number when the same
    title exists already ("… _02")."""
    words = [w for w in re.sub(r"[\[\]{}()\"'`*_#>|]+", " ", text or "").split() if w]
    title = ""
    for word in words[:TITLE_WORDS]:
        if title and len(title) + 1 + len(word) > TITLE_CHARS:
            break
        title = (title + " " + word).strip()
    title = (title or "Chat")[:TITLE_CHARS].rstrip(" ,.;:!?-")
    if title in taken:
        n = 2
        while "%s_%02d" % (title, n) in taken:
            n += 1
        title = "%s_%02d" % (title, n)
    return title


def save_current_chat():
    if not core.session_id or not core.messages:
        return
    chats = list_chats()
    entry = next((c for c in chats if c.get("id") == core.session_id), None)
    if entry is None:
        entry = {"id": core.session_id}
        chats.insert(0, entry)
    first_user = next((m for m in core.messages if m["role"] == "user"), None)
    title = (first_user["text"] if first_user and first_user["text"] else
             (first_user["attachments"][0]["name"] if first_user and first_user.get("attachments") else "Chat"))
    if core.pending_title and not entry.get("renamed"):
        entry.update(title=core.pending_title, renamed=True)
        core.pending_title = ""
    if not entry.get("renamed") and (not entry.get("title") or entry["title"] == "Chat" and first_user):
        others = {c.get("title") for c in chats if c is not entry}
        entry["title"] = short_title(title, others)
    entry.update(updated=time.time(), messages=core.messages[-400:], context=dict(core.context))
    try:
        import bpy
        if bpy.data.filepath:
            entry["file"] = _file_key(bpy.data.filepath)   # reopened with this file (restore_for_file)
    except AttributeError:
        pass
    prefs = get_prefs()
    if prefs:  # the chat continues with the model it was used with
        entry["model"] = {key: getattr(prefs, key) for key in MODEL_KEYS}
    chats.sort(key=lambda c: -c.get("updated", 0))
    del chats[core.MAX_SAVED_CHATS:]

    def put(stored):
        stored = [c for c in stored if c.get("id") != entry["id"]] + [entry]
        stored.sort(key=lambda c: -c.get("updated", 0))
        return stored[:core.MAX_SAVED_CHATS]
    try:
        _write_chats(put)
    except OSError:
        traceback.print_exc()


_chat_serial = [0]   # changes with every new/loaded chat: code values are kept per chat


def new_chat():
    if core.is_busy():
        return
    _chat_serial[0] += 1
    save_current_chat()
    core.messages.clear()
    core.attachments.clear()
    core.session_id = None
    core.pending_title = ""
    core._last_sent.clear()
    core._skills_sent.clear()
    core.context.update(used=0, window=0)
    _restore_model(default_model())  # every new chat starts with the default model (preferences)
    core.mark_dirty()


def load_chat(chat_id):
    if core.is_busy():
        return
    _chat_serial[0] += 1
    entry = next((c for c in list_chats() if c.get("id") == chat_id), None)
    if entry is None:
        return
    save_current_chat()
    core.messages[:] = [dict(m) for m in entry.get("messages", [])]
    core.session_id = chat_id
    core._last_sent.clear()
    core._skills_sent.clear()
    _restore_model(entry.get("model"))
    core.context.update((entry.get("context") or {"used": 0, "window": 0}))
    core.mark_dirty()


def _file_key(filepath):
    """A file and its backup copies count as one: <project folder>/<name>."""
    from . import autosave
    try:
        return os.path.normcase(os.path.join(autosave.main_folder(filepath), autosave.project_name(filepath)))
    except Exception:
        return os.path.normcase(filepath)


def restore_for_file(filepath, chat_id=""):
    """Blender (re)opened a file and the chat is still empty: the chat that was open last with this file
    comes back (after a crash: the chat that was open then). Returns the chat's id or ""."""
    if core.messages or core.is_busy():
        return ""
    chats = list_chats()
    entry = next((c for c in chats if chat_id and c.get("id") == chat_id), None)
    if entry is None and filepath:
        key = _file_key(filepath)
        entry = max((c for c in chats if c.get("file") == key), key=lambda c: c.get("updated", 0), default=None)
    if entry is None:
        return ""
    load_chat(entry["id"])
    return entry["id"]


def current_title():
    entry = next((c for c in list_chats() if c.get("id") == core.session_id), None) if core.session_id else None
    return entry.get("title", "") if entry else core.pending_title


def set_current_title(title):
    title = " ".join((title or "").split())[:70]
    if core.session_id and any(c.get("id") == core.session_id for c in list_chats()):
        if title:
            rename_chat(core.session_id, title)
    else:
        core.pending_title = title
    core.mark_dirty()


MODEL_KEYS = ("backend", "model", "effort", "local_model", "local_effort")


def default_model():
    """What a new chat starts with: Preferences › General › Default Model and Effort."""
    prefs = get_prefs()
    choice = getattr(prefs, "default_model", 'sonnet')
    if choice == 'LOCAL':
        return {"backend": 'LOCAL'}
    return {"backend": 'CLOUD', "model": choice, "effort": getattr(prefs, "default_effort", 'medium')}


def _restore_model(saved):
    prefs = get_prefs()
    if not prefs or not isinstance(saved, dict):
        return
    for key in MODEL_KEYS:  # model before effort: switching the model resets the effort
        if key in saved and getattr(prefs, key) != saved[key]:
            try:
                setattr(prefs, key, saved[key])
            except (TypeError, ValueError):
                pass  # e.g. a model that no longer exists


def rename_chat(chat_id, title):
    title = " ".join((title or "").split())[:70]
    entry = next((c for c in list_chats() if c.get("id") == chat_id), None)
    if entry is None or not title:
        return
    entry.update(title=title, renamed=True)

    def rename(stored):
        for c in stored:
            if c.get("id") == chat_id:
                c.update(title=title, renamed=True)
        return stored
    try:
        _write_chats(rename)
    except OSError:
        traceback.print_exc()
    core.mark_dirty()


def delete_chat(chat_id):
    _write_chats(lambda stored: [c for c in stored if c.get("id") != chat_id])
    if core.session_id == chat_id and not core.is_busy():
        core.messages.clear()
        core.session_id = None
    core.mark_dirty()

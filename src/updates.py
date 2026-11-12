# SPDX-License-Identifier: GPL-3.0-or-later
"""Updates of AI Studio itself (Preferences › General › Updates).

Installed from the add-on's repository (the install page on GitHub), Blender knows where new
versions come from. A few seconds after Blender starts the add-on
reads the repository's index.json in a thread, at most once a day. A newer version is then
- AUTO: installed right away, once Blender is idle (no chat answer, no render running): the
  extension's own "Update" (extensions.package_install), after which Blender loads it again;
  the chat says so;
- ASK: shown as a card in the chat with an Install button;
- OFF: not looked for.
Installed from a zip (no repository) there is nothing to update from: then nothing happens.
"""

import json
import os
import threading
import time
import urllib.request

import bpy

PKG_ID = "AI_Studio"
CHECK_EVERY = 24 * 3600
START_DELAY = 8.0

state = {"available": "", "thread": None, "error": "", "installing": False}


def _data_file():
    from .studio import paths
    return os.path.join(paths.data_dir(), "updates.json")


def _read():
    try:
        with open(_data_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data):
    try:
        with open(_data_file(), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def version_tuple(text):
    parts = []
    for part in str(text or "0").split("-")[0].split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts + [0] * (3 - len(parts)))


def installed_version():
    import tomllib
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "blender_manifest.toml")
    try:
        with open(path, "rb") as f:
            return tomllib.load(f).get("version", "0.0.0")
    except (OSError, ValueError):
        return "0.0.0"


def own_repo():
    """(index, repo) of the repository AI Studio was installed from, None for a local one
    (installed from a zip) – there is nothing to update from then."""
    parts = __package__.split(".")
    if len(parts) < 3 or parts[0] != "bl_ext":
        return None
    for index, repo in enumerate(bpy.context.preferences.extensions.repos):
        if repo.module == parts[1]:
            return (index, repo) if repo.use_remote_url and repo.remote_url else None
    return None


def newest_in_index(data, blender=None):
    """The newest version of AI Studio in an index.json that this Blender can run."""
    blender = tuple(blender or bpy.app.version)
    best = ""
    for item in data.get("data", []):
        if item.get("id") != PKG_ID:
            continue
        if version_tuple(item.get("blender_version_min", "0")) > blender:
            continue
        if item.get("blender_version_max") and version_tuple(item["blender_version_max"]) <= blender:
            continue
        if version_tuple(item.get("version")) > version_tuple(best):
            best = item.get("version", "")
    return best


def mode():
    from .preferences import get_prefs
    prefs = get_prefs()
    return getattr(prefs, "updates", 'AUTO') if prefs else 'OFF'


def check(force=False):
    """Look for a newer version in the background (state["available"])."""
    if mode() == 'OFF' or not bpy.app.online_access:
        return False
    found = own_repo()
    if found is None:
        return False
    if state["thread"] is not None and state["thread"].is_alive():
        return False
    data = _read()
    if not force and time.time() - data.get("checked", 0) < CHECK_EVERY:
        if version_tuple(data.get("available")) > version_tuple(installed_version()):
            state["available"] = data["available"]
        return False
    url = found[1].remote_url
    current = installed_version()
    blender = tuple(bpy.app.version)

    def run():
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Blender-AI-Studio"})
            with urllib.request.urlopen(request, timeout=15) as response:
                newest = newest_in_index(json.loads(response.read().decode("utf-8")), blender)
            state["error"] = ""
        except Exception as exc:
            state["error"] = str(exc)
            return
        available = newest if version_tuple(newest) > version_tuple(current) else ""
        state["available"] = available
        _write(dict(_read(), checked=time.time(), available=available))

    state["thread"] = threading.Thread(target=run, name="ai-studio-updates", daemon=True)
    state["thread"].start()
    return True


def busy():
    from . import core
    from .studio import runner
    return bool(core.is_busy() or core.pending or core.questions or runner.RUN.busy)


def install():
    """Install the newer version now (Blender disables, replaces and enables the add-on).
    True when it was started."""
    found = own_repo()
    if found is None or not state["available"] or state["installing"]:
        return False
    index, _repo = found
    state["installing"] = True
    target = state["available"]
    _write(dict(_read(), updated_from=installed_version(), updated_to=target))
    try:
        bpy.ops.extensions.repo_sync(repo_index=index)
        bpy.ops.extensions.package_install(repo_index=index, pkg_id=PKG_ID, enable_on_install=True)
    except (RuntimeError, AttributeError, TypeError) as exc:
        state["error"] = str(exc)
        data = _read()
        data.pop("updated_to", None)
        _write(data)
        return False
    finally:
        state["installing"] = False
    state["available"] = ""
    return True


def announce():
    """After an update: one line in the chat (run by the new version when it starts)."""
    data = _read()
    target = data.pop("updated_to", None)
    if not target:
        return
    _write(data)
    if version_tuple(installed_version()) >= version_tuple(target):
        from . import core
        from .i18n import tr
        core.add_message("info", tr("AI Studio was updated to {}.").format(installed_version()))


def tick():
    """core's timer: install a found update once Blender is idle (AUTO)."""
    if state["available"] and mode() == 'AUTO' and not state["installing"] and not busy():
        bpy.app.timers.register(_install_later, first_interval=0.1)
        state["installing"] = True   # not twice; install() sets it again


def _install_later():
    state["installing"] = False
    if state["available"] and not busy():
        install()
    return None


def _start():
    announce()
    check()
    return None


def register():
    from . import core
    if tick not in core.tick_hooks:
        core.tick_hooks.append(tick)
    if not bpy.app.background:
        bpy.app.timers.register(_start, first_interval=START_DELAY)


def unregister():
    from . import core
    if tick in core.tick_hooks:
        core.tick_hooks.remove(tick)
    if bpy.app.timers.is_registered(_start):
        bpy.app.timers.unregister(_start)
    if bpy.app.timers.is_registered(_install_later):
        bpy.app.timers.unregister(_install_later)

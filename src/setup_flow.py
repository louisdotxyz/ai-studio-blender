# SPDX-License-Identifier: GPL-3.0-or-later
"""Setup in one click (setup card › Set Up Everything): online access, Claude Code, login,
base materials, Live Connection for the other Claude apps. Each step that is already done is
skipped. The slow steps (download, login, Blender in the background) run in a thread, the
steps that touch Blender's data run on the main thread (core's timer calls ``tick``).

The single buttons of the card use the same steps (Install = install + log in).
"""

import os
import queue
import subprocess
import threading
import time

import bpy

from . import claude_cli, core, live_clients
from .i18n import tr

ALL = ("online", "install", "login", "materials", "connect")
LOGIN_WAIT = 600.0     # seconds the login window gets
LOGIN_POLL = 3.0

flow = {"thread": None, "step": "", "error": "", "installer": None, "since": 0.0}
_main = queue.Queue()  # callables for the main thread


def running():
    return flow["thread"] is not None and flow["thread"].is_alive()


def missing():
    """True when the card has something left that the button can do."""
    state = core.setup_state()
    prefs = core.get_prefs()
    live = bool(prefs and prefs.live_connection)
    return (state["online"] == 'OFF' or not state["cli"] or state["login"] is False or not state["base"]
            or live and state["cli"] and not state["live_code"]
            or live and state["live_desktop"] is False)


def status():
    """(text, factor or None) for the card while it runs, else None."""
    if not running():
        return None
    step = flow["step"]
    if step == "install":
        installer = flow["installer"]
        factor, phase = installer.progress() if installer else (None, 'DOWNLOAD')
        if phase == 'SETUP':
            return tr("Setting up Claude Code…"), None
        if factor is not None and installer.size:
            return tr("Downloading Claude Code: {} of {} MB").format(
                int(installer.downloaded() / 1e6), int(installer.size / 1e6)), factor
        return tr("Downloading Claude Code…"), None
    if step == "login":
        return tr("Log in in the browser (the terminal window closes by itself)"), None
    if step == "materials":
        return tr("Creating the base materials…"), None
    if step == "connect":
        return tr("Connecting the Claude apps…"), None
    return tr("Setting up…"), None


def _on_main(func, *args):
    _main.put((func, args))


def start(steps=ALL):
    """Run the steps that are still missing. False (and core message) when it can't start."""
    if running():
        return False
    flow["error"] = ""
    if "online" in steps and core.online_state() != 'ON':
        if core.online_state() == 'LOCKED' or not core.allow_online():
            core.add_message("error", tr("Blender was started with --offline-mode: start it without it"))
            return False
    prefs = core.get_prefs()
    from .studio import tab_material
    job = {
        "steps": steps,
        "claude_config": prefs.claude_path if prefs else "",
        "live": bool(prefs and prefs.live_connection),
        "materials": "materials" in steps and not tab_material.base_library_file(),
        "materials_folder": materials_folder(),
        "blender": bpy.app.binary_path,
    }
    flow["since"] = time.monotonic()
    flow["thread"] = threading.Thread(target=_run, args=(job,), name="ai-studio-setup", daemon=True)
    flow["thread"].start()
    core.mark_dirty()
    return True


def cancel():
    installer = flow["installer"]
    if installer:
        installer.cancel()
    flow["error"] = "cancelled"


def materials_folder():
    from .studio import paths
    libraries = bpy.context.preferences.filepaths.asset_libraries
    return bpy.path.abspath(libraries[0].path) if len(libraries) else paths.data_dir()


def build_base_materials(blender, folder):
    """'Base Materials.blend' made by a background Blender. Returns its path; RuntimeError."""
    script = os.path.join(os.path.dirname(__file__), "studio", "make_base_materials.py")
    result = subprocess.run([blender, "-b", "--factory-startup", "-noaudio", "--python", script, "--", folder],
                            capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    path = os.path.join(folder, "Base Materials.blend")
    if not os.path.isfile(path):
        raise RuntimeError(tr("Could not create the base materials: {}").format(
            (result.stderr or result.stdout)[-300:]))
    return path


def use_base_materials(path):
    """Main thread: the Material tab uses the new file."""
    from .studio import runner as studio_runner, tab_material
    p = studio_runner.prefs()
    if p is not None:
        p.material_library = path
    tab_material._base_loaded.clear()
    bpy.context.preferences.is_dirty = True


def _fail(text):
    flow["error"] = text
    _on_main(core.add_message, "error", text)


def _run(job):
    steps = job["steps"]
    try:
        path = claude_cli.find_claude(job["claude_config"])
        if "install" in steps and not path:
            flow["step"] = "install"
            installer = flow["installer"] = claude_cli.Installer().start()
            while installer.running:
                time.sleep(0.5)
            flow["installer"] = None
            path = claude_cli.find_claude(job["claude_config"])
            if installer.cancelled:
                return
            if not installer.ok or not path:
                _fail(tr("Claude Code could not be installed:") + "\n" + installer.error())
                return
            _on_main(core.add_message, "info", tr("Claude Code is installed."))

        if "login" in steps and path:
            flow["step"] = "login"
            if claude_cli.logged_in(path) is False:
                _on_main(core.open_login)
                end = time.monotonic() + LOGIN_WAIT
                done = False
                while not done and time.monotonic() < end and flow["error"] != "cancelled":
                    time.sleep(LOGIN_POLL)
                    done = bool(claude_cli.logged_in(path))
                if not done:
                    if flow["error"] != "cancelled":
                        _fail(tr("The login was not finished: log in with the button in the card"))
                    return
                _on_main(_logged_in)
            else:
                _on_main(_logged_in, False)

        if job["materials"]:
            flow["step"] = "materials"
            try:
                _on_main(use_base_materials, build_base_materials(job["blender"], job["materials_folder"]))
            except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
                _fail(str(exc))   # the other steps still run

        if "connect" in steps and job["live"]:
            flow["step"] = "connect"
            try:
                if path and not live_clients.code_connected():
                    live_clients.connect_code(path)
                if live_clients.desktop_installed() and not live_clients.desktop_connected():
                    live_clients.connect_desktop()
                    _on_main(core.add_message, "info", tr("The Claude app gets the Blender tools after it "
                                                          "was quit and opened again once."))
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                _fail(tr("Could not connect the Claude apps: {}").format(exc))
        if not flow["error"] and len(steps) > 2:
            _on_main(core.add_message, "info", tr("Setup complete. Ask Claude something about your scene."))
    finally:
        flow["step"] = ""
        _on_main(core.mark_dirty)


def _logged_in(say=True):
    core._set_login_needed(False)
    core._set_flag("login_ok", True)
    if say:
        core.add_message("info", tr("Logged in to Claude."))


def tick():
    """core's timer: main-thread work of the setup, and redraws while it runs (progress)."""
    while True:
        try:
            func, args = _main.get_nowait()
        except queue.Empty:
            break
        func(*args)
        core._setup_cache["state"] = None
        core.mark_dirty()
    if running():
        core.mark_dirty()


def register():
    if tick not in core.tick_hooks:
        core.tick_hooks.append(tick)


def unregister():
    if tick in core.tick_hooks:
        core.tick_hooks.remove(tick)
    cancel()

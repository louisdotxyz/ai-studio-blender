# SPDX-License-Identifier: GPL-3.0-or-later
"""Auto save: regular backups, and extra ones right before risky moments.

- Every few minutes, if the scene changed since the last save.
- Before the model's code runs and before renders (preview, queue, environment bake):
  the moments in which Blender most often crashes. Throttled, so a run of code
  steps does not save every time.
- When macOS reports memory pressure (rising to warn, or critical) – usually shortly
  before a crash from running out of memory.

Project folder (one per .blend file, made when a new file is saved the first time, or with
the button "Make project folder" for older files):

    <name>/<name>.blend              the main file (with a suffix if set, e.g. <name>_now.blend)
    <name>/<name>.blend1             Blender's previous save
    <name>/<name>.claude.md          the project memory (memory.py)
    <name>/backups/<name>_2026-10-24_12-58.blend   the newest N backups
    <name>/assets/                   textures, HDRIs … of this project
    <name>/archive/                  your own milestones – the add-on never puts anything there

Default mode: backup copies, the user's file itself is not touched. Mode "File"
saves the .blend itself. Unsaved files go to <extension user dir>/autosave.
Folder names, the main file suffix and the time stamp are set in Preferences › Files.

Freeze watch: a timer touches a heartbeat file every few seconds. A small helper
process (freeze_watch.py) samples Blender's threads with macOS `sample` when the
heartbeat stops (Blender hangs) and writes freeze_<time>.txt; faulthandler adds
the Python side to crash_trace.txt.
"""

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback

import bpy

CHECK_INTERVAL = 15.0     # seconds between timer checks
RISKY_MIN_GAP = 120.0     # at most one "before risky" save per 2 minutes
CRITICAL_MIN_GAP = 300.0  # while memory stays critical: at most one save per 5 minutes
HEARTBEAT = 2.0           # seconds between heartbeats for the freeze watch
STALL = 30.0              # no heartbeat for this long = Blender hangs

# Time stamps in backup names (all are recognized, the preferences choose the one written)
_STAMPS = {'YMD': "%Y-%m-%d_%H-%M", 'HMD': "%H-%M_%d.%m.%y"}
_STAMP_RES = (re.compile(r"_\d\d-\d\d_\d\d\.\d\d\.\d\d(?:-\d+)?$"),            # _12-58_24.10.26
              re.compile(r"_\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d(?:-\d+)?$"),         # _2026-10-24_12-58-07
              re.compile(r"_\d{4}-\d\d-\d\d_\d\d-\d\d(?:-\d+)?$"))                # _2026-10-24_12-58


def _setting(attr, default):
    """A Files setting from the preferences (Preferences › Files)."""
    prefs = _prefs()
    value = getattr(prefs, attr, None) if prefs is not None else None
    if isinstance(value, str):
        value = value.strip()
        return value if value or attr == "main_suffix" else default
    return default if value is None else value


def backup_dir_name():
    return _setting("backup_dir", "backups")


def archive_dir_name():
    return _setting("archive_dir", "archive")


def assets_dir_name():
    return _setting("assets_dir", "assets")


def main_suffix():
    return _setting("main_suffix", "")

state = {"last": 0.0, "path": "", "reason": "", "error": "", "pressure": 1, "step": ""}
last_crash = None   # {"time", "file", "step", "backup"} if the previous session did not end cleanly
_changed = True           # scene edited since the last save
_last_pressure_check = 0.0
_last_pressure_level = 1
_pressure_thread = None


def _prefs():
    from .preferences import get_prefs
    return get_prefs()


def _workdir():
    from .core import workdir
    return workdir()


@bpy.app.handlers.persistent
def _on_change(*_args):
    global _changed
    _changed = True


_new_file = [False]   # the file being saved had never been saved before


@bpy.app.handlers.persistent
def _on_save_pre(*_args):
    _new_file[0] = not bpy.data.filepath


@bpy.app.handlers.persistent
def _on_save_or_load(*_args):
    global _changed
    if _queue_preparing():
        return   # the render queue saved its job copy, not the user's file
    _changed = False
    state["last"] = time.time()
    if _new_file[0] and bpy.data.filepath and _setting("project_folders", True) and not in_project_folder():
        # A new file was saved the first time: give it its own project folder.
        _new_file[0] = False
        if not bpy.app.timers.is_registered(_adopt_new_file):
            bpy.app.timers.register(_adopt_new_file, first_interval=0.0)
    _new_file[0] = False


def _adopt_new_file():
    try:
        path = move_to_project_folder(fresh=True)
        print("Claude Studio: new project folder", os.path.dirname(path))
    except Exception:
        traceback.print_exc()
    return None


# ---------------------------------------------------------------------------
# Crash detection: a marker file exists while Blender runs; if it is still there
# at the next start, the previous session ended without a clean quit.

def _marker_path():
    return os.path.join(_workdir(), "running.json")


def write_marker():
    try:
        import json
        with open(_marker_path(), "w", encoding="utf-8") as f:
            try:
                filepath = bpy.data.filepath
            except AttributeError:  # during registration bpy.data is restricted
                filepath = ""
            json.dump({"time": time.time(), "file": filepath, "step": state["step"],
                       "backup": state["path"], "pid": os.getpid()}, f)
    except OSError:
        pass


def _clear_marker():
    try:
        os.remove(_marker_path())
    except OSError:
        pass


def note_step(text):
    """Remember what is about to happen (shown after a crash)."""
    state["step"] = f"{time.strftime('%H:%M')} {text}"
    write_marker()


def _check_marker():
    global last_crash
    import json
    path = _marker_path()
    if not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        info = {}
    pid = info.get("pid")
    alive = False
    if pid and pid != os.getpid():  # a second Blender may be running right now: not a crash
        try:
            os.kill(int(pid), 0)
            alive = True
        except OSError:
            alive = False
    if not alive:
        last_crash = {"time": info.get("time", 0), "file": info.get("file", ""),
                      "step": info.get("step", ""), "backup": info.get("backup", "")}


def dismiss_crash():
    global last_crash
    last_crash = None


def project_name(filepath):
    """'2.2_now.blend', '2.2_12-58_24.10.26.blend', old '2.2_now_2026-10-24_13-46-29.blend' → '2.2'."""
    stem = os.path.splitext(os.path.basename(filepath))[0]
    while True:
        short = stem
        for pattern in _STAMP_RES:
            short = pattern.sub("", short)
        suffix = main_suffix()
        if suffix and short.endswith(suffix) and len(short) > len(suffix):
            short = short[:-len(suffix)]
        if short == stem:
            return stem or "untitled"
        stem = short


def _is_copy_folder(name):
    return name in (backup_dir_name(), archive_dir_name())


def main_folder(filepath):
    """The folder of the main file: for a backup or archived copy the folder above autosave/ or archiv/."""
    folder = os.path.dirname(filepath)
    while _is_copy_folder(os.path.basename(folder)) and os.path.dirname(folder) != folder:
        folder = os.path.dirname(folder)
    return folder


def in_copy(filepath=None):
    """The open file is a backup or archived copy, not the main file."""
    filepath = filepath if filepath is not None else bpy.data.filepath
    return bool(filepath) and main_folder(filepath) != os.path.dirname(filepath)


def original_path(filepath=None):
    """Where the open file belongs as the main file (<folder>/<name>_now.blend), or None if it is there."""
    filepath = filepath if filepath is not None else bpy.data.filepath
    if not filepath:
        return None
    target = os.path.join(main_folder(filepath), project_name(filepath) + main_suffix() + ".blend")
    same = os.path.normcase(os.path.abspath(target)) == os.path.normcase(os.path.abspath(filepath))
    return None if same else target


def _stem():
    return project_name(bpy.data.filepath) if bpy.data.filepath else "untitled"


def backup_folder():
    if bpy.data.filepath:
        return os.path.join(main_folder(bpy.data.filepath), backup_dir_name())
    return os.path.join(_workdir(), "autosave")


def _unique_path(folder, stem, when=None):
    """<folder>/<stem>_12-58_24.10.26.blend, with -2, -3 … if that minute is taken."""
    base = os.path.join(folder, "%s_%s" % (stem, time.strftime(_STAMPS.get(_setting("backup_stamp", 'YMD'), _STAMPS['YMD']), time.localtime(when))))
    path, n = base + ".blend", 2
    while os.path.exists(path) or os.path.exists(path + "1"):
        path, n = "%s-%d.blend" % (base, n), n + 1
    return path


def _same(a, b):
    return bool(a and b) and os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _prune(folder, stem, keep):
    """Keep the newest `keep` backups of `stem`; older ones are deleted.
    The open file is never touched, even if it is an old backup."""
    try:
        names = os.listdir(folder)
    except OSError:
        return
    files = [os.path.join(folder, n) for n in names
             if n.endswith(".blend") and project_name(n) == stem]
    files.sort(key=os.path.getmtime, reverse=True)
    for path in files[keep:]:
        if _same(path, bpy.data.filepath):
            continue
        for p in (path, path + "1"):
            if not os.path.exists(p):
                continue
            try:
                os.remove(p)
            except OSError:
                pass


def _to_backups(path, folder):
    """Move a file (and its .blend1) into the autosave folder `folder`, named by its time."""
    stem = project_name(path)
    os.makedirs(folder, exist_ok=True)
    for p in (path, path + "1"):
        if os.path.exists(p):
            shutil.move(p, _unique_path(folder, stem, os.path.getmtime(p)))


def make_main():
    """Save the open file as <name>_now.blend in the main folder. A file already there, and the
    open file itself if it was a main file under another name, become backups in autosave/."""
    source = bpy.data.filepath
    target = original_path()
    if not target:
        return None
    backups = os.path.join(main_folder(target), backup_dir_name())
    if os.path.exists(target):
        _to_backups(target, backups)
    bpy.ops.wm.save_as_mainfile(filepath=target, check_existing=False)
    if source and not in_copy(source) and os.path.exists(source):
        _to_backups(source, backups)   # e.g. Chair.blend → a backup, Chair_now.blend is the main file now
    _prune_project(backups, project_name(target))
    return target


def _prune_project(backups, stem):
    prefs = _prefs()
    _prune(backups, stem, prefs.autosave_keep if prefs is not None else 5)


def in_project_folder(filepath=None):
    """The main file lies in a folder named after the project (…/2.2/2.2_now.blend)."""
    filepath = filepath if filepath is not None else bpy.data.filepath
    return bool(filepath) and os.path.basename(main_folder(filepath)) == project_name(filepath)


def project_folder_target(filepath=None):
    """The project folder the open file should move to, or None if it is in one (or unsaved)."""
    filepath = filepath if filepath is not None else bpy.data.filepath
    if not filepath or in_project_folder(filepath):
        return None
    return os.path.join(main_folder(filepath), project_name(filepath))


def move_to_project_folder(fresh=False):
    """Make <folder>/<name>/ with autosave/, assets/ and archiv/ and save the open file there as
    <name>_now.blend (relative paths are adjusted by Blender). Everything of this project that
    lay in <folder> comes along: the main file and the open file become backups, the backups
    and the project memory move. Nothing is deleted – except, with fresh=True, the file that
    was just saved for the first time (it is the same as the new one)."""
    source = bpy.data.filepath
    folder = project_folder_target(source)
    if folder is None:
        return None
    name = project_name(source)
    old = main_folder(source)
    target = os.path.join(folder, name + main_suffix() + ".blend")
    if os.path.exists(target):
        raise RuntimeError("%s already exists" % target)
    for sub in (backup_dir_name(), assets_dir_name(), archive_dir_name()):
        os.makedirs(os.path.join(folder, sub), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=target, check_existing=False)
    backups = os.path.join(folder, backup_dir_name())
    if fresh and os.path.exists(source):
        os.remove(source)
    else:
        main = os.path.join(old, name + main_suffix() + ".blend")
        for path in {source, main}:
            if path and os.path.exists(path) and not _same(path, target):
                _to_backups(path, backups)
    old_backups = os.path.join(old, backup_dir_name())
    try:
        names = os.listdir(old_backups)
    except OSError:
        names = []
    for n in names:
        if n.endswith((".blend", ".blend1")) and project_name(n) == name:
            src = os.path.join(old_backups, n)
            shutil.move(src, _unique_path(backups, name, os.path.getmtime(src)))
    memory_file = os.path.join(old, name + ".claude.md")
    if os.path.exists(memory_file) and not os.path.exists(os.path.join(folder, name + ".claude.md")):
        shutil.move(memory_file, os.path.join(folder, name + ".claude.md"))
    _prune_project(backups, name)
    return target


def _queue_preparing():
    """The render queue is writing its job copy right now (the scene is briefly changed)."""
    try:
        from .studio.runner import RUN
        return RUN.preparing
    except Exception:
        return False


def _busy_rendering():
    try:
        return bpy.app.is_job_running('RENDER') or bpy.app.is_job_running('COMPOSITE')
    except Exception:
        return False


def save_now(reason, force=False):
    """Save a backup (or the file) now. Returns the path or None. Main thread only."""
    global _changed
    prefs = _prefs()
    if prefs is None or (not prefs.autosave and not force):
        return None
    if not force and not _changed:
        return None
    if _busy_rendering() or _queue_preparing():
        return None  # saving during a render can crash Blender
    try:
        if prefs.autosave_mode == 'FILE' and bpy.data.filepath and bpy.data.is_saved:
            bpy.ops.wm.save_mainfile(check_existing=False)
            path = bpy.data.filepath
        else:
            folder = backup_folder()
            os.makedirs(folder, exist_ok=True)
            stem = _stem()
            path = _unique_path(folder, stem)   # never overwrite (Blender would also leave a .blend1)
            bpy.ops.wm.save_as_mainfile(filepath=path, copy=True, compress=False, check_existing=False)
            _prune(folder, stem, prefs.autosave_keep)
    except Exception as exc:
        state["error"] = str(exc)
        traceback.print_exc()
        return None
    _changed = False
    state.update(last=time.time(), path=path, reason=reason, error="")
    write_marker()
    return path


def before_risky(reason):
    """Call right before something that might crash Blender (model code, renders)."""
    prefs = _prefs()
    if prefs is None or not prefs.autosave or not prefs.autosave_before_risky:
        return None
    if time.time() - state["last"] < RISKY_MIN_GAP:
        return None
    return save_now(reason)


# ---------------------------------------------------------------------------
# Memory pressure (macOS): 1 normal, 2 warn, 4 critical

def _read_pressure():
    try:
        out = subprocess.run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        state["pressure"] = int(out or 1)
    except Exception:
        state["pressure"] = 1


def _tick():
    global _last_pressure_check, _pressure_thread, _last_pressure_level
    try:
        prefs = _prefs()
        if prefs is None or not prefs.autosave:
            return CHECK_INTERVAL
        now = time.time()
        if prefs.autosave_on_memory and (_pressure_thread is None or not _pressure_thread.is_alive()):
            if now - _last_pressure_check >= 10:
                _last_pressure_check = now
                _pressure_thread = threading.Thread(target=_read_pressure, daemon=True, name="autosave-mem")
                _pressure_thread.start()
            level, before = state["pressure"], _last_pressure_level
            _last_pressure_level = level
            # macOS can stay at "warn" for hours: save once when it starts, again only if critical.
            if (level >= 2 and before < 2) or (level >= 4 and now - state["last"] >= CRITICAL_MIN_GAP):
                save_now("memory low", force=True)
                return CHECK_INTERVAL
        if now - state["last"] >= prefs.autosave_minutes * 60:
            save_now("regular")
    except Exception:
        traceback.print_exc()
    return CHECK_INTERVAL


_trace_file = None


def _enable_crash_trace():
    """If Blender dies from a fatal error, Python writes where each thread was to crash_trace.txt."""
    global _trace_file
    import faulthandler
    try:
        path = os.path.join(_workdir(), "crash_trace.txt")
        if os.path.exists(path) and os.path.getsize(path) > 500_000:
            os.remove(path)
        _trace_file = open(path, "a", encoding="utf-8")
        _trace_file.write("\n=== Blender %s started %s (pid %d) ===\n"
                          % (bpy.app.version_string, time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid()))
        _trace_file.flush()
        faulthandler.enable(file=_trace_file, all_threads=True)
    except OSError:
        _trace_file = None


# ---------------------------------------------------------------------------
# Freeze watch (see the module docstring)

_watcher = None


def _heartbeat_path():
    return os.path.join(_workdir(), "heartbeat")


def _heartbeat():
    try:
        path = _heartbeat_path()
        if os.path.exists(path):
            os.utime(path, None)
        else:
            open(path, "w").close()
    except OSError:
        pass
    if _trace_file is not None:
        # If Blender stops answering, Python's own watchdog thread (no GIL needed) writes
        # where the Python code was into crash_trace.txt.
        import faulthandler
        faulthandler.cancel_dump_traceback_later()
        faulthandler.dump_traceback_later(STALL, repeat=False, file=_trace_file)
    return HEARTBEAT


def _start_watch():
    global _watcher
    if sys.platform != "darwin" or bpy.app.background:
        return
    if _watcher is not None and _watcher.poll() is None:
        return
    _heartbeat()
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "freeze_watch.py")
    try:
        _watcher = subprocess.Popen([sys.executable, "-I", script, str(os.getpid()), _heartbeat_path(), _workdir()],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        _watcher = None
    if not bpy.app.timers.is_registered(_heartbeat):
        bpy.app.timers.register(_heartbeat, first_interval=HEARTBEAT, persistent=True)


def _stop_watch():
    global _watcher
    if bpy.app.timers.is_registered(_heartbeat):
        bpy.app.timers.unregister(_heartbeat)
    try:
        import faulthandler
        faulthandler.cancel_dump_traceback_later()
    except Exception:
        pass
    if _watcher is not None and _watcher.poll() is None:
        _watcher.terminate()
    _watcher = None


def freeze_reports():
    """The freeze reports, newest first."""
    folder = _workdir()
    try:
        names = [n for n in os.listdir(folder) if n.startswith("freeze_") and n.endswith(".txt")]
    except OSError:
        return []
    return sorted((os.path.join(folder, n) for n in names), key=os.path.getmtime, reverse=True)


# ---------------------------------------------------------------------------

def register():
    state["last"] = time.time()
    _enable_crash_trace()
    _start_watch()
    _check_marker()
    write_marker()
    import atexit
    atexit.register(_clear_marker)   # a clean quit removes the marker
    for handlers, func in ((bpy.app.handlers.depsgraph_update_post, _on_change),
                           (bpy.app.handlers.save_pre, _on_save_pre),
                           (bpy.app.handlers.save_post, _on_save_or_load),
                           (bpy.app.handlers.load_post, _on_save_or_load)):
        if func not in handlers:
            handlers.append(func)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=CHECK_INTERVAL, persistent=True)


def unregister():
    _stop_watch()
    _clear_marker()
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    for timer in (_adopt_new_file,):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    for handlers, func in ((bpy.app.handlers.depsgraph_update_post, _on_change),
                           (bpy.app.handlers.save_pre, _on_save_pre),
                           (bpy.app.handlers.save_post, _on_save_or_load),
                           (bpy.app.handlers.load_post, _on_save_or_load)):
        if func in handlers:
            handlers.remove(func)

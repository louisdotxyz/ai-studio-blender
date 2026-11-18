# SPDX-License-Identifier: GPL-3.0-or-later
"""Fast timeline: half-size JPEG previews of the section frames, used by the Video Editor's preview
instead of the 4K PNGs (Blender's strip proxies, 50 %). Decoding a 4K PNG took longer than a frame lasts:
playback stuttered and the read-ahead never caught up; a half-size JPEG is read in a few milliseconds.

- Made in the background as frames arrive (macOS `sips`, a few at a time, low priority), kept in
  ~/.cache/ai_studio_proxies/<folder>/images/50/<frame>.png_proxy.jpg (Blender's naming), never next to
  the frames.
- A frame rendered again: its old preview goes at once (the preview shows the new full frame) and is made
  again.
- Without a preview yet Blender shows the full frame. Renders and the export always use the full frames.
- Switched with "Fast Preview" (the Video Editor's sidebar); the previews are pruned after 30 days unused.
Blender is only touched in tick() (main thread); the files are checked and made in a thread."""

import hashlib
import json
import os
import shutil
import struct
import subprocess
import threading
import time

import bpy

SIZE = 50
ROOT = os.path.expanduser("~/.cache/ai_studio_proxies")
WORKERS = 3
SETTLE = 2.0                   # a frame younger than this may still be written
PRUNE_AFTER = 30 * 86400
_lock = threading.Lock()
_wake = threading.Event()
_want = {"folders": [], "on": True, "size": SIZE}   # [(folder, (names …))] in the order to make them; size %
_changed = set()                            # folders whose previews went (Blender reads them again)
_status = {"left": 0, "made": 0, "error": ""}
_thread = [None]
_applied = {}                               # strip pointer → folder it was set up for
_ready = {}                                 # folder → names whose preview is there and current
_new_ready = set()                          # folders with new previews since the last tick
REAL_KEY = "studio_real"                    # on a section strip: its real frame names (it shows previews)


def available():
    return shutil.which("sips") is not None


def folder_for(directory):
    key = hashlib.sha1(os.path.normcase(os.path.abspath(directory)).encode("utf-8")).hexdigest()[:16]
    return os.path.join(ROOT, key)


def target(directory, name):
    return os.path.join(folder_for(directory), "images", str(_want["size"]), name + "_proxy.jpg")


def png_size(path):
    """(width, height) from a PNG's header, None for other files."""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n" or len(head) < 24:
        return None
    return struct.unpack(">II", head[16:24])


def make(src, dst):
    """One preview: exactly half the size (Blender expects that of a 50 % proxy)."""
    size = png_size(src)
    if not size:
        return False
    pct = _want["size"]
    w, h = max(1, size[0] * pct // 100), max(1, size[1] * pct // 100)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part.jpg"
    try:
        done = subprocess.run(["nice", "-n", "10", "sips", "-s", "format", "jpeg", "-s", "formatOptions", "85",
                               "-z", str(h), str(w), src, "--out", tmp],
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
    except (OSError, subprocess.SubprocessError) as ex:
        _status["error"] = str(ex)
        return False
    if done.returncode != 0 or not os.path.exists(tmp):
        _status["error"] = (done.stderr or b"").decode("utf-8", "replace")[-200:]
        return False
    os.replace(tmp, dst)
    return True


def _scan(folders):
    """[(src, dst)] still to make; old previews of frames rendered again are removed right away."""
    todo, gone = [], set()
    now = time.time()
    for folder, names in folders:
        ready = set()
        for name in names:
            src = os.path.join(folder, name)
            try:
                s = os.stat(src).st_mtime
            except OSError:
                continue
            if now - s < SETTLE:
                continue
            dst = target(folder, name)
            try:
                d = os.stat(dst).st_mtime
            except OSError:
                d = None
            if d is not None:                      # there (also an older one of a frame rendered again: it
                ready.add(name)                    # stays until the new one replaces it – never the 4K PNG)
                if d >= s:
                    continue
            todo.append((src, dst))
        with _lock:
            if _ready.get(folder) != ready:
                _new_ready.add(folder)
            _ready[folder] = ready
    return todo, gone


def _loop():
    from concurrent.futures import ThreadPoolExecutor
    pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="ai-studio-proxy")
    pruned = 0.0
    while True:
        _wake.wait(2.0)                            # new frames get their preview within seconds
        _wake.clear()
        with _lock:
            folders, on = list(_want["folders"]), _want["on"]
        if not on or not folders:
            _status["left"] = 0
            continue
        try:
            todo, gone = _scan(folders)
        except Exception as ex:                    # never let the thread die
            _status["error"] = str(ex)
            continue
        if gone:
            with _lock:
                _changed.update(gone)
        _status["left"] = len(todo)
        for start in range(0, len(todo), WORKERS * 4):
            if _wake.is_set():                     # new frames or another order: look again first
                break
            batch = todo[start:start + WORKERS * 4]
            before = [os.path.exists(dst) for _src, dst in batch]
            for (src, dst), was, ok in zip(batch, before, pool.map(lambda pair: make(*pair), batch)):
                _status["made"] += 1 if ok else 0
                _status["left"] = max(0, _status["left"] - 1)
                if ok:
                    folder = os.path.dirname(src)
                    with _lock:
                        _ready.setdefault(folder, set()).add(os.path.basename(src))
                        _new_ready.add(folder)
                        if was:
                            _changed.add(folder)   # replaced a preview: Blender reads it again
        if time.time() - pruned > 6 * 3600:
            pruned = time.time()
            prune()


def prune(now=None):
    """Previews of folders not used for 30 days go (only this cache, never frames)."""
    now = now or time.time()
    try:
        names = os.listdir(ROOT)
    except OSError:
        return 0
    with _lock:
        used = {os.path.basename(folder_for(f)) for f, _n in _want["folders"]}
    count = 0
    for name in names:
        path = os.path.join(ROOT, name)
        try:
            if name in used or now - os.path.getmtime(path) < PRUNE_AFTER:
                continue
            shutil.rmtree(path)
            count += 1
        except OSError:
            pass
    return count


def _start():
    if _thread[0] is None or not _thread[0].is_alive():
        _thread[0] = threading.Thread(target=_loop, daemon=True, name="ai-studio-proxies")
        _thread[0].start()


def active():
    """The timeline shows previews (Fast Preview on, previews can be made here)."""
    return bool(_want["on"]) and available()


def ready_names(folder):
    """The frames of a folder whose preview is there (None: not looked yet)."""
    with _lock:
        found = _ready.get(os.path.normpath(folder)) or _ready.get(folder)
        return set(found) if found is not None else None


def left():
    return _status["left"] if _want["on"] else 0


def _spaces():
    wm = bpy.context.window_manager
    for window in (wm.windows if wm else ()):
        for area in window.screen.areas:
            if area.type == 'SEQUENCE_EDITOR':
                yield area.spaces.active


def _set(owner, attr, value):
    try:
        if getattr(owner, attr) != value:
            setattr(owner, attr, value)
        return True
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return False


def _setup(strip, directory, on):
    """The strip uses its previews (50 %, our folder) or not. Read-ahead stopped first."""
    from . import cut
    key = strip.as_pointer()
    if not on:
        if getattr(strip, "use_proxy", False):
            cut.quiet(strip)
            _set(strip, "use_proxy", False)
        _applied.pop(key, None)
        return
    folder = folder_for(directory) + os.sep
    pct = _want["size"]
    proxy = getattr(strip, "proxy", None)
    if _applied.get(key) == folder and getattr(strip, "use_proxy", False) and (
            proxy is None or (getattr(proxy, "build_%d" % pct, False)
                              and getattr(proxy, "use_proxy_custom_directory", False)
                              and getattr(proxy, "directory", "") == folder)):
        return                                     # set up and still so (a rebuild or sync can switch it off)
    cut.quiet(strip)
    _set(strip, "use_proxy", True)
    proxy = getattr(strip, "proxy", None)
    if proxy is None:
        return
    for size in (25, 50, 75, 100):
        if size == pct:
            continue
        _set(proxy, "build_%d" % size, False)
    _set(proxy, "build_%d" % pct, True)
    _set(proxy, "use_overwrite", False)
    _set(proxy, "use_proxy_custom_directory", True)
    _set(proxy, "directory", folder)
    _applied[key] = folder


def tick(on=None):
    """(main thread, every few seconds) The strips use the previews, the preview shows 50 %, the thread
    knows which frames to make (the section under the playhead first)."""
    from . import cut
    if on is None:
        on = getattr(bpy.context.window_manager, "studio_cut_fast", True)
    on = bool(on) and available()
    pct = 25 if getattr(bpy.context.window_manager, "studio_cut_preview_size", '50') == '25' else 50
    if pct != _want["size"]:                       # another preview size: its own previews (made anew)
        with _lock:
            _want["size"] = pct
            _ready.clear()
        _applied.clear()
    folders = []
    for ed in [s for s in bpy.data.scenes if s.get(cut.FILM_KEY)]:
        film = cut.film_scene(ed)
        here = ed.frame_current
        strips = [s for s in cut.ours(film) if s.type == 'IMAGE']
        strips.sort(key=lambda s: not (s.frame_final_start <= here < s.frame_final_end))
        for strip in strips:
            try:
                directory = os.path.normpath(bpy.path.abspath(strip.directory))
                real = strip.get(REAL_KEY)              # the real frames (the strip may show previews held)
                names = tuple(dict.fromkeys(json.loads(real) if real else (e.filename for e in strip.elements)))
            except (AttributeError, ReferenceError, ValueError):
                continue
            _setup(strip, directory, on)
            folders.append((directory, names))
    for space in _spaces():
        if on:
            _set(space, "proxy_render_size", 'PROXY_%d' % _want["size"])
            _set(space, "use_proxies", True)
        elif getattr(space, "proxy_render_size", "") in {'PROXY_25', 'PROXY_50'}:
            _set(space, "proxy_render_size", 'SCENE')
    with _lock:
        if _want["folders"] != folders or _want["on"] != on:
            _want["folders"], _want["on"] = folders, on
            _wake.set()
        changed = set(_changed)
        _changed.clear()
    if changed:                                    # old previews went: Blender reads those strips again
        for ed in [s for s in bpy.data.scenes if s.get(cut.FILM_KEY)]:
            for strip in cut.ours(cut.film_scene(ed)):
                try:
                    if strip.type == 'IMAGE' and os.path.normpath(bpy.path.abspath(strip.directory)) in changed:
                        strip.invalidate_cache('RAW')
                except (AttributeError, ReferenceError, RuntimeError):
                    pass
    if on and folders:
        _start()
    with _lock:
        fresh = bool(_new_ready)
        _new_ready.clear()
    if fresh or _want.get("shown_on") != on:     # new previews (or switched): the strips show them
        _want["shown_on"] = on
        cut.show_previews()


def fast_changed(wm, _context):
    try:
        tick(wm.studio_cut_fast)
    except Exception as ex:
        print("AI Studio: fast preview:", ex)

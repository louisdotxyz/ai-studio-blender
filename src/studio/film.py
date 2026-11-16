# SPDX-License-Identifier: GPL-3.0-or-later
"""Films (animations) render in their own Blender process (film_job.py), not in the queue's worker:

- each film has its own frame range (the queue entry keeps the scene's range of when it was added)
  and renders in N parts one after the other, each part also as a video, at the end the whole film;
- the process has its own process group: it goes on when this Blender crashes or is closed;
- frames that are there are skipped: a film that stopped (crash, Stop) resumes where it was;
- progress (part, frame, time left) comes from its status.json, shown above the chat and in the
  Render tab.

The films are listed in <data dir>/films.json; each one's files are in its folder's .film/
(scene copy, job, status, log)."""

import json
import os
import signal
import subprocess
import time

import bpy
from bpy.props import StringProperty
from bpy.types import Operator

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "film_job.py")
FILM_DIR = ".film"
VIDEOS = {'MP4': "MP4", 'PRORES': "PRORES"}


def _registry():
    from .paths import data_dir
    return os.path.join(data_dir(), "films.json")


def _load():
    try:
        with open(_registry(), encoding="utf-8") as f:
            films = json.load(f)
        return [f for f in films if isinstance(f, dict) and f.get("dir")]
    except (OSError, ValueError):
        return []


def _save(films):
    _films_cache[0] = 0.0
    path = _registry()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(films, f, indent=1)
    os.replace(tmp, path)


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    try:   # a finished child that was not collected yet counts as gone
        done, _status = os.waitpid(int(pid), os.WNOHANG)
        return done == 0
    except ChildProcessError:
        return True
    except OSError:
        return True


def read_status(film):
    try:
        with open(os.path.join(film["dir"], "status.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _command(film_dir):
    script = SCRIPT
    cmd = [bpy.app.binary_path, "-b", os.path.join(film_dir, "scene.blend"), "--factory-startup", "-noaudio"]
    try:
        if bpy.context.preferences.filepaths.use_scripts_auto_execute:
            cmd.append("-y")   # drivers with Python, as in your Blender
    except AttributeError:
        pass
    return cmd + ["--python-exit-code", "3", "--python", script, "--", os.path.join(film_dir, "job.json")]


def _launch(film_dir):
    log = open(os.path.join(film_dir, "log.txt"), "a", encoding="utf-8")
    try:
        proc = subprocess.Popen(_command(film_dir), stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)
    finally:
        log.close()
    return proc.pid


def start(scene, job, folder, stem, frames, parts, video, label, fresh=True, archive=None, film_dir=FILM_DIR,
          extra=None):
    """Save a copy of the file into <folder>/.film, write the job and start the film's process.
    `job` is the queue's job (overrides, camera, variant). fresh: older frames go to archiv first, else
    only the frames of `frames` render again (the others stay). film_dir: the job's folder in `folder`
    (a draft has its own, .draft); extra: more values on the film's entry. Returns the film's entry."""
    from . import runner
    film_dir = os.path.join(folder, film_dir)
    os.makedirs(film_dir, exist_ok=True)
    if fresh:
        moved = _clear_old_frames(folder, stem, archive)
        if moved:
            runner.log("film %s: %d older frames moved to archiv" % (label, moved))
    else:
        _remove_frames(folder, stem, frames)
    for name in ("status.json",):          # a new film: no progress from an earlier one
        try:
            os.remove(os.path.join(film_dir, name))
        except OSError:
            pass
    runner.RUN.preparing = True   # the save handlers must not take this copy for the user's save
    try:
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(film_dir, "scene.blend"), copy=True, compress=False,
                                    relative_remap=True, check_existing=False)
    finally:
        runner.RUN.preparing = False
    data = {"scene": scene.name, "overrides": job["overrides"], "camera": job.get("camera"),
            "variant": job.get("variant") or [], "frames": list(frames), "parts": int(max(1, parts)),
            "pattern": os.path.join(folder, stem + "_"), "out": os.path.join(folder, stem),
            "video": VIDEOS.get(video), "devices": runner._device_settings(), "runs": job.get("runs"),
            "titles": job.get("titles"), "quality": job.get("quality", ""), "section": job.get("section", "")}
    with open(os.path.join(film_dir, "job.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    entry = {"dir": film_dir, "folder": folder, "label": label, "pid": _launch(film_dir),
             "added": time.time(), "blend": bpy.data.filepath, "scene": scene.name,
             "quality": job.get("quality", ""), "section": job.get("section", "")}
    entry.update(extra or {})
    films = [f for f in _load() if f["dir"] != film_dir] + [entry]
    _save(films)
    runner.log("film %s started (%d frames, %d parts, pid %s)" % (label, frames[1] - frames[0] + 1,
                                                                   data["parts"], entry["pid"]))
    _ensure_timer()
    return entry


_films_cache = [0.0, []]


def films(fresh=False):
    """[(entry, status, state)] state: 'RENDERING', 'ENCODING', 'PAUSED' (stopped or crashed: resume),
    'FAILED' or 'DONE'. Read again at most every second (panels draw often)."""
    now = time.monotonic()
    if not fresh and now - _films_cache[0] < 1.0:
        return _films_cache[1]
    out = []
    for film in _load():
        st = read_status(film)
        alive = _alive(film.get("pid"))
        state = st.get("state", "")
        if state == "done":
            kind = 'DONE'
        elif alive:
            kind = 'ENCODING' if state == "encoding" else 'RENDERING'
        elif state == "failed":
            kind = 'FAILED'
        else:
            kind = 'PAUSED'
        out.append((film, st, kind))
    _films_cache[:] = [now, out]
    return out


def mine(film):
    """A film of the file open in this Blender (a second Blender shows only its own)."""
    return film.get("blend", "") in ("", bpy.data.filepath)


def films_here():
    return [f for f in films() if mine(f[0])]


def running():
    return [f for f in films() if f[2] in {'RENDERING', 'ENCODING'}]


def running_here():
    """The films of this file that render or encode now (fresh: the queue decides on it)."""
    return [f for f in films(fresh=True) if f[2] in {'RENDERING', 'ENCODING'} and mine(f[0])]


FINISHED = []      # films finished while this Blender ran: {"label", "video", "sections", "folder", "when"}


def results(folder):
    """(whole video or "", [(section, video)]) of a film's folder."""
    import glob
    import re
    try:
        with open(os.path.join(folder, FILM_DIR, "job.json"), encoding="utf-8") as f:
            stem = os.path.basename(json.load(f)["out"])
    except (OSError, ValueError, KeyError):
        stem = ""
    videos = sorted(glob.glob(os.path.join(glob.escape(folder), "*.mp4")) +
                    glob.glob(os.path.join(glob.escape(folder), "*.mov")))
    names = [(os.path.splitext(os.path.basename(v))[0], v) for v in videos]
    whole = next((v for n, v in names if n == stem or re.fullmatch(re.escape(stem) + r"\d+-\d+", n)), "")
    parts = [(n[len(stem) + 1:] if stem and n.startswith(stem + "_") else n, v) for n, v in names if v != whole]
    return whole, parts


def _publish(folder, whole, parts):
    """A finished film's videos next to its folder under the sections' names: <animation>/01 Hero.mp4
    (the newest render of each section; the one before goes to archiv). Returns the film's scene name."""
    try:
        with open(os.path.join(folder, FILM_DIR, "job.json"), encoding="utf-8") as f:
            job = json.load(f)
    except (OSError, ValueError):
        return ""
    if job.get("cut") or not job.get("runs"):
        return ""
    titles = job.get("titles") or {}
    runs = job["runs"]
    videos = {}
    if len(runs) == 1 and whole:
        videos[titles.get(runs[0][2], runs[0][2])] = whole
    for safe, video in parts:
        videos[titles.get(safe, safe)] = video
    from .cut import video_name
    root = os.path.dirname(folder)
    import shutil
    for title, video in videos.items():
        target = os.path.join(root, video_name(title) + os.path.splitext(video)[1])
        if os.path.isfile(target):
            old = os.path.join(root, "archiv", "%s %s%s" % (video_name(title), time.strftime("%Y-%m-%d %H%M%S"),
                                                            os.path.splitext(target)[1]))
            os.makedirs(os.path.dirname(old), exist_ok=True)
            os.replace(target, old)
        shutil.copyfile(video, target)
    return job.get("scene", "")


_FRAME_EXTS = (".png", ".jpg", ".jpeg", ".exr", ".tif", ".tiff", ".webp")


def _frames_of(folder, stem):
    """A film's frame files; the drafts next to them (<stem>_draft_0001.png) are not the film's (they stay
    until the real frames are there)."""
    draft, poster = stem + "_draft_", stem + "_poster_"
    try:
        return [n for n in os.listdir(folder) if n.startswith(stem + "_") and n.lower().endswith(_FRAME_EXTS)
                and (stem.endswith(("_draft", "_poster")) or not n.startswith((draft, poster)))]
    except OSError:
        return []


def _remove_frames(folder, stem, frames):
    """Only these frames render again: their files go (the film skips frames that are there)."""
    import re
    a, b = frames
    for name in _frames_of(folder, stem):
        found = re.fullmatch(re.escape(stem) + r"_(\d+)\.\w+", name)
        if found and a <= int(found.group(1)) <= b:
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass


def _clear_old_frames(folder, stem, archive=None):
    """A new film into a folder that has frames already (a name used again): they go to archiv first,
    else Blender would keep them (frames that are there are skipped so a film can resume)."""
    old = _frames_of(folder, stem)
    if not old:
        return 0
    target = os.path.join(archive or os.path.join(os.path.dirname(folder), "archiv"),
                          "%s %s" % (os.path.basename(folder), time.strftime("%Y-%m-%d %H%M%S")))
    os.makedirs(target, exist_ok=True)
    for name in old:
        os.replace(os.path.join(folder, name), os.path.join(target, name))
    return len(old)


def time_left(st):
    per, done, total = st.get("per_frame"), st.get("done", 0), st.get("total", 0)
    return per * max(0, total - done) if per else None


def text(film, st, kind):
    """"P_front · Part 2/4 · 250/960 · ~12 min left" """
    from .runner import format_time
    parts = st.get("parts", 1)
    bits = [film.get("label", "Film")]
    if parts > 1:
        bits.append("Part %d/%d" % (st.get("part", 1), parts))
    bits.append("%d/%d" % (st.get("done", 0), st.get("total", 0) or 1))
    if kind == 'RENDERING':
        left = time_left(st)
        if left is not None:
            bits.append("~%s left" % format_time(left))
    elif kind == 'ENCODING':
        bits.append("making the video")
    elif kind == 'PAUSED':
        bits.append("stopped")
    elif kind == 'FAILED':
        bits.append(st.get("error", "failed")[:60])
    return " · ".join(bits)


def progress(st):
    total = st.get("total", 0)
    return min(1.0, st.get("done", 0) / total) if total else 0.0


def stop(film_dir):
    """Stop the film's process (its frames stay, Resume goes on)."""
    for film in _load():
        if film["dir"] == film_dir and _alive(film.get("pid")):
            try:
                os.killpg(int(film["pid"]), signal.SIGTERM)
            except (OSError, ValueError):
                try:
                    os.kill(int(film["pid"]), signal.SIGTERM)
                except (OSError, ValueError):
                    pass


def resume(film_dir):
    films_now = _load()
    for film in films_now:
        if film["dir"] == film_dir and not _alive(film.get("pid")):
            if not os.path.exists(os.path.join(film_dir, "scene.blend")):
                raise ValueError("The film's scene copy is gone: render it again")
            film["pid"] = _launch(film_dir)
    _save(films_now)
    _ensure_timer()


def _has_video(film_dir):
    try:
        with open(os.path.join(film_dir, "job.json"), encoding="utf-8") as f:
            out = json.load(f)["out"]
    except (OSError, ValueError, KeyError):
        return True
    import glob
    return any(os.path.isfile(out + ext) for ext in (".mp4", ".mov")) or bool(
        glob.glob(glob.escape(out) + "[0-9]*-[0-9]*.mp4"))


def needs_video(film_dir):
    """A finished film from before 3.7 (an image sequence only) that can still become a video."""
    st = read_status({"dir": film_dir})
    return (st.get("state") == "done" and not _has_video(film_dir)
            and os.path.isfile(os.path.join(film_dir, "scene.blend"))
            and not any(f["dir"] == film_dir for f in _load()))


def register_process(film_dir, folder, label, pid, extra=None):
    """Another process that writes status.json like a film (the cut's export, a farm job): its bar, its
    result."""
    entry = {"dir": film_dir, "folder": folder, "label": label, "pid": pid, "added": time.time(),
             "blend": bpy.data.filepath}
    entry.update(extra or {})
    _save([f for f in _load() if f["dir"] != film_dir] + [entry])
    _ensure_timer()
    return entry


def make_video(film_dir, label="Video"):
    """Make the videos of a finished film (its frames are all there, so only the encoding runs)."""
    entry = {"dir": film_dir, "folder": os.path.dirname(film_dir), "label": label, "pid": _launch(film_dir),
             "added": time.time(), "blend": bpy.data.filepath}
    _save([f for f in _load() if f["dir"] != film_dir] + [entry])
    _ensure_timer()
    return entry


def forget(film_dir):
    """Take the film off the list (its files stay)."""
    _save([f for f in _load() if f["dir"] != film_dir])


def _video_or_frame(film):
    """The film's video (<name>.mp4/.mov), else its last frame."""
    try:
        with open(os.path.join(film["dir"], "job.json"), encoding="utf-8") as f:
            out = json.load(f)["out"]
    except (OSError, ValueError, KeyError):
        return ""
    for ext in (".mp4", ".mov"):
        if os.path.isfile(out + ext):
            return out + ext
    folder = os.path.dirname(out)
    try:
        frames = sorted(n for n in os.listdir(folder) if n.lower().endswith((".png", ".jpg", ".exr")))
    except OSError:
        return ""
    return os.path.join(folder, frames[-1]) if frames else ""


_seen = {}
_filled = {}         # film → (frames done, when) at the last refresh of its strips


def _poll():
    """Every 2 s while films run: redraw the bars; a film that finished is announced and leaves the list."""
    from . import runner
    try:
        now = films(fresh=True)
    except Exception as ex:
        print("AI Studio: films:", ex)
        return 5.0
    for film, st, kind in now:
        key = film["dir"]
        if kind == 'RENDERING' and film.get("scene") and mine(film):     # its new frames into the strips
            done = st.get("done", 0)
            last = _filled.get(key, (0, 0.0))
            try:
                from . import film_tools
                film_tools.record_time(film, st)
                film_tools.check_camera(film, st)
            except Exception as ex:                  # never stop the bars
                print("AI Studio: render times:", ex)
            if done != last[0] and time.monotonic() - last[1] > 3.0:
                _filled[key] = (done, time.monotonic())
                from . import cut
                cut.on_progress(film["scene"])
        if kind == 'DONE' and _seen.get(key) != 'DONE':
            runner.log("film done: %s" % film.get("label"))
            if mine(film) and not film.get("draft"):
                runner.notify("%s is ready" % film.get("label", "The film"))
            result = _video_or_frame(film)
            if result:
                runner.log("film result: %s" % result)
            whole, parts = ("", []) if film.get("draft") else results(film.get("folder") or os.path.dirname(key))
            try:
                scene_name = "" if film.get("draft") else _publish(film.get("folder") or os.path.dirname(key),
                                                                    whole, parts)
            except OSError as ex:
                runner.log("film videos not put next to the films: %s" % ex)
                scene_name = ""
            scene_name = scene_name or film.get("scene", "")
            if scene_name and mine(film) and not film.get("label", "").startswith("Export"):
                from . import cut
                cut.on_film_done(scene_name)
            FINISHED.append({"label": film.get("label", ""), "video": whole, "sections": parts,
                             "folder": film.get("folder", ""), "when": time.time(), "blend": film.get("blend", "")})
            del FINISHED[:-20]
            forget(key)
        _seen[key] = kind
    runner.tag_redraw()
    return 2.0 if any(k in {'RENDERING', 'ENCODING'} for _f, _s, k in now) else None


def _ensure_timer():
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=1.0, persistent=True)


def draw(layout, buttons=True):
    """One row per film: progress with part, frame and time left; Stop / Resume / ✕."""
    for film, st, kind in films_here():
        if kind == 'DONE':
            continue
        row = layout.row(align=True)
        if kind in {'RENDERING', 'ENCODING'}:
            row.progress(factor=progress(st), type='BAR', text=text(film, st, kind))
            if buttons:
                row.operator("studio.film", text="", icon='PAUSE', emboss=False).film = film["dir"]
                op = row.operator("studio.film", text="", icon='X', emboss=False)
                op.film, op.action = film["dir"], "REMOVE"
        else:
            row.label(text=text(film, st, kind), icon='ERROR' if kind == 'FAILED' else 'PAUSE',
                      translate=False)
            if buttons:
                op = row.operator("studio.film", text="", icon='PLAY')
                op.film, op.action = film["dir"], "RESUME"
                op = row.operator("studio.film", text="", icon='X')
                op.film, op.action = film["dir"], "FORGET"


class STUDIO_OT_film(Operator):
    bl_idname = "studio.film"
    bl_label = "Film"
    bl_options = {'INTERNAL'}

    film: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})
    action: StringProperty(default="STOP", options={'HIDDEN', 'SKIP_SAVE'})   # STOP / RESUME / FORGET / REMOVE

    @classmethod
    def description(cls, _context, props):
        return {"STOP": "Stop the film (the frames stay; Resume goes on where it stopped)",
                "RESUME": "Go on where the film stopped (frames that are there are skipped)",
                "FORGET": "Take the film off the list (its files stay)",
                "REMOVE": "Stop the film and take it out of the queue (the frames rendered so far stay)"}.get(
                    props.action, "")

    def execute(self, context):
        try:
            if self.action == "REMOVE":
                stop(self.film)
                forget(self.film)
            else:
                {"STOP": stop, "RESUME": resume, "FORGET": forget}[self.action](self.film)
        except (ValueError, KeyError) as ex:
            self.report({'ERROR'}, str(ex))
            return {'CANCELLED'}
        return {'FINISHED'}


classes = (STUDIO_OT_film,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    if any(k in {'RENDERING', 'ENCODING'} for _f, _s, k in films()):
        _ensure_timer()     # films still running from before (also after a crash of this Blender)


def unregister():
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

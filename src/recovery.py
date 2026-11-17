# SPDX-License-Identifier: GPL-3.0-or-later
"""After a crash: Blender is set up again by itself, carefully.

autosave.py finds that the last session did not end cleanly (its marker file is still there). Then:

1. Why: Blender's crash report (<temp>/<file>.crash.txt), the macOS report and the Python trace are read
   and summed up in one line (where it crashed, the step before it).
2. Learn: the crash becomes a lesson of the model (lessons.py, its short rule goes into the system
   prompt), with a rule for what to avoid (known crashes have their own rule).
3. The file: Blender started empty → the newest saved state of the file of then opens (as the button
   Continue did). The render farm comes back with it (renderfarm.py: connect, take over the jobs of this
   file, send again what is missing).
4. The chat: its chat of then comes back (core._restore_chat); if Claude was working on a task, it goes
   on by itself with what happened and how to go on more carefully (check what is there, small steps,
   avoid what crashed). Saved more often for 30 minutes.
Never in a loop: a second crash within 20 minutes of a recovery stops it, the card asks instead.
This module touches bpy only in its timers."""

import glob
import json
import os
import re
import tempfile
import time

import bpy

STATE_FILE = "recovery.json"
LOOP_WINDOW = 20 * 60          # a crash again this soon after a recovery: no second automatic one
CAREFUL_FOR = 30 * 60
WAIT_CHAT = 60.0               # seconds to wait for the file and its chat after the start

# (pattern in the report, what it was, the rule) – the newest crashes of Blender 5.3 on the Mac
KNOWN = (
    (r"CAClock|BKE_sound|CoreAudio|stopSynchronizer", "audio sync while playing back",
     "Never start or stop playback with audio sync in the Video Editor scene; the cut plays with sync "
     "'FRAME_DROP'."),
    (r"seq_prefetch|prefetch_frames|seq_render_image_strip|render_give_ibuf", "Video Editor read-ahead",
     "Before changing Video Editor strips from Python call strip.invalidate_cache('RAW') first (it stops "
     "the read-ahead thread); change few strips per step, never while playback runs."),
    (r"rna_StripModifier_update|StripModifier", "a strip modifier",
     "Change strip modifiers only on strips that exist in their scene's editor; never keep modifier "
     "references across steps."),
    (r"cycles|Cycles|ccl::|device_metal|MTL", "rendering (Cycles / Metal)",
     "Render a small preview first (low samples, 50 % size) before a full render; no render while "
     "another one runs."),
    (r"EXC_RESOURCE|memory|malloc|MEM_|jemalloc|out of memory", "memory",
     "Watch the memory: free images and caches, lower resolution or samples before heavy steps."),
    (r"depsgraph|DEG_|BKE_object_eval|geometry_nodes|GeometryNodes", "evaluating the scene",
     "Change the scene in small steps and let Blender update between them; avoid big modifier stacks "
     "in one go."),
)


def _workdir():
    from . import core
    return core.workdir()


def _read_state():
    try:
        with open(os.path.join(_workdir(), STATE_FILE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_state(data):
    try:
        path = os.path.join(_workdir(), STATE_FILE)
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


# ------------------------------------------------------------------ 1. why

def _newer(paths, since):
    found = []
    for path in paths:
        try:
            if os.path.getmtime(path) >= since:
                found.append(path)
        except OSError:
            pass
    return sorted(found, key=os.path.getmtime)


def reports(crash):
    """The crash reports written after the crash's last sign of life: (blender crash.txt, macOS .ips)."""
    since = float(crash.get("time") or 0) - 5
    temps = {tempfile.gettempdir(), os.environ.get("TMPDIR", ""), "/tmp"}
    if bpy.app.tempdir:
        temps.add(os.path.dirname(bpy.app.tempdir.rstrip("/\\")))
    texts = _newer([p for t in temps if t for p in glob.glob(os.path.join(t, "*.crash.txt"))], since)
    stem = os.path.splitext(os.path.basename(crash.get("file") or ""))[0]
    mine = [p for p in texts if stem and os.path.basename(p).startswith(stem + ".crash")]
    ips = _newer(glob.glob(os.path.expanduser("~/Library/Logs/DiagnosticReports/Blender*.ips")), since)
    return (mine or texts or [None])[-1], (ips or [None])[0]


def _ips_frames(path, limit=14):
    """The faulting thread's functions of a macOS crash report (.ips: one JSON line, then JSON)."""
    try:
        with open(path, encoding="utf-8") as f:
            body = f.read().split("\n", 1)[1]
        data = json.loads(body)
        thread = data["threads"][data.get("faultingThread", 0)]
        out = [fr.get("symbol", "") for fr in thread.get("frames", [])[:limit]]
        kind = (data.get("exception") or {}).get("type", "")
        return kind, [f for f in out if f]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return "", []


def analyse(crash):
    """{"where": one line, "rule": what to avoid, "details": the evidence} of the crash."""
    text_path, ips_path = reports(crash)
    text = ""
    if text_path:
        try:
            with open(text_path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            pass
    kind, frames = _ips_frames(ips_path) if ips_path else ("", [])
    python = re.findall(r'File "[^"]*?([^/"]+\.py)", line (\d+) in (\w+)', text)
    ops = [ln.strip() for ln in text.split("# backtrace")[0].splitlines()
           if ln.strip() and not ln.startswith("#")][-4:]
    c_frames = re.findall(r"Blender\s+0x[0-9a-f]+\s+(\S+)", text)[:12]
    evidence = " ".join(frames + c_frames + [" ".join(p) for p in python] + [kind])
    where, rule = "", ""
    for pattern, what, advice in KNOWN:
        if re.search(pattern, evidence):
            where, rule = what, advice
            break
    first = next((f for f in frames + c_frames if not re.search(r"sig|backtrace|raise|pthread_kill|abort|"
                                                                r"_sigtramp|faulthandler|CAVerbose|CAAssert", f)),
                 "")
    if not where:
        where = first[:80] or "unknown"
        rule = ("Save before the step that crashed (%s) and do it in smaller parts." % crash.get("step", "")
                if crash.get("step") else "Work in smaller steps and save before heavy ones.")
    details = []
    if crash.get("step"):
        details.append("last step: " + crash["step"])
    if first:
        details.append("crashed in: " + first[:120])
    if python:
        details.append("python: " + ", ".join("%s:%s %s" % p for p in python[-3:]))
    if ops:
        details.append("last actions: " + " | ".join(o[:80] for o in ops))
    if kind:
        details.append(kind)
    return {"where": where, "rule": rule, "details": details, "report": text_path or ips_path or ""}


# ------------------------------------------------------------------ 2. learn

def learn(crash, found):
    """The crash as a lesson of the current model (its rule goes into the system prompt)."""
    from . import core, lessons
    prefs = core.get_prefs()
    if prefs is not None and not getattr(prefs, "learn_from_errors", True):
        return None
    try:
        name, _new = lessons.save(core.lessons_root(prefs), core.model_slug(prefs), core.model_label(prefs),
                                  "Blender crash: " + found["where"], found["rule"],
                                  "\n".join(found["details"]) or "Blender quit without a clean exit.",
                                  "Blender restarted; the file, the render farm and the task were set up again "
                                  "automatically. Avoid it with the rule above.")
        return name
    except Exception as ex:                   # learning is a bonus: never stop the recovery
        print("AI Studio: crash lesson not saved:", ex)
        return None


# ------------------------------------------------------------------ 3. + 4. the file, the farm, the chat

_run = {"crash": None, "found": None, "since": 0.0, "opened": False, "sent": False}


def continue_prompt(crash, found):
    task = (crash.get("task") or "").strip()
    lines = ["Blender ist während deiner letzten Aufgabe abgestürzt und wurde neu gestartet.",
             "Letzte Aufgabe: «%s»" % task[:1200]]
    if crash.get("step"):
        lines.append("Letzter Schritt vor dem Absturz: %s" % crash["step"])
    lines.append("Ursache laut Absturzbericht: %s%s" % (found["where"], (" (" + "; ".join(found["details"][1:3]) + ")")
                                                     if len(found["details"]) > 1 else ""))
    lines.append("Mach dort weiter, wo du warst, aber vorsichtiger: prüfe zuerst, was vom Stand schon da ist "
                 "(die Datei ist der zuletzt gespeicherte Stand, es kann etwas fehlen), arbeite in kleinen "
                 "Schritten und vermeide, was abgestürzt ist: " + found["rule"])
    return "\n".join(lines)


def _tick():
    crash, found = _run["crash"], _run["found"]
    if crash is None:
        return None
    from . import autosave, core
    try:
        path = bpy.data.filepath
        if not path and not _run["opened"] and crash.get("file") and time.monotonic() - _run["since"] > 1.5:
            _run["opened"] = True                 # started empty: the file of then, its newest state
            newest = autosave.newest_copy(crash["file"]) or crash.get("backup")
            if newest and os.path.isfile(newest):
                core.add_message("info", "Nach dem Absturz: %s wird geöffnet" % os.path.basename(newest))
                bpy.ops.claude.crash_open_backup('EXEC_DEFAULT')
                return 1.0
        same = bool(path) and bool(crash.get("file")) and \
            autosave.project_name(path) == autosave.project_name(crash["file"])
        waited = time.monotonic() - _run["since"]
        if not same:
            if waited > WAIT_CHAT:
                _finish(False)
                return None
            return 1.0
        if crash.get("busy") and crash.get("task") and not _run["sent"]:
            if core.is_busy() or (crash.get("chat") and core.session_id != crash["chat"] and waited < WAIT_CHAT):
                return 1.0                         # its chat is not back yet
            _run["sent"] = True
            core.queue_prompt(continue_prompt(crash, found))
        _finish(True)
    except Exception as ex:
        print("AI Studio: recovery:", ex)
        _finish(False)
    return None


def _finish(ok):
    from . import autosave, core
    crash, found = _run["crash"], _run["found"]
    _run["crash"] = None
    if crash is None:
        return
    when = time.strftime("%H:%M", time.localtime(float(crash.get("time") or time.time())))
    parts = ["Absturz um %s erkannt: %s." % (when, found["where"])]
    if ok:
        parts.append("Datei und Renderfarm wieder aufgesetzt" +
                     (", die letzte Aufgabe läuft vorsichtig weiter." if _run["sent"] else "."))
        crash["resumed"] = True
        autosave.dismiss_crash()
    core.add_message("info", " ".join(parts))


def start(crash):
    """Called once at the start when the last session crashed (not in the background)."""
    if not crash or bpy.app.background:
        return
    from . import autosave, core
    state = _read_state()
    now = time.time()
    again = now - float(state.get("last", 0)) < LOOP_WINDOW
    found = analyse(crash)
    lesson = learn(crash, found)
    _write_state({"last": now, "count": int(state.get("count", 0)) + 1 if again else 1,
                  "where": found["where"], "lesson": lesson or ""})
    autosave.careful_until[0] = now + CAREFUL_FOR
    core.notes.append("Blender crashed last time (%s%s). %s Be careful: small steps, save before heavy ones."
                      % (found["where"], ", during: " + crash["step"] if crash.get("step") else "",
                         found["rule"]))
    if again and int(state.get("count", 0)) >= 1:     # crashed again right after a recovery: ask instead
        core.add_message("info", "Blender ist kurz nach dem letzten Neustart wieder abgestürzt (%s). Ich setze "
                                 "nicht automatisch fort: öffne die Datei und sag mir, wie es weitergehen soll."
                         % found["where"])
        return
    _run.update(crash=crash, found=found, since=time.monotonic(), opened=False, sent=False)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=2.0, persistent=True)   # survives opening the file


def register():
    from . import autosave
    start(autosave.last_crash)


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)

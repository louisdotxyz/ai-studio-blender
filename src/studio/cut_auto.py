# SPDX-License-Identifier: GPL-3.0-or-later
"""Export when ready: the film's export starts by itself once every frame of the chosen sections is
rendered and up to date (Video Editor · Export · "When Ready", or api.cut_export_when_ready from the chat).

Replaces the chat's own timers (they stopped with an error, ran twice, were gone after a restart):
- The wishes live in ~/.cache/ai_studio_farm/exports/<file>-<hash>.json: they survive a restart and a
  crash (recovery.py opens the file again, the check goes on).
- Checked every 10 s on the main thread with what the timeline knows anyway (the local frames and the
  farm jobs, cached): no network, no share.
- Ready = every frame of every section up to date (green) in the wanted quality, nothing renders for them,
  twice in a row 30 s apart (the last files are complete).
- One export at a time; the user's export settings stay as they were. A failed export is tried again
  twice, then reported. The result can be copied into a folder under a name (FINAL …).
- Done / failed: a notification and a line in the chat."""

import hashlib
import json
import os
import re
import shutil
import time

import bpy

CHECK_EVERY = 10.0
SETTLE = 30.0
TRIES = 3
STATES_WAIT = ('WAITING', 'EXPORTING')
_cache = {"path": "", "mtime": None, "items": []}
_last = [0.0]


def _path(blend):
    from .renderfarm import LOCAL
    stem = re.sub(r"[^A-Za-z0-9_\-]+", "_", os.path.splitext(os.path.basename(blend))[0]) or "untitled"
    return os.path.join(LOCAL, "exports", "%s-%s.json" % (stem, hashlib.sha1(blend.encode()).hexdigest()[:8]))


def load(blend=None):
    blend = blend or bpy.data.filepath
    if not blend:
        return []
    path = _path(blend)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    if _cache["path"] != path or _cache["mtime"] != mtime:
        try:
            with open(path, encoding="utf-8") as f:
                items = json.load(f)
        except (OSError, ValueError):
            items = []
        _cache.update(path=path, mtime=mtime, items=items if isinstance(items, list) else [])
    return _cache["items"]


def save(items, blend=None):
    blend = blend or bpy.data.filepath
    path = _path(blend)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        json.dump(items, f, indent=1)
    os.replace(path + ".tmp", path)
    _cache.update(path=path, mtime=os.path.getmtime(path), items=items)


def add(scene, sections=None, split=None, fmt=None, quality=None, size=None, folder=None, name=None,
        frames='HIGH', names=None):
    """A wish: these sections (None: as chosen in Export) exported once they are all rendered. frames:
    'HIGH' (all up to date in High Quality), 'ANY' (up to date, any quality) or 'EXISTING' (the frames that
    are there, nothing still rendering for them – no up-to-date check). names: {section: file name} for one
    file per section (each its own name, all into folder). Returns it."""
    from . import cut
    if not bpy.data.filepath:
        raise ValueError("Save the file first: the wish belongs to it")
    film = cut.film_scene(scene)
    rq = film.studio
    every = [n for n, _s, _e, _m in cut.sections_of(film)]
    if sections is None:
        skip = set((getattr(rq, "cut_export_skip", "") or "").split("\n"))
        sections = [n for n in every if n not in skip]
    unknown = [s for s in sections if s not in every]
    if unknown or not sections:
        raise ValueError("Unknown sections: %s (there are: %s)" % (", ".join(unknown) or "none", ", ".join(every)))
    item = {"id": "%x" % int(time.time() * 1000), "sections": list(sections),
            "split": split or getattr(rq, "cut_export_split", 'ONE'),
            "format": fmt or getattr(rq, "cut_export_format", 'MP4'),
            "quality": quality or getattr(rq, "cut_export_quality", 'HIGH'),
            "size": (size if size == 'FRAMES' else str(int(size))) if size else getattr(rq, "cut_export_size", '100'),
            "names": dict(names or {}),
            "folder": folder or "", "name": name or "", "frames": frames, "state": 'WAITING',
            "added": time.time(), "tries": 0, "ready_since": 0.0, "outs": [], "error": ""}
    items = [i for i in load() if not (i["state"] in STATES_WAIT and i["sections"] == item["sections"]
                                       and i["split"] == item["split"])]        # the same wish again: replaced
    save(items + [item])
    return item


def cancel(item_id):
    items = [i for i in load() if i["id"] != item_id]
    save(items)
    return len(items)


def ready(film, section, frames='HIGH'):
    """(ready, up-to-date frames, all frames) of a section."""
    from . import cut, cut_overlay
    span = next(((s, e) for n, s, e, _m in cut.sections_of(film) if n == section), None)
    if span is None:
        return False, 0, 0
    states = cut_overlay.states(section, span[0], span[1])
    good = {'HIGH'} if frames == 'HIGH' else {'HIGH', 'LOW', 'OLD'} if frames == 'EXISTING' else {'HIGH', 'LOW'}
    count = sum(1 for s in states if s in good)
    if frames == 'EXISTING' and 'RENDERING' in states:
        return False, count, len(states)
    return bool(states) and count == len(states), count, len(states)


def waits_for(film, item):
    """Why a wish has not started: "238 render · 2 missing · 15 old · LQ only", "" when it is ready."""
    from . import cut, cut_overlay
    counts = {}
    for section in item["sections"]:
        span = next(((s, e) for n, s, e, _m in cut.sections_of(film) if n == section), None)
        if span is None:
            counts["gone"] = counts.get("gone", 0) + 1
            continue
        for state in cut_overlay.states(section, span[0], span[1]):
            counts[state] = counts.get(state, 0) + 1
    words = []
    for key, text in (('RENDERING', "render"), ('MISSING', "missing"), ('DRAFT', "draft"), ('gone', "section gone")):
        if counts.get(key):
            words.append("%d %s" % (counts[key], text))
    if counts.get('OLD') and item.get("frames") != 'EXISTING':
        words.append("%d old" % counts['OLD'])
    if counts.get('LOW') and item.get("frames", 'HIGH') == 'HIGH':
        words.append("%d LQ only" % counts['LOW'])
    return " · ".join(words)


def progress(film, item):
    have = total = 0
    for section in item["sections"]:
        _ok, h, t = ready(film, section, item.get("frames", 'HIGH'))
        have, total = have + h, total + t
    return have, total


def _export_running():
    from . import cut, film as film_module
    work = os.path.join(cut.cut_folder(), film_module.FILM_DIR)
    for entry in film_module._load():
        if os.path.normcase(entry.get("dir", "")) == os.path.normcase(work) and _alive(entry.get("pid")):
            return entry
    return None


def _alive(pid):
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def _outputs(film, fmt):
    from . import cut
    ranges, join = cut.export_plan(film)
    ext = "" if fmt == 'PNG' else cut.VIDEO_FORMATS[fmt][2]
    if join:
        return [join + ext]
    return [os.path.dirname(r[3]) if fmt == 'PNG' else r[3] + ext for r in ranges]


def _labels(film):
    """The section of each output (one file per section), "" for joined ranges."""
    from . import cut
    ranges, join = cut.export_plan(film)
    return [""] if join else [r[0] for r in ranges]


def _start(film, item):
    """The export with the wish's settings (the user's settings back afterwards). Returns the outputs."""
    from . import cut
    rq = film.studio
    every = [n for n, _s, _e, _m in cut.sections_of(film)]
    wanted = {"cut_export_format": item["format"], "cut_export_quality": item["quality"],
              "cut_export_size": item["size"], "cut_export_split": item["split"], "cut_export_range": 'ALL',
              "cut_export_skip": "\n".join(n for n in every if n not in item["sections"])}
    keep = {k: getattr(rq, k) for k in wanted if hasattr(rq, k)}
    try:
        for k, v in wanted.items():
            if hasattr(rq, k) and getattr(rq, k) != v:
                setattr(rq, k, v)
        outs = _outputs(film, item["format"])
        item["labels"] = _labels(film)
        cut.export(film)
    finally:
        for k, v in keep.items():
            if getattr(rq, k) != v:
                setattr(rq, k, v)
    return outs


def _deliver(item):
    """The finished export into the wish's folder under its name (copies; the export stays)."""
    if not item.get("folder"):
        return list(item["outs"])
    os.makedirs(item["folder"], exist_ok=True)
    done = []
    labels = item.get("labels") or []
    for n, src in enumerate(item["outs"]):
        base = os.path.basename(src.rstrip("/\\"))
        own = (item.get("names") or {}).get(labels[n] if n < len(labels) else "")
        if own:                                     # this section's own name
            base = own + os.path.splitext(base)[1]
        elif item.get("name"):
            ext = os.path.splitext(base)[1]
            base = item["name"] + ("" if len(item["outs"]) == 1 else " %d" % (n + 1)) + ext
        dst = os.path.join(item["folder"], base)
        if os.path.isdir(src):
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst + ".part")
            os.replace(dst + ".part", dst)
        done.append(dst)
    return done


def _tell(text):
    from . import runner
    try:
        from .. import core
        core.add_message("info", text)
    except Exception:
        pass
    try:
        runner.notify(text)
    except Exception:
        pass


def _log_tail():
    from . import cut, film as film_module
    try:
        with open(os.path.join(cut.cut_folder(), film_module.FILM_DIR, "log.txt"), encoding="utf-8",
                  errors="replace") as f:
            lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
        return " | ".join(lines[-3:])[-300:]
    except OSError:
        return ""


def tick(now=None):
    """(main thread, from the Video Editor's timer) Waiting wishes start, running ones are checked."""
    now = now or time.time()
    if now - _last[0] < CHECK_EVERY:
        return
    _last[0] = now
    items = load()
    if not any(i["state"] in STATES_WAIT for i in items):
        return
    from . import cut
    film = next((cut.film_scene(s) for s in bpy.data.scenes if s.get(cut.FILM_KEY)), None)
    if film is None:
        return
    changed = False
    for item in items:
        label = " + ".join(item["sections"])
        if item["state"] == 'EXPORTING':
            if _export_running():
                continue
            outs = [p for p in item["outs"] if os.path.exists(p) and os.path.getmtime(p) >= item["started"] - 5]
            if outs and len(outs) == len(item["outs"]):
                try:
                    item["delivered"] = _deliver(item)
                    item["state"] = 'DONE'
                    names = ", ".join(os.path.basename(p) for p in item["delivered"])
                    _tell("Export ready: %s → %s" % (label, names))
                except OSError as ex:
                    item["state"], item["error"] = 'FAILED', "copy: %s" % ex
                    _tell("Export of %s done, but copying failed: %s" % (label, ex))
            else:
                item["tries"] += 1
                item["error"] = _log_tail() or "no file came out"
                if item["tries"] >= TRIES:
                    item["state"] = 'FAILED'
                    _tell("Export of %s failed %d times: %s" % (label, item["tries"], item["error"]))
                else:
                    item["state"], item["ready_since"] = 'WAITING', 0.0
            item["finished"] = now
            changed = True
            continue
        if item["state"] != 'WAITING':
            continue
        if all(ready(film, s, item.get("frames", 'HIGH'))[0] for s in item["sections"]):
            if not item["ready_since"]:
                item["ready_since"], changed = now, True
                continue
            if now - item["ready_since"] < SETTLE or _export_running():
                continue
            try:
                item["outs"] = _start(film, item)
                item["state"], item["started"] = 'EXPORTING', now
                _tell("All frames of %s are there: export started" % label)
            except Exception as ex:                    # never stop the timer
                item["tries"] += 1
                item["error"] = str(ex)
                item["state"] = 'FAILED' if item["tries"] >= TRIES else 'WAITING'
                item["ready_since"] = 0.0
                if item["state"] == 'FAILED':
                    _tell("Export of %s could not start: %s" % (label, ex))
            changed = True
            break                                      # one export at a time
        elif item["ready_since"]:
            item["ready_since"], changed = 0.0, True
    if changed:
        keep = [i for i in items if i["state"] in STATES_WAIT or now - i.get("finished", now) < 86400]
        save(keep)

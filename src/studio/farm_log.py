# SPDX-License-Identifier: GPL-3.0-or-later
"""The render farm's history: when it worked well, when not, and why Macs dropped out.

Two sources:
- This Mac (renderfarm._watch_workers, every minute while jobs are out): the share reachable or not, each
  Mac's state (rendering / waiting / offline), the frames there → ~/.cache/ai_studio_farm/history.jsonl.
- Each studio Mac's log book (worker 0.8.0+, 0.9 also FREI / FEHLER / WARTET: _worker/logs/<Mac>.log on the share): start, jobs, packages
  with their time, server gone/back, and how a session ended (Ctrl+C, logout, shutdown, update, no clean
  end) with the Mac's rules (log out automatically after …, scheduled shutdown, sleep).

report() puts both together for the chat (api.farm_report): hour by hour, the events, why each Mac ended,
patterns (several Macs gone at the same minute, a logout rule), and what was learned (kept in
farm_lessons.json so it is known next time)."""

import json
import os
import re
import threading
import time

KEEP_DAYS = 14
MAX_BYTES = 5_000_000


def _local():
    from .renderfarm import LOCAL
    return LOCAL


def history_path():
    return os.path.join(_local(), "history.jsonl")


def lessons_path():
    return os.path.join(_local(), "farm_lessons.json")


_last = {"t": 0.0, "macs": {}, "share": None}


def record(share_ok, states, jobs, now=None):
    """One line per minute: {"t", "share", "macs": {name: state}, "have": frames there, "jobs": open}."""
    now = now or time.time()
    if now - _last["t"] < 55:
        return None
    macs = {n: st for n, st, _j, _s in states} if share_ok else dict(_last["macs"])
    line = {"t": round(now), "share": bool(share_ok), "macs": macs,
            "have": sum(int(j.get("have", 0) or 0) for j in jobs), "jobs": len(jobs)}
    _last.update(t=now, macs=macs, share=share_ok)
    path = history_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
            _prune(path, now)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
    except OSError:
        return None
    return line


def _prune(path, now):
    keep = []
    with open(path, encoding="utf-8") as f:
        for raw in f:
            try:
                if now - json.loads(raw)["t"] < KEEP_DAYS * 86400:
                    keep.append(raw)
            except (ValueError, KeyError):
                pass
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.writelines(keep[-50000:])
    os.replace(path + ".tmp", path)


def history(hours=24, now=None):
    now = now or time.time()
    out = []
    try:
        with open(history_path(), encoding="utf-8") as f:
            for raw in f:
                try:
                    line = json.loads(raw)
                except ValueError:
                    continue
                if now - line.get("t", 0) <= hours * 3600:
                    out.append(line)
    except OSError:
        pass
    return out


# ------------------------------------------------------------------ the studio Macs' log books

LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \| (\w+) ?(.*)$")


def parse_log(text):
    """[(epoch, kind, detail)] of a worker log book."""
    out = []
    for raw in text.splitlines():
        m = LINE.match(raw.strip())
        if not m:
            continue
        try:
            stamp = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        out.append((stamp, m.group(2), m.group(3).strip()))
    return out


def read_logs(root, timeout=20.0):
    """{Mac: [(epoch, kind, detail)]} from the share (in a thread: a hanging share costs at most timeout)."""
    found = {}

    def work():
        folder = os.path.join(root, "_worker", "logs")
        try:
            names = os.listdir(folder)
        except OSError:
            return
        for name in names:
            if not name.endswith(".log"):
                continue
            try:
                with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as f:
                    found[name[:-4]] = parse_log(f.read()[-400_000:])
            except OSError:
                pass
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout)
    return found


def end_reason(entries, start, end):
    """How a Mac's sessions in [start, end] ended: [(when, reason)]. A session that just stops is read
    from the next start's VORHER / SYSTEM lines (logout, restart, shutdown, sleep)."""
    out = []
    for n, (stamp, kind, detail) in enumerate(entries):
        if not start <= stamp <= end:
            continue
        if kind == "ENDE":
            out.append((stamp, detail))
        elif kind == "VORHER":
            when = re.search(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)", detail)
            last = time.mktime(time.strptime(when.group(1), "%Y-%m-%d %H:%M:%S")) if when else stamp
            system = " ".join(d for s, k, d in entries[n + 1:n + 4] if k == "SYSTEM")
            out.append((last, "no clean end (" + _guess(system, last) + ")"))
    return out


def _guess(system, when):
    """From `last` / pmset lines: logout, restart, shutdown or sleep near `when`."""
    hhmm = time.strftime("%H:%M", time.localtime(when))
    text = system.lower()
    near = hhmm[:4] in text                     # the same ten minutes appear in the system lines
    for word, what in (("shutdown", "switched off"), ("reboot", "restarted"), ("sleep", "went to sleep")):
        if word in text and near:
            return "%s around %s" % (what, hhmm)
    if "console" in text and near:
        return "logged out around %s" % hhmm
    return "logged out, switched off or the power went, around %s" % hhmm


def rules(entries):
    """The Mac's rules from its newest REGELN line."""
    for _stamp, kind, detail in reversed(entries):
        if kind == "REGELN":
            return detail
    return ""


# ------------------------------------------------------------------ report

def report(hours=24, root=None, now=None):
    """{"text": the summary, "hours": [...], "events": [...], "macs": {...}, "learned": [...]}."""
    now = now or time.time()
    since = now - hours * 3600
    lines = history(hours, now)
    hours_out = []
    for start in range(int(since // 3600 * 3600), int(now), 3600):
        chunk = [x for x in lines if start <= x["t"] < start + 3600]
        if not chunk:
            continue
        share = sum(1 for x in chunk if x["share"]) / len(chunk)
        rendering = sum(sum(1 for s in x["macs"].values() if s == 'rendering') for x in chunk) / len(chunk)
        online = sum(sum(1 for s in x["macs"].values() if s != 'offline') for x in chunk) / len(chunk)
        gained = max(0, chunk[-1]["have"] - chunk[0]["have"]) if chunk[-1]["jobs"] else 0
        hours_out.append({"hour": time.strftime("%d.%m %H:00", time.localtime(start)), "share": round(share, 2),
                          "online": round(online, 1), "rendering": round(rendering, 1), "frames": gained})
    events = []
    prev = None
    for x in lines:
        if prev is not None:
            if prev["share"] != x["share"]:
                events.append((x["t"], "share " + ("back" if x["share"] else "gone")))
            for name, state in x["macs"].items():
                was = prev["macs"].get(name)
                if was and was != 'offline' and state == 'offline':
                    events.append((x["t"], "%s offline" % name))
                elif was == 'offline' and state != 'offline':
                    events.append((x["t"], "%s back" % name))
        prev = x
    logs = read_logs(root) if root else {}
    macs = {}
    for name, entries in logs.items():
        recent = [(t, k, d) for t, k, d in entries if since <= t <= now]
        macs[name] = {"ends": [(time.strftime("%d.%m %H:%M", time.localtime(s)), r)
                               for s, r in end_reason(entries, since, now)],
                      "rules": rules(entries),
                      "freed": sum(1 for _t, k, _d in recent if k == "FREI"),      # worker 0.9: hung frames freed
                      "errors": [d for _t, k, d in recent if k == "FEHLER"][-3:]}
    learned = learn(events, macs)
    text = _text(hours, hours_out, events, macs, learned, bool(lines), bool(logs))
    return {"text": text, "hours": hours_out, "events": [(time.strftime("%d.%m %H:%M", time.localtime(t)), e)
                                                         for t, e in events], "macs": macs, "learned": learned}


def learn(events, macs):
    """Patterns, kept in farm_lessons.json: several Macs gone within 10 minutes (a schedule of the
    rooms or of IT), a Mac with a logout rule, the same reason again and again."""
    found = []
    offline = sorted((t, e.split(" ")[0]) for t, e in events if e.endswith(" offline"))
    i = 0
    while i < len(offline):
        group = [m for t, m in offline if 0 <= t - offline[i][0] <= 600]
        if len(group) >= 3:
            when = time.strftime("%H:%M", time.localtime(offline[i][0]))
            found.append("%d Macs went offline together around %s (%s): a schedule of the rooms or of IT, "
                         "not the farm" % (len(group), when, ", ".join(group)))
            i += len(group)
        else:
            i += 1
    for name, info in macs.items():
        m = re.search(r"abmelden nach: (\d+) s", info.get("rules", ""))
        if m and int(m.group(1)) > 0:
            found.append("%s logs out by itself after %d min without anyone using it" % (name, int(m.group(1)) // 60))
        planned = info.get("rules", "").split("geplant:")[1].split("| Schlaf")[0] if "geplant:" in \
            info.get("rules", "") else ""
        if re.search(r"(shutdown|restart|sleep|poweroff)", planned, re.I):
            found.append("%s has a scheduled power event: %s" % (name, planned.strip()[:80]))
        reasons = [r for _w, r in info["ends"]]
        for reason in set(reasons):
            if reasons.count(reason) >= 2:
                found.append("%s ended %d times the same way: %s" % (name, reasons.count(reason), reason))
    _keep(found)
    return found


def _keep(found):
    if not found:
        return
    path = lessons_path()
    try:
        with open(path, encoding="utf-8") as f:
            known = json.load(f)
    except (OSError, ValueError):
        known = {}
    now = time.strftime("%Y-%m-%d %H:%M")
    for item in found:
        key = re.sub(r"\d\d:\d\d", "", item)[:120]
        entry = known.get(key, {"text": item, "first": now, "seen": 0})
        entry.update(text=item, last=now, seen=entry["seen"] + 1)
        known[key] = entry
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(known, f, indent=1)
    except OSError:
        pass


def lessons():
    try:
        with open(lessons_path(), encoding="utf-8") as f:
            return sorted(json.load(f).values(), key=lambda e: e.get("last", ""), reverse=True)
    except (OSError, ValueError):
        return []


def _text(hours, hours_out, events, macs, learned, have_history, have_logs):
    out = ["Render farm, last %d h:" % hours]
    if not have_history:
        out.append("- No history yet on this Mac (it is written every minute while jobs are out).")
    good = [h for h in hours_out if h["share"] > 0.9 and h["rendering"] >= 1]
    bad = [h for h in hours_out if h["share"] < 0.9 or (h["online"] and h["rendering"] < 1 and h["frames"] == 0)]
    for h in hours_out:
        out.append("- %s: share %d %%, %.1f Macs online, %.1f rendering, %d frames" %
                   (h["hour"], h["share"] * 100, h["online"], h["rendering"], h["frames"]))
    if good or bad:
        out.append("Good hours: %d · poor hours: %d" % (len(good), len(bad)))
    if events:
        out.append("Events: " + "; ".join("%s %s" % (time.strftime("%H:%M", time.localtime(t)), e)
                                          for t, e in events[-25:]))
    if not have_logs:
        out.append("- The studio Macs' log books are not there yet (worker 0.8.0 writes them after its next "
                   "start) or the share does not answer.")
    freed = sum(info.get("freed", 0) for info in macs.values())
    if freed:
        out.append("Frames freed again (left empty by a Mac that dropped out): %d" % freed)
    for name, info in sorted(macs.items()):
        for err in info.get("errors", []):
            out.append("%s: %s" % (name, err))
        if info["ends"]:
            out.append("%s: " % name + "; ".join("%s %s" % (w, r) for w, r in info["ends"][-4:]))
    if learned:
        out.append("Learned: " + " | ".join(learned))
    older = [e["text"] for e in lessons() if e["text"] not in learned][:5]
    if older:
        out.append("Known from before: " + " | ".join(older))
    return "\n".join(out)

# SPDX-License-Identifier: GPL-3.0-or-later
"""Freeze watch: runs as its own small process next to Blender (started by autosave.py).

    python -I freeze_watch.py <blender pid> <heartbeat file> <output folder>

Blender touches the heartbeat file every few seconds. If that stops for STALL
seconds while Blender is still alive, Blender hangs: macOS `sample` records what
every thread is doing into freeze_<time>.txt (once per hang). Ends with Blender.
Not part of the add-on itself: never imported.
"""

import os
import subprocess
import sys
import time

STALL = 30.0
POLL = 5.0
KEEP = 5


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def prune(folder):
    files = sorted((f for f in os.listdir(folder) if f.startswith("freeze_") and f.endswith(".txt")),
                   key=lambda f: os.path.getmtime(os.path.join(folder, f)), reverse=True)
    for name in files[KEEP:]:
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass


def record(pid, folder, age):
    stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
    path = os.path.join(folder, "freeze_%s.txt" % stamp)
    with open(path, "w", encoding="utf-8") as f:
        f.write("Blender (pid %d) has not answered for %.0f s.\n" % (pid, age))
        try:
            with open(os.path.join(folder, "running.json"), encoding="utf-8") as m:
                f.write("Last step: %s\n" % m.read())
        except OSError:
            pass
        f.write("\n")
    subprocess.run(["/usr/bin/sample", str(pid), "5", "-mayDie", "-file", path + ".sample"],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    try:
        with open(path + ".sample", encoding="utf-8", errors="replace") as s, open(path, "a", encoding="utf-8") as f:
            f.write(s.read())
        os.remove(path + ".sample")
    except OSError:
        pass
    with open(os.path.join(folder, "freeze.log"), "a", encoding="utf-8") as log:
        log.write("%s  no answer for %.0f s → %s\n" % (stamp, age, os.path.basename(path)))
    prune(folder)


def main():
    pid, beat, folder = int(sys.argv[1]), sys.argv[2], sys.argv[3]
    recorded = None   # heartbeat time of the hang already recorded
    last_loop = time.monotonic()
    while alive(pid):
        time.sleep(POLL)
        now = time.monotonic()
        slept = now - last_loop > POLL * 4   # the Mac was asleep: Blender was not hanging
        last_loop = now
        try:
            mtime = os.path.getmtime(beat)
        except OSError:
            continue
        age = time.time() - mtime
        if slept:
            recorded = mtime
            continue
        if age > STALL and recorded != mtime and alive(pid):
            recorded = mtime
            try:
                record(pid, folder, age)
            except Exception:
                pass


if __name__ == "__main__":
    main()

# SPDX-License-Identifier: GPL-3.0-or-later
"""CAD jobs: a change Blender asks the CAD side for, handed to a Claude chat.

A job is a JSON file in ~/.config/ai-studio/jobs/<id>.json:
    {"id", "created", "status": "open" | "claimed" | "done", "text", "part", "cad_folder",
     "source" (Onshape element), "chat": {"name", "url"} (the chat the export came from),
     "images": [paths], "claimed_by", "claimed_at", "done_at", "note"}
Blender writes it (CAD box › Send CAD Job, or Claude's studio_cad_job tool); the chat picks it
up with the MCP tool blender_jobs (list / claim / done) of live_mcp.py, which imports this
module directly (no bpy here, no Blender needed for it). A chat that calls blender_jobs with its
name is noted in jobs/seen.json: Blender offers the known chats for the next job.
"""

import datetime
import json
import os
import re
import tempfile
import urllib.parse
import uuid

JOBS_DIR = os.path.expanduser("~/.config/ai-studio/jobs")
STATUSES = ("open", "claimed", "done")


def _dir(jobs_dir=None):
    folder = jobs_dir or os.environ.get("AI_STUDIO_JOBS_DIR") or JOBS_DIR
    os.makedirs(folder, exist_ok=True)
    return folder


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _write(path, job):
    folder = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".job-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(job, f, indent=1, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def clean_chat(chat):
    """{"name", "url"} or None – the same rule as the sidecar's (studio/cadfiles.py)."""
    if not isinstance(chat, dict):
        return None
    out = {key: str(chat.get(key) or "").strip()[:200] for key in ("name", "url")}
    if out["url"] and not out["url"].startswith(("https://claude.ai/", "claude://")):
        out["url"] = ""
    return out if any(out.values()) else None


def new_job(text, part="", cad_folder="", source="", chat=None, images=(), jobs_dir=None):
    """Write a new open job. Returns it (with "path"). The images are copied next to the job
    (jobs/<id>/), so they stay when the original was a temporary file (a chat's tool result)."""
    import shutil
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("A CAD job needs a text: what should change?")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    job_id = "cad-%s-%s" % (stamp, uuid.uuid4().hex[:4])
    folder = _dir(jobs_dir)
    kept = []
    for i, image in enumerate(p for p in images if p and os.path.isfile(p)):
        target_dir = os.path.join(folder, job_id)
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, "%d%s" % (i + 1, os.path.splitext(image)[1].lower() or ".png"))
        shutil.copyfile(image, target)
        kept.append(target)
    job = {"id": job_id, "created": _now(), "status": "open",
           "text": text, "part": str(part or ""), "cad_folder": str(cad_folder or ""),
           "source": str(source or ""), "chat": clean_chat(chat), "images": kept}
    path = os.path.join(folder, job["id"] + ".json")
    _write(path, job)
    return dict(job, path=path)


# ---------------------------------------------------------------- the chat of a CAD folder
# The chat a job went to is remembered per CAD export folder (jobs/chats.json), so the next job of
# that project goes there too when the export itself does not name its chat.

def _chats_file(jobs_dir=None):
    return os.path.join(_dir(jobs_dir), "chats.json")


def remembered_chat(cad_folder, jobs_dir=None):
    """The chat the last job of this CAD folder went to ({"name", "url"}), or None."""
    if not cad_folder:
        return None
    try:
        with open(_chats_file(jobs_dir), encoding="utf-8") as f:
            known = json.load(f)
    except (OSError, ValueError):
        return None
    return clean_chat(known.get(os.path.normpath(cad_folder))) if isinstance(known, dict) else None


def remember_chat(cad_folder, chat, jobs_dir=None):
    chat = clean_chat(chat)
    if not cad_folder or not chat:
        return
    path = _chats_file(jobs_dir)
    try:
        with open(path, encoding="utf-8") as f:
            known = json.load(f)
        known = known if isinstance(known, dict) else {}
    except (OSError, ValueError):
        known = {}
    known[os.path.normpath(cad_folder)] = chat
    _write(path, known)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _name(name):
    return " ".join(str(name or "").split())[:60]


def seen_chat(name, jobs_dir=None):
    """A chat called blender_jobs with its name: note it (and when)."""
    name = _name(name)
    if not name or name.lower() == "a claude chat":
        return
    path = os.path.join(_dir(jobs_dir), "seen.json")
    seen = _read(path)
    seen[name.lower()] = {"name": name, "last": _now()}
    _write(path, seen)


def known_chats(jobs_dir=None):
    """[{"name", "last"}] of the chats Blender knows, last active first: the ones that called
    blender_jobs with their name ("last": when), then the ones jobs went to ("last": "")."""
    known = {}
    for job in jobs(jobs_dir=jobs_dir):
        name = _name((job.get("chat") or {}).get("name"))
        if name:
            known.setdefault(name.lower(), {"name": name, "last": ""})
    for chat in _read(_chats_file(jobs_dir)).values():
        name = _name((chat or {}).get("name") if isinstance(chat, dict) else "")
        if name:
            known.setdefault(name.lower(), {"name": name, "last": ""})
    for key, item in _read(os.path.join(_dir(jobs_dir), "seen.json")).items():
        if isinstance(item, dict) and _name(item.get("name")):
            known[key] = {"name": _name(item["name"]), "last": str(item.get("last") or "")}
    return sorted(known.values(), key=lambda c: (-_stamp(c["last"]), c["name"].lower()))


def _stamp(text):
    try:
        return datetime.datetime.fromisoformat(text).timestamp()
    except (TypeError, ValueError):
        return 0.0


def ago(text, now=None):
    """(number, unit) since the stamp: unit "now", "min", "h" or "d"; None without a stamp."""
    if not _stamp(text):
        return None
    seconds = (now or datetime.datetime.now()).timestamp() - _stamp(text)
    if seconds < 90:
        return 0, "now"
    if seconds < 5400:
        return round(seconds / 60), "min"
    if seconds < 129600:
        return round(seconds / 3600), "h"
    return round(seconds / 86400), "d"


def load(job_id, jobs_dir=None):
    path = os.path.join(_dir(jobs_dir), os.path.basename(str(job_id)) + ".json")
    with open(path, encoding="utf-8") as f:
        return dict(json.load(f), path=path)


def jobs(status=None, jobs_dir=None):
    """The jobs, oldest first (status: one of STATUSES, or None for all)."""
    out = []
    folder = _dir(jobs_dir)
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json") or name.startswith("."):
            continue
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                job = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(job, dict) and (status is None or job.get("status") == status):
            out.append(dict(job, path=os.path.join(folder, name)))
    return out


def _update(job_id, jobs_dir=None, **changes):
    job = load(job_id, jobs_dir)
    path = job.pop("path")
    job.update(changes)
    _write(path, job)
    return dict(job, path=path)


def claim(job_id, by="", jobs_dir=None):
    """The chat takes the job (others see it as claimed). ValueError when it is done already."""
    job = load(job_id, jobs_dir)
    if job.get("status") == "done":
        raise ValueError("Job %s is done already" % job_id)
    seen_chat(by, jobs_dir)
    return _update(job_id, jobs_dir, status="claimed", claimed_by=_name(by) or "a Claude chat", claimed_at=_now())


def done(job_id, note="", jobs_dir=None):
    return _update(job_id, jobs_dir, status="done", done_at=_now(), note=" ".join(str(note or "").split()))


def message(job):
    """The text for the chat (a new chat opens with it; blender_jobs answers with it). It works on
    its own: what to change, where the files are, and what to do when the chat lacks the tools."""
    to = (job.get("chat") or {}).get("name")
    lines = ["CAD job from Blender (ID Toolkit), id %s%s:" % (job["id"], " – for the chat '%s'" % to if to else ""),
             job["text"]]
    if job.get("part"):
        lines.append("Part: %s" % job["part"])
    if job.get("source"):
        lines.append("Onshape: %s (where the version in Blender came from: change the newest version of that model)"
                     % job["source"])
    if job.get("cad_folder"):
        lines.append("Send the new version straight into Blender with OS.exportPackage (GLB + .cad.json, same part "
                     "names; no Mac folder needed); Blender keeps it in: %s" % job["cad_folder"])
    if job.get("images"):
        lines.append("Images (on the Mac): %s" % ", ".join(job["images"]))
    lines.append("Steps: blender_jobs action claim (id %s, by: this chat's name) → change the CAD model → export "
                 "again → blender_jobs action done with a short note." % job["id"])
    lines.append("Needs: a chat in the Claude desktop app that is linked to the Mac (for Onshape in the built-in "
                 "browser, signed in: check the sign-in first and tell the user if it is missing).")
    here = os.path.dirname(os.path.abspath(__file__))
    lines.append("If this chat has no blender_jobs tool but has blender_python (the Mac's 'blender' tools), use it "
                 "instead: claim with `import sys; sys.path.insert(0, %r); import cadjobs; "
                 "print(cadjobs.message(cadjobs.claim(%r, '<this chat's name>')))` and finish with "
                 "`cadjobs.done(%r, '<short note>')` the same way. With neither tool, say so and ask the user to "
                 "quit and reopen the Claude app once (Cmd+Q)." % (here, job["id"], job["id"]))
    return "\n".join(lines)


def summary(job):
    """One line for lists."""
    who = (job.get("chat") or {}).get("name") or "new chat"
    return "%s · %s · %s · %s" % (job.get("status", "?"), job.get("part") or "general", job.get("text", ""), who)


# ---------------------------------------------------------------- delivery to the Claude app
# Links of the Claude app (macOS, checked in the app and its docs, support.claude.com article
# "Open Claude desktop with a link"):
#   claude://claude.ai/new?q=<text>     a new chat with the text filled in (not sent; about 14 000
#                                        characters at most)
#   claude://claude.ai/chat/<uuid>      an existing chat (no text parameter; without a valid id the
#                                        app shows Recents)
# A chat in the app cannot read its own link, so mostly only the chat's name is known: then the
# app opens at Recents with a notification, and the chat takes the job with blender_jobs.

NEW_CHAT = "claude://claude.ai/new?q=%s"
CHAT = "claude://claude.ai/chat/%s"
RECENTS = "claude://claude.ai/recents"
MAX_Q = 12000
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def chat_uuid(url):
    match = _UUID.search(str(url or "")) if "/chat/" in str(url or "") else None
    return match.group(0).lower() if match else ""


def chat_link(job, target="origin"):
    """(link, how) that opens the chat for this job: its origin chat ("origin") by its link, or
    Recents when only its name is known; else a new chat with the job's text."""
    chat = job.get("chat") or {}
    if target == "origin" and chat:
        uuid_ = chat_uuid(chat.get("url"))
        if uuid_:
            return CHAT % uuid_, "opened the chat '%s'" % (chat.get("name") or uuid_)
        if chat.get("name"):
            return RECENTS, "opened Claude: the chat '%s' takes it with blender_jobs" % chat["name"]
    return NEW_CHAT % urllib.parse.quote(message(job)[:MAX_Q], safe=""), "opened a new chat with the job"


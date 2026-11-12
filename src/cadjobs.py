# SPDX-License-Identifier: GPL-3.0-or-later
"""CAD jobs: a change Blender asks the CAD side for, handed to a Claude chat.

A job is a JSON file in ~/.config/ai-studio/jobs/<id>.json:
    {"id", "created", "status": "open" | "claimed" | "done", "text", "part", "cad_folder",
     "source" (Onshape element), "chat": {"name", "url"} (the chat the export came from),
     "images": [paths], "claimed_by", "claimed_at", "done_at", "note"}
Blender writes it (CAD box › Send CAD Job, or Claude's studio_cad_job tool); the chat picks it
up with the MCP tool blender_jobs (list / claim / done) of live_mcp.py, which imports this
module directly (no bpy here, no Blender needed for it).
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
    """{"name", "url", "session"} or None – the same rule as the sidecar's (studio/cadfiles.py)."""
    if not isinstance(chat, dict):
        return None
    out = {key: str(chat.get(key) or "").strip()[:200] for key in ("name", "url", "session")}
    if out["url"] and not out["url"].startswith(("https://claude.ai/", "claude://")):
        out["url"] = ""
    return out if any(out.values()) else None


def new_job(text, part="", cad_folder="", source="", chat=None, images=(), jobs_dir=None):
    """Write a new open job. Returns it (with "path")."""
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("A CAD job needs a text: what should change?")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    job = {"id": "cad-%s-%s" % (stamp, uuid.uuid4().hex[:4]), "created": _now(), "status": "open",
           "text": text, "part": str(part or ""), "cad_folder": str(cad_folder or ""),
           "source": str(source or ""), "chat": clean_chat(chat),
           "images": [str(p) for p in images if p and os.path.isfile(p)]}
    path = os.path.join(_dir(jobs_dir), job["id"] + ".json")
    _write(path, job)
    return dict(job, path=path)


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
    return _update(job_id, jobs_dir, status="claimed", claimed_by=str(by or "a Claude chat"), claimed_at=_now())


def done(job_id, note="", jobs_dir=None):
    return _update(job_id, jobs_dir, status="done", done_at=_now(), note=" ".join(str(note or "").split()))


def message(job):
    """The text for the chat (opened with it, and in the clipboard)."""
    lines = ["CAD job from Blender (AI Studio), id %s:" % job["id"], job["text"]]
    if job.get("part"):
        lines.append("Part: %s" % job["part"])
    if job.get("source"):
        lines.append("Onshape: %s" % job["source"])
    if job.get("images"):
        lines.append("Images: %s" % ", ".join(job["images"]))
    lines.append("Take it with the tool blender_jobs (action claim, id %s), make the change in the CAD model, "
                 "export again, then blender_jobs action done with a short note." % job["id"])
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
# A chat in the app cannot read its own link, so a sidecar often has only the chat's name: then
# the app opens at Recents and the text is in the clipboard to paste into that chat.

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
            return CHAT % uuid_, "opened the chat '%s' (paste the text)" % (chat.get("name") or uuid_)
        if chat.get("name"):
            return RECENTS, "opened Claude: paste the text into the chat '%s'" % chat["name"]
    return NEW_CHAT % urllib.parse.quote(message(job)[:MAX_Q], safe=""), "opened a new chat with the job"


# ---------------------------------------------------------------- open Claude Code sessions
# Claude Code (2.1.224+, macOS) lets sessions on this computer message each other
# (code.claude.com/docs/en/cross-session-messaging). `claude agents --json` lists the running
# sessions; a short `claude -p` with only the SendMessage tool delivers the job's text to the
# session named in the sidecar ("session"), which then sees it between its steps or as a new turn.

def code_sessions(claude_path, run=None):
    """[{"name", "sessionId", "status", "cwd"}] of the running Claude Code sessions ([] if none)."""
    import subprocess
    run = run or subprocess.run
    try:
        result = run([claude_path, "agents", "--json"], capture_output=True, text=True, timeout=20,
                     stdin=subprocess.DEVNULL)
        data = json.loads(result.stdout or "[]")
    except (OSError, ValueError, subprocess.SubprocessError):
        return []
    items = data.get("agents", data.get("sessions", [])) if isinstance(data, dict) else data
    return [i for i in items if isinstance(i, dict)]


def find_session(sessions, wanted):
    wanted = str(wanted or "").strip().lower()
    if not wanted:
        return None
    for item in sessions:
        if wanted in (str(item.get("name") or "").lower(), str(item.get("sessionId") or "").lower()):
            return item
    return None


def notify_session(claude_path, session_name, job, run=None):
    """Send the job's text to the open Claude Code session `session_name`. True when it went out."""
    import subprocess
    run = run or subprocess.run
    prompt = ("Use SendMessage to send the following message, exactly as written, to the session "
              "named %s. Do nothing else.\n\n%s" % (json.dumps(session_name), message(job)))
    try:
        result = run([claude_path, "-p", prompt, "--allowedTools", "SendMessage", "ListAgents"],
                     capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0

# SPDX-License-Identifier: GPL-3.0-or-later
"""The CAD skill: the link to the CAD chats lives in the chat, no box of its own.

The skill "cad" (skill row of the chat) switches it on and off. On: Claude in Blender has
studio_cad_job (a change for a Claude chat that works on the CAD model) and studio_cad_feedback
(points in CAD-Feedback.md), and the chat says when a CAD chat takes a job or finishes it. The new
version itself comes in from the CAD chat (Live Connection, api.cad_import)."""

import bpy

from ..i18n import tr

SKILL = "cad"
POLL = 3.0   # seconds between looks at the job files
_seen = {}   # job id -> status last seen
_started = [False]


def jobs_on():
    """The CAD skill is checked in the chat."""
    from .. import core
    return SKILL in core.active_skills


def ago_text(stamp):
    """"active 3 min ago" (translated), or "not seen yet" for a chat that never called blender_jobs."""
    from .. import cadjobs
    age = cadjobs.ago(stamp)
    if age is None:
        return tr("not seen yet")
    number, unit = age
    return {"now": tr("active just now"), "min": tr("active {} min ago"), "h": tr("active {} h ago"),
            "d": tr("active {} days ago")}[unit].format(number)


def chats_text(limit=8):
    """The known CAD chats, last active first, one line each (for Claude's ask_user)."""
    from .. import cadjobs
    try:
        chats = cadjobs.known_chats()[:limit]
    except OSError:
        chats = []
    return "\n".join("- %s (%s)" % (c["name"], ago_text(c["last"])) for c in chats) or "- none yet"


def job_news(jobs, seen, started=True):
    """Chat lines for the jobs whose status changed since `seen` (updated in place). Before the
    first look (started False) everything is only noted. A new open job says nothing: it was sent
    from here and the chat already said so."""
    lines = []
    for job in jobs:
        job_id, status = job.get("id"), job.get("status")
        before = seen.get(job_id)
        seen[job_id] = status
        if not started or before == status or status == "open":
            continue
        text = job.get("text", "")
        if status == "claimed":
            lines.append(tr("CAD job taken by {}: {}").format(job.get("claimed_by") or tr("a chat"), text))
        elif status == "done":
            who = job.get("claimed_by") or ""
            line = tr("CAD job done{}: {}").format(" (%s)" % who if who else "", text)
            if job.get("note"):
                line += " – " + job["note"]
            lines.append(line)
    return lines


def _poll():
    try:
        if not jobs_on():
            _seen.clear()
            _started[0] = False
            return POLL
        from .. import cadjobs, core
        for line in job_news(cadjobs.jobs(), _seen, _started[0]):
            core.add_message("info", line)
        _started[0] = True
    except Exception:   # never stop the timer over a bad job file
        pass
    return POLL


def register():
    if not bpy.app.timers.is_registered(_poll):
        bpy.app.timers.register(_poll, first_interval=POLL, persistent=True)


def unregister():
    if bpy.app.timers.is_registered(_poll):
        bpy.app.timers.unregister(_poll)

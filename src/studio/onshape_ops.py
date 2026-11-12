# SPDX-License-Identifier: GPL-3.0-or-later
"""The CAD box of the chat: CAD comments (CAD-Feedback.md next to the export) and CAD jobs for a
Claude chat that works on the CAD model (cadjobs). CAD jobs can be switched off (Studio preference
cad_jobs, the link button of the box); the comments and the import stay."""

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from ..i18n import tr

_points = {"time": -10.0, "folder": None, "open": [], "jobs": [], "app": "ok", "chat": None}   # read at most every 2 s
_choices = []   # [{"name", "last"}]: the chats of the open job dialog (its enum items point into it)
MAX_CHATS = 8


def jobs_on():
    """CAD jobs are switched on (default)."""
    from .runner import prefs
    p = prefs()
    return p is None or bool(getattr(p, "cad_jobs", True))


def _app_state():
    """"ok" (the Claude app has Blender's tools), "off" (installed, not connected) or "missing"."""
    from .. import live_clients
    try:
        if not live_clients.desktop_installed():
            return "missing"
        return "ok" if live_clients.desktop_connected() else "off"
    except (OSError, ValueError, RuntimeError):
        return "ok"


def comment_state(context, force=False):
    """{"folder", "open", "jobs", "app", "chat"} of the last CAD import (folder None: no import).
    chat: {"name", "last"} of the chat the next job goes to, None for a new chat."""
    import time
    if force or time.monotonic() - _points["time"] > 2.0:
        from . import cadfiles, cadimport
        from .. import cadjobs
        try:
            folder = cadimport.feedback_folder()
        except (ValueError, AttributeError):
            folder = None
        try:
            jobs = [j for j in cadjobs.jobs() if folder and j.get("cad_folder") == folder]
            chat = cadimport.job_chat(None, folder) if folder else None
            known = {c["name"].lower(): c for c in cadjobs.known_chats()}
        except OSError:
            jobs, chat, known = [], None, {}
        name = (chat or {}).get("name") or ""
        _points.update(time=time.monotonic(), folder=folder,
                       open=cadfiles.open_feedback(folder) if folder else [], jobs=jobs,
                       app=_app_state() if folder and jobs_on() else "ok",
                       chat=(known.get(name.lower()) or {"name": name, "last": ""}) if name else None)
    return _points


def _refresh():
    _points["time"] = -10.0


def ago_text(stamp):
    """"active 3 min ago" (translated), or "not seen yet" for a chat that never called blender_jobs."""
    from .. import cadjobs
    age = cadjobs.ago(stamp)
    if age is None:
        return tr("not seen yet")
    number, unit = age
    return {"now": tr("active just now"), "min": tr("active {} min ago"), "h": tr("active {} h ago"),
            "d": tr("active {} days ago")}[unit].format(number)


class RS_OT_cad_connect(Operator):
    bl_idname = "render_studio.cad_connect"
    bl_label = "Connect"
    bl_description = ("Give the chats of the Claude app Blender's tools (blender_jobs), so they can take CAD jobs. "
                      "Quit and reopen Claude once afterwards")

    def execute(self, context):
        from .. import live_clients
        try:
            live_clients.connect_desktop()
        except (OSError, ValueError, RuntimeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _refresh()
        self.report({'INFO'}, tr("Connected: quit Claude (Cmd+Q) and open it again"))
        return {'FINISHED'}


class RS_OT_cad_comment(Operator):
    bl_idname = "render_studio.cad_comment"
    bl_label = "Add CAD Comment"
    bl_description = "Note a change for the CAD side in CAD-Feedback.md next to the export (the CAD chat reads it)"

    part: StringProperty(name="Part", options={'SKIP_SAVE'})
    text: StringProperty(name="Comment", options={'SKIP_SAVE'})

    def invoke(self, context, event):
        obj = context.active_object
        if obj is not None and obj.select_get():
            from . import cadswap
            self.part = obj.get(cadswap.CAD_ID) or obj.name
        return context.window_manager.invoke_props_dialog(self, width=460)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "part")
        col.prop(self, "text")

    def execute(self, context):
        from . import cadimport
        text = " ".join(self.text.split())
        if not text:
            self.report({'WARNING'}, "Write the comment first")
            return {'CANCELLED'}
        part = self.part.strip()
        obj = bpy.data.objects.get(part)
        try:
            folder = cadimport.feedback_folder(obj)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        cadimport.feedback(obj if obj is not None else part, text, folder=folder)
        _refresh()
        self.report({'INFO'}, "CAD comment noted")
        return {'FINISHED'}


class RS_OT_cad_comment_done(Operator):
    bl_idname = "render_studio.cad_comment_done"
    bl_label = "Done"
    bl_description = "Tick this point off"
    bl_options = {'INTERNAL'}

    point: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        from . import cadfiles
        folder = comment_state(context, force=True)["folder"]
        if not folder or not cadfiles.tick_feedback(folder, self.point, note="done in Blender"):
            return {'CANCELLED'}
        _refresh()
        return {'FINISHED'}


class RS_OT_cad_comments_open(Operator):
    bl_idname = "render_studio.cad_comments_open"
    bl_label = "Open CAD-Feedback.md"
    bl_description = "Open the list of CAD comments next to the export"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        import os
        from . import cadfiles
        folder = comment_state(context, force=True)["folder"]
        path = os.path.join(folder or "", cadfiles.FEEDBACK_FILE)
        if not folder or not os.path.isfile(path):
            return {'CANCELLED'}
        bpy.ops.wm.path_open(filepath=path)
        return {'FINISHED'}


def _job_targets(self, context):
    items = [('NEW', tr("New Chat"), tr("Start a new chat in the Claude app with the job"), 'ADD', 0)]
    for i, chat in enumerate(_choices):
        items.append(("CHAT%d" % i, chat["name"], ago_text(chat["last"]), 'USER', i + 1))
    items.append(('OTHER', tr("Other Chat…"), tr("Type the name of the chat"), 'TEXT', len(_choices) + 1))
    _job_targets.items = items   # Blender needs the strings to live on
    return items


def chat_choices(default_name=""):
    """The chats for the dialog: the known ones, last active first; the default one is always in it."""
    from .. import cadjobs
    try:
        chats = cadjobs.known_chats()[:MAX_CHATS]
    except OSError:
        chats = []
    if default_name and not any(c["name"].lower() == default_name.lower() for c in chats):
        chats.insert(0, {"name": default_name, "last": ""})
    return chats


class RS_OT_cad_job(Operator):
    bl_idname = "render_studio.cad_job"
    bl_label = "Send CAD Job"
    bl_description = ("Ask the CAD side for a change: a new chat in the Claude app opens with the job, or a "
                      "chat that knows the model gets it (a notification; it takes the job from Blender)")

    part: StringProperty(name="Part", options={'SKIP_SAVE'})
    text: StringProperty(name="Change", description="What should change, with numbers", options={'SKIP_SAVE'})
    target: EnumProperty(name="Chat", description="Which Claude chat does it", items=_job_targets,
                         options={'SKIP_SAVE'})
    other: StringProperty(name="Name", description="Name of the chat in the Claude app, e.g. 'CAD 5' "
                          "(remembered for this CAD export)", options={'SKIP_SAVE'})

    @classmethod
    def poll(cls, context):
        if not jobs_on():
            cls.poll_message_set("CAD jobs are switched off (link button of the CAD box)")
            return False
        return True

    def invoke(self, context, event):
        from . import cadimport
        obj = context.active_object
        if obj is not None and obj.select_get():
            from . import cadswap
            self.part = obj.get(cadswap.CAD_ID) or obj.name
        else:
            obj = None
        known = cadimport.job_chat(obj, comment_state(context)["folder"])
        name = (known or {}).get("name") or ""
        _choices[:] = chat_choices(name)
        index = next((i for i, c in enumerate(_choices) if name and c["name"].lower() == name.lower()), None)
        self.target = "CHAT%d" % index if index is not None else 'NEW'
        return context.window_manager.invoke_props_dialog(self, width=480, confirm_text="Send")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "part")
        col.prop(self, "text")
        col.prop(self, "target")
        if self.target == 'OTHER':
            col.prop(self, "other")
        if comment_state(context)["app"] == "off":
            sub = col.row()
            sub.active = False
            sub.label(text="The Claude app gets Blender's tools now; reopen Claude once", icon='INFO')

    def chat_name(self):
        """"" for a new chat, else the chosen chat's name (None: Other without a name)."""
        if self.target == 'NEW':
            return ""
        if self.target == 'OTHER':
            return " ".join(self.other.split()) or None
        try:
            return _choices[int(self.target[4:])]["name"]
        except (ValueError, IndexError):
            return ""

    def execute(self, context):
        from . import cadimport
        if not self.text.strip():
            self.report({'WARNING'}, "Write what should change first")
            return {'CANCELLED'}
        name = self.chat_name()
        if name is None:
            self.report({'WARNING'}, "Name the chat (e.g. CAD 5) or choose New Chat")
            return {'CANCELLED'}
        try:
            job, how = cadimport.send_job(self.text, self.part.strip() or None, "origin" if name else "new",
                                          chat_name=name)
        except (OSError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _refresh()
        self.report({'INFO'}, "CAD job %s" % how)
        return {'FINISHED'}


SHOWN = 5


def draw_status(layout, context):
    """One line under the CAD box's buttons: where the next job goes, or what is missing."""
    state = comment_state(context)
    if not state["folder"] or not jobs_on():
        return 0
    row = layout.row(align=True)
    if state["app"] == "off":
        row.alert = True
        row.label(text=tr("Claude app not connected to Blender"), icon='ERROR', translate=False)
        row.operator(RS_OT_cad_connect.bl_idname, text="Connect")
        return 1
    row.active = False
    chat = state["chat"]
    if chat:
        text = "→ %s · %s" % (chat["name"], ago_text(chat["last"]))
    else:
        text = "→ " + tr("new chat")
    if state["app"] == "missing":
        text += " · " + tr("Claude app not found")
    row.label(text=text, icon='LINKED', translate=False)
    return 1


def draw_comments(layout, context):
    """The CAD comments and jobs under the CAD box's buttons (only once a CAD export is known)."""
    state = comment_state(context)
    if not state["folder"]:
        return
    box = layout.box()
    row = box.row(align=True)
    points = state["open"]
    row.label(text="CAD Comments · %d open" % len(points) if points else "CAD Comments", icon='WORDWRAP_ON')
    row.operator(RS_OT_cad_comments_open.bl_idname, text="", icon='TEXT')
    col = box.column(align=True)
    for point in points[-SHOWN:]:
        _day, _sep, rest = point.partition(" · ")
        line = col.row(align=True)
        line.label(text=rest or point, translate=False, icon='DOT')
        line.operator(RS_OT_cad_comment_done.bl_idname, text="", icon='CHECKMARK').point = point
    if len(points) > SHOWN:
        sub = col.row()
        sub.active = False
        sub.label(text="… and %d earlier ones" % (len(points) - SHOWN))
    row = box.row(align=True)
    row.operator(RS_OT_cad_comment.bl_idname, icon='ADD')
    if jobs_on():
        row.operator(RS_OT_cad_job.bl_idname, text="Send CAD Job", icon='EXPORT')
    jobs = state.get("jobs") or []
    if jobs:
        col = box.column(align=True)
        for job in jobs[-3:]:
            icon = {'open': 'TIME', 'claimed': 'PLAY', 'done': 'CHECKMARK'}.get(job.get("status"), 'DOT')
            line = col.row()
            line.active = job.get("status") != "done"
            note = (" · " + job["note"]) if job.get("note") else ""
            who = job.get("claimed_by") if job.get("status") == "claimed" else ""
            line.label(text="%s%s%s" % (job.get("text", ""), " · " + who if who else "", note), icon=icon,
                       translate=False)


classes = (RS_OT_cad_connect, RS_OT_cad_comment, RS_OT_cad_comment_done, RS_OT_cad_comments_open, RS_OT_cad_job)

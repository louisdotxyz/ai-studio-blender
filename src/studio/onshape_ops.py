# SPDX-License-Identifier: GPL-3.0-or-later
"""Preferences › Studio › Onshape: API keys into the keychain, test them, sync CAD feedback."""

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator

from . import onshape

KEYS_URL = "https://dev-portal.onshape.com/keys"
_status = {"text": ""}   # the last test, shown under the keys
_found = {"time": -10.0, "keys": None}   # the keychain is asked at most every few seconds (drawn often)


def cached_keys():
    import time
    if time.monotonic() - _found["time"] > 5.0:
        _found.update(time=time.monotonic(), keys=onshape.keys())
    return _found["keys"]


class RS_OT_onshape_keys(Operator):
    bl_idname = "render_studio.onshape_keys"
    bl_label = "Save Onshape Keys"
    bl_description = ("Keep the Onshape API keys in the macOS keychain (not in the preferences) and test them. "
                      "Create them at dev-portal.onshape.com/keys with read and write access")

    access: StringProperty(name="Access Key", options={'SKIP_SAVE'})
    secret: StringProperty(name="Secret Key", subtype='PASSWORD', options={'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "access")
        col.prop(self, "secret")
        col.operator("wm.url_open", text="Create keys at dev-portal.onshape.com", icon='URL').url = KEYS_URL

    def execute(self, context):
        try:
            name = onshape.whoami(credentials=(self.access.strip(), self.secret.strip()))
            onshape.save_keys(self.access, self.secret)
        except (onshape.OnshapeError, ValueError, RuntimeError) as exc:
            _status["text"] = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _status["text"] = "Keys work: Onshape user %s" % name
        _found["time"] = -10.0
        self.report({'INFO'}, _status["text"])
        return {'FINISHED'}


class RS_OT_onshape_forget(Operator):
    bl_idname = "render_studio.onshape_forget"
    bl_label = "Remove Onshape Keys"
    bl_description = "Remove the Onshape API keys from the keychain"

    def execute(self, context):
        onshape.forget_keys()
        _status["text"] = ""
        _found["time"] = -10.0
        return {'FINISHED'}


class RS_OT_onshape_sync(Operator):
    bl_idname = "render_studio.onshape_sync"
    bl_label = "Sync CAD Feedback"
    bl_description = ("Open points of CAD-Feedback.md that are not in Onshape yet become comments there; points "
                      "whose comment was resolved in Onshape are ticked off")

    def execute(self, context):
        from . import cadimport
        try:
            text = cadimport.sync_feedback()
        except (ValueError, onshape.OnshapeError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _refresh()
        self.report({'INFO'}, text)
        return {'FINISHED'}


def draw_prefs(layout, studio):
    """The Onshape box of Preferences › Studio."""
    box = layout.box()
    box.label(text="Onshape", icon='MOD_BUILD')
    col = box.column()
    found = cached_keys()
    row = col.row(align=True)
    row.label(text=("API keys in the keychain (%s…)" % found[0][:6]) if found else "No API keys yet",
              icon='CHECKMARK' if found else 'INFO')
    row.operator(RS_OT_onshape_keys.bl_idname, text="Change" if found else "Add Keys…")
    if found:
        row.operator(RS_OT_onshape_forget.bl_idname, text="", icon='X')
    if _status["text"]:
        sub = col.row()
        sub.active = False
        sub.label(text=_status["text"])
    col.prop(studio, "onshape_comments")
    sub = col.row()
    sub.enabled = bool(found)
    sub.operator(RS_OT_onshape_sync.bl_idname, icon='FILE_REFRESH')


# ---------------------------------------------------------------- CAD comments (Material tab › CAD)

_points = {"time": -10.0, "folder": None, "open": [], "sent": {}}   # read from disk at most every 2 s


def comment_state(context, force=False):
    """{"folder", "open", "sent"} of the last CAD import's CAD-Feedback.md (folder None: no import)."""
    import time
    if force or time.monotonic() - _points["time"] > 2.0:
        from . import cadfiles, cadimport
        try:
            folder = cadimport.feedback_folder()
        except (ValueError, AttributeError):
            folder = None
        from .. import cadjobs
        try:
            jobs = [j for j in cadjobs.jobs() if folder and j.get("cad_folder") == folder]
        except OSError:
            jobs = []
        _points.update(time=time.monotonic(), folder=folder,
                       open=cadfiles.open_feedback(folder) if folder else [],
                       sent=cadfiles.feedback_comments(folder) if folder else {}, jobs=jobs)
    return _points


def _refresh():
    _points["time"] = -10.0


_key_check = {"time": -1e9, "ok": True, "text": "", "thread": None}
KEY_CHECK_EVERY = 600.0   # seconds


def key_hint(base):
    """A sentence when the saved Onshape keys do not work (checked in the background at most every
    10 minutes, when the CAD box is shown), else ""."""
    import threading
    import time
    if not cached_keys():
        return ""
    running = _key_check["thread"] is not None and _key_check["thread"].is_alive()
    if not running and time.monotonic() - _key_check["time"] > KEY_CHECK_EVERY:
        _key_check["time"] = time.monotonic()

        def run():
            ok, text = onshape.check(base)
            _key_check.update(ok=ok, text=text)
        _key_check["thread"] = threading.Thread(target=run, name="ai-studio-onshape-check", daemon=True)
        _key_check["thread"].start()
    return "" if _key_check["ok"] else _key_check["text"]


class RS_OT_cad_comment(Operator):
    bl_idname = "render_studio.cad_comment"
    bl_label = "Add CAD Comment"
    bl_description = ("Note a change for the CAD side: into CAD-Feedback.md next to the export and, when the "
                      "part came from Onshape, as a comment there")

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
        from . import cadimport
        source = cadimport.onshape_source(context.active_object) if comment_state(context)["folder"] else ""
        if source and onshape.parse_source(source):
            sub = col.row()
            sub.active = False
            sub.label(text="Also goes to Onshape as a comment" if cached_keys() else
                      "Onshape: add API keys in Preferences › Studio to send it there", icon='INFO')

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
        _path, note = cadimport.feedback(obj if obj is not None else part, text, folder=folder)
        _refresh()
        self.report({'WARNING'} if note.startswith("not in") else {'INFO'},
                    "CAD comment noted" + (": " + note if note else ""))
        return {'FINISHED'}


class RS_OT_cad_comment_done(Operator):
    bl_idname = "render_studio.cad_comment_done"
    bl_label = "Done"
    bl_description = "Tick this point off (and resolve its comment in Onshape)"
    bl_options = {'INTERNAL'}

    point: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        from . import cadfiles
        state = comment_state(context, force=True)
        folder = state["folder"]
        if not folder or not cadfiles.tick_feedback(folder, self.point, note="done in Blender"):
            return {'CANCELLED'}
        known = state["sent"].get(self.point)
        if known:
            try:
                onshape.resolve_comment(known["base"], known["cid"])
            except onshape.OnshapeError as exc:
                self.report({'WARNING'}, "Ticked off; Onshape: %s" % exc)
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
    from . import cadimport
    chat = cadimport.origin_chat(context.active_object, comment_state(context)["folder"])
    if chat:
        name = chat.get("name") or "the chat of the export"
        items = [('ORIGIN', "Send to %s" % name, "The Claude chat this CAD export came from"),
                 ('NEW', "New Chat", "Start a new chat in the Claude app with the job")]
    else:
        items = [('NEW', "New Chat", "The export does not say which chat it came from: a new chat gets the job")]
    _job_targets.items = items   # Blender needs the list to live on
    return items


class RS_OT_cad_job(Operator):
    bl_idname = "render_studio.cad_job"
    bl_label = "Send CAD Job"
    bl_description = ("Ask the CAD side for a change: the Claude app opens at the chat that made the CAD export "
                      "(or a new chat) with the job, the text is also in the clipboard")

    part: StringProperty(name="Part", options={'SKIP_SAVE'})
    text: StringProperty(name="Change", description="What should change, with numbers", options={'SKIP_SAVE'})
    target: EnumProperty(name="To", items=_job_targets, options={'SKIP_SAVE'})

    def invoke(self, context, event):
        obj = context.active_object
        if obj is not None and obj.select_get():
            from . import cadswap
            self.part = obj.get(cadswap.CAD_ID) or obj.name
        return context.window_manager.invoke_props_dialog(self, width=480, confirm_text="Send")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "part")
        col.prop(self, "text")
        col.row().prop(self, "target", expand=True)

    def execute(self, context):
        from . import cadimport
        if not self.text.strip():
            self.report({'WARNING'}, "Write what should change first")
            return {'CANCELLED'}
        try:
            job, how = cadimport.send_job(self.text, self.part.strip() or None,
                                          "origin" if self.target == 'ORIGIN' else "new")
        except (OSError, ValueError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        _refresh()
        self.report({'INFO'}, "CAD job %s" % how)
        return {'FINISHED'}


SHOWN = 5


def _wrap(text, width):
    import textwrap
    return textwrap.wrap(text, width) or [text]


def draw_comments(layout, context):
    """The CAD comments under Import CAD (only once a CAD export is known)."""
    state = comment_state(context)
    if not state["folder"]:
        return
    box = layout.box()
    row = box.row(align=True)
    points = state["open"]
    row.label(text="CAD Comments · %d open" % len(points) if points else "CAD Comments", icon='WORDWRAP_ON')
    row.operator(RS_OT_cad_comments_open.bl_idname, text="", icon='TEXT')
    from . import cadimport
    where = onshape.parse_source(cadimport.onshape_source(None, state["folder"]))
    hint = key_hint(where["base"]) if where else ""
    if cached_keys() and where:
        row.operator(RS_OT_onshape_sync.bl_idname, text="", icon='FILE_REFRESH')
    if hint:
        sub = box.column(align=True)
        sub.alert = True
        for part in _wrap(hint, 60):
            sub.label(text=part, translate=False)
    col = box.column(align=True)
    for point in points[-SHOWN:]:
        _day, _sep, rest = point.partition(" · ")
        line = col.row(align=True)
        line.label(text=rest or point, translate=False,
                   icon='URL' if point in state["sent"] else 'DOT')
        line.operator(RS_OT_cad_comment_done.bl_idname, text="", icon='CHECKMARK').point = point
    if len(points) > SHOWN:
        sub = col.row()
        sub.active = False
        sub.label(text="… and %d earlier ones" % (len(points) - SHOWN))
    row = box.row(align=True)
    row.operator(RS_OT_cad_comment.bl_idname, icon='ADD')
    row.operator(RS_OT_cad_job.bl_idname, text="Send CAD Job", icon='EXPORT')
    jobs = state.get("jobs") or []
    if jobs:
        col = box.column(align=True)
        for job in jobs[-3:]:
            icon = {'open': 'TIME', 'claimed': 'PLAY', 'done': 'CHECKMARK'}.get(job.get("status"), 'DOT')
            line = col.row()
            line.active = job.get("status") != "done"
            note = (" · " + job["note"]) if job.get("note") else ""
            line.label(text="%s%s" % (job.get("text", ""), note), icon=icon, translate=False)


classes = (RS_OT_onshape_keys, RS_OT_onshape_forget, RS_OT_onshape_sync,
           RS_OT_cad_comment, RS_OT_cad_comment_done, RS_OT_cad_comments_open, RS_OT_cad_job)

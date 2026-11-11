# SPDX-License-Identifier: GPL-3.0-or-later
"""Preferences › Studio › Onshape: API keys into the keychain, test them, sync CAD feedback."""

import bpy
from bpy.props import StringProperty
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


classes = (RS_OT_onshape_keys, RS_OT_onshape_forget, RS_OT_onshape_sync)

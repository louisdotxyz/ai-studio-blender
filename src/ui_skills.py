# SPDX-License-Identifier: GPL-3.0-or-later
"""Skill row: toggles, one operator per skill, new skills."""

import os
import re

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from . import core, skills
from .i18n import tip, tr


# One small operator per skill (registered from a timer, never while drawing): Blender's tooltip
# shows the operator's label big and its description below, so "SORT" and then what it does.
_skill_ops = {}            # skill name -> (class, title, description)
_skill_sync_pending = [False]


def _toggle_skill(name):
    if name in core.active_skills:
        core.active_skills.remove(name)
    else:
        core.active_skills.append(name)
    _save_skills()
    core.mark_dirty()


def _save_skills():
    """Checked skills stay checked after a restart (e.g. the CAD skill as the switch for the CAD link)."""
    skills.save_active(core.workdir(), core.active_skills)


def _restore_skills():
    try:
        if not core.active_skills:
            names = skills.load_active(core.workdir())
            if names is None:   # first start of 2.0: the CAD skill is on for whoever used CAD jobs before
                from . import cadjobs
                used = os.path.isdir(cadjobs.JOBS_DIR) and skills.find(core.workdir(), "cad")
                names = ["cad"] if used else []
                if names:
                    skills.save_active(core.workdir(), names)
            core.active_skills[:] = names
            core.mark_dirty()
    except (OSError, AttributeError, ValueError):
        pass
    return None


def _skill_execute(self, context):
    _toggle_skill(self.skill_name)
    return {'FINISHED'}


def skill_operator(skill):
    """bl_idname of the skill's own operator, or "" (then scheduled, the generic one is used)."""
    entry = _skill_ops.get(skill["name"])
    if entry and entry[1] == skill["title"] and entry[2] == skill["description"]:
        return entry[0].bl_idname
    if not _skill_sync_pending[0]:
        _skill_sync_pending[0] = True
        bpy.app.timers.register(sync_skill_operators, first_interval=0.0)
    return ""


def sync_skill_operators():
    _skill_sync_pending[0] = False
    wanted = {s["name"]: s for s in skills.list_skills(core.workdir())}
    for name in list(_skill_ops):
        cls, title, description = _skill_ops[name]
        skill = wanted.get(name)
        if skill is None or skill["title"] != title or skill["description"] != description:
            try:
                bpy.utils.unregister_class(cls)
            except RuntimeError:
                pass
            del _skill_ops[name]
    for name, skill in wanted.items():
        if name in _skill_ops:
            continue
        ident = re.sub(r"[^a-z0-9_]", "_", name.lower())[:40] or "skill"
        cls = type("CLAUDE_OT_skill_" + ident, (bpy.types.Operator,), {
            "bl_idname": "claude.skill_" + ident,
            "bl_label": skill["title"],
            "bl_description": skill["description"] or tip("Check or uncheck this skill"),
            "bl_options": {'INTERNAL'},
            "skill_name": name,
            "execute": _skill_execute,
        })
        try:
            bpy.utils.register_class(cls)
        except (RuntimeError, ValueError) as ex:
            print("Claude: skill button for %s: %s" % (name, ex))
            continue
        _skill_ops[name] = (cls, skill["title"], skill["description"])
    tag = getattr(core, "_tag_redraw", None)
    if tag:
        tag()
    return None


def _unregister_skill_operators():
    for cls, _title, _description in _skill_ops.values():
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass
    _skill_ops.clear()


class CLAUDE_OT_use_skill(bpy.types.Operator):
    bl_idname = "claude.use_skill"
    bl_label = "Use Skill"
    bl_description = "Check or uncheck this skill (checked skills apply to all your messages)"

    name: StringProperty(options={'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        if not properties.name:
            return tip("Remove skill")
        skill = skills.find(core.workdir(), properties.name) or {}
        text = skill.get("description") or tip(cls.bl_description)
        title = skill.get("title") or properties.name
        state = tip("on") if properties.name in core.active_skills else tip("off")
        return "%s (%s): %s" % (title, state, text)

    def execute(self, context):
        if not self.name:
            core.active_skills.clear()
        elif self.name in core.active_skills:
            core.active_skills.remove(self.name)
        else:
            core.active_skills.append(self.name)
        _save_skills()
        core.mark_dirty()
        return {'FINISHED'}


class CLAUDE_OT_new_skill(bpy.types.Operator):
    bl_idname = "claude.new_skill"
    bl_label = "New Skill"
    bl_description = "Create a new skill: reusable instructions you can check for your messages"

    title: StringProperty(name="Name", options={'SKIP_SAVE'})
    description: StringProperty(name="Short Description", options={'SKIP_SAVE'})
    body: StringProperty(name="Instructions", options={'SKIP_SAVE'},
                         description="What the model should do when this skill is checked")
    open_file: BoolProperty(name="Edit Longer Text in the Editor", default=False, options={'SKIP_SAVE'})
    icon: EnumProperty(name="Icon", items=[(i, "", i.replace("_", " ").title(), i, n)
                                           for n, i in enumerate(skills.ICON_CHOICES)],
                       options={'SKIP_SAVE'})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=420)

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "title")
        col.row(align=True).prop(self, "icon", expand=True, icon_only=True)
        col.prop(self, "description")
        col.prop(self, "body")
        col.prop(self, "open_file")

    def execute(self, context):
        if not self.title.strip():
            self.report({'WARNING'}, tr("Give the skill a name"))
            return {'CANCELLED'}
        icon = self.icon if self.icon != 'SHADERFX' else ""
        name = skills.create(core.workdir(), self.title, self.description, self.body, icon)
        if name not in core.active_skills:
            core.active_skills.append(name)
            _save_skills()
        if self.open_file:
            bpy.ops.wm.path_open(filepath=os.path.join(skills.skills_dir(core.workdir()), name + ".md"))
        core.mark_dirty()
        return {'FINISHED'}

# SPDX-License-Identifier: GPL-3.0-or-later
"""The Video Editor's sidebar (Studio): two compact panels without tabs, everything at a glance.

- Above, in the preview's sidebar: the selected clip's look (grading) and a simple fade in / out.
- Below, in the timeline's sidebar: one bar for every render, the sections with the transition into the next
  one, and the export (which sections, one file or one per section, format).
Each function is in one place only.
"""

import bpy
from bpy.props import StringProperty
from bpy.types import Operator, Panel

from . import cut, film_tools

STATE_ICONS = {'HIGH': 'STRIP_COLOR_04', 'LOW': 'STRIP_COLOR_02', 'DRAFT': 'STRIP_COLOR_09', '': 'STRIP_COLOR_01'}
_icons = [None]


def _icon(name, fallback='NONE'):
    """An icon that this Blender has (the strip colour icons were renamed over the versions)."""
    if _icons[0] is None:
        _icons[0] = set(bpy.types.UILayout.bl_rna.functions['prop'].parameters['icon'].enum_items.keys())
    return name if name in _icons[0] else fallback


def _state_icon(st):
    codes = st.get("codes") or []
    if codes and all(c == 'HIGH' for c in codes):
        return _icon(STATE_ICONS['HIGH'], 'CHECKMARK')
    if st.get("done"):
        return _icon(STATE_ICONS['LOW'], 'RENDER_ANIMATION')
    if st.get("drafts") or st.get("poster"):
        return _icon(STATE_ICONS['DRAFT'], 'SHADING_SOLID')
    return _icon(STATE_ICONS[''], 'DOT')


def _space_view(context):
    return getattr(getattr(context, "space_data", None), "view_type", "")


def _hint(layout, text, icon='INFO'):
    row = layout.row()
    row.active = False
    row.label(text=text, icon=icon)


def _other_scene(layout, context):
    """The Video Editor shows another scene than the one with the sections: offer to show the film here."""
    scene = cut._scene(context)
    if scene is not context.scene and not scene.timeline_markers and context.scene.timeline_markers:
        col = layout.column()
        col.label(text="The film is in %s" % context.scene.name, icon='INFO', translate=False)
        col.operator(cut.STUDIO_OT_cut.bl_idname, text="Show it Here", icon='SEQUENCE').action = 'OPEN'
        return True
    return False


def _active(scene):
    sed = cut._sed_of(scene)
    strip = sed.active_strip if sed is not None else None
    if strip is None or cut._get(strip, cut.KIND_KEY) in {"BARS", "MUSIC", "DIP", "CROSS", "FADE"} or \
            strip.type not in {'IMAGE', 'MOVIE', 'ADJUSTMENT'}:
        return None
    return strip


def _tool(layout, action, text, icon):
    layout.operator(cut.STUDIO_OT_cut_tool.bl_idname, text=text, icon=icon).action = action


def draw_look(layout, strip):
    col = layout.column(align=True)
    row = col.row(align=True)
    row.prop(strip, "studio_brightness", text="Brightness", slider=True)
    row.prop(strip, "studio_contrast", text="Contrast", slider=True)
    row = col.row(align=True)
    row.prop(strip, "studio_saturation", text="Saturation", slider=True)
    row.prop(strip, "studio_warmth", text="Warmth", slider=True)


# ------------------------------------------------------------------ above: the clip

class STUDIO_PT_cut_clip(Panel):
    """The selected clip (in the preview's sidebar): its look and a fade in / out."""
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Studio"
    bl_label = "Clip"
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        return _space_view(context) in {'PREVIEW', 'SEQUENCER_PREVIEW'}

    def draw(self, context):
        layout = self.layout
        if _other_scene(layout, context):
            return
        scene = cut._scene(context)
        wm = context.window_manager
        strip = _active(scene)
        if strip is None:
            _hint(layout, "Pick a clip in the timeline: its look and fades")
        else:
            section = cut._get(strip, cut.SECTION_KEY)
            head = layout.row(align=True)
            found = next(((s, e) for n, s, e, _m in cut.sections_of(scene) if n == section), None)
            st = cut.frame_status(section, found[0], found[1]) if found else None
            head.label(text=section or strip.name, icon=_state_icon(st) if st else 'SEQUENCE', translate=False)
            if st is not None:
                right = head.row()
                right.alignment = 'RIGHT'
                right.active = False
                right.label(text=film_tools.label_of(st, cut._rendering(section)) or "not rendered",
                            translate=False)
            box = layout.box()                      # the look of this clip
            draw_look(box, strip)
            row = box.row(align=True)
            _tool(row, 'LOOK_ALL', "To All Clips", 'DUPLICATE')
            _tool(row, 'LOOK_RESET', "Reset", 'LOOP_BACK')
            box = layout.box()                      # fade in from black, out to black (Shift: white)
            row = box.row(align=True)
            _tool(row, 'FADE_IN', "Fade In", 'TRIA_RIGHT_BAR')
            _tool(row, 'FADE_OUT', "Fade Out", 'TRIA_LEFT_BAR')
            frames = row.row(align=True)
            frames.ui_units_x = 4
            frames.prop(wm, "studio_cut_fade", text="")
        row = layout.row(align=True)
        row.label(text="Letterbox", icon='SEQ_PREVIEW')
        row.prop(wm, "studio_cut_ratio", text="")


# ------------------------------------------------------------------ below: the film

class STUDIO_OT_cut_export_pick(Operator):
    bl_idname = "studio.cut_export_pick"
    bl_label = "Export Section"
    bl_description = "In the export or not (click a section to leave it out or take it in again)"
    bl_options = {'INTERNAL'}

    section: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        rq = cut.film_scene(cut._scene(context)).studio
        skip = cut.export_left_out(cut.film_scene(cut._scene(context)))
        skip.symmetric_difference_update({self.section})
        rq.cut_export_skip = "\n".join(sorted(skip))
        return {'FINISHED'}


class STUDIO_PT_cut_queue(Panel):
    """Everything in the queue (films of this Mac and the farm's jobs), each with its buttons."""
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'HEADER'
    bl_label = "Queue"
    bl_ui_units_x = 18

    def draw(self, context):
        from . import film, renderfarm
        col = self.layout.column(align=True)
        film.draw(col)
        if not renderfarm.draw_rows(col) and not film.films_here():
            _hint(col, "Nothing renders")


def _problems(layout, scene):
    found = [p for p in film_tools.problems(scene) if p[1] in film_tools.BLOCKING]
    for section, code, text in found:
        row = layout.row(align=True)
        row.alert = True
        row.label(text=text, icon='ERROR', translate=False)
        if code in film_tools.FIXES:
            op = row.operator(film_tools.STUDIO_OT_film_fix.bl_idname, text=film_tools.FIXES[code])
            op.section, op.code = section, code


def _draw_sections(layout, scene):
    """One line per section: ▶ name · what is rendered · the transition into the next · render."""
    found = cut.sections_of(scene)
    frame = cut.playhead(scene)
    sed = cut._sed_of(scene)
    active = sed.active_strip if sed is not None else None
    col = layout.column(align=True)
    for n, (name, start, end, marker) in enumerate(found):
        strip = cut.section_strip(scene, name)
        here = (strip is not None and active is not None and cut._get(active, cut.SECTION_KEY) == name) or \
            (strip is None and start <= frame <= end)
        st = cut.frame_status(name, start, end)
        row = col.row(align=True)
        row.operator(cut.STUDIO_OT_cut_go.bl_idname, text=name, icon='PLAY' if here else 'TRIA_RIGHT',
                     depress=here, translate=False).section = name
        state = row.row(align=True)
        state.ui_units_x = 4.5
        state.label(text=film_tools.label_of(st) or "open", icon=_state_icon(st), translate=False)
        if n < len(found) - 1 and marker is not None:      # into the next section
            item = next((c for c in scene.studio.cut_items if c.section == name), None)
            kind = row.row(align=True)
            kind.ui_units_x = 3.4
            if item is None:
                kind.operator(cut.STUDIO_OT_cut_settings.bl_idname, text="Cut", icon='TRIA_DOWN').section = name
                row.label(text="", icon='BLANK1')
            else:
                kind.prop(item, "transition", text="")
                frames = row.row(align=True)
                frames.ui_units_x = 2.2
                frames.active = item.transition != 'CUT'
                frames.prop(item, "frames", text="")
        else:
            spacer = row.row(align=True)
            spacer.ui_units_x = 5.6
            spacer.label(text="")
        if cut.outdated(scene, name, start, end):
            op = row.operator(cut.STUDIO_OT_cut_strip.bl_idname, text="", icon='ERROR')
            op.action, op.section = 'UPDATE', name
        clean = st["done"] == st["total"] and film_tools.next_quality(scene, name, st) is None
        op = row.operator("studio.add", text="", icon='FILE_REFRESH' if clean else 'RENDER_ANIMATION')
        op.mode, op.section, op.force = 'ANIM', name, clean
    return found


def _draw_export(layout, scene, found):
    """Which sections, one file or one per section, format · quality · size, Export."""
    rq = scene.studio
    box = layout.box()
    skip = cut.export_left_out(scene)
    picks = box.grid_flow(row_major=True, columns=min(6, max(1, len(found))), even_columns=True, align=True)
    for name, _s, _e, _m in found:
        number = name.split(" ", 1)[0] if name[:2].isdigit() else name[:6]
        picks.operator(STUDIO_OT_cut_export_pick.bl_idname, text=number, depress=name not in skip,
                       translate=False).section = name
    row = box.row(align=True)
    row.prop(rq, "cut_export_split", expand=True)
    row = box.row(align=True)
    row.prop(rq, "cut_export_format", text="")
    sub = row.row(align=True)
    sub.enabled = rq.cut_export_format not in {'PRORES', 'PNG'}
    sub.prop(rq, "cut_export_quality", text="")
    size = row.row(align=True)
    size.ui_units_x = 3
    size.prop(rq, "cut_export_size", text="")
    plan, join = cut.export_plan(scene)
    row = box.row(align=True)
    row.scale_y = 1.3
    go = row.row(align=True)
    go.enabled = bool(plan)
    frames = sum(b - a + 1 for _l, a, b, _p in plan)
    go.operator(cut.STUDIO_OT_cut.bl_idname, text="Export %d file%s · %d frames" % (
        1 if join or rq.cut_export_split == 'ONE' else len(plan), "" if (join or rq.cut_export_split == 'ONE'
                                                                         or len(plan) == 1) else "s", frames)
                if plan else "Export: nothing rendered", icon='EXPORT').action = 'EXPORT'
    try:
        folder = cut.cut_folder()
    except Exception:
        folder = ""
    if folder:
        row.operator("wm.path_open", text="", icon='FILE_FOLDER').filepath = folder


class STUDIO_PT_cut(Panel):
    """The film (in the timeline's sidebar): the render bar, the sections with their transitions, export."""
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Studio"
    bl_label = "Film"
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        return _space_view(context) in {'SEQUENCER', 'SEQUENCER_PREVIEW', ''}

    def draw(self, context):
        layout = self.layout
        if _other_scene(layout, context):
            return
        scene = cut._scene(context)
        from . import film, renderfarm
        head = layout.row(align=True)                 # one bar for every render · the queue · 3D view
        bar = head.column()
        span = cut.strip_range(scene)
        from . import motion
        if film.draw_total(bar, empty="Strips %d–%d · %.1f s" % (span[0], span[1], motion.seconds(
                scene, span[1] - span[0] + 1)) if span else "Nothing rendered yet"):
            renderfarm.watch()
        trouble = film.queue_trouble()
        queue = head.row(align=True)
        queue.alert = bool(trouble)
        queue.popover(STUDIO_PT_cut_queue.__name__, text=str(trouble) if trouble else "",
                      icon='ERROR' if trouble else 'COLLAPSEMENU')
        head.operator(cut.STUDIO_OT_cut.bl_idname, text="", icon='VIEW3D').action = 'BACK'
        _problems(layout, scene)
        found = _draw_sections(layout, scene)
        _draw_export(layout, scene, found)


classes = (STUDIO_OT_cut_export_pick, STUDIO_PT_cut_queue, STUDIO_PT_cut, STUDIO_PT_cut_clip)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

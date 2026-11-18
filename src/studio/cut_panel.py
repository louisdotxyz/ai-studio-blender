# SPDX-License-Identifier: GPL-3.0-or-later
"""The Video Editor's Studio sidebar: one panel (in the preview's sidebar) without scrolling, like the
Studio tab of the 3D view: one bar for every render on top, then four buttons switch what is below:

  Clips        the sections with what is rendered (a band under each clip shows it frame by frame), the
               selected clip (render again, freeze, still, length; your own: opacity, trim, mute), import
  Cuts         one row per transition (cut, cross, black, white · frames · hold), all at once, fade in /
               out of film and clip
  Look         a clip's or the whole film's look: presets, light, colour, before/after, copy/paste,
               to all clips, letterbox
The clock button in the head switches the fast preview (half-size frames, cut_proxy.py).
  Export       which sections, one file or one per section, format, quality, size
Each function is in one place only."""

import os

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, StringProperty
from bpy.types import Operator, OperatorFileListElement, Panel

from . import cut, film_tools
TABS = (('CLIPS', "Clips", "The sections and the selected clip", 'SEQUENCE', 0),
        ('MOVES', "Cuts", "Transitions between the sections, fades of film and clip", 'IPO_EASE_IN_OUT', 1),
        ('LOOK', "Look", "Light and colour of a clip or of the whole film", 'COLOR', 2),
        ('EXPORT', "Export", "The film as video or frames", 'EXPORT', 3))
TARGETS = (('CLIP', "Clip", "The selected clip's look", 'IMAGE_DATA', 0),
           ('FILM', "Whole Film", "A look over the whole film (on top of the clips' own)", 'SEQUENCE', 1))
KINDS = ('CUT', 'CROSS', 'BLACK', 'WHITE')
KIND_ITEMS = (('CUT', 'IPO_CONSTANT'), ('CROSS', 'IPO_EASE_IN_OUT'), ('BLACK', 'RADIOBUT_ON'),
              ('WHITE', 'RADIOBUT_OFF'))
LEGEND = (('HIGH', "up to date", 'STRIP_COLOR_04'), ('RENDERING', "renders", 'STRIP_COLOR_06'),
          ('OLD', "old", 'STRIP_COLOR_02'), ('MISSING', "missing", 'STRIP_COLOR_01'),
          ('DRAFT', "draft", 'STRIP_COLOR_09'))

STATE_ICONS = {'HIGH': 'STRIP_COLOR_04', 'LOW': 'STRIP_COLOR_02', 'DRAFT': 'STRIP_COLOR_09', '': 'STRIP_COLOR_01'}
MOVIES = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
SOUNDS = {".wav", ".mp3", ".aif", ".aiff", ".flac", ".ogg", ".m4a"}
IMAGES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".webp", ".bmp"}
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


def _hint(layout, text, icon='INFO'):
    row = layout.row()
    row.active = False
    row.label(text=text, icon=icon, translate=False)


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
    """The selected clip (a section, a hold, a freeze or your own video / image), else None."""
    sed = cut._sed_of(scene)
    strip = sed.active_strip if sed is not None else None
    if strip is None or cut._get(strip, cut.KIND_KEY) in {"BARS", "MUSIC", "DIP", "CROSS", "FADE", "LOOK"} or \
            strip.type not in {'IMAGE', 'MOVIE'}:
        return None
    return strip


def _tool(layout, action, text, icon):
    layout.operator(cut.STUDIO_OT_cut_tool.bl_idname, text=text, icon=icon).action = action


def _strip_op(layout, action, section, icon, text=None):
    op = layout.operator(cut.STUDIO_OT_cut_strip.bl_idname, icon=icon,
                         text=text if text is not None else next(i[1] for i in cut.STRIP_ACTIONS if i[0] == action))
    op.action, op.section = action, section


# ------------------------------------------------------------------ operators

class STUDIO_OT_cut_export_pick(Operator):
    bl_idname = "studio.cut_export_pick"
    bl_label = "Export Section"
    bl_description = "In the export or not (click a section to leave it out or take it in again)"
    bl_options = {'INTERNAL'}

    section: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        scene = cut.film_scene(cut._scene(context))
        skip = cut.export_left_out(scene)
        skip.symmetric_difference_update({self.section})
        scene.studio.cut_export_skip = "\n".join(sorted(skip))
        return {'FINISHED'}


class STUDIO_OT_cut_import(Operator):
    bl_idname = "studio.cut_import"
    bl_label = "Import"
    bl_description = ("Videos, images or sound into the film at the playhead, above the sections (several "
                      "images: one image sequence)")
    bl_options = {'UNDO', 'INTERNAL'}

    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})
    files: CollectionProperty(type=OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    filter_glob: StringProperty(default="*" + ";*".join(sorted(MOVIES | SOUNDS | IMAGES)),
                                options={'HIDDEN', 'SKIP_SAVE'})

    def invoke(self, context, _event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from . import film_edit
        scene = cut._scene(context)
        names = sorted(f.name for f in self.files if f.name)
        if not names:
            return {'CANCELLED'}
        sed = film_edit._sed(scene)
        strips = cut._strips(sed)
        at = cut.playhead(scene)
        images = [n for n in names if os.path.splitext(n)[1].lower() in IMAGES]
        added = []
        try:
            if images:
                channel = film_edit._free_channel(scene, at, at + max(len(images), 48), film_edit.TOP)
                strip = strips.new_image(name=os.path.splitext(images[0])[0], filepath=os.path.join(
                    self.directory, images[0]), channel=channel, frame_start=at)
                for name in images[1:]:
                    strip.elements.append(name)
                if len(images) == 1:                      # one picture: two seconds
                    strip.frame_final_duration = 2 * scene.render.fps
                added.append(strip)
            for name in names:
                ext = os.path.splitext(name)[1].lower()
                path = os.path.join(self.directory, name)
                if ext in MOVIES:
                    channel = film_edit._free_channel(scene, at, at + 1, film_edit.TOP)
                    added.append(strips.new_movie(name=os.path.splitext(name)[0], filepath=path, channel=channel,
                                                  frame_start=at))
                elif ext in SOUNDS:
                    channel = film_edit._free_channel(scene, at, at + 1, film_edit.TOP + 4)
                    added.append(strips.new_sound(name=os.path.splitext(name)[0], filepath=path, channel=channel,
                                                  frame_start=at))
        except (RuntimeError, TypeError, ValueError) as ex:
            self.report({'WARNING'}, "Could not import: %s" % ex)
            return {'CANCELLED'}
        for strip in _all_strips(sed):
            strip.select = strip in added
        if added:
            sed.active_strip = added[0]
        self.report({'INFO'}, "Imported: %s" % ", ".join(s.name for s in added))
        return {'FINISHED'}


def _all_strips(sed):
    return cut._all(sed)


class STUDIO_OT_cut_clip(Operator):
    bl_idname = "studio.cut_clip"
    bl_label = "Clip"
    bl_options = {'UNDO', 'INTERNAL'}

    action: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})      # REMOVE

    @classmethod
    def description(cls, _context, props):
        return {"REMOVE": "Take this clip out of the film (your own clips only; Ctrl+Z brings it back)"}.get(
            props.action, "")

    def execute(self, context):
        scene = cut._scene(context)
        strip = _active(scene)
        if strip is None or cut._get(strip, cut.KIND_KEY):
            return {'CANCELLED'}
        if self.action == 'REMOVE':
            cut._strips(strip.id_data.sequence_editor).remove(strip)
        return {'FINISHED'}


def _look_target(context, scene):
    """What the Look tab changes: the selected clip, or the look over the film (None if there is none)."""
    from . import film_edit
    if context.window_manager.studio_cut_look_target == 'FILM':
        return film_edit.film_look(scene, create=False)
    return _active(scene)


class STUDIO_OT_cut_look(Operator):
    bl_idname = "studio.cut_look"
    bl_label = "Look"
    bl_options = {'UNDO', 'INTERNAL'}

    action: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})     # PRESET COPY PASTE
    preset: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        from . import film_edit
        if props.action == 'PRESET':
            return next((p[2] for p in film_edit.LOOK_PRESETS if p[0] == props.preset), "")
        return {"COPY": "Remember this look (to paste it on other clips)",
                "PASTE": "The remembered look on the selected clips (or on the look over the film)"}.get(
            props.action, "")

    def execute(self, context):
        from . import film_edit
        scene = cut._scene(context)
        target = _look_target(context, scene)
        if self.action == 'PASTE':
            if context.window_manager.studio_cut_look_target == 'FILM':
                strips = [target] if target is not None else []
            else:
                sed = cut._sed_of(scene)
                strips = [s for s in film_edit.clips(scene) if s.select] if sed is not None else []
                if not strips and target is not None:
                    strips = [target]
            if not film_edit.paste_look(strips):
                self.report({'WARNING'}, "Copy a look first, then select clips")
                return {'CANCELLED'}
            return {'FINISHED'}
        if target is None:
            self.report({'WARNING'}, "Select a clip first")
            return {'CANCELLED'}
        if self.action == 'COPY':
            film_edit.copy_look_to_clipboard(target)
        elif self.action == 'PRESET':
            film_edit.apply_preset(target, self.preset)
        return {'FINISHED'}


class STUDIO_OT_cut_transition(Operator):
    bl_idname = "studio.cut_transition"
    bl_label = "Transition"
    bl_options = {'UNDO', 'INTERNAL'}

    section: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})    # "": all
    kind: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, _context, props):
        text = {"CUT": "A hard cut", "CROSS": "Blend into the next section", "BLACK": "Through black",
                "WHITE": "Through white"}.get(props.kind, "")
        return text + (" between all sections (as long as the fades below)" if not props.section else "")

    def execute(self, context):
        scene = cut.film_scene(cut._scene(context))
        names = [self.section] if self.section else _boundaries(scene)
        for name in names:
            item = cut.settings(scene, name)
            if item.transition != self.kind:
                item.transition = self.kind
            if not self.section and self.kind != 'CUT':
                frames = max(1, context.window_manager.studio_cut_fade)
                if item.frames != frames:
                    item.frames = frames
        return {'FINISHED'}


def _boundaries(scene):
    """The sections followed by a section that is in the film (a transition goes there)."""
    found = cut.sections_of(scene)
    out = []
    for n, (name, _s, _e, marker) in enumerate(found[:-1]):
        if marker is not None and cut.section_strip(scene, name) is not None and \
                cut.section_strip(scene, found[n + 1][0]) is not None:
            out.append(name)
    return out


class STUDIO_OT_cut_orphans(Operator):
    bl_idname = "studio.cut_orphans"
    bl_label = "Remove Old Cut"
    bl_description = ("Removes cut scenes whose film is gone (left behind when the film's scene was renamed "
                      "before AI Studio 4.27). Your film and its cut stay")
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        gone = [s.name for s in cut.orphan_cuts()]
        for name in gone:
            scene = bpy.data.scenes.get(name)
            if scene is not None and all(w.scene != scene for w in context.window_manager.windows):
                bpy.data.scenes.remove(scene)
        self.report({'INFO'}, "Removed: %s" % (", ".join(gone) or "nothing"))
        return {'FINISHED'}


class STUDIO_PT_cut_renders(Panel):
    """The render queue next to the timeline: what renders, what is new, on this Mac and on the farm."""
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Renders"
    bl_label = "Renders"

    @classmethod
    def poll(cls, context):
        view = getattr(getattr(context, "space_data", None), "view_type", "")
        return view == 'SEQUENCER' and bool(cut.sections_of(cut._scene(context)))

    def draw(self, context):
        from . import tab_queue
        tab_queue.draw_queue(self.layout, context, buttons=True)


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


# ------------------------------------------------------------------ the parts of the panel

def _draw_head(layout, scene):
    """One bar for every render · the queue · back to 3D."""
    from . import film, motion, renderfarm
    head = layout.row(align=True)
    bar = head.column()
    span = cut.strip_range(scene)
    empty = "Film %d–%d · %.1f s" % (span[0], span[1], motion.seconds(scene, span[1] - span[0] + 1)) if span \
        else "Nothing rendered yet"
    if film.draw_total(bar, empty=empty):
        renderfarm.watch()
    trouble = film.queue_trouble()
    queue = head.row(align=True)
    queue.alert = bool(trouble)
    queue.popover(STUDIO_PT_cut_queue.__name__, text=str(trouble) if trouble else "",
                  icon='ERROR' if trouble else 'COLLAPSEMENU')
    head.prop(bpy.context.window_manager, "studio_cut_fast", text="", icon='MOD_TIME')
    head.operator(cut.STUDIO_OT_cut.bl_idname, text="", icon='VIEW3D').action = 'BACK'
    for section, code, text in [p for p in film_tools.problems(scene) if p[1] in film_tools.BLOCKING]:
        row = layout.row(align=True)
        row.alert = True
        row.label(text=text, icon='ERROR', translate=False)
        if code in film_tools.FIXES:
            op = row.operator(film_tools.STUDIO_OT_film_fix.bl_idname, text=film_tools.FIXES[code])
            op.section, op.code = section, code


def _legend(layout):
    grid = layout.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
    grid.scale_y = 0.75
    for _state, text, icon in LEGEND:
        cell = grid.row()
        cell.active = False
        cell.label(text=text, icon=_icon(icon, 'DOT'), translate=False)


def _section_text(name, start, end):
    """'255 HQ · 40 renders · 10 old' from the frame band."""
    from . import cut_overlay
    counts = cut_overlay.summary(name, start, end)
    parts = ["%d %s" % (counts[s], t) for s, t, _i in LEGEND if counts.get(s)]
    return " · ".join(parts[:3]) or "—"


def _draw_selected(layout, scene, found, strip):
    """What is selected, big: the clip (its section and 3D frame) or nothing."""
    box = layout.box()
    row = box.row(align=True)
    row.scale_y = 1.15
    if strip is None:
        row.active = False
        row.label(text="No clip selected: click one in the timeline", icon='RESTRICT_SELECT_ON')
        return None
    section = cut._get(strip, cut.SECTION_KEY)
    row.label(text=section or strip.name, icon='IMAGE_DATA' if strip.type == 'IMAGE' else 'FILE_MOVIE',
              translate=False)
    span = next(((s, e) for n, s, e, _m in found if n == section), None)
    if section and span is not None:
        shown = cut.scene_frame(scene, strip)[1]
        info = row.row()
        info.alignment = 'RIGHT'
        info.active = False
        info.label(text=("3D frame %d" % shown) if shown is not None else "", translate=False)
    return box


def _draw_clips(layout, scene, context, strip):
    """Clips: every section with what is rendered of it; the selected clip's tools; import."""
    found = cut.sections_of(scene)
    chosen = cut._get(strip, cut.SECTION_KEY) if strip is not None else ""
    col = layout.column(align=True)
    for name, start, end, _marker in found:
        here = name == chosen
        st = cut.frame_status(name, start, end)
        row = col.row(align=True)
        go = row.row(align=True)
        go.ui_units_x = 6
        go.operator(cut.STUDIO_OT_cut_go.bl_idname, text=name, icon='PLAY' if here else 'TRIA_RIGHT',
                    depress=here, translate=False).section = name
        state = row.row(align=True)
        state.active = here
        state.label(text=_section_text(name, start, end), icon=_state_icon(st), translate=False)
        if cut.outdated(scene, name, start, end):
            _strip_op(row, 'UPDATE', name, 'ERROR', text="")
        clean = st["done"] == st["total"] and film_tools.next_quality(scene, name, st) is None
        op = row.operator("studio.add", text="", icon='FILE_REFRESH' if clean else 'RENDER_ANIMATION')
        op.mode, op.section, op.force = 'ANIM', name, clean
    if not found:
        _hint(layout, "No sections yet: add them in the 3D view's Animation tab")
    _legend(layout)
    box = _draw_selected(layout, scene, found, strip)
    if box is not None:
        section = cut._get(strip, cut.SECTION_KEY)
        if section:
            row = box.row(align=True)
            _strip_op(row, 'FRAME', section, 'RENDER_STILL', text="This Frame")
            _strip_op(row, 'REST', section, 'FF', text="From Here")
            _strip_op(row, 'GO', section, 'VIEW3D', text="3D")
            row = box.row(align=True)
            _tool(row, 'FREEZE', "Freeze", 'PAUSE')
            frames = row.row(align=True)
            frames.ui_units_x = 3.5
            frames.prop(context.window_manager, "studio_cut_hold", text="")
            _strip_op(row, 'STILL', section, 'IMAGE_RGB_ALPHA', text="Still")
            _strip_op(row, 'LENGTH', section, 'ARROW_LEFTRIGHT', text="Length")
        else:                                        # your own clip
            row = box.row(align=True)
            row.prop(strip, "blend_alpha", text="Opacity", slider=True)
            row.prop(strip, "mute", text="", icon='HIDE_ON' if strip.mute else 'HIDE_OFF')
            row = box.row(align=True)
            row.prop(strip, "frame_offset_start", text="Trim In")
            row.prop(strip, "frame_offset_end", text="Trim Out")
            row.operator(STUDIO_OT_cut_clip.bl_idname, text="", icon='TRASH').action = 'REMOVE'
    old = cut.orphan_cuts()
    if old:
        row = layout.row(align=True)
        row.alert = True
        row.label(text="Unused old cut: %s" % ", ".join(s.name for s in old), icon='ERROR', translate=False)
        row.operator(STUDIO_OT_cut_orphans.bl_idname, text="", icon='TRASH')
    row = layout.row(align=True)
    row.operator(STUDIO_OT_cut_import.bl_idname, text="Import", icon='IMPORT')
    row.operator(cut.STUDIO_OT_cut.bl_idname, text="Build Again", icon='FILE_REFRESH').action = 'BUILD'
    from . import cut_proxy
    row = layout.row(align=True)                     # how the timeline plays: preview size, read-ahead
    row.prop(bpy.context.window_manager, "studio_cut_preview_size", expand=True)
    cache = row.row(align=True)
    cache.ui_units_x = 5
    cache.prop(bpy.context.preferences.system, "memory_cache_limit", text="Cache")
    if cut_proxy.left():
        _hint(layout, "Fast preview: %d frames still to prepare" % cut_proxy.left(), 'MOD_TIME')


def _short(name, others=()):
    """A section's short name: its number ("03"), the whole name (cut) when several share the number
    (02 Orbit, 02 Orbit Ein, 02 Orbit Loop …)."""
    number = name.split(" ", 1)[0] if name[:2].isdigit() else name[:8]
    if sum(1 for o in others if o.split(" ", 1)[0] == number) > 1:
        return name[:14]
    return number


def _kind_buttons(row, section, current):
    for kind in KINDS:
        icon = dict(KIND_ITEMS)[kind]
        op = row.operator(STUDIO_OT_cut_transition.bl_idname, text="", icon=icon, depress=kind == current)
        op.section, op.kind = section, kind


def _draw_transitions(layout, scene, context, strip):
    """Cuts: one row per transition (kind · frames · hold), all at once; fades of the film and the clip."""
    wm = context.window_manager
    found = cut.sections_of(scene)
    names = _boundaries(scene)
    box = layout.box()
    if names:
        head = box.row(align=True)
        head.active = False
        head.scale_y = 0.7
        cell = head.row()
        cell.ui_units_x = 3.2
        cell.label(text="")
        head.label(text="Cut · Cross · Black · White")
        for text in ("Frames", "Hold"):
            cell = head.row()
            cell.ui_units_x = 2.8
            cell.label(text=text)
    col = box.column(align=True)
    order = [n for n, _s, _e, _m in found]
    for name in names:
        after = order[order.index(name) + 1]
        item = next((c for c in scene.studio.cut_items if c.section == name), None)
        row = col.row(align=True)
        label = row.row(align=True)
        label.ui_units_x = 3.2
        label.label(text="%s → %s" % (_short(name, order), _short(after, order)), translate=False)
        kinds = row.row(align=True)
        _kind_buttons(kinds, name, item.transition if item is not None else 'CUT')
        frames = row.row(align=True)
        frames.ui_units_x = 2.8
        hold = row.row(align=True)
        hold.ui_units_x = 2.8
        if item is None:
            frames.enabled = hold.enabled = False
            frames.label(text="—")
            hold.label(text="—")
            continue
        frames.active = item.transition != 'CUT'
        frames.prop(item, "frames", text="")
        hold.prop(item, "hold", text="")
    if names:
        row = box.row(align=True)
        label = row.row(align=True)
        label.ui_units_x = 3.2
        label.label(text="All", icon='DUPLICATE')
        _kind_buttons(row.row(align=True), "", None)
        spacer = row.row()
        spacer.ui_units_x = 5.6
        spacer.label(text="")
    else:
        _hint(box, "Transitions come with a second rendered section")
    box = layout.box()
    row = box.row(align=True)
    label = row.row(align=True)
    label.ui_units_x = 3.2
    label.label(text="Film", icon='SEQUENCE')
    _tool(row, 'FILM_FADE_IN', "Fade In", 'TRIA_RIGHT_BAR')
    _tool(row, 'FILM_FADE_OUT', "Fade Out", 'TRIA_LEFT_BAR')
    row = box.row(align=True)
    label = row.row(align=True)
    label.ui_units_x = 3.2
    label.label(text="Clip", icon='IMAGE_DATA')
    sub = row.row(align=True)
    sub.enabled = strip is not None
    _tool(sub, 'FADE_IN', "Fade In", 'TRIA_RIGHT_BAR')
    _tool(sub, 'FADE_OUT', "Fade Out", 'TRIA_LEFT_BAR')
    row = box.row(align=True)
    row.prop(wm, "studio_cut_fade", text="Frames")
    seconds = row.row()
    seconds.active = False
    seconds.alignment = 'RIGHT'
    seconds.label(text="%.1f s" % (wm.studio_cut_fade / max(1.0, scene.render.fps / scene.render.fps_base)),
                  translate=False)
    hint = box.row()
    hint.active = False
    hint.scale_y = 0.8
    hint.label(text="Shift+click: to white · 0 frames: the fade goes", translate=False)


LIGHT = (("studio_exposure", "Exposure"), ("studio_contrast", "Contrast"), ("studio_shadows", "Shadows"),
         ("studio_highlights", "Highlights"))
COLOUR = (("studio_saturation", "Saturation"), ("studio_warmth", "Warmth"), ("studio_tint", "Tint"))


def _draw_color(layout, scene, context, strip):
    """Look: for the selected clip or the whole film: presets, light, colour, before/after, copy, letterbox."""
    from . import film_edit
    wm = context.window_manager
    layout.row(align=True).prop(wm, "studio_cut_look_target", expand=True)
    film_look = film_edit.film_look(scene, create=False)
    target = _look_target(context, scene)
    if wm.studio_cut_look_target == 'FILM':
        row = layout.row(align=True)
        if film_look is None:
            row.scale_y = 1.2
            row.operator(cut.STUDIO_OT_cut_tool.bl_idname, text="Add a Look over the Film",
                         icon='ADD').action = 'FILM_LOOK'
        else:
            row.label(text="Look over the whole film", icon='MOD_MASK')
            row.operator(cut.STUDIO_OT_cut_tool.bl_idname, text="", icon='X').action = 'FILM_LOOK'
    else:
        _draw_selected(layout, scene, cut.sections_of(scene), strip)
    body = layout.column()
    body.enabled = target is not None
    grid = body.grid_flow(row_major=True, columns=4, even_columns=True, align=True)
    for key, label, _desc, _values in film_edit.LOOK_PRESETS:
        op = grid.operator(STUDIO_OT_cut_look.bl_idname, text=label, translate=False)
        op.action, op.preset = 'PRESET', key
    owner = target if target is not None else strip
    for title, icon, props in (("Light", 'LIGHT_SUN', LIGHT), ("Colour", 'COLOR', COLOUR)):
        box = body.box()
        box.label(text=title, icon=icon)
        cells = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
        for prop, text in props:
            if owner is not None:
                cells.prop(owner, prop, text=text)
            else:
                cells.label(text=text)
        if title == "Light" and owner is not None and abs(owner.studio_brightness) > 1e-6:
            cells.prop(owner, "studio_brightness", text="Brightness")
    row = layout.row(align=True)
    row.prop(wm, "studio_cut_compare", text="Before", icon='HIDE_ON' if wm.studio_cut_compare else 'HIDE_OFF',
             toggle=True)
    sub = row.row(align=True)
    sub.enabled = target is not None
    sub.operator(STUDIO_OT_cut_look.bl_idname, text="Copy", icon='COPYDOWN').action = 'COPY'
    row.operator(STUDIO_OT_cut_look.bl_idname, text="Paste", icon='PASTEDOWN').action = 'PASTE'
    sub = row.row(align=True)
    sub.enabled = wm.studio_cut_look_target == 'CLIP' and strip is not None
    _tool(sub, 'LOOK_ALL', "To All", 'DUPLICATE')
    row = layout.row(align=True)
    label = row.row(align=True)
    label.ui_units_x = 3.5
    label.label(text="Letterbox", icon='FULLSCREEN_EXIT')
    row.prop(wm, "studio_cut_ratio", expand=True)


def _draw_export(layout, scene):
    """Export: which sections, one file or one per section, format · quality · size, Export."""
    rq = scene.studio
    found = cut.sections_of(scene)
    skip = cut.export_left_out(scene)
    if found:
        every = [n for n, _s, _e, _m in found]
        current = {n for n, _a, _b in cut.export_sections(scene)}
        picks = layout.grid_flow(row_major=True, columns=min(4, len(found)), even_columns=True, align=True)
        for name in every:
            cell = picks.row(align=True)
            cell.enabled = name in current               # only what is in the picture (Current / New)
            cell.operator(STUDIO_OT_cut_export_pick.bl_idname, text=_short(name, every), depress=name not in skip
                          and name in current, translate=False).section = name
    layout.row(align=True).prop(rq, "cut_export_split", expand=True)
    row = layout.row(align=True)
    row.prop(rq, "cut_export_format", text="")
    sub = row.row(align=True)
    sub.enabled = rq.cut_export_format not in {'PRORES', 'PNG'}
    sub.prop(rq, "cut_export_quality", text="")
    size = row.row(align=True)
    size.ui_units_x = 3
    size.prop(rq, "cut_export_size", text="")
    layout.prop(rq, "cut_export_name", text="Name")
    plan, join = cut.export_plan(scene)
    one = bool(join) or rq.cut_export_split == 'ONE' or len(plan) == 1
    frames = sum(b - a + 1 for _l, a, b, _p in plan)
    row = layout.row(align=True)
    row.scale_y = 1.3
    go = row.row(align=True)
    go.enabled = bool(plan)
    go.operator(cut.STUDIO_OT_cut.bl_idname, icon='EXPORT', text="Export %s · %.1f s" % (
        "1 file" if one else "%d files" % len(plan), frames / max(1.0, scene.render.fps / scene.render.fps_base))
        if plan else "Export: nothing rendered").action = 'EXPORT'
    try:
        folder = cut.cut_folder()
    except Exception:
        folder = ""
    if folder:
        row.operator("wm.path_open", text="", icon='FILE_FOLDER').filepath = folder
    _draw_when_ready(layout, scene)


AUTO_ICONS = {'WAITING': 'TIME', 'EXPORTING': 'RENDER_ANIMATION', 'DONE': 'CHECKMARK', 'FAILED': 'ERROR'}


def _draw_when_ready(layout, scene):
    """Export when ready: the button, and each wish with how far its frames are."""
    from . import cut_auto
    row = layout.row(align=True)
    row.enabled = bool(bpy.data.filepath)
    row.operator(STUDIO_OT_cut_auto.bl_idname, text="Export When All Frames Are There",
                 icon='TIME').action = 'ADD'
    for item in cut_auto.load()[-3:]:
        row = layout.row(align=True)
        every = [n for n, _s, _e, _m in cut.sections_of(scene)]
        text = " + ".join(_short(s, every) for s in item["sections"])
        if item["state"] == 'WAITING':
            have, total = cut_auto.progress(scene, item)
            why = cut_auto.waits_for(scene, item)
            text += " · %d / %d" % (have, total) + (" · waits: " + why if why else " · starts")
        elif item["state"] == 'EXPORTING':
            text += " · exports"
        elif item["state"] == 'DONE':
            text += " · " + os.path.basename((item.get("delivered") or item["outs"] or [""])[0])
        else:
            text += " · " + (item.get("error") or "failed")[:60]
        row.label(text=text, icon=AUTO_ICONS.get(item["state"], 'DOT'), translate=False)
        op = row.operator(STUDIO_OT_cut_auto.bl_idname, text="", icon='X', emboss=False)
        op.action, op.item = 'CANCEL', item["id"]


class STUDIO_OT_cut_auto(Operator):
    bl_idname = "studio.cut_auto"
    bl_label = "Export When Ready"
    bl_description = ("The export with these settings starts by itself once every frame of the chosen sections "
                      "is rendered and up to date (also after a restart)")
    bl_options = {'INTERNAL'}

    action: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})      # ADD CANCEL
    item: StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def execute(self, context):
        from . import cut_auto
        if self.action == 'CANCEL':
            cut_auto.cancel(self.item)
            return {'FINISHED'}
        try:
            item = cut_auto.add(cut._scene(context))
        except ValueError as ex:
            self.report({'WARNING'}, str(ex))
            return {'CANCELLED'}
        self.report({'INFO'}, "Exports when ready: %s" % " + ".join(item["sections"]))
        return {'FINISHED'}


class STUDIO_PT_cut(Panel):
    """The film in the Video Editor: the render bar, four buttons, what they show."""
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Studio"
    bl_label = "Film"
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        view = getattr(getattr(context, "space_data", None), "view_type", "")
        return view in {'PREVIEW', 'SEQUENCER_PREVIEW'}

    def draw(self, context):
        layout = self.layout
        if _other_scene(layout, context):
            return
        scene = cut._scene(context)
        strip = _active(scene)
        wm = context.window_manager
        _draw_head(layout, scene)
        tabs = layout.row(align=True)
        tabs.scale_y = 1.3
        tabs.prop(wm, "studio_cut_tab", expand=True)
        tab = wm.studio_cut_tab
        if tab == 'MOVES':
            _draw_transitions(layout, scene, context, strip)
        elif tab == 'LOOK':
            _draw_color(layout, scene, context, strip)
        elif tab == 'EXPORT':
            _draw_export(layout, scene)
        else:
            _draw_clips(layout, scene, context, strip)


classes = (STUDIO_OT_cut_export_pick, STUDIO_OT_cut_import, STUDIO_OT_cut_clip, STUDIO_OT_cut_look,
           STUDIO_OT_cut_transition, STUDIO_OT_cut_auto, STUDIO_OT_cut_orphans, STUDIO_PT_cut_queue,
           STUDIO_PT_cut_renders, STUDIO_PT_cut)


def _compare_changed(wm, context):
    from . import film_edit
    try:
        film_edit.compare(cut._scene(context), wm.studio_cut_compare)
    except (AttributeError, RuntimeError, ReferenceError) as ex:
        print("AI Studio: compare:", ex)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    wm = bpy.types.WindowManager
    wm.studio_cut_tab = EnumProperty(name="Film Tab", items=TABS, default='CLIPS', options={'SKIP_SAVE'})
    wm.studio_cut_look_target = EnumProperty(name="Look of", items=TARGETS, default='CLIP', options={'SKIP_SAVE'})
    wm.studio_cut_compare = BoolProperty(name="Before", default=False, update=_compare_changed, options={'SKIP_SAVE'},
                                         description="Before / after: every look of the film off while pressed "
                                         "(as rendered)")


def unregister():
    for prop in ("studio_cut_tab", "studio_cut_look_target", "studio_cut_compare"):
        if hasattr(bpy.types.WindowManager, prop):
            delattr(bpy.types.WindowManager, prop)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

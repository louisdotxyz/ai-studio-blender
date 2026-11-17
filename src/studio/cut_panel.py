# SPDX-License-Identifier: GPL-3.0-or-later
"""The Video Editor's Studio sidebar: one panel (in the preview's sidebar), in the order you work:

1. Clips      the sections as clips (what is rendered of each), the selected clip (render again, freeze,
              still, length; your own clips: opacity, trim, mute), import of videos, images and sound
2. Transitions  between the sections (cut, cross, through black or white, hold), fade in / out of the film
                and of the selected clip
3. Colour & Light  the selected clip: exposure, contrast, shadows, highlights, saturation, warmth, tint;
                   to all clips, a look over the whole film, letterbox
4. Export     which sections, one file or one per section, format, quality, size

Above them: one bar for every render (the queue in a popover, red when something needs a look).
Each function is in one place only.
"""

import os

import bpy
from bpy.props import CollectionProperty, StringProperty
from bpy.types import Operator, OperatorFileListElement, Panel

from . import cut, film_tools
from .ui_common import section as fold

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
    head.operator(cut.STUDIO_OT_cut.bl_idname, text="", icon='VIEW3D').action = 'BACK'
    for section, code, text in [p for p in film_tools.problems(scene) if p[1] in film_tools.BLOCKING]:
        row = layout.row(align=True)
        row.alert = True
        row.label(text=text, icon='ERROR', translate=False)
        if code in film_tools.FIXES:
            op = row.operator(film_tools.STUDIO_OT_film_fix.bl_idname, text=film_tools.FIXES[code])
            op.section, op.code = section, code


def _draw_clips(layout, scene, context, strip):
    """1. The sections as clips with their state; the selected clip's tools; import."""
    box, head, is_open = fold(layout, "cut_clips", "Clips", 'SEQUENCE')
    head.operator(STUDIO_OT_cut_import.bl_idname, text="", icon='IMPORT')
    head.operator(cut.STUDIO_OT_cut.bl_idname, text="", icon='FILE_REFRESH').action = 'BUILD'
    if not is_open:
        return
    found = cut.sections_of(scene)
    frame = cut.playhead(scene)
    chosen = cut._get(strip, cut.SECTION_KEY) if strip is not None else ""
    col = box.column(align=True)
    for name, start, end, _marker in found:
        here = name == chosen or (not chosen and start <= frame <= end and cut.section_strip(scene, name) is None)
        st = cut.frame_status(name, start, end)
        row = col.row(align=True)
        row.operator(cut.STUDIO_OT_cut_go.bl_idname, text=name, icon='PLAY' if here else 'TRIA_RIGHT',
                     depress=here, translate=False).section = name
        state = row.row(align=True)
        state.ui_units_x = 5
        state.label(text=film_tools.label_of(st, cut._rendering(name)) or "not rendered", icon=_state_icon(st),
                    translate=False)
        if cut.outdated(scene, name, start, end):
            _strip_op(row, 'UPDATE', name, 'ERROR', text="")
        clean = st["done"] == st["total"] and film_tools.next_quality(scene, name, st) is None
        op = row.operator("studio.add", text="", icon='FILE_REFRESH' if clean else 'RENDER_ANIMATION')
        op.mode, op.section, op.force = 'ANIM', name, clean
    if not found:
        _hint(box, "No sections yet: add them in the 3D view's Animation tab")
    if strip is None:
        _hint(box, "Click a clip in the timeline to work on it")
        return
    clip = box.box()
    section = cut._get(strip, cut.SECTION_KEY)
    top = clip.row(align=True)
    top.label(text=strip.name if not section else section, icon='IMAGE_DATA' if strip.type == 'IMAGE'
              else 'FILE_MOVIE', translate=False)
    if section:                                      # a section's clip: the 3D frame under the playhead
        span = next(((s, e) for n, s, e, _m in found if n == section), None)
        shown = cut.scene_frame(scene, strip)[1]
        if span is not None and shown is not None:
            st = cut.frame_status(section, span[0], span[1])
            codes = st.get("codes") or []
            index = shown - span[0]
            code = codes[index] if 0 <= index < len(codes) else None
            words = {'HIGH': "HQ", 'LOW': "LQ", 'DRAFT': "draft", '': "not rendered"}
            info = top.row()
            info.alignment = 'RIGHT'
            info.active = False
            info.label(text="3D frame %d · %s" % (shown, words.get(code, "")), translate=False)
        row = clip.row(align=True)
        row.label(text="Render again", icon='RENDER_ANIMATION')
        _strip_op(row, 'FRAME', section, 'RENDER_STILL', text="This Frame")
        _strip_op(row, 'REST', section, 'FF', text="From Here")
        row = clip.row(align=True)
        row.label(text="Freeze", icon='PAUSE')
        _tool(row, 'FREEZE', "Here", 'PAUSE')
        frames = row.row(align=True)
        frames.ui_units_x = 4
        frames.prop(context.window_manager, "studio_cut_hold", text="")
        row = clip.row(align=True)
        _strip_op(row, 'STILL', section, 'IMAGE_RGB_ALPHA', text="Still")
        _strip_op(row, 'LENGTH', section, 'ARROW_LEFTRIGHT', text="Take Length")
        _strip_op(row, 'GO', section, 'VIEW3D', text="3D View")
        return
    row = clip.row(align=True)                       # your own clip
    row.prop(strip, "blend_alpha", text="Opacity", slider=True)
    row.prop(strip, "mute", text="", icon='HIDE_ON' if strip.mute else 'HIDE_OFF', emboss=True)
    row = clip.row(align=True)
    row.prop(strip, "frame_offset_start", text="Trim In")
    row.prop(strip, "frame_offset_end", text="Trim Out")
    row = clip.row(align=True)
    row.prop(strip, "frame_start", text="Starts at")
    row.operator(STUDIO_OT_cut_clip.bl_idname, text="", icon='TRASH').action = 'REMOVE'


def _draw_transitions(layout, scene, context, strip):
    """2. Between the sections; fade in / out of the film and of the selected clip."""
    box, _head, is_open = fold(layout, "cut_transitions", "Transitions", 'IPO_EASE_IN_OUT')
    if not is_open:
        return
    wm = context.window_manager
    found = cut.sections_of(scene)
    col = box.column(align=True)
    for n, (name, _s, _e, marker) in enumerate(found[:-1]):
        if marker is None:
            continue
        after = found[n + 1][0]
        item = next((c for c in scene.studio.cut_items if c.section == name), None)
        row = col.row(align=True)
        label = row.row(align=True)
        label.ui_units_x = 6
        label.label(text="%s → %s" % (name.split(" ", 1)[0], after.split(" ", 1)[0]), icon='ARROW_LEFTRIGHT',
                    translate=False)
        if item is None:
            row.operator(cut.STUDIO_OT_cut_settings.bl_idname, text="Cut", icon='TRIA_DOWN').section = name
            continue
        row.prop(item, "transition", text="")
        frames = row.row(align=True)
        frames.ui_units_x = 3
        frames.active = item.transition != 'CUT'
        frames.prop(item, "frames", text="")
        hold = row.row(align=True)
        hold.ui_units_x = 3.5
        hold.prop(item, "hold", text="Hold")
    if len(found) < 2:
        _hint(box, "Transitions come with a second section")
    fades = box.column(align=True)
    row = fades.row(align=True)
    row.label(text="Film", icon='SEQUENCE')
    _tool(row, 'FILM_FADE_IN', "Fade In", 'TRIA_RIGHT_BAR')
    _tool(row, 'FILM_FADE_OUT', "Fade Out", 'TRIA_LEFT_BAR')
    row = fades.row(align=True)
    row.label(text="Clip", icon='IMAGE_DATA')
    sub = row.row(align=True)
    sub.enabled = strip is not None
    _tool(sub, 'FADE_IN', "Fade In", 'TRIA_RIGHT_BAR')
    _tool(sub, 'FADE_OUT', "Fade Out", 'TRIA_LEFT_BAR')
    row = fades.row(align=True)
    row.prop(wm, "studio_cut_fade", text="Fade Frames")
    hint = fades.row()
    hint.active = False
    hint.scale_y = 0.8
    hint.label(text="Shift+click: to white · 0 frames: the fade goes", translate=False)


def _draw_color(layout, scene, context, strip):
    """3. The selected clip's light and colour, to every clip, a look over the film, letterbox."""
    from . import film_edit
    box, head, is_open = fold(layout, "cut_color", "Colour & Light", 'COLOR')
    film_look = film_edit.film_look(scene, create=False)
    head.operator(cut.STUDIO_OT_cut_tool.bl_idname, text="", icon='MOD_MASK' if film_look else 'ADD',
                  depress=film_look is not None).action = 'FILM_LOOK'
    if not is_open:
        return
    target = strip
    if film_look is not None and (strip is None or cut._get(strip, cut.KIND_KEY) == "LOOK"):
        target = film_look
    if target is None:
        _hint(box, "Click a clip (or + : a look over the whole film)")
    else:
        if target is film_look:
            _hint(box, "Look over the whole film", icon='MOD_MASK')
        light = box.column(align=True)
        light.label(text="Light", icon='LIGHT_SUN')
        light.prop(target, "studio_exposure", text="Exposure", slider=True)
        row = light.row(align=True)
        row.prop(target, "studio_shadows", text="Shadows", slider=True)
        row.prop(target, "studio_highlights", text="Highlights", slider=True)
        light.prop(target, "studio_contrast", text="Contrast", slider=True)
        color = box.column(align=True)
        color.label(text="Colour", icon='COLOR')
        color.prop(target, "studio_saturation", text="Saturation", slider=True)
        row = color.row(align=True)
        row.prop(target, "studio_warmth", text="Warmth", slider=True)
        row.prop(target, "studio_tint", text="Tint", slider=True)
        row = box.row(align=True)
        sub = row.row(align=True)
        sub.enabled = target is not film_look
        _tool(sub, 'LOOK_ALL', "To All Clips", 'DUPLICATE')
        _tool(row, 'LOOK_RESET', "Reset", 'LOOP_BACK')
    row = box.row(align=True)
    row.label(text="Letterbox", icon='SEQ_PREVIEW')
    row.prop(context.window_manager, "studio_cut_ratio", text="")


def _draw_export(layout, scene):
    """4. Which sections, one file or one per section, format · quality · size, Export."""
    rq = scene.studio
    box, head, is_open = fold(layout, "cut_export", "Export", 'EXPORT')
    try:
        folder = cut.cut_folder()
    except Exception:
        folder = ""
    if folder:
        head.operator("wm.path_open", text="", icon='FILE_FOLDER').filepath = folder
    if not is_open:
        return
    found = cut.sections_of(scene)
    skip = cut.export_left_out(scene)
    if found:
        picks = box.grid_flow(row_major=True, columns=min(6, len(found)), even_columns=True, align=True)
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
    box.prop(rq, "cut_export_name", text="Name")
    plan, join = cut.export_plan(scene)
    one = bool(join) or rq.cut_export_split == 'ONE' or len(plan) == 1
    frames = sum(b - a + 1 for _l, a, b, _p in plan)
    row = box.row(align=True)
    row.scale_y = 1.3
    row.enabled = bool(plan)
    row.operator(cut.STUDIO_OT_cut.bl_idname, icon='EXPORT', text="Export %s · %.1f s" % (
        "1 file" if one else "%d files" % len(plan), frames / max(1.0, scene.render.fps / scene.render.fps_base))
        if plan else "Export: nothing rendered").action = 'EXPORT'


class STUDIO_PT_cut(Panel):
    """The film in the Video Editor: clips, transitions, colour and light, export."""
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
        _draw_head(layout, scene)
        _draw_clips(layout, scene, context, strip)
        _draw_transitions(layout, scene, context, strip)
        _draw_color(layout, scene, context, strip)
        _draw_export(layout, scene)


classes = (STUDIO_OT_cut_export_pick, STUDIO_OT_cut_import, STUDIO_OT_cut_clip, STUDIO_PT_cut_queue, STUDIO_PT_cut)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

# SPDX-License-Identifier: GPL-3.0-or-later
"""The Video Editor's sidebar (Studio): two panels with icon tabs.

- Above, in the preview's sidebar: the clip that is selected (Clip · Look · Fades · Title).
- Below, in the timeline's sidebar: the film (Sections · Transitions · Music · Export).
A Video Editor that shows preview and timeline in one has both. Rendering starts per section (or ⌘E);
the whole film has no Render button here.
"""

import os

import bpy
from bpy.props import EnumProperty
from bpy.types import Panel

from . import cut, film_tools, motion

CLIP_TABS = (('CLIP', "Clip", "The selected clip: render again, freeze, still", 'SEQUENCE', 0),
             ('LOOK', "Look", "Grading: the clip's look and a look over the whole film, letterbox", 'COLOR', 1),
             ('FADE', "Fades", "Fades of the clip and of the whole film, the transition after it",
              'IPO_EASE_IN_OUT', 2),
             ('TITLE', "Title", "Text over the film", 'FONT_DATA', 3))
FILM_TABS = (('SECTIONS', "Sections", "The sections: camera, proof, draft, what is rendered, render",
              'MARKER_HLT', 0),
             ('CUT', "Transitions", "Transitions and holds between the sections", 'ARROW_LEFTRIGHT', 1),
             ('MUSIC', "Music", "Music under the film, beats", 'SOUND', 2),
             ('EXPORT', "Export", "The film as a video or frames: format, quality, size, range", 'EXPORT', 3))
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


def _tabs(layout, wm, prop, items):
    """The tabs as equal icons over the full width, like the Studio tab of the 3D view (names in the
    tooltips)."""
    row = layout.row(align=True)
    row.scale_y = 1.3
    grid = row.grid_flow(row_major=True, columns=len(items), even_columns=True, align=True)
    for key, _name, _text, icon, _n in items:
        grid.prop_enum(wm, prop, key, text="", icon=icon)


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
    sed = scene.sequence_editor
    strip = sed.active_strip if sed is not None else None
    if strip is None or cut._get(strip, cut.KIND_KEY) in {"BARS", "MUSIC", "DIP", "CROSS", "FADE"} or \
            strip.type not in {'IMAGE', 'MOVIE', 'ADJUSTMENT', 'TEXT'}:
        return None
    return strip


def _strip_op(layout, action, section, icon, text=None):
    op = layout.operator(cut.STUDIO_OT_cut_strip.bl_idname, icon=icon,
                         text=text if text is not None else next(i[1] for i in cut.STRIP_ACTIONS if i[0] == action))
    op.action, op.section = action, section
    return op


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

def _clip_header(layout, scene, strip):
    """Name · what is rendered of its section · the 3D frame under the playhead and its state."""
    section = cut._get(strip, cut.SECTION_KEY)
    box = layout.box()
    row = box.row(align=True)
    found = next(((s, e) for n, s, e, _m in cut.sections_of(scene) if n == section), None)
    st = cut.frame_status(section, found[0], found[1]) if found else None
    row.label(text=section or strip.name, icon=_state_icon(st) if st else 'SEQUENCE', translate=False)
    if st is None or strip.type != 'IMAGE':
        return
    sub = row.row()
    sub.alignment = 'RIGHT'
    sub.label(text=film_tools.label_of(st, cut._rendering(section)) or "not rendered", translate=False)
    shown = cut.scene_frame(scene, strip)[1]
    if shown is None:
        return
    index = int(scene.frame_current - round(strip.frame_start))
    codes = st.get("codes") or []
    code = codes[index] if 0 <= index < len(codes) else None
    words = {'HIGH': "High Quality", 'LOW': "Low Quality", 'DRAFT': "draft", '': "not rendered yet"}
    line = box.row()
    line.active = False
    line.label(text="3D frame %d · %s" % (shown, words.get(code, "")), translate=False,
               icon=_icon(STATE_ICONS.get(code, ''), 'DOT') if code is not None else 'NONE')


def _clip_tab(layout, scene, context, strip):
    if strip is None:
        _hint(layout, "Pick a clip in the timeline")
        return
    section = cut._get(strip, cut.SECTION_KEY)
    wm = context.window_manager
    if section and cut._get(strip, cut.KIND_KEY) == "SECTION":
        col = layout.column(align=True)
        col.label(text="Render again")
        row = col.row(align=True)
        _strip_op(row, 'FRAME', section, 'IMAGE_DATA')
        _strip_op(row, 'REST', section, 'FRAME_NEXT')
    if strip.type == 'IMAGE':
        col = layout.column(align=True)
        col.label(text="Hold")
        row = col.row(align=True)
        _tool(row, 'FREEZE', "Freeze Here", 'PAUSE')
        row.prop(wm, "studio_cut_hold", text="Frames")
    if section:
        col = layout.column(align=True)
        col.label(text="Section")
        row = col.row(align=True)
        _strip_op(row, 'STILL', section, 'OUTPUT')
        _strip_op(row, 'LENGTH', section, 'ARROW_LEFTRIGHT')
        _strip_op(row, 'GO', section, 'VIEW3D')


def _look_tab(layout, scene, context, strip):
    from . import film_edit
    if strip is not None and strip.type in {'IMAGE', 'MOVIE', 'ADJUSTMENT'}:
        layout.label(text="This Clip", icon='SEQUENCE')
        draw_look(layout, strip)
        row = layout.row(align=True)
        _tool(row, 'LOOK_ALL', "To All Clips", 'DUPLICATE')
        _tool(row, 'LOOK_RESET', "", 'LOOP_BACK')
    else:
        _hint(layout, "Pick a clip for its own look")
    layout.separator()
    layout.label(text="Whole Film", icon='COLOR')
    look = film_edit.film_look(scene, create=False)
    layout.operator(cut.STUDIO_OT_cut_tool.bl_idname, text="Look over Everything",
                    icon='CHECKBOX_HLT' if look else 'CHECKBOX_DEHLT', depress=look is not None).action = 'FILM_LOOK'
    if look is not None:
        draw_look(layout, look)
    row = layout.row(align=True)
    row.label(text="Letterbox")
    row.prop(context.window_manager, "studio_cut_ratio", text="")


def _fade_tab(layout, scene, context, strip):
    wm = context.window_manager
    layout.prop(wm, "studio_cut_fade", text="Fade Frames")
    if strip is not None:
        col = layout.column(align=True)
        col.label(text="This Clip (Shift: white)", icon='SEQUENCE')
        row = col.row(align=True)
        _tool(row, 'FADE_IN', "In", 'TRIA_RIGHT_BAR')
        _tool(row, 'FADE_OUT', "Out", 'TRIA_LEFT_BAR')
    col = layout.column(align=True)
    col.label(text="Whole Film (Shift: white)", icon='RENDER_ANIMATION')
    row = col.row(align=True)
    _tool(row, 'FILM_FADE_IN', "In", 'TRIA_RIGHT_BAR')
    _tool(row, 'FILM_FADE_OUT', "Out", 'TRIA_LEFT_BAR')
    section = cut._get(strip, cut.SECTION_KEY) if strip is not None else ""
    names = [n for n, _s, _e, _m in cut.sections_of(scene)]
    if section in names[:-1]:
        item = next((c for c in scene.studio.cut_items if c.section == section), None)
        col = layout.column(align=True)
        col.label(text="Into %s" % names[names.index(section) + 1], icon='ARROW_LEFTRIGHT', translate=False)
        if item is None:
            col.operator(cut.STUDIO_OT_cut_settings.bl_idname, text="Set a Transition").section = section
        else:
            row = col.row(align=True)
            row.prop(item, "transition", text="")
            sub = row.row(align=True)
            sub.active = item.transition != 'CUT'
            sub.prop(item, "frames", text="")
            col.prop(item, "hold", text="Hold after it")


def _title_tab(layout, scene, context, strip):
    layout.operator(cut.STUDIO_OT_cut_tool.bl_idname, text="Title at the Playhead", icon='FONT_DATA').action = 'TITLE'
    titles = [s for s in cut.ours(scene) if cut._get(s, cut.KIND_KEY) == "TITLE"]
    for title in sorted(titles, key=lambda s: s.frame_final_start):
        row = layout.row(align=True)
        if hasattr(title, "text"):
            row.prop(title, "text", text="")
        row.label(text="%d–%d" % (title.frame_final_start, title.frame_final_end - 1), translate=False)


class STUDIO_PT_cut_clip(Panel):
    """The selected clip (in the preview's sidebar)."""
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
        if strip is not None:
            _clip_header(layout, scene, strip)
        _tabs(layout, wm, "studio_cut_tab_clip", CLIP_TABS)
        {'CLIP': _clip_tab, 'LOOK': _look_tab, 'FADE': _fade_tab, 'TITLE': _title_tab}[wm.studio_cut_tab_clip](
            layout.box(), scene, context, strip)


# ------------------------------------------------------------------ below: the film

def _problems(layout, scene):
    found = film_tools.problems(scene)
    if not found:
        return
    box = layout.box()
    for section, code, text in found:
        row = box.row(align=True)
        row.alert = code in film_tools.BLOCKING
        row.label(text=text, icon='ERROR' if code in film_tools.BLOCKING else 'INFO', translate=False)
        if code not in film_tools.FIXES:
            continue
        op = row.operator(film_tools.STUDIO_OT_film_fix.bl_idname, text=film_tools.FIXES[code])
        op.section, op.code = section, code
        if code == 'SHARED':
            op = row.operator(film_tools.STUDIO_OT_film_fix.bl_idname, text="", icon='CHECKMARK')
            op.section, op.code = section, 'IGNORE'


def _section_card(layout, scene, name, start, end, marker, here):
    """One section as a box: ▶ name · state · render; its camera (lock); frames, seconds, the next render's
    time; the active section also shows its proof (the camera's view)."""
    st = cut.frame_status(name, start, end)
    item = next((c for c in scene.studio.cut_items if c.section == name), None)
    box = layout.box()
    row = box.row(align=True)                         # ▶ name · state · render
    row.operator(cut.STUDIO_OT_cut_go.bl_idname, text="", icon='PLAY' if here else 'TRIA_RIGHT',
                 emboss=here).section = name
    if marker is not None:
        row.prop(marker, "name", text="")
    else:
        row.label(text=name, translate=False)
    state = row.row(align=True)
    state.ui_units_x = 4.2
    state.label(text=film_tools.label_of(st) or "open", icon=_state_icon(st), translate=False)
    clean = st["done"] == st["total"] and film_tools.next_quality(scene, name, st) is None
    op = row.operator("studio.add", text="", icon='FILE_REFRESH' if clean else 'RENDER_ANIMATION')
    op.mode, op.section, op.force = 'ANIM', name, clean
    col = box.column(align=True)
    row = col.row(align=True)                         # 🔒 camera
    locked = bool(item and item.camera_lock)
    row.operator(film_tools.STUDIO_OT_section_lock.bl_idname, text="", icon='LOCKED' if locked else 'UNLOCKED',
                 depress=locked).section = name
    if marker is not None:
        sub = row.row(align=True)
        sub.enabled = not locked
        sub.prop(marker, "camera", text="")
    row.operator(film_tools.STUDIO_OT_film_proof.bl_idname, text="", icon='CAMERA_DATA').section = name
    row.operator(film_tools.STUDIO_OT_film_draft.bl_idname, text="", icon='SHADING_SOLID').section = name
    row = col.row(align=True)                         # frames · seconds · the next render
    seconds = "%d–%d · %.1f s" % (start, end, motion.seconds(scene, end - start + 1))
    if marker is not None:
        row.operator("studio.section_edit", text=seconds, emboss=False, translate=False).name = name
    else:
        row.label(text=seconds, translate=False)
    est = row.row()
    est.alignment = 'RIGHT'
    est.active = False
    est.label(text=film_tools.estimate_text(scene, name, st), translate=False)
    if cut.outdated(scene, name, start, end):
        _strip_op(row, 'UPDATE', name, 'FILE_REFRESH', text="")
    icon = film_tools.proof_icon(name) if here else 0
    if icon:                                          # the active section: what its camera sees
        try:
            box.template_icon(icon_value=icon, scale=7.0)
        except (AttributeError, TypeError):
            box.label(text="", icon_value=icon)
        info = film_tools.proof_info(name)
        cam = marker.camera if marker is not None else scene.camera
        if cam is not None and info.get("camera") not in (None, cam.name):
            warn = box.row()
            warn.alert = True
            warn.label(text="Proof from another camera: make it again", icon='ERROR')


def _sections_tab(layout, scene, context):
    from .runner import format_time
    row = layout.row(align=True)                      # the head, like every Studio tab
    row.scale_y = 1.2
    row.operator(film_tools.STUDIO_OT_film_proof.bl_idname, text="Proof", icon='CAMERA_DATA')
    row.operator(film_tools.STUDIO_OT_film_draft.bl_idname, text="Draft", icon='SHADING_SOLID')
    row.operator("studio.section", text="", icon='ADD').action = 'ADD'
    total = film_tools.total_estimate(scene)
    if total:
        info = layout.row()
        info.active = False
        info.label(text="Next renders together ~%s" % format_time(total), icon='TIME', translate=False)
    _problems(layout, scene)
    frame = scene.frame_current
    sed = scene.sequence_editor
    active = sed.active_strip if sed is not None else None
    for name, start, end, marker in cut.sections_of(scene):
        strip = cut.section_strip(scene, name)
        here = (strip is not None and active is not None and cut._get(active, cut.SECTION_KEY) == name) or \
            (strip is None and start <= frame <= end)
        _section_card(layout, scene, name, start, end, marker, here)


def _cut_tab(layout, scene, context):
    found = cut.sections_of(scene)
    if len(found) < 2 or found[0][3] is None:
        _hint(layout, "Transitions come with two sections or more")
    for n, (name, _start, _end, marker) in enumerate(found[:-1]):
        if marker is None:
            continue
        item = next((c for c in scene.studio.cut_items if c.section == name), None)
        col = layout.column(align=True)
        col.label(text="%s → %s" % (name, found[n + 1][0]), icon='ARROW_LEFTRIGHT', translate=False)
        if item is None:
            col.operator(cut.STUDIO_OT_cut_settings.bl_idname, text="Cut").section = name
            continue
        row = col.row(align=True)
        row.prop(item, "transition", text="")
        sub = row.row(align=True)
        sub.active = item.transition != 'CUT'
        sub.prop(item, "frames", text="")
        row.prop(item, "hold", text="Hold")
    layout.separator()
    row = layout.row(align=True)
    row.operator(cut.STUDIO_OT_cut.bl_idname, text="Build Again", icon='FILE_REFRESH').action = 'BUILD'
    if scene.studio.cut_bpm > 0:
        row.operator(cut.STUDIO_OT_cut.bl_idname, text="On Beats", icon='SNAP_ON').action = 'SNAP'


def _music_tab(layout, scene, context):
    layout.prop(scene.studio, "cut_music", text="")
    row = layout.row(align=True)
    row.prop(scene.studio, "cut_bpm")
    row.operator(cut.STUDIO_OT_cut.bl_idname, text="On Beats", icon='SNAP_ON').action = 'SNAP'
    music = next((s for s in cut.ours(scene) if cut._get(s, cut.KIND_KEY) == "MUSIC"), None)
    if music is not None:
        col = layout.column(align=True)
        col.prop(music, "volume", text="Volume")
        col.prop(music, "mute", text="Muted")
    elif scene.studio.cut_music:
        _hint(layout, "The music comes in with the first rendered section")


def _export_tab(layout, scene, context):
    rq = scene.studio
    col = layout.column(align=True)
    col.prop(rq, "cut_export_format", text="")
    sub = col.row(align=True)
    sub.enabled = rq.cut_export_format not in {'PRORES', 'PNG'}
    sub.prop(rq, "cut_export_quality", text="")
    col.prop(rq, "cut_export_size", expand=True)
    col.prop(rq, "cut_export_range", text="")
    row = col.row(align=True)
    row.enabled = rq.cut_export_format != 'PNG'
    row.prop(rq, "cut_export_music")
    try:
        col.prop(rq, "cut_export_name", text="", placeholder=scene.name)
    except TypeError:                                  # older Blender: no placeholder
        col.prop(rq, "cut_export_name", text="")
    span = cut.export_range(scene)
    target, ext = cut.export_target(scene)
    info = layout.row()
    info.active = False
    shown = os.path.basename(os.path.dirname(target)) + "/" if rq.cut_export_format == 'PNG' else \
        os.path.basename(target) + ext
    info.label(text="%s · %s" % (shown, "%d–%d · %.1f s" % (span[0], span[1], motion.seconds(
        scene, span[1] - span[0] + 1)) if span else "nothing rendered yet"), translate=False)
    row = layout.row()
    row.scale_y = 1.4
    row.enabled = span is not None
    row.operator(cut.STUDIO_OT_cut.bl_idname, text="Export", icon='EXPORT').action = 'EXPORT'
    row = layout.row(align=True)
    row.operator(cut.STUDIO_OT_cut.bl_idname, text="EDL", icon='TEXT').action = 'EDL'
    try:
        folder = cut.cut_folder()
    except Exception:
        folder = ""
    if folder:
        row.operator("wm.path_open", text="Folder", icon='FILE_FOLDER').filepath = folder


class STUDIO_PT_cut(Panel):
    """The film (in the timeline's sidebar): sections, transitions, music, export."""
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
        wm = context.window_manager
        from . import film, renderfarm
        queue = layout.column(align=True)         # what renders: films of this Mac and the farm's jobs
        film.draw(queue)
        if renderfarm.draw_rows(queue):
            renderfarm.watch()
        _tabs(layout, wm, "studio_cut_tab_film", FILM_TABS)
        tab = wm.studio_cut_tab_film
        {'SECTIONS': _sections_tab, 'CUT': _cut_tab, 'MUSIC': _music_tab, 'EXPORT': _export_tab}[tab](
            layout.column() if tab == 'SECTIONS' else layout.box(), scene, context)
        row = layout.row(align=True)
        span = cut.strip_range(scene)
        info = row.row()
        info.active = False
        info.label(text="Strips %d–%d · %.1f s" % (span[0], span[1], motion.seconds(scene, span[1] - span[0] + 1))
                   if span else "Nothing rendered yet", translate=False)
        row.operator(cut.STUDIO_OT_cut.bl_idname, text="", icon='VIEW3D', emboss=False).action = 'BACK'


classes = (STUDIO_PT_cut, STUDIO_PT_cut_clip)


def register():
    wm = bpy.types.WindowManager
    wm.studio_cut_tab_clip = EnumProperty(name="Clip", items=CLIP_TABS, default='CLIP', options={'SKIP_SAVE'})
    wm.studio_cut_tab_film = EnumProperty(name="Film", items=FILM_TABS, default='SECTIONS', options={'SKIP_SAVE'})
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    for prop in ("studio_cut_tab_clip", "studio_cut_tab_film"):
        if hasattr(bpy.types.WindowManager, prop):
            delattr(bpy.types.WindowManager, prop)

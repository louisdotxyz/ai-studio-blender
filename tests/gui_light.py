"""In a real Blender window (not headless): the light map and placing on the product, driven by simulated
mouse events; saves screenshots to look at.

    AI_STUDIO_NO_LIVE=1 BLENDER_USER_RESOURCES=<test profile> Blender --enable-event-simulate \
        --python tests/gui_light.py -- <output folder>

Writes one line per check and GUI_OK / GUI_FAIL into <output folder>/gui.txt, then quits Blender.
(The extension must be installed and enabled in that profile; AI_STUDIO_NO_LIVE keeps this window from
taking over the Live Connection of a Blender that is open for work.)
"""
import math
import os
import sys
import traceback

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else "/tmp/ai_studio_gui"
os.makedirs(OUT, exist_ok=True)
PKG = next(n for n in sys.modules if n.startswith("bl_ext.") and n.endswith(".AI_Studio"))
mod = lambda name: sys.modules[PKG + "." + name]   # noqa: E731
lines, results, state = [], [], {}


def log(text):
    lines.append(text)
    with open(os.path.join(OUT, "gui.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def check(name, ok, detail=""):
    results.append(bool(ok))
    log("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


def view():
    win = bpy.context.window_manager.windows[0]
    area = max((a for a in win.screen.areas if a.type == 'VIEW_3D'), key=lambda a: a.width * a.height)
    region = next(r for r in area.regions if r.type == 'WINDOW')
    return win, area, region


def override():
    win, area, region = view()
    return bpy.context.temp_override(window=win, screen=win.screen, area=area, region=region)


def shot(name):
    with override():
        bpy.ops.screen.screenshot(filepath=os.path.join(OUT, name))


def mouse(kind, value, pos, **mods):
    win, _area, region = view()
    win.event_simulate(type=kind, value=value, x=int(region.x + pos[0]), y=int(region.y + pos[1]), **mods)


def light_on_map():
    items, lmap = mod("studio.light_items"), mod("studio.light_map")
    with override():
        rect = lmap.map_rect(bpy.context)
        around, height = items.angles(bpy.context.scene, state["item"])
    return rect, around, height, lmap.to_map(rect, around, height)


# ------------------------------------------------------------------ the steps, one per timer tick

def setup():
    win, area, _region = view()
    with override():
        scene = bpy.context.scene
        rs = scene.render_studio
        bpy.context.window_manager.render_studio_tab = 'LIGHT'
        rs.world_mode = 'STUDIO'
        for obj in mod("studio.lights").scene_lights(scene):
            bpy.data.objects.remove(obj)
        light = mod("studio.lights").add_light(scene, None)
        state["item"] = ('LIGHT', light)
        mod("studio.light_items").select(scene, state["item"])
        space = area.spaces.active
        space.show_region_ui = True
        space.show_gizmo = True
        for region in area.regions:
            if region.type == 'UI':
                try:
                    region.active_panel_category = "Studio"
                except Exception as exc:
                    log("NOTE sidebar tab: %s" % exc)
    log("NOTE Blender %s, window %dx%d, map on: %s" % (
        bpy.app.version_string, win.width, win.height, bpy.context.window_manager.render_studio_light_map))


def splash_away():
    view()[0].event_simulate(type='ESC', value='PRESS')


def splash_gone():
    view()[0].event_simulate(type='ESC', value='RELEASE')


def map_drawn():
    shot("1_map.png")
    rect, around, height, point = light_on_map()
    state["start"] = (around, height, point, rect)
    check("light map: has a place in the viewport", rect[2] > 100 and rect[3] > 30, rect)
    mouse('MOUSEMOVE', 'NOTHING', (point[0] - 6, point[1] - 4))


def hover():
    mouse('MOUSEMOVE', 'NOTHING', state["start"][2])


def press():
    mouse('LEFTMOUSE', 'PRESS', state["start"][2])


def drag_a():
    x, y = state["start"][2]
    mouse('MOUSEMOVE', 'NOTHING', (x + 40, y + 10))


def drag_b():
    x, y = state["start"][2]
    mouse('MOUSEMOVE', 'NOTHING', (x + 90, y + 24))


def release():
    x, y = state["start"][2]
    mouse('LEFTMOUSE', 'RELEASE', (x + 90, y + 24))


def dragged():
    lmap = mod("studio.light_map")
    around0, height0, _point, rect = state["start"]
    _rect, around, height, _p = light_on_map()
    want_a = around0 + 90 / rect[2] * 2 * math.pi
    want_h = height0 + 24 / rect[3] * (lmap.HIGH - lmap.LOW)
    check("light map: dragging a light moves it", abs(around - want_a) < 0.02 and abs(height - want_h) < 0.02,
          "around %.3f (want %.3f), height %.3f (want %.3f)" % (around, want_a, height, want_h))
    shot("2_dragged.png")
    scene = view()[0].scene
    state["energy"] = state["item"][1].data.energy
    state["point"] = light_on_map()[3]
    mouse('MOUSEMOVE', 'NOTHING', state["point"])
    state["pins"] = len(scene.render_studio.hdri_pins)


def shift_press():
    mouse('LEFTMOUSE', 'PRESS', state["point"], shift=True)


def shift_drag():
    x, y = state["point"]
    mouse('MOUSEMOVE', 'NOTHING', (x, y + 45), shift=True)


def shift_release():
    x, y = state["point"]
    mouse('LEFTMOUSE', 'RELEASE', (x, y + 45), shift=True)


def brighter():
    energy = state["item"][1].data.energy
    check("light map: Shift + drag changes the brightness", abs(energy / state["energy"] - math.exp(45 / 90.0)) < 0.05,
          "%.1f → %.1f" % (state["energy"], energy))
    # now the tool: a click on the product places the open light
    paint = mod("studio.light_paint")
    win, area, region = view()
    with override():
        scene = bpy.context.scene
        scene.render_studio.place_mode = 'ILLUMINATION'
        check("place tool: becomes the active tool", paint.activate_tool(bpy.context) and paint.tool_active(bpy.context))
        rv3d = area.spaces.active.region_3d
        cube = bpy.data.objects.get("Cube") or next(o for o in scene.objects if o.type == 'MESH')
        spot = view3d_utils.location_3d_to_region_2d(region, rv3d, cube.matrix_world.translation)
        state["spot"] = (spot.x, spot.y)
        state["ray"] = paint._ray(bpy.context, region, rv3d, state["spot"])
    check("place tool: the product is under the click", state["ray"] is not None, state["spot"])
    mouse('MOUSEMOVE', 'NOTHING', state["spot"])


def click_press():
    mouse('LEFTMOUSE', 'PRESS', state["spot"])


def click_release():
    mouse('LEFTMOUSE', 'RELEASE', state["spot"])


def placed():
    light = state["item"][1]
    if state["ray"] is not None:
        hit, normal, _view = state["ray"]
        towards = (light.matrix_world.translation - hit).normalized()
        check("place tool: a click on the product puts the light over that spot", towards.dot(normal.normalized()) > 0.99,
              "angle %.2f°" % math.degrees(towards.angle(normal)))
    state["count"] = len(mod("studio.lights").scene_lights(view()[0].scene))
    shot("3_placed.png")


def ctrl_press():
    x, y = state["spot"]
    mouse('LEFTMOUSE', 'PRESS', (x + 12, y + 8), ctrl=True)


def ctrl_release():
    x, y = state["spot"]
    mouse('LEFTMOUSE', 'RELEASE', (x + 12, y + 8), ctrl=True)


def new_light():
    scene = view()[0].scene
    count = len(mod("studio.lights").scene_lights(scene))
    check("place tool: Ctrl + click makes a new light", count == state["count"] + 1, "%d → %d" % (state["count"], count))
    # a studio World light opened: its settings in the panel
    with override():
        mod("studio.light_items").select(scene, ('PIN', 0))
        bpy.context.window_manager.render_studio_sections = "light_more"
    for area in view()[0].screen.areas:
        area.tag_redraw()


def studio_tab():
    """Click the "Studio" tab of the sidebar (the second one), so the screenshots show the panel."""
    win, area, _region = view()
    ui = next(r for r in area.regions if r.type == 'UI')
    scale = bpy.context.preferences.system.ui_scale
    spot = (int(ui.x + ui.width - 11 * scale), int(ui.y + ui.height - 78 * scale))
    state["tab"] = spot
    win.event_simulate(type='MOUSEMOVE', value='NOTHING', x=spot[0], y=spot[1])


def studio_tab_press():
    view()[0].event_simulate(type='LEFTMOUSE', value='PRESS', x=state["tab"][0], y=state["tab"][1])


def studio_tab_release():
    view()[0].event_simulate(type='LEFTMOUSE', value='RELEASE', x=state["tab"][0], y=state["tab"][1])


def panel():
    shot("4_panel.png")
    with override():
        mod("studio.light_items").select(bpy.context.scene, state["item"])
    for area in view()[0].screen.areas:
        area.tag_redraw()


def panel_light():
    shot("5_panel_light.png")


def finish():
    log("GUI_OK %d/%d" % (sum(results), len(results)) if all(results) and results else
        "GUI_FAIL %d/%d" % (sum(results), len(results)))
    bpy.ops.wm.quit_blender()


STEPS = [splash_away, splash_gone, setup, map_drawn, hover, press, drag_a, drag_b, release, dragged, shift_press,
         shift_drag, shift_release, brighter, click_press, click_release, placed, ctrl_press, ctrl_release, new_light,
         studio_tab, studio_tab_press, studio_tab_release, panel, panel_light, finish]


def tick():
    step = STEPS.pop(0)
    try:
        step()
    except Exception:
        check(step.__name__, False, traceback.format_exc(limit=4).strip().replace("\n", " | "))
        if step is not finish:
            STEPS[:] = [finish]
    return 0.5 if STEPS else None


def watchdog():
    """Never leave the test window open."""
    log("GUI_FAIL timeout, %d steps left" % len(STEPS))
    bpy.ops.wm.quit_blender()


log("START")
bpy.app.timers.register(tick, first_interval=2.0)
bpy.app.timers.register(watchdog, first_interval=90.0)

"""In a real Blender window (not headless): placing a light by a click on the product, driven by simulated
mouse events; saves screenshots of the Environment, Camera and Material tabs to look at.

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


# ------------------------------------------------------------------ the steps, one per timer tick

def setup():
    win, area, _region = view()
    with override():
        scene = bpy.context.scene
        rs = scene.studio
        bpy.context.window_manager.studio_tab = 'LIGHT'
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
    log("NOTE Blender %s, window %dx%d" % (bpy.app.version_string, win.width, win.height))


def splash_away():
    view()[0].event_simulate(type='ESC', value='PRESS')


def splash_gone():
    view()[0].event_simulate(type='ESC', value='RELEASE')


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
        bpy.context.window_manager.studio_sections = "light_more"
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


def finish():
    log("GUI_OK %d/%d" % (sum(results), len(results)) if all(results) and results else
        "GUI_FAIL %d/%d" % (sum(results), len(results)))
    bpy.ops.wm.quit_blender()


def tool_on():
    """A click on the product places the open light (the tool of the Environment tab)."""
    paint = mod("studio.light_paint")
    _win, area, region = view()
    with override():
        scene = bpy.context.scene
        scene.studio.place_mode = 'ILLUMINATION'
        check("place tool: becomes the active tool", paint.activate_tool(bpy.context) and paint.tool_active(bpy.context))
        rv3d = area.spaces.active.region_3d
        cube = bpy.data.objects.get("Cube") or next(o for o in scene.objects if o.type == 'MESH')
        spot = view3d_utils.location_3d_to_region_2d(region, rv3d, cube.matrix_world.translation)
        state["spot"] = (spot.x, spot.y)
        state["ray"] = paint._ray(bpy.context, region, rv3d, state["spot"])
    check("place tool: the product is under the click", state["ray"] is not None, state["spot"])
    mouse('MOUSEMOVE', 'NOTHING', (state["spot"][0] - 5, state["spot"][1] - 5))


def tool_hover():
    mouse('MOUSEMOVE', 'NOTHING', state["spot"])


def camera_tab():
    shot("5_panel_light.png")
    with override():
        bpy.context.window_manager.studio_tab = 'RENDER'
    for area in view()[0].screen.areas:
        area.tag_redraw()


def camera_shot():
    shot("6_panel_camera.png")
    with override():
        cube = bpy.data.objects.get("Cube") or next(o for o in bpy.context.scene.objects if o.type == 'MESH')
        for obj in bpy.context.selected_objects:
            obj.select_set(False)
        cube.select_set(True)
        bpy.context.view_layer.objects.active = cube
        mat = mod("studio.matstructures").create('DARK_ANODIZED', "Gehäuse Alu eloxiert")
        mod("studio.matstructures").add_wear(mat, fingerprints=0.4)
        cube.data.materials.clear()
        cube.data.materials.append(mat)
        bpy.context.window_manager.studio_tab = 'MATERIAL'
    for area in view()[0].screen.areas:
        area.tag_redraw()


def material_shot():
    shot("7_panel_material.png")
    with override():
        bpy.context.window_manager.studio_tab = 'QUEUE'
    for area in view()[0].screen.areas:
        area.tag_redraw()


def finish_shot():
    shot("8_panel_finish.png")


def mockup_make():
    """The product (the cube) in a photo: the mockup scene, its panel and the photo behind the camera."""
    import numpy as np
    shot("8_panel_finish.png")
    photo = os.path.join(OUT, "photo.png")
    img = bpy.data.images.new("gui photo", 900, 600)
    px = np.zeros((600, 900, 4), dtype=np.float32)
    px[..., 0] = np.linspace(0.25, 0.55, 600)[:, None]
    px[..., 1] = np.linspace(0.3, 0.5, 900)[None, :]
    px[..., 2], px[..., 3] = 0.45, 1.0
    px[290:296] = (0.9, 0.9, 0.9, 1.0)                 # a horizon to line the grid up with
    img.pixels.foreach_set(px.reshape(-1))
    img.filepath_raw, img.file_format = photo, 'PNG'
    img.save()
    with override():
        scene = mod("studio.mockup").create(bpy.context, photo)
        mod("studio.mockup").set_values(scene, on_ground=True, pos_x=0.4, pos_y=0.3, horizon=0.49, turn=25)
    state["mockup"] = scene.name


def mockup_shot():
    shot("9_mockup.png")
    win, area, _region = view()
    scene = win.scene
    check("mockup: the window shows its scene through its camera, the panel draws",
          scene.name == state.get("mockup") and area.spaces.active.region_3d.view_perspective == 'CAMERA', scene.name)
    with override():
        back = bpy.data.scenes.get(scene.get("studio_mockup_of", ""))
        mod("studio.mockup").show(bpy.context, back)
        mod("studio.mockup").remove(scene)


def photo_open():
    """The Photo workspace: only the big picture and the Studio sidebar."""
    win = view()[0]
    with override():
        bpy.ops.studio.open_review()
    state["photo"] = win.workspace.name


def photo_tidy():
    win = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=win, screen=win.screen):
        mod("studio.photo_view").tidy(win)


def photo_shot():
    win = bpy.context.window_manager.windows[0]
    with bpy.context.temp_override(window=win, screen=win.screen, area=win.screen.areas[0]):
        bpy.ops.screen.screenshot(filepath=os.path.join(OUT, "10_photo.png"))
    kinds = sorted(a.type for a in win.screen.areas)
    check("photo workspace: only the picture with its sidebar is left", kinds == ['IMAGE_EDITOR'], kinds)
    with bpy.context.temp_override(window=win, screen=win.screen):
        opened = mod("studio.photo_view").toggle_compositor(win)
    kinds = sorted(a.type for a in win.screen.areas)
    check("photo workspace: the look button brings the compositor under the picture",
          opened and kinds == ['IMAGE_EDITOR', 'NODE_EDITOR'], kinds)


def idle():
    """One tick for Blender to draw what the step before changed."""
    for area in bpy.context.window_manager.windows[0].screen.areas:
        area.tag_redraw()


STEPS = [splash_away, splash_gone, setup, tool_on, tool_hover, click_press, click_release, placed, ctrl_press,
         ctrl_release, new_light, studio_tab, studio_tab_press, studio_tab_release, idle, panel, idle, camera_tab,
         idle, idle, camera_shot, idle, idle, material_shot, idle, idle, mockup_make, idle, idle, idle, mockup_shot, idle, photo_open, idle,
         photo_tidy, idle, idle, photo_shot, finish]


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

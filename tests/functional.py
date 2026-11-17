"""Headless functional checks of Claude Studio (never opens a window).

    BLENDER_USER_RESOURCES=<test profile> Blender -b --python tests/functional.py

The extension must be installed and enabled in that profile. Prints one line per check
and FUNCTIONAL_OK / FUNCTIONAL_FAIL at the end. Renders are tiny (Cycles, few samples).
"""

import faulthandler
import json
import socket
import math
import os
import sys
import tempfile
import time
import traceback

faulthandler.enable(all_threads=True)   # a crash prints where it happened

import bpy
from mathutils import Vector

PKG = next(n for n in sys.modules if n.startswith("bl_ext.") and n.endswith(".AI_Studio"))
mod = lambda name: sys.modules[PKG + "." + name]   # noqa: E731
TMP = tempfile.mkdtemp(prefix="claude_studio_test_")
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


try:                                    # a stray SIGTERM (see _cpu_compositor) must not end the run silently
    import signal as _signal
    _signal.signal(_signal.SIGTERM, lambda *_a: print("FUNCTIONAL got SIGTERM (ignored)", flush=True))
except (ValueError, OSError):
    pass


def _cpu_compositor():
    """macOS ends a windowless Blender (SIGTERM) after a render with the GPU compositor when the GPU is
    busy (the open Blender, renders): the tests composite on the CPU (same image)."""
    for scene in bpy.data.scenes:
        try:
            scene.render.compositor_device = 'CPU'
        except (AttributeError, TypeError):
            pass


def section(func):
    _cpu_compositor()
    try:
        func()
    except Exception:
        traceback.print_exc()
        check(func.__name__, False, "exception")
    return func


def log_mark(runner):
    """A line in queue.log to read from (the log shortens itself, offsets don't hold)."""
    mark = "TEST MARK %f" % time.time()
    runner.log(mark)
    return mark


def log_since(path, mark):
    text = open(path, encoding="utf-8").read()
    return text[text.rfind(mark):]


# The other sections use Blender's names ("Camera"): the name scheme is on only in short_names.
mod("studio").runner.prefs().name_scheme = False
for _obj, _name in ((bpy.context.scene.camera, "Camera"),
                    (next((o for o in bpy.data.objects if o.type == 'LIGHT'), None), "Light")):
    if _obj is not None:   # the scheme named them when the start file was loaded ("P_iso_01")
        _obj.name = _obj.data.name = _name


def small_scene():
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 8
    scene.render.resolution_x, scene.render.resolution_y = 160, 120
    return scene


@section
def execute_python_keeps_values():
    bt = mod("blender_tools")
    out, ok = bt.execute_python("g = 5\nv = Vector((1, 2, 3))\nobj = bpy.data.objects['Cube']\n"
                                "def twice(x): return x * 2\nprint(np.__name__, re.__name__)", chat=1)
    check("imports preloaded", ok and "numpy re" in out, out)
    out, ok = bt.execute_python("print(g, tuple(v), twice(4), obj.name)", chat=1)
    check("plain values and data blocks kept (looked up again)", ok and out.strip() == "5 (1.0, 2.0, 3.0) 8 Cube",
          out)
    # the lessons: imports, `from math import`, scene, collections, renamed objects, helpers using them
    out, ok = bt.execute_python("import zipfile\nfrom math import cos, radians\nsc = bpy.context.scene\n"
                                "col = bpy.data.collections.new('Lesson Test')\nsc.collection.children.link(col)\n"
                                "cube = bpy.data.objects['Cube']\nkeep = [cube, 3]\n"
                                "def where(): return col.name + ':' + cube.name\n"
                                "tree = bmesh.new()\nprint(glob.__name__, shutil.__name__, inspect.__name__)", chat=1)
    check("more modules preloaded", ok and out.strip() == "glob shutil inspect", out)
    bpy.data.objects["Cube"].name = "Cube Renamed"
    bpy.ops.ed.undo_push(message="user step")
    out, ok = bt.execute_python("print(zipfile.__name__, round(cos(radians(60)), 2), sc.name == bpy.context.scene.name, "
                                "where(), keep[0].name, keep[1])", chat=1)
    check("imports, from-imports, scene, collection, renamed object and helper kept",
          ok and out.strip() == "zipfile 0.5 True Lesson Test:Cube Renamed Cube Renamed 3", out)
    out, ok = bt.execute_python("print(tree)", chat=1)
    check("a value that does not carry over says why", not ok and "does not carry over" in out, out)
    out, ok = bt.execute_python("print(nothing_here)", chat=1)
    check("an unknown name gets the hint", not ok and "Kept from earlier calls" in out, out)
    bpy.data.objects["Cube Renamed"].name = "Cube"
    bpy.data.collections.remove(bpy.data.collections["Lesson Test"])
    out, ok = bt.execute_python("print('col' in globals(), cube.name)", chat=1)
    check("a deleted data block is gone, the others stay", ok and out.strip() == "False Cube", out)
    out, ok = bt.execute_python("print('g' in globals())", chat=2)
    check("other chat starts empty", ok and out.strip() == "False", out)


@section
def worker_queue_and_variants():
    studio = mod("studio")
    runner, V = studio.runner, studio.variants.V
    prefs = runner.prefs()
    prefs.output_root, prefs.auto_render = os.path.join(TMP, "out"), False
    scene = small_scene()
    material = bpy.data.materials.new("Holz TH")
    cube = bpy.data.objects["Cube"]
    cube.data.materials.clear()
    cube.data.materials.append(material)
    node = next(n for n in material.node_tree.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled')
    V.save({"targets": {"SUB": "Holz TH/" + node.name},
            "variants": {"rot": {"SUB": {"color": "#FF0000"}}, "blau": {"SUB": {"color": "#0000FF"}}}})
    before = tuple(node.inputs["Base Color"].default_value)
    log_path = os.path.join(runner.jobs_dir(), "..", "queue.log")
    mark = log_mark(runner)
    V.queue(["rot", "blau"], camera="Camera", quality="LOW")
    log = log_since(log_path, mark)
    check("queue rendered both", runner.RUN.message == "2 of 2 saved", runner.RUN.message)
    check("one worker for the series", log.count("worker launched") == 1 and log.count("(same worker)") == 1, log)
    check("scene unchanged by the queue", tuple(node.inputs["Base Color"].default_value) == before)
    renders = V.renders(quality="LOW")
    colors = {}
    for path, variant in renders:
        image = bpy.data.images.load(path)
        px = list(image.pixels[:])
        n = len(px) // 4
        colors[variant] = (sum(px[0::4]) / n, sum(px[2::4]) / n)
        bpy.data.images.remove(image)
    check("variants differ", len(colors) == 2 and colors["rot"][0] > colors["blau"][0]
          and colors["blau"][1] > colors["rot"][1], colors)


@section
def preview_through_worker():
    runner = mod("studio").runner
    scene = small_scene()
    got = {}
    runner.request_preview(scene, scene.camera, 200, 4, lambda path, err: got.update(path=path, err=err))
    check("render_preview via worker", got.get("path") and os.path.exists(got["path"]) and not got.get("err"),
          got)


@section
def orbit_and_top_view():
    scene = bpy.context.scene
    rq, cam = scene.studio, scene.camera
    rq.orbit_target = None
    scene.cursor.location = (1, 2, 0)
    rq.orbit_vertical = math.radians(90)
    forward = cam.matrix_world.to_3x3() @ Vector((0, 0, -1))
    check("straight top view", abs(forward.z + 1) < 1e-4 and abs(cam.location.x - 1) < 1e-4)
    rq.orbit_vertical, rq.orbit_horizontal, rq.orbit_distance = math.radians(30), math.radians(45), 5
    before = cam.location.copy()
    rq.orbit_side_offset = 0.5
    check("left/right moves the camera", abs((cam.location - before).length - 0.5) < 1e-4)
    rq.orbit_side_offset = 0.0
    # straight top view: Height only changes the distance, Up / Down moves the picture
    rq.orbit_vertical = math.radians(90)
    bpy.context.view_layer.update()
    before, look = cam.matrix_world.translation.copy(), cam.matrix_world.to_3x3() @ Vector((0, 0, -1))
    rq.orbit_up_offset = 0.3
    bpy.context.view_layer.update()
    moved = cam.matrix_world.translation - before
    check("up/down in a top view moves the picture, not the distance", abs(moved.length - 0.3) < 1e-4
          and abs(moved.z) < 1e-6 and ((cam.matrix_world.to_3x3() @ Vector((0, 0, -1))) - look).length < 1e-6, moved)
    rq.orbit_horizontal = math.radians(60)
    check("orbiting keeps the up/down offset", abs(rq.orbit_up_offset - 0.3) < 1e-6
          and abs(math.degrees(rq.orbit_vertical) - 90) < 1e-3)
    rq.orbit_up_offset = 0.0
    rq.orbit_vertical = math.radians(30)
    # the values stay exactly as set while Height, Left / Right, Up / Down change
    rq.orbit_horizontal, rq.orbit_vertical, rq.orbit_distance = 0.0, math.radians(90), 0.611
    for name, value in (("orbit_height", 0.05), ("orbit_side_offset", 0.02), ("orbit_up_offset", -0.03),
                        ("orbit_height", 0.0)):
        setattr(rq, name, value)
        bpy.context.view_layer.update()
    check("angles stay exactly as set (0 stays 0, 90 stays 90)", rq.orbit_horizontal == 0.0
          and abs(rq.orbit_vertical - math.radians(90)) < 1e-6 and abs(rq.orbit_distance - 0.611) < 1e-6,
          (rq.orbit_horizontal, rq.orbit_vertical, rq.orbit_distance))
    cam.location.x += 0.1   # moved by hand: the fields follow the camera again
    bpy.context.view_layer.update()
    check("moved by hand: values recomputed", abs(rq.orbit_vertical - math.radians(90)) > 1e-4)
    rq.orbit_side_offset = rq.orbit_up_offset = 0.0
    rq.orbit_horizontal, rq.orbit_vertical, rq.orbit_distance = math.radians(30), math.radians(20), 2.0
    cam.data.type = 'PERSP'
    bpy.ops.studio.toggle_ortho()
    sensor = cam.data.sensor_width
    check("to ortho: the framing stays (scale from the distance)", cam.data.type == 'ORTHO'
          and abs(cam.data.ortho_scale - 2.0 * sensor / cam.data.lens) < 1e-4, cam.data.ortho_scale)
    cam.data.ortho_scale *= 2
    bpy.ops.studio.toggle_ortho()
    check("back to perspective: distance from the ortho scale", cam.data.type == 'PERSP'
          and abs(rq.orbit_distance - 4.0) < 1e-3, rq.orbit_distance)


@section
def material_library():
    studio = mod("studio")
    tab = studio.tab_material
    cube = bpy.data.objects["Cube"]
    bpy.context.view_layer.objects.active = cube
    cube.select_set(True)
    for name in ("18DS115 | Gummi", "18DS115 | Alu"):
        bpy.data.materials.new(name)
    groups = dict(tab._library_groups())
    check("groups by kind", [s for _m, s in groups.get("Rubber", [])] == ["Gummi"]
          and "Alu" in [s for _m, s in groups.get("Metal", [])], list(groups))
    kinds = {}
    for name, values in (("Glas klar", {}), ("LED Streifen", {}), ("Mat A", {"Metallic": 1.0}),
                         ("Mat B", {"Transmission Weight": 1.0}), ("Eiche geölt", {}), ("Plain", {})):
        m = bpy.data.materials.new(name)
        node = tab._principled(m)
        for key, value in values.items():
            node.inputs[key].default_value = value
        kinds[name] = tab.material_kind(m)
    check("kinds from name and shader", kinds == {"Glas klar": "Glass", "LED Streifen": "Light", "Mat A": "Metal",
                                                  "Mat B": "Glass", "Eiche geölt": "Wood", "Plain": "Other"}, kinds)
    lib = dict(tab._library_groups(source='LIBRARY'))
    kind_items = [m for g, items in lib.items() if g not in (tab.FAVORITES_GROUP, tab.RECENT_GROUP)
                  for m, _s in items]
    check("library view: only base copies", all(m.get(tab.BASE_KEY) for m in kind_items), list(lib))
    bpy.ops.studio.material_new()
    check("new plain material", cube.active_material is not None and cube.active_material.name.startswith("Material"))
    bpy.ops.studio.material_assign(name="18DS115 | Gummi")
    check("assign from library", cube.active_material.name == "18DS115 | Gummi")
    check("recent: the assigned one first", tab.recent()[:1] == ["18DS115 | Gummi"], tab.recent()[:3])
    recent = dict(tab._library_groups()).get(tab.RECENT_GROUP, [])
    check("recent group on top", recent and recent[0][0].name == "18DS115 | Gummi")


@section
def material_presets():
    cube = bpy.data.objects["Cube"]
    bpy.context.view_layer.objects.active = cube
    bpy.ops.studio.material_new(preset="Chrome")
    node = mod("studio").matpresets._principled(cube.active_material)
    check("new material from preset", node is not None and node.inputs["Metallic"].default_value == 1.0,
          cube.active_material.name)


@section
def generated_hdri():
    studio = mod("studio")
    prefs = studio.runner.prefs()
    prefs.hdri_folder = os.path.join(TMP, "hdri")
    path = studio.api.hdri_setup({"name": "Test Sky", "sky": {"top": [0.8, 0.9, 1], "horizon": [1, 1, 1],
                                                             "ground": [0.3, 0.3, 0.3]}, "resolution": 1024})
    check("generated HDRI", os.path.exists(path) and bpy.context.scene.world is not None, path)


@section
def local_models_module():
    lm = mod("lmstudio")
    prefs = mod("preferences").get_prefs()
    check("local models available", hasattr(lm, "prepare") and hasattr(prefs, "local_model")
          and prefs.bl_rna.properties["backend"].enum_items.get("LOCAL") is not None)


@section
def skills_and_buttons():
    core, skills, ui = mod("core"), mod("skills"), mod("ui")
    listed = {s["name"]: s for s in skills.list_skills(core.workdir())}
    check("shipped skills installed", {"sort", "camera", "material"} <= set(listed) and "render" not in listed, sorted(listed))
    check("studio tab binding", listed.get("camera", {}).get("studio_tab") == "RENDER")
    ui.sync_skill_operators()
    bpy.ops.claude.skill_sort()
    check("skill button toggles", "sort" in core.active_skills, core.active_skills)
    bpy.ops.claude.skill_sort()


@section
def project_folder():
    autosave = mod("autosave")
    prefs = mod("preferences").get_prefs()
    prefs.project_folders = prefs.autosave = True   # off for new users until they choose it
    timers = bpy.app.timers.register
    bpy.app.timers.register = lambda fn, first_interval=0, **k: fn()   # no timers in background mode
    try:
        os.makedirs(os.path.join(TMP, "new"), exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(TMP, "new", "Stuhl.blend"))
    finally:
        bpy.app.timers.register = timers
    folder = os.path.join(TMP, "new", "Stuhl")
    main = "Stuhl%s.blend" % autosave.main_suffix()
    dirs = (autosave.backup_dir_name(), autosave.assets_dir_name(), autosave.archive_dir_name())
    check("new file gets its project folder", bpy.data.filepath == os.path.join(folder, main)
          and all(os.path.isdir(os.path.join(folder, d)) for d in dirs)
          and not os.path.exists(os.path.join(TMP, "new", "Stuhl.blend")), bpy.data.filepath)
    path = autosave.save_now("test", force=True)
    check("backups inside the project folder",
          path and os.path.dirname(path) == os.path.join(folder, autosave.backup_dir_name()))
    check("archive stays empty", not os.listdir(os.path.join(folder, autosave.archive_dir_name())))


@section
def backup_names():
    autosave = mod("autosave")
    cases = {"2.2.blend": "2.2", "2.2_12-58_24.10.26.blend": "2.2", "2.2_2026-10-24_12-58.blend": "2.2",
             "Box_2026-10-17_13-53-18_2026-10-19_19-19-49.blend": "Box", "Box 2.blend": "Box 2"}
    wrong = {k: autosave.project_name(k) for k, v in cases.items() if autosave.project_name(k) != v}
    check("backup project names", not wrong, wrong)


@section
def configurable_names():
    autosave, studio = mod("autosave"), mod("studio")
    prefs = mod("preferences").get_prefs()
    keep = (prefs.backup_dir, prefs.main_suffix, prefs.studio.output_suffix, prefs.studio.high_dir)
    try:
        prefs.backup_dir, prefs.main_suffix = "sicherungen", "_jetzt"
        prefs.studio.output_suffix, prefs.studio.high_dir = "_bilder", "gross"
        check("backup folder name from settings", autosave.backup_folder().endswith(os.sep + "sicherungen"))
        check("main suffix from settings", autosave.project_name("Tisch_jetzt.blend") == "Tisch")
        path = studio.core.job_path(TMP, "Cam", 'HIGH', name="Tisch")
        check("render folders from settings", os.sep.join(["Tisch_bilder", "gross"]) in path, path)
    finally:
        prefs.backup_dir, prefs.main_suffix, prefs.studio.output_suffix, prefs.studio.high_dir = keep


@section
def studio_tools_offered():
    names = {t["name"] for t in mod("core").extension_tools()}
    check("studio tools offered to Claude", {"studio_render_queue", "studio_light_setup", "studio_hdri_setup",
                                             "studio_camera", "studio_base_material"} <= names, sorted(names))


@section
def environment_tab():
    env = mod("studio.environment")
    api = mod("studio.api")
    scene = bpy.context.scene
    rs = scene.studio
    cube = bpy.data.objects["Cube"]
    low = min((cube.matrix_world @ Vector(c)).z for c in cube.bound_box)
    obj = api.floor("COVE", color=(0.8, 0.8, 0.8))
    zmin = min((obj.matrix_world @ Vector(c)).z for c in obj.bound_box)
    zmax = max((obj.matrix_world @ Vector(c)).z for c in obj.bound_box)
    check("cove under the product", obj is not None and abs(zmin - low) < 1e-4 and zmax > low + 1.0, (zmin, low, zmax))
    check("floor is not the product", obj not in env.product_objects(scene) and cube in env.product_objects(scene))
    rs.floor_mode = 'FLOOR'
    flat = bpy.data.objects.get(env.FLOOR_NAME)
    check("flat floor", flat is not None and max((flat.matrix_world @ Vector(c)).z for c in flat.bound_box) - low < 1e-4)
    rs.floor_mode = 'NONE'
    check("no floor", bpy.data.objects.get(env.FLOOR_NAME) is None)
    rs.haze_density = 0.05
    out = env._world_output(scene.world)
    check("haze in the World volume", out is not None and out.inputs["Volume"].is_linked
          and scene.world.node_tree.nodes.get(env.HAZE_NODE) is not None)
    rs.haze_density = 0.0
    check("haze off removes it", scene.world.node_tree.nodes.get(env.HAZE_NODE) is None)
    created = api.light_setup(mod("studio.lights").BUILTIN_PRESETS["3-Point"])
    key, fill = created[0], created[1]
    env.toggle_solo(scene, key)
    check("solo: only this light renders", not key.hide_render and all(o.hide_render for o in created[1:]))
    env.toggle_solo(scene, key)
    check("solo again: all back", not any(o.hide_render for o in created))
    api.only_product(fill, True)
    coll = fill.light_linking.receiver_collection
    check("light linking: only the product", coll is not None and cube.name in coll.objects)
    api.only_product(fill, False)
    check("light linking off", fill.light_linking.receiver_collection is None)
    flag = api.flag(key, "BLINDS")
    check("gobo in front of the light", flag is not None and flag.parent == key and not flag.visible_camera
          and flag.visible_shadow and env.flag_of(key) == flag)
    api.flag(key, None)
    check("gobo removed", env.flag_of(key) is None)
    name = env.save_scene(scene, "Test Scene")
    rs.floor_mode = 'COVE'
    notes = env.apply_scene(scene, name)
    check("scene preset round trip", rs.floor_mode == 'NONE' and rs.scene_preset == "Test Scene", (rs.floor_mode, notes))
    env.delete_scene("Test Scene")
    env.apply_scene(scene, "Studio White")
    check("built-in scene", rs.floor_mode == 'COVE' and rs.background_mode == 'COLOR'
          and bpy.data.objects.get(env.FLOOR_NAME) is not None)
    api.hdri_setup({"name": "Sun Test", "resolution": 256, "sky": {"top": [0.2, 0.3, 0.6], "horizon": [0.6, 0.7, 0.9],
                    "ground": [0.2, 0.2, 0.2]}, "strength": 1.0,
                    "sun": {"horizontal": 0, "vertical": 40, "size": 3, "strength": 80, "kelvin": 5600}})
    rs.world_rotation = 0.0
    sun = api.sun_from_hdri()
    up = (sun.rotation_euler.to_matrix() @ Vector((0, 0, 1))).normalized()
    check("sun from HDRI: above the horizon at about 40°", sun.data.type == 'SUN'
          and abs(math.degrees(math.asin(up.z)) - 40) < 8, math.degrees(math.asin(up.z)))
    front = mod("studio.hdri_gen").FRONT
    turn = abs((math.atan2(up.y, up.x) - front + math.pi) % (2 * math.pi) - math.pi)
    check("sun from HDRI: from the sun's side", math.degrees(turn) < 8, math.degrees(turn))
    rs.world_rotation = 0.5
    turned = api.sun_from_hdri()
    up2 = (turned.rotation_euler.to_matrix() @ Vector((0, 0, 1))).normalized()
    delta = (math.atan2(up2.y, up2.x) - math.atan2(up.y, up.x) + math.pi) % (2 * math.pi) - math.pi
    check("sun from HDRI follows the World rotation", abs(delta + 0.5) < 0.15, delta)
    rs.world_rotation = 0.0
    for o in mod("studio.lights").scene_lights(scene):
        bpy.data.objects.remove(o)
    rs.floor_mode = 'NONE'
    rs.background_mode = 'NORMAL'
    rs.look_glow = rs.look_contrast = rs.look_vignette = 0.0


@section
def light_placing_and_worlds():
    scene = bpy.context.scene
    rs = scene.studio
    paint, modes, env = mod("studio.light_paint"), mod("studio.world_modes"), mod("studio.environment")
    view, normal = Vector((0, 1, 0)), Vector((0, -1, 0))
    check("click placing: reflection straight back, light along the normal",
          (paint.light_direction(view, normal, 'REFLECTION') - Vector((0, -1, 0))).length < 1e-6
          and (paint.light_direction(view, Vector((1, -1, 0)), 'ILLUMINATION') - Vector((1, -1, 0)).normalized()).length < 1e-6)
    tilted = paint.light_direction(view, Vector((1, -1, 0)), 'REFLECTION')
    check("click placing: mirrored at a tilted face", (tilted - Vector((1, 0, 0))).length < 1e-6, tilted)
    rim = paint.light_direction(view, normal, 'RIM')
    check("click placing: rim comes from behind", rim.dot(view) > 0.3, rim)
    light = mod("studio.lights").add_light(scene, None)
    hit = Vector((0.1, 0.2, 0.3))
    paint.place_light(light, hit, tilted, 2.0)
    aim = (light.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
    check("click placing: light at the distance, looking at the spot",
          abs((light.matrix_world.translation - hit).length - 2.0) < 1e-5 and (aim + tilted).length < 1e-4)
    bpy.data.objects.remove(light)
    # the studio World: lights painted into an HDRI
    rs.world_mode = 'STUDIO'
    path = mod("studio.hdri").current_path(scene)
    check("studio world: three lights, its own file", len(rs.hdri_pins) == 3 and path.endswith("Studio.hdr")
          and os.path.isfile(path), path)
    key = max(rs.hdri_pins, key=lambda p: p.strength)
    image = env._env_image(scene)
    brightest, _color = env.brightest_direction(image, rs.world_rotation)
    check("studio world: brightest where the key light is", brightest.angle(modes.pin_direction(scene, key)) < 0.4,
          math.degrees(brightest.angle(modes.pin_direction(scene, key))))
    want = Vector((0.3, -0.5, 0.6)).normalized()
    modes.set_pin_direction(scene, key, want)
    check("studio world: a light follows a direction", modes.pin_direction(scene, key).angle(want) < 1e-4)
    brightest, _color = env.brightest_direction(env._env_image(scene), rs.world_rotation)
    check("studio world: rendered again after the change", brightest.angle(want) < 0.4, math.degrees(brightest.angle(want)))
    name = env.save_scene(scene, "Pins")
    rs.hdri_pins.clear()
    env.apply_scene(scene, name)
    check("scene preset keeps the studio lights", len(rs.hdri_pins) == 3 and rs.world_mode == 'STUDIO')
    env.delete_scene("Pins")
    # the sky by time of day
    el, az = modes.sun_position(12.0)
    el6, az6 = modes.sun_position(6.0)
    check("sky: noon in the south, 6 h in the east at the horizon",
          abs(math.degrees(el) - 43) < 1 and abs(math.degrees(az) - 180) < 1 and abs(math.degrees(el6)) < 1
          and abs(math.degrees(az6) - 90) < 1, (math.degrees(el), math.degrees(az), math.degrees(el6), math.degrees(az6)))
    rs.sky_time = 14.0
    rs.world_mode = 'SKY'
    day = modes.sky_spec(scene)
    rs.sky_time = 23.0
    night = modes.sky_spec(scene)
    check("sky: day has a sun, night is dark", "sun" in day and "sun" not in night and night["strength"] < 0.1 < day["strength"])
    if not modes.atmosphere_available():
        check("sky: generated file used", mod("studio.hdri").current_path(scene).endswith("Sky.hdr"))
    rs.world_mode = 'IMAGE'
    up = mod("updates")
    index = {"data": [{"id": "AI_Studio", "version": "9.9.9", "archive_url": "./AI_Studio-9.9.9.zip"}]}
    check("updates: zip address from the install page",
          up.archive_in_index(index, "9.9.9", up.INDEX_URL) == up.INDEX_URL.rsplit("/", 1)[0] + "/AI_Studio-9.9.9.zip")
    check("updates: a zip install knows its local repository", up.local_repo() == PKG.split(".")[1], up.local_repo())


@section
def light_list_map_looks():
    scene = bpy.context.scene
    rs = scene.studio
    items, paint = mod("studio.light_items"), mod("studio.light_paint")
    looks, env, api, lights_mod = mod("studio.looks"), mod("studio.environment"), mod("studio.api"), mod("studio.lights")
    deg = math.degrees
    for o in lights_mod.scene_lights(scene):
        bpy.data.objects.remove(o)
    rs.light_orbit_target = None
    rs.world_mode = 'IMAGE'
    key = lights_mod.add_light(scene, None)
    item = ('LIGHT', key)
    check("one list: only scene lights while the World is an image", [i[0] for i in items.all_items(scene)] == ['LIGHT'])
    rs.world_mode = 'STUDIO'
    kinds = [i[0] for i in items.all_items(scene)]
    check("one list: scene lights and studio World lights",
          kinds == ['LIGHT'] + ['PIN'] * len(rs.hdri_pins) and len(rs.hdri_pins) >= 3, kinds)
    # the light map: around = relative to the camera, + = the camera's right
    center = items.center_of(scene, key)
    distance = (key.matrix_world.translation - center).length
    items.set_angles(scene, item, math.radians(60), math.radians(30))
    around, height = items.angles(scene, item)
    check("light map: a light goes where it is put", abs(deg(around) - 60) < 0.01 and abs(deg(height) - 30) < 0.01,
          (deg(around), deg(height)))
    right = scene.camera.matrix_world.to_3x3() @ Vector((1, 0, 0))
    check("light map: right on the map is the camera's right", (key.matrix_world.translation - center).dot(right) > 0)
    aim = (key.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
    check("light map: moving keeps the distance and the aim",
          abs((key.matrix_world.translation - center).length - distance) < 1e-4
          and aim.angle((center - key.matrix_world.translation).normalized()) < 1e-3)
    items.set_angles(scene, ('PIN', 0), math.radians(40), math.radians(20))
    around, height = items.angles(scene, ('PIN', 0))
    check("light map: studio World lights move the same way", abs(deg(around) - 40) < 0.01 and abs(deg(height) - 20) < 0.01,
          (deg(around), deg(height)))
    items.set_angles(scene, item, math.radians(50), math.radians(25))
    items.mirror(scene, item)
    around, height = items.angles(scene, item)
    check("mirror: other side, same height", abs(deg(around) + 50) < 0.01 and abs(deg(height) - 25) < 0.01)
    count = len(lights_mod.scene_lights(scene))
    copy = items.mirror(scene, item, copy=True)
    check("mirrored copy: a second light on the other side", len(lights_mod.scene_lights(scene)) == count + 1
          and copy[1] != key and abs(deg(items.angles(scene, copy)[0]) - 50) < 0.01
          and abs(deg(items.angles(scene, item)[0]) + 50) < 0.01)
    items.remove(scene, copy)
    check("light removed", len(lights_mod.scene_lights(scene)) == count)
    pins = len(rs.hdri_pins)
    twin = items.mirror(scene, ('PIN', 0), copy=True)
    check("mirrored copy of a studio World light", twin == ('PIN', pins) and len(rs.hdri_pins) == pins + 1
          and abs(deg(items.angles(scene, twin)[0]) + 40) < 0.01)
    items.remove(scene, twin)
    # the same few values for every light
    own = key.studio_light
    key.data.shape = 'RECTANGLE'
    key.data.size, key.data.size_y = 1.0, 0.5
    own.size = 2.0
    check("size: width and height together", abs(key.data.size - 2.0) < 1e-6 and abs(key.data.size_y - 1.0) < 1e-6)
    own.softness = 0.5
    check("softness: how far the light spreads", abs(key.data.spread - math.pi / 2) < 1e-4, key.data.spread)
    own.kelvin = 3200
    check("color temperature in Kelvin", key.data.use_temperature and abs(key.data.temperature - 3200) < 1)
    pin = rs.hdri_pins[0]
    pin.width, pin.height = 0.5, 0.3
    pin.size = 1.0
    check("studio World light: size keeps its shape", abs(pin.width - 1.0) < 1e-5 and abs(pin.height - 0.6) < 1e-5)
    pin.kelvin = 3000
    check("studio World light: warm by Kelvin", pin.color[0] > pin.color[2] + 0.2, tuple(pin.color))
    # placing on the product: shade and shadow
    view, normal = Vector((0, 1, 0)), Vector((0, -1, 0))
    shade = paint.light_direction(view, normal, 'SHADE')
    check("click placing: shade puts the light behind the spot", shade.dot(normal) < -0.9 and shade.z > 0, shade)
    top = paint.light_direction(Vector((0, 1, -1)), Vector((0, 0, 1)), 'SHADE')
    check("click placing: shade on a face that looks up goes behind the product", top.y > 0.5 and top.z > 0, top)
    spot = Vector((1.0, 0.0, -0.5))
    shadow = paint.light_direction(view, Vector((0, 0, 1)), 'SHADOW', hit=spot, center=center)
    paint.place_item(scene, item, spot, shadow, 'SHADOW', 2.0)
    eye = key.matrix_world.translation
    check("click placing: light, product and shadow spot in one line",
          (spot - eye).normalized().angle((center - eye).normalized()) < 1e-3 and abs((eye - center).length - 2.0) < 1e-4)
    keys = paint.STUDIO_WT_light_place.bl_keymap
    check("place tool: click, Ctrl + click (new light) and the wheel", len(keys) == 8
          and keys[0][1] == {"type": 'LEFTMOUSE', "value": 'PRESS'} and keys[1][2]["properties"] == [("new", True)])
    items.select(scene, ('PIN', 0))
    new = items.add_like(scene, items.active_item(scene))
    check("new light: of the open light's kind", new == ('PIN', pins) and items.active_item(scene) == new, new)
    items.remove(scene, new)
    check("studio World light removed", len(rs.hdri_pins) == pins)
    items.select(scene, item)
    check("open light: one at a time", items.active_item(scene) == item and rs.hdri_pin_index == -1)
    # moving gobos
    flag = api.flag(key, "BRANCHES", speed=2.0, scale=1.5)
    speed, scale = env.gobo_nodes(flag)
    tree = flag.data.materials[0].node_tree
    drivers = tree.animation_data.drivers if tree.animation_data else []
    check("moving gobo: speed, scale and a clock that follows the frame",
          speed is not None and abs(speed.default_value - 2.0) < 1e-6 and abs(scale.default_value - 1.5) < 1e-6
          and len(drivers) == 1 and drivers[0].driver.expression == "frame / 24" and drivers[0].driver.is_valid)
    check("moving gobos: leaves, water and clouds build",
          all(env.gobo_nodes(api.flag(key, pattern))[0] is not None for pattern in ("DAPPLE", "WATER", "CLOUDS")))
    check("still gobo: no speed", env.gobo_nodes(api.flag(key, "BLINDS")) == (None, None))
    api.flag(key, "WATER")
    api.flag(key, None)
    check("gobo removed, with its material", env.flag_of(key) is None and not [
        m.name for m in bpy.data.materials if m.name.startswith(env.GOBO_PREFIX) and m.name != env.GOBO_PREFIX + "Flag"])
    # looks
    rs.light_looks.clear()
    rs.light_look_index = -1
    key.data.energy = 100.0
    items.set_angles(scene, item, math.radians(-40), math.radians(30))
    rs.background_mode = 'COLOR'
    rs.background_color = (0.9, 0.9, 0.9)
    rs.hdri_pins[0].strength = 5.0
    name = looks.add(scene)
    check("looks: the first one keeps A and opens B", looks.names(scene) == ["A", "B"] and name == "B"
          and looks.active(scene) == 1)
    key.data.energy = 400.0
    items.set_angles(scene, item, math.radians(70), math.radians(10))
    rs.background_color = (0.05, 0.05, 0.05)
    rs.hdri_pins[0].strength = 33.0
    extra = lights_mod.add_light(scene, None)
    api.flag(key, "DAPPLE", speed=0.5)
    looks.switch(scene, 0)
    check("looks: A is back as it was", abs(key.data.energy - 100) < 1e-3
          and abs(deg(items.angles(scene, item)[0]) + 40) < 0.05 and rs.background_color[0] > 0.8
          and abs(rs.hdri_pins[0].strength - 5.0) < 1e-4 and extra.hide_render and env.flag_of(key) is None,
          (key.data.energy, deg(items.angles(scene, item)[0]), rs.hdri_pins[0].strength))
    looks.switch(scene, 1)
    flag = env.flag_of(key)
    check("looks: B keeps what was changed", abs(key.data.energy - 400) < 1e-3
          and abs(deg(items.angles(scene, item)[0]) - 70) < 0.05 and rs.background_color[0] < 0.1
          and abs(rs.hdri_pins[0].strength - 33.0) < 1e-4 and not extra.hide_render
          and flag is not None and abs(env.gobo_values(flag)["speed"] - 0.5) < 1e-6,
          (key.data.energy, deg(items.angles(scene, item)[0]), rs.hdri_pins[0].strength))
    result = api.look("A")
    check("looks from Claude", result["open"] == "A" and result["looks"] == ["A", "B"], result)
    looks.switch(scene, 1)
    looks.remove(scene)
    check("looks: removing the open one goes back to its neighbor", looks.names(scene) == ["A"]
          and looks.active(scene) == 0 and abs(key.data.energy - 100) < 1e-3)
    looks.remove(scene)
    check("looks: none left", not looks.names(scene) and looks.active(scene) == -1)
    listing = api.lights_list()
    api.move_light(key.name, around=90, height=45)
    check("lights from Claude: list and move", any(entry["name"] == key.name for entry in listing)
          and any(entry["kind"] == "world" for entry in listing)
          and abs(deg(items.angles(scene, item)[0]) - 90) < 0.05)
    api.mirror_light()
    check("lights from Claude: mirror everything", abs(deg(items.angles(scene, item)[0]) + 90) < 0.05)
    for o in lights_mod.scene_lights(scene):
        items.remove(scene, ('LIGHT', o))
    rs.world_mode = 'IMAGE'
    rs.background_mode = 'NORMAL'
    scene.frame_set(1)


@section
def material_structures():
    ms, api, presets = mod("studio.matstructures"), mod("studio.api"), mod("studio.matpresets")
    cube = bpy.data.objects["Cube"]
    before = list(cube.data.materials)
    made = {}
    for kind in ms.STRUCTURES:
        material = made[kind] = ms.create(kind)
        tree = material.node_tree
        shader, group = presets._principled(material), tree.nodes.get(ms.NODE_GROUP)
        ok = shader is not None and group is not None and group.inputs["Vector"].is_linked \
            and group.node_tree.name == ms.PREFIX + ms.STRUCTURES[kind][0] and material[ms.STRUCTURE_KEY] == kind
        if kind == 'RESIN':
            out = next(n for n in tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial')
            ok = ok and out.inputs["Volume"].is_linked and shader.inputs["Transmission Weight"].default_value == 1.0
        else:
            ok = ok and shader.inputs["Base Color"].is_linked and shader.inputs["Roughness"].is_linked \
                and shader.inputs["Normal"].is_linked and tree.nodes.get(ms.NODE_BUMP) is not None
        check("structure %s: one group between mapping and shader" % kind, ok)
    check("structures: metals are metals", made['WORN_METAL'].metallic == 1.0
          and presets._principled(made['HAMMERED']).inputs["Metallic"].default_value == 1.0
          and presets._principled(made['SPECKLED']).inputs["Metallic"].default_value == 0.0)
    again = ms.create('WORN_METAL')
    check("structures: a second material shares the node group",
          again.node_tree.nodes[ms.NODE_GROUP].node_tree == made['WORN_METAL'].node_tree.nodes[ms.NODE_GROUP].node_tree
          and again != made['WORN_METAL'])
    blocks = [g.name for g in bpy.data.node_groups if g.name.startswith(ms.PREFIX)]
    check("structures: the building blocks are node groups of their own",
          ms.PREFIX + "Scratches" in blocks and all(not n.endswith(".001") for n in blocks), blocks)
    material = api.material_structure("speckled", name="Test Speckled", objects=[cube], fleck_size=2.0,
                                      color=(0.2, 0.5, 0.3), amount=0.25)
    group = material.node_tree.nodes[ms.NODE_GROUP]
    check("structure from Claude: values by name, put on the object",
          abs(group.inputs["Fleck Size"].default_value - 2.0) < 1e-6 and abs(group.inputs["Amount"].default_value - 0.25) < 1e-6
          and abs(group.inputs["Color"].default_value[1] - 0.5) < 1e-6 and cube.active_material == material)
    # wear over a plain material: what it had comes back when the layer goes
    plain = bpy.data.materials.new("Test Plain")
    shader = presets._principled(plain)
    shader.inputs["Base Color"].default_value = (0.1, 0.2, 0.3, 1.0)
    shader.inputs["Roughness"].default_value = 0.22
    values = api.wear(plain, scratches=0.6, dust=0.1)
    wear = plain.node_tree.nodes.get(ms.NODE_WEAR)
    check("wear: between the material's values and the shader", wear is not None and shader.inputs["Base Color"].is_linked
          and shader.inputs["Roughness"].is_linked and shader.inputs["Normal"].is_linked
          and abs(wear.inputs["Roughness"].default_value - 0.22) < 1e-6 and abs(wear.inputs["Color"].default_value[2] - 0.3) < 1e-6
          and abs(values["Scratches"] - 0.6) < 1e-6 and abs(values["Dust"] - 0.1) < 1e-6, values)
    api.wear(plain, remove=True)
    check("wear removed: values back, nothing left plugged in", not ms.has_wear(plain)
          and not shader.inputs["Base Color"].is_linked and not shader.inputs["Normal"].is_linked
          and abs(shader.inputs["Roughness"].default_value - 0.22) < 1e-6
          and abs(shader.inputs["Base Color"].default_value[1] - 0.2) < 1e-6)
    metal = made['WORN_METAL']
    shader = presets._principled(metal)
    source = shader.inputs["Base Color"].links[0].from_node
    api.wear(metal)
    wear = metal.node_tree.nodes[ms.NODE_WEAR]
    check("wear over a structure: the structure feeds the wear layer",
          wear.inputs["Color"].links[0].from_node == source and shader.inputs["Base Color"].links[0].from_node == wear
          and metal.node_tree.nodes[ms.NODE_WEAR_BUMP].inputs["Normal"].links[0].from_node.name == ms.NODE_BUMP
          and len(ms.control_nodes(metal)) == 2)
    api.wear(metal, remove=True)
    check("wear off a structure: as before", shader.inputs["Base Color"].links[0].from_node == source
          and shader.inputs["Normal"].links[0].from_node.name == ms.NODE_BUMP)
    scene = small_scene()
    cube.data.materials.clear()
    cube.data.materials.append(made['DARK_ANODIZED'])
    api.wear(made['DARK_ANODIZED'], scratches=0.5, fingerprints=0.5, dust=0.3)
    path, keep = os.path.join(TMP, "structure.png"), scene.render.filepath
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
    scene.render.filepath = keep
    check("structure with wear renders", os.path.isfile(path) and os.path.getsize(path) > 2000)
    tab = mod("studio.tab_material")
    favs = {material.name}
    check("library: favorites and project", tab._in_source(material, 'FAVORITES', {}, favs)
          and not tab._in_source(plain, 'FAVORITES', {}, favs) and tab._in_source(plain, 'PROJECT', {}, favs))
    kinds = tab.library_kinds('PROJECT')
    groups = tab._library_groups("", 'PROJECT', "Metal")
    check("library: filter by kind", "Metal" in kinds and len(groups) == 1 and groups[0][0] == "Metal"
          and all(tab.material_kind(m) == "Metal" for m, _short in groups[0][1]), (kinds, [g[0] for g in groups]))
    cube.data.materials.clear()
    for old in before:
        cube.data.materials.append(old)
    for mat in list(made.values()) + [again, material, plain]:
        bpy.data.materials.remove(mat)


@section
def tabs_unified():
    scene = bpy.context.scene
    rs = scene.studio
    kinds, presets, api, tab = mod("studio.matkinds"), mod("studio.matpresets"), mod("studio.api"), mod("studio.tab_material")
    cams, look, lights_mod = mod("studio.cameras"), mod("studio.look"), mod("studio.lights")
    # material kinds: what the kind fixes, and only the controls that fit
    mat = bpy.data.materials.new("Test Gehäuse")
    node = presets._principled(mat)
    node.inputs["Metallic"].default_value = 0.6
    kinds.apply(mat, node, "Plastic")
    check("kind: a plastic is not metallic, and has no Metallic control", node.inputs["Metallic"].default_value == 0.0
          and tab.material_kind(mat) == "Plastic" and "Metallic" not in [n for n, _l in kinds.CONTROLS["Plastic"]]
          and [n for n, _l in kinds.CONTROLS["Plastic"]][:2] == ["Base Color", "Roughness"])
    kinds.apply(mat, node, "Metal")
    check("kind: a metal is metallic", node.inputs["Metallic"].default_value == 1.0 and tab.material_kind(mat) == "Metal")
    kinds.apply(mat, node, "Glass")
    check("kind: glass lets light through", node.inputs["Transmission Weight"].default_value == 1.0
          and "IOR" in [n for n, _l in kinds.CONTROLS["Glass"]])
    check("kinds: color and roughness first for every surface",
          all([n for n, _l in kinds.CONTROLS[k]][:2] == ["Base Color", "Roughness"] for k in kinds.KINDS if k != "Light"))
    bpy.data.materials.remove(mat)
    # camera presets: orthographic sides, hero, detail
    before, old_camera = set(cams.scene_cameras(scene)), scene.camera
    center, radius = lights_mod.subject(scene, rs.orbit_target)
    made = {key: cams.camera_preset(scene, bpy.context.view_layer, key) for key, *_rest in cams.VIEW_PRESETS}
    front, top, hero, detail = made['FRONT'], made['TOP'], made['HERO'], made['DETAIL']
    aim = (front.matrix_world.to_3x3() @ Vector((0, 0, -1))).normalized()
    check("camera preset: front is orthographic, in front of the product, looking at it",
          front.data.type == 'ORTHO' and front.matrix_world.translation.y < center.y - radius
          and abs(front.matrix_world.translation.x - center.x) < 1e-3 and aim.y > 0.999
          and abs(front.data.ortho_scale - 2.5 * radius) < 1e-4, (tuple(front.matrix_world.translation), aim))
    check("camera preset: top looks down", top.matrix_world.translation.z > center.z + radius
          and (top.matrix_world.to_3x3() @ Vector((0, 0, -1))).z < -0.99)
    check("camera preset: hero and detail are perspective, detail is closer",
          hero.data.type == 'PERSP' and abs(hero.data.lens - 85) < 1e-3 and abs(detail.data.lens - 100) < 1e-3
          and (detail.matrix_world.translation - center).length < (hero.matrix_world.translation - center).length
          and (hero.matrix_world.translation - center).length > radius)
    check("camera presets: eight cameras, each with its own name",
          len(set(cams.scene_cameras(scene)) - before) == len(cams.VIEW_PRESETS) and len({c.name for c in made.values()}) == 8)
    for cam in made.values():
        data = cam.data
        bpy.data.objects.remove(cam)
        if data.users == 0:
            bpy.data.cameras.remove(data)
    scene.camera = old_camera
    # look: saturation is one more node of ours, gone again at 1
    bg = mod("studio.background")
    rs.look_saturation = 0.6
    tree = bg.compositor_tree(scene)
    node = tree.nodes.get(look.SATURATION) if tree else None
    check("look: saturation in the compositor", node is not None and node.inputs["Image"].is_linked)
    rs.look_glow = 0.3
    check("look: saturation and glow in one chain", tree.nodes.get(look.GLOW) is not None
          and tree.nodes[look.GLOW].inputs["Image"].links[0].from_node == tree.nodes[look.SATURATION])
    bpy.ops.studio.look_reset()
    tree = bg.compositor_tree(scene)
    check("look reset: nothing of ours left", rs.look_saturation == 1.0 and scene.view_settings.exposure == 0.0
          and (tree is None or not [n for n in tree.nodes if n.name.startswith(look.PREFIX)]))
    # a material straight into the library (the batch from pictures calls this once per picture)
    prefs = mod("studio.runner").prefs()
    keep_library, library = prefs.material_library, os.path.join(TMP, "Library Test.blend")
    seed = bpy.data.materials.new("Seed")
    seed.asset_mark()
    bpy.data.libraries.write(library, {seed}, fake_user=True)
    bpy.data.materials.remove(seed)
    prefs.material_library = library
    name = api.library_material("Test Bibliothek gesprenkelt", structure="speckled", kind="plastic",
                                wear={"dust": 0.2}, amount=0.3)
    built = bpy.data.materials.get("Test Bibliothek gesprenkelt")
    check("library material: built, with its kind and wear, saved to the library",
          name == "Test Bibliothek gesprenkelt" and built is not None and built.get(tab.BASE_KEY) == name
          and tab.material_kind(built) == "Plastic" and mod("studio.matstructures").has_wear(built))
    plain = api.library_material("Test Bibliothek Lack rot", kind="Plastic", color=(0.6, 0.02, 0.02), roughness=0.25,
                                 coat_weight=1.0)
    red = bpy.data.materials.get("Test Bibliothek Lack rot")
    shader = presets._principled(red)
    check("library material: a plain one by kind and values", plain == "Test Bibliothek Lack rot"
          and abs(shader.inputs["Roughness"].default_value - 0.25) < 1e-6 and shader.inputs["Coat Weight"].default_value == 1.0
          and abs(shader.inputs["Base Color"].default_value[0] - 0.6) < 1e-6 and shader.inputs["Metallic"].default_value == 0.0)
    for mat_name in (name, plain):
        api.remove_base_material(mat_name)
    for m in (built, red):
        bpy.data.materials.remove(m)
    prefs.material_library = keep_library


@section
def chat_queue_finish_wear():
    core, chats = mod("core"), mod("chats")
    # the chat queue: skills and files of the moment go with the message
    keep_skills, keep_queue = list(core.active_skills), list(core.chat_queue)
    core.chat_queue.clear()
    core.active_skills[:] = ["material"]
    picture = os.path.join(TMP, "queue_ref.png")
    img = bpy.data.images.new("queue_ref", 8, 8)
    img.filepath_raw, img.file_format = picture, 'PNG'
    img.save()
    bpy.data.images.remove(img)
    core.attachments.clear()
    core.add_attachment(picture)
    check("chat queue: an empty message is not queued", core.queue_chat("   ") is True and len(core.chat_queue) == 1)
    core.chat_queue.clear()
    core.add_attachment(picture)
    core.queue_chat("/camera make three views")
    entry = core.chat_queue[0] if core.chat_queue else {}
    check("chat queue: text, checked skills, the /skill and the files go with it",
          entry.get("text") == "make three views" and set(entry.get("skills", [])) == {"material", "camera"}
          and [a["path"] for a in entry.get("attachments", [])] == [picture] and not core.attachments, entry)
    saved = core._read_json(core.CHAT_QUEUE_FILE, {})
    check("chat queue: kept on disk", [c["id"] for c in saved.get("chats", [])] == [entry.get("id")])
    sent = []
    real_send, real_pause = core._send, core.QUEUE_PAUSE
    core._send = lambda text, extra=(): sent.append((text, list(extra), [a["path"] for a in core.attachments],
                                                    list(core.active_skills)))
    core.QUEUE_PAUSE = 0.0
    try:
        core.set_chat_queue_paused(True)
        core._next_queued_chat(); core._next_queued_chat()
        check("chat queue: paused waits", not sent and len(core.chat_queue) == 1)
        core.set_chat_queue_paused(False)
        core.messages.append({"role": "user", "text": "the chat before"})
        core._next_queued_chat(); core._next_queued_chat()
        check("chat queue: next chat starts in a new chat with its own skills and files",
              len(sent) == 1 and sent[0][0] == "make three views" and set(sent[0][1]) == {"material", "camera"}
              and sent[0][2] == [picture] and sent[0][3] == [] and not core.chat_queue
              and not any(m.get("text") == "the chat before" for m in core.messages), sent)
        check("chat queue: the checked skills are back after", core.active_skills == ["material"])
        # edit, move and hold before it starts
        core.add_attachment(picture)
        core.queue_chat("first wish")
        core.queue_chat("second wish")
        first, second = core.chat_queue[0]["id"], core.chat_queue[1]["id"]
        check("chat queue: edited before it starts", core.edit_queued_chat(first, "first wish, changed",
                                                                         keep_files=[False], keep_skills=[True])
              and core.chat_queue[0]["text"] == "first wish, changed" and core.chat_queue[0]["attachments"] == []
              and core.chat_queue[0]["skills"] == ["material"])
        core.move_queued_chat(second, -1)
        check("chat queue: moved up", [c["id"] for c in core.chat_queue] == [second, first]
              and [c["id"] for c in core._read_json(core.CHAT_QUEUE_FILE, {})["chats"]] == [second, first])
        sent.clear()
        core.messages.clear()
        core.hold_queued_chat(second)
        core._next_queued_chat(); core._next_queued_chat()
        check("chat queue: open for editing, it waits", not sent and len(core.chat_queue) == 2)
        core.hold_queued_chat(None)
        core._next_queued_chat(); core._next_queued_chat()
        check("chat queue: then it starts", len(sent) == 1 and sent[0][0] == "second wish")
        check("chat queue: editing one that started says so", not core.edit_queued_chat(second, "x"))
        check("chat queue: emptied by editing, it is removed", core.edit_queued_chat(first, " ")
              and not core.chat_queue)
    finally:
        core._send, core.QUEUE_PAUSE = real_send, real_pause
    core.queue_chat("one more")
    core.remove_queued_chat(core.chat_queue[0]["id"])
    check("chat queue: removed", not core.chat_queue)
    core.attachments.clear()
    core.active_skills[:] = keep_skills
    core.chat_queue[:] = keep_queue
    core.set_chat_queue_paused(False)
    # Krea: the render's size goes with the request and the result comes back at exactly that size
    krea = mod("studio.krea")
    check("krea: nearest ratio", krea.nearest_ratio(1500, 2000) == "3:4" and krea.nearest_ratio(1920, 1080) == "16:9"
          and krea.nearest_ratio(1080, 1350) == "4:5")
    body = krea.request_body('NANO', "a hand holds it", "https://x/y.png", 1500, 2000)
    check("krea: free model gets the render's size", body["width"] == 1500 and body["height"] == 2000
          and body["aspect_ratio"] == "3:4" and body["resolution"] == "2K" and "Keep the product" in body["prompt"])
    body = krea.request_body('PRECISE', "a hand holds it.", "https://x/y.png", 1500, 2000)
    check("krea: precise model edits in place", body["image_url"] == "https://x/y.png" and "width" not in body
          and "Keep the camera angle" in body["prompt"])
    check("krea: auto picks precise to add, free for a new place, zoom or perspective",
          krea.pick_model('AUTO') == 'PRECISE' and krea.pick_model('AUTO', "/x/room.jpg") == 'NANO'
          and krea.pick_model('AUTO', zoom='FAR') == 'NANO' and krea.pick_model('AUTO', view='ABOVE') == 'NANO'
          and krea.pick_model('PRECISE', "/x/room.jpg") == 'PRECISE')
    body = krea.request_body('NANO', "", "https://x/r.png", 1500, 2000, "https://x/room.png", 'WIDER', 'HIGHER')
    check("krea: environment picture as the second image, framing and height in the prompt, framing free",
          body["image_urls"] == ["https://x/r.png", "https://x/room.png"] and "environment of the reference" in body["prompt"]
          and "Zoom out" in body["prompt"] and "higher camera height" in body["prompt"]
          and krea.zoom_key('SOME') == 'WIDER' and krea.view_key('ABOVE') == 'HIGHER' and krea.zoom_key('x') == 'KEEP'
          and "closer" in krea.full_prompt("x", zoom='CLOSER')
          and "Keep the camera angle" not in body["prompt"], body["prompt"])
    body = krea.request_body('PRECISE', "add a vase", "https://x/r.png", 1500, 2000, "https://x/room.png")
    check("krea: precise takes the environment as a reference", body["reference_images"] == ["https://x/room.png"])
    krea._add_log('NANO', 1500, 2000)
    count, known, _unknown, _credits = krea.month_summary()
    check("krea: what this month cost", count >= 1 and known >= 0.15)
    render = os.path.join(TMP, "Pod_01.png")
    img = bpy.data.images.new("pod", 60, 80)
    img.filepath_raw, img.file_format = render, 'PNG'
    img.save()
    bpy.data.images.remove(img)
    check("krea: the size from the file", tuple(krea.image_size(render)) == (60, 80))
    first = krea.result_path(render)
    check("krea: result next to the render, never over it", first == os.path.join(TMP, "Pod_01_krea_01.png"))
    big = bpy.data.images.new("big", 72, 96)
    big.filepath_raw, big.file_format = first + ".download", 'PNG'
    big.save()
    bpy.data.images.remove(big)
    krea.fit_size(first, 60, 80)
    check("krea: result fitted to the render's pixels", tuple(krea.image_size(first)) == (60, 80)
          and not os.path.exists(first + ".download") and krea.result_path(first).endswith("Pod_01_krea_02.png"))
    try:
        krea.start('PRECISE', "x", render, 60, 80) if not krea.token() else None
        no_token = krea.token() != ""
    except ValueError:
        no_token = True
    check("krea: without a token it says so", no_token)
    # wear: strength, kind and where, also on a layer made by an older version
    ms, presets = mod("studio.matstructures"), mod("studio.matpresets")
    mat = bpy.data.materials.new("Test Wear Kinds")
    shader = presets._principled(mat)
    shader.inputs["Roughness"].default_value = 0.3
    mod("studio.api").wear(mat, scratches=0.6, one_direction=1.0, print_size=2.0, dust_on_top=0.0,
                           **{"from": 0.1, "to": 0.4})
    node = mat.node_tree.nodes[ms.NODE_WEAR]
    check("wear: kinds and height set by name", abs(node.inputs["One Direction"].default_value - 1.0) < 1e-6
          and abs(node.inputs["Print Size"].default_value - 2.0) < 1e-6 and node.inputs["Dust On Top"].default_value == 0.0
          and abs(node.inputs["From"].default_value - 0.1) < 1e-6 and abs(node.inputs["To"].default_value - 0.4) < 1e-6
          and node.inputs["Generated"].is_linked)
    group = node.node_tree
    group[ms.VERSION_KEY] = ms.VERSION - 1          # as if made by the version before
    ms.wear_node(mat)
    check("wear: an older layer is rebuilt inside, its links and values stay",
          group.get(ms.VERSION_KEY) == ms.VERSION and node.node_tree == group
          and shader.inputs["Base Color"].links[0].from_node == node and node.inputs["Generated"].is_linked
          and abs(node.inputs["Scratches"].default_value - 0.6) < 1e-6 and abs(node.inputs["Roughness"].default_value - 0.3) < 1e-6)
    bpy.data.materials.remove(mat)
    # format row: the aspect ratio
    rs = bpy.context.scene.studio
    keep = (bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y)
    bpy.ops.studio.set_aspect(x=3, y=4)
    check("format: 3 : 4 sets the picture", (rs.aspect_x, rs.aspect_y) == (3, 4)
          and bpy.context.scene.render.resolution_x * 4 == bpy.context.scene.render.resolution_y * 3,
          (bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y))
    bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y = keep
    # finish tab: looks saved for every project
    finish = mod("studio.tab_finish")
    rs.look_saturation, rs.look_glow = 0.7, 0.2
    data = finish.capture_look(bpy.context.scene)
    rs.look_saturation, rs.look_glow = 1.0, 0.0
    finish.apply_look(bpy.context.scene, data)
    check("finish: a look comes back", abs(rs.look_saturation - 0.7) < 1e-6 and abs(rs.look_glow - 0.2) < 1e-6)
    bpy.ops.studio.look_reset()


def _raises(func):
    try:
        func()
    except (ValueError, RuntimeError):
        return True
    return False


@section
def krea_plan_scale_mask():
    import json as _json
    import numpy as np
    from mathutils import Vector
    krea, kscene, finish = mod("studio.krea"), mod("studio.krea_scene"), mod("studio.tab_finish")
    core, cli = mod("core"), mod("claude_cli")
    scene = bpy.context.scene
    # the run with the user's Krea connector: no strict config, Krea and ToolSearch allowed, tools deferred
    run = cli.ClaudeRun("/bin/claude", None, cwd=TMP, mcp_config_path="/x/mcp.json", system_prompt="S",
                        connectors=["Krea"])
    allowed = run.cmd[run.cmd.index("--allowedTools") + 1]
    check("krea plan: the run gets the Krea connector", "--strict-mcp-config" not in run.cmd
          and "mcp__claude_ai_Krea" in allowed and "ToolSearch" in run.cmd[run.cmd.index("--tools") + 1]
          and "mcp__ai-studio" in run.cmd[run.cmd.index("--disallowedTools") + 1]
          and run.env.get("ENABLE_TOOL_SEARCH") == "true"
          and "ToolSearch" in run.cmd[run.cmd.index("--append-system-prompt") + 1], run.cmd)
    plain = cli.ClaudeRun("/bin/claude", None, cwd=TMP, mcp_config_path="/x/mcp.json", system_prompt="S")
    check("krea plan: other runs stay strict", "--strict-mcp-config" in plain.cmd
          and "claude_ai" not in plain.cmd[plain.cmd.index("--allowedTools") + 1]
          and plain.env.get("ENABLE_TOOL_SEARCH") == "false")
    keep_queue = list(core.chat_queue)
    core.queue_chat("Refine this render with Krea: a hand", ["finish"], connectors=("Krea",))
    check("krea plan: a queued chat keeps the connector", core.chat_queue[-1].get("connectors") == ["Krea"])
    core.chat_queue[:] = keep_queue
    core._save_chat_queue()
    # a 10 cm product in front of a camera; the render's file name has the camera's name
    render_state = {o.name: o.hide_render for o in scene.objects}
    for obj in list(scene.objects):
        obj.hide_render = True
    hidden_before = set(render_state)
    bpy.ops.mesh.primitive_cube_add(size=0.1, location=(0, 0, 0.05))
    cube = bpy.context.active_object
    cube.name = "Krea Test Pod"
    cam_data = bpy.data.cameras.new("Krea Test Cam")
    cam = bpy.data.objects.new("Krea Test Cam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0, -0.6, 0.05)
    cam.rotation_euler = (1.5708, 0, 0)
    cam_data.lens = 50
    keep_camera = scene.camera
    other = bpy.data.objects.new("Krea Other Cam", bpy.data.cameras.new("Krea Other Cam"))
    scene.collection.objects.link(other)
    scene.camera = other
    scene.render.resolution_x, scene.render.resolution_y = 400, 300
    bpy.context.view_layer.update()            # new objects get their place only with an update
    render = os.path.join(TMP, "Pod_Krea Test Cam_01.png")
    img = bpy.data.images.new("krea render", 400, 300)
    img.filepath_raw, img.file_format = render, 'PNG'
    img.save()
    bpy.data.images.remove(img)
    check("krea scale: camera from the file name", kscene.render_camera(scene, render) == cam)
    info = kscene.product_scale(scene, cam, 400, 300)
    check("krea scale: true size in cm", info and all(abs(v - 10.0) < 0.2 for v in info["size_cm"]), info)
    check("krea scale: share of the picture", info and 0.05 < info["frame"][0] < 0.6 and 0.05 < info["frame"][1] < 0.8
          and info.get("px_per_cm", 0) > 1, info)
    text = kscene.scale_text(info, 400, 300)
    check("krea scale: the sentence for the prompt", "10 × 10 × 10 cm" in text and "19 cm" in text
          and "1.9 times" in text and "% of the image width" in text, text)
    wide = kscene.scale_text(info, 400, 300, framing_kept=False)
    check("krea scale: zooming out drops the picture shares", "of the width" not in wide and "10 × 10 × 10 cm" in wide)
    other_mesh = bpy.data.objects.new("Krea Test Bar", cube.data.copy())
    scene.collection.objects.link(other_mesh)
    other_mesh.location = (0.5, 0, 0)
    other_mesh.scale = (4, 1, 1)
    bpy.context.view_layer.update()
    wide_info = kscene.product_scale(scene, cam, 400, 300)
    kscene.mark_product(scene, [cube])
    marked_info = kscene.product_scale(scene, cam, 400, 300)
    check("krea product: the remembered selection is measured, not the rest",
          kscene.is_marked(scene) and kscene.product_objects(scene) == [cube] and wide_info["size_cm"][0] > 30
          and abs(marked_info["size_cm"][0] - 10.0) < 0.2, (wide_info, marked_info))
    bpy.data.objects.remove(other_mesh)
    body = krea.request_body('PRECISE', "a hand holds it", "https://x/r.png", 400, 300, scale=text,
                             mask_url="https://x/m.png")
    check("krea: precise gets the mask and the scale", body["mask_url"] == "https://x/m.png" and text in body["prompt"]
          and body["prompt"].endswith(krea.KEEP.strip() + krea.KEEP_FRAME))
    nano = krea.request_body('NANO', "a hand", "https://x/r.png", 400, 300, scale=text, mask_url="https://x/m.png")
    check("krea: the free model has no mask", "mask_url" not in nano and text in nano["prompt"])
    # the mask: product black, the rest white, at the render's size, nothing else changed
    engine, res = scene.render.engine, (scene.render.resolution_x, scene.render.resolution_y)
    mask_ok = False
    try:
        mask = kscene.render_mask(scene, render, os.path.join(TMP, "mask.png"))
        image = bpy.data.images.load(mask)
        px = np.empty(400 * 300 * 4, dtype=np.float32)
        image.pixels.foreach_get(px)
        size = tuple(image.size)
        bpy.data.images.remove(image)
        center = px[((150 * 400) + 200) * 4]
        corner = px[0]
        mask_ok = size == (400, 300) and center < 0.1 and corner > 0.9
        detail = (size, center, corner)
    except Exception as exc:          # e.g. no GPU for Workbench in this Blender
        detail = repr(exc)
    check("krea mask: product black, rest white, render size", mask_ok, detail)
    check("krea mask: the scene is as it was", scene.render.engine == engine and scene.camera == other
          and (scene.render.resolution_x, scene.render.resolution_y) == res
          and all(o.hide_render for o in scene.objects if o.name in hidden_before))
    # prepare for Claude: uploads go to the connector's upload URL, the input is ready for generate_image
    uploads = []
    keep_upload, keep_current = krea.upload_to, finish.current_image
    krea.upload_to = lambda url, path, fields=None: uploads.append(path) or "https://assets/%d.png" % len(uploads)
    finish.current_image = lambda _scene: render
    wm = bpy.context.window_manager
    wm.studio_krea_model, wm.studio_krea_refs = 'AUTO', ""
    wm.studio_krea_zoom = wm.studio_krea_view = 'KEEP'
    try:
        p = finish.prepare(scene, "A hand holds the pod", "https://upload/presigned", protect=True)
        check("krea plan: prepare uploads render and mask", p["model"] == "ideogram/ideogram-4.5-precise"
              and p["input"]["image_url"] == "https://assets/1.png" and p["input"].get("mask_url") == "https://assets/2.png"
              and "10 × 10 × 10 cm" in p["input"]["prompt"] and uploads[0] == render, p)
        uploads.clear()
        p = finish.prepare(scene, "A hand holds the pod", "https://upload/presigned", protect=False)
        check("krea plan: a hand on the product goes without a mask", "mask_url" not in p["input"] and len(uploads) == 1)
        api = mod("studio.api")
        api.krea_quote(163, 2946.9)
        check("krea plan: the price shows before it is made", krea.busy() and krea.job["quote"] == [163, 2946.9])
        check("krea plan: save needs a real image URL", _raises(lambda: api.krea_save("not a url")))
        api.krea_failed("content_policy")
        check("krea plan: a failure frees the tab", not krea.busy() and krea.job["text"] == "content_policy")
    finally:
        krea.upload_to, finish.current_image = keep_upload, keep_current
    krea._add_log('PRECISE', 400, 300, 163, 2783.9, via="plan")
    count, _known, _unknown, credits = krea.month_summary()
    check("krea plan: credits this month and what is left", credits >= 163 and krea.last_balance() == 2783.9)
    with open(krea._log_file(), encoding="utf-8") as f:
        entries = _json.load(f)
    check("krea plan: logged as plan", entries[-1]["via"] == "plan" and entries[-1]["credits"] == 163)
    finish_skill = open(os.path.join(os.path.dirname(core.__file__), "skills_default", "finish.md"), encoding="utf-8").read()
    check("krea plan: the skill uses the connector, price first", "krea_prepare" in finish_skill
          and "preflight=true" in finish_skill and "krea_quote" in finish_skill and "krea_save" in finish_skill)
    kscene.mark_product(scene, [])
    check("krea product: forgotten again", not kscene.is_marked(scene))
    scene.camera = keep_camera
    for obj in (cube, cam, other):
        bpy.data.objects.remove(obj)
    for name, value in render_state.items():
        if scene.objects.get(name) is not None:
            scene.objects[name].hide_render = value


@section
def finish_image_adjust():
    import numpy as np
    edit = mod("studio.image_edit")
    rs = bpy.context.scene.studio
    # dark gray, light gray, gray, a red: one row
    px = np.array([0.1, 0.1, 0.1, 1, 0.9, 0.9, 0.9, 1, 0.5, 0.5, 0.5, 1, 0.8, 0.2, 0.2, 1], dtype=np.float32)
    zero = {k: 0.0 for k in edit.KEYS}
    check("adjust: nothing set, nothing changes", np.allclose(edit.apply(px, zero), px, atol=1e-5))
    out = edit.apply(px, dict(zero, edit_exposure=1.0)).reshape(-1, 4)
    check("adjust: exposure brightens", out[0, 0] > 0.12 and out[2, 0] > 0.6, out[:, 0])
    out = edit.apply(px, dict(zero, edit_saturation=-100.0)).reshape(-1, 4)
    check("adjust: saturation −100 is gray", abs(out[3, 0] - out[3, 1]) < 1e-4 and abs(out[3, 1] - out[3, 2]) < 1e-4)
    out = edit.apply(px, dict(zero, edit_temperature=60.0)).reshape(-1, 4)
    check("adjust: warmer is more red than blue", out[2, 0] > out[2, 2])
    out = edit.apply(px, dict(zero, edit_highlights=-100.0)).reshape(-1, 4)
    check("adjust: highlights darken the bright parts only", out[1, 0] < 0.88 and abs(out[0, 0] - 0.1) < 0.01, out[:, 0])
    out = edit.apply(px, dict(zero, edit_shadows=100.0)).reshape(-1, 4)
    check("adjust: shadows lift the dark parts only", out[0, 0] > 0.12 and abs(out[1, 0] - 0.9) < 0.02, out[:, 0])
    out = edit.apply(px, dict(zero, edit_contrast=50.0)).reshape(-1, 4)
    check("adjust: contrast spreads light and dark", out[0, 0] < 0.1 and out[1, 0] > 0.9 and abs(out[2, 0] - 0.5) < 0.01)
    check("adjust: alpha stays", np.allclose(out[:, 3], 1.0))
    # files: the preview copy (in a timer) and the saved image next to the render
    render = os.path.join(TMP, "Adjust_Cam_01.png")
    img = bpy.data.images.new("adjust", 40, 30)
    img.pixels.foreach_set(np.tile(np.array([0.5, 0.4, 0.3, 1.0], dtype=np.float32), 40 * 30))
    img.filepath_raw, img.file_format = render, 'PNG'
    img.save()
    bpy.data.images.remove(img)
    scene = bpy.context.scene
    check("adjust: preview is the render while nothing is set", edit.preview_file(scene, render) == render)
    rs.edit_exposure = 0.7
    first = edit.preview_file(scene, render)
    edit._make_preview()
    shown = edit.preview_file(scene, render)
    check("adjust: preview is an adjusted copy", first == render and shown != render and os.path.exists(shown))
    target = edit.save(scene, render)
    check("adjust: saved next to the render, full size, render untouched",
          target == os.path.join(TMP, "Adjust_Cam_01_edit_01.png") and tuple(mod("studio.krea").image_size(target)) == (40, 30)
          and edit.edit_path(render).endswith("_edit_02.png"),
          (target, mod("studio.krea").image_size(target), edit.edit_path(render)))
    saved = bpy.data.images.load(target)
    value = saved.pixels[0]
    bpy.data.images.remove(saved)
    check("adjust: the saved image is brighter", value > 0.55, value)
    bpy.ops.studio.edit_reset()
    check("adjust: reset", edit.is_neutral(edit.values(scene)))
    check("adjust: look, Krea and adjustments in Finish, not in the camera tab",
          "draw_look(layout, context)" in open(mod("studio.tab_finish").__file__).read()
          and "draw_look" not in open(mod("studio.tab_camera").__file__).read())


@section
def variants_size_material_open():
    ab, size = mod("studio.variants_ab"), mod("studio.render_size")
    tm, krea, finish = mod("studio.tab_material"), mod("studio.krea"), mod("studio.tab_finish")
    scene = bpy.context.scene
    # material variants: which material sits on which object
    m1, m2 = bpy.data.materials.new("Var Red"), bpy.data.materials.new("Var Blue")
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(3, 3, 0))
    cube = bpy.context.active_object
    cube.name = "Var Cube"
    cube.data.materials.append(m1)
    ab.add(scene, 'MATERIAL')
    cube.material_slots[0].material = m2
    ab.switch(scene, 'MATERIAL', 0)
    check("variants: material A comes back", cube.material_slots[0].material == m1 and ab.names(scene, 'MATERIAL') == ["A", "B"])
    ab.switch(scene, 'MATERIAL', 1)
    check("variants: material B comes back", cube.material_slots[0].material == m2 and ab.active(scene, 'MATERIAL') == 1)
    ab.remove(scene, 'MATERIAL')
    check("variants: removing down to one clears them", ab.names(scene, 'MATERIAL') == [] and cube.material_slots[0].material == m1)
    # camera variants: place and lens of the camera looked through
    cam = scene.camera
    if cam is not None:
        keep_matrix, keep_lens = cam.matrix_world.copy(), cam.data.lens
        ab.add(scene, 'CAMERA')
        cam.data.lens = 135
        cam.location.x += 1.0
        bpy.context.view_layer.update()
        ab.switch(scene, 'CAMERA', 0)
        bpy.context.view_layer.update()
        check("variants: camera A brings place and lens back",
              abs(cam.data.lens - keep_lens) < 1e-4 and (cam.matrix_world.translation - keep_matrix.translation).length < 1e-4)
        ab.switch(scene, 'CAMERA', 1)
        check("variants: camera B again", abs(cam.data.lens - 135) < 1e-4)
        ab.remove(scene, 'CAMERA')
        cam.matrix_world, cam.data.lens = keep_matrix, keep_lens
    # render size: 1K/2K/4K on the long side, it stays when the ratio changes
    rq = scene.studio
    keep = (scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage, rq.quality)
    rq.aspect_x, rq.aspect_y = 3, 4
    size.set_long_side(scene, 2048)
    check("size: 2K on the long side", abs(size.long_side(scene) - 2048) <= 4 and size.chosen(scene) == "2K", size.pixels(scene))
    rq.aspect_x, rq.aspect_y = 16, 9
    check("size: stays 2K when the ratio changes", abs(size.long_side(scene) - 2048) <= 4
          and size.pixels(scene)[0] > size.pixels(scene)[1], size.pixels(scene))
    ab.add(scene, 'RENDER')
    rq.quality = 'LOW' if keep[3] == 'HIGH' else 'HIGH'
    size.set_long_side(scene, 4096)
    ab.switch(scene, 'RENDER', 0)
    check("variants: render A brings quality and size back", rq.quality == keep[3] and size.chosen(scene) == "2K")
    ab.remove(scene, 'RENDER')
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage, rq.quality = keep
    scene.pop(size.KEY, None)
    # a library material: open it to change it, then give it to the object clicked
    bpy.context.view_layer.objects.active = cube
    cube.select_set(True)
    tm.open_material(bpy.context, m2)
    check("material: a library click opens it, also when another is on the object",
          tm.current_material(bpy.context) == m2 and cube.active_material == m1)
    check("material: given to the clicked slot", tm.give_material(cube, 0, m2) and cube.material_slots[0].material == m2)
    bpy.ops.mesh.primitive_plane_add(size=0.2, location=(5, 5, 0))
    plane = bpy.context.active_object
    check("material: an object without slots gets one", tm.give_material(plane, 3, m1) and plane.material_slots[0].material == m1)
    check("material: another object, its own material again", tm.current_material(bpy.context) == m1)
    for obj in (cube, plane):
        bpy.data.objects.remove(obj)
    # Krea: pressed buttons and the typed wish, several pictures, the place or a reference
    wm = bpy.context.window_manager
    wm.studio_krea_hand, wm.studio_krea_prompt = True, "on a wooden table"
    text = finish.wish(wm)
    check("krea: buttons and text make the wish", text.startswith("Add a realistic adult hand") and
          text.endswith("on a wooden table.") and not wm.studio_krea_protect)
    wm.studio_krea_hand, wm.studio_krea_prompt, wm.studio_krea_protect = False, "", True
    body = krea.request_body('PRECISE', "x", "https://r", 400, 300, ["https://a", "https://b", "https://c", "https://d"],
                             mask_url="https://m", room=False)
    check("krea: pictures as references (three with a mask), not as a place",
          body["reference_images"] == ["https://a", "https://b", "https://c"] and krea.REF_TEXT.strip() in body["prompt"])
    nano = krea.request_body('NANO', "x", "https://r", 400, 300, "https://a\nhttps://b")
    check("krea: the free model gets render and pictures", nano["image_urls"] == ["https://r", "https://a", "https://b"]
          and krea.ENV_TEXT.strip() in nano["prompt"])
    check("krea: a place picture with Room needs the free model", krea.pick_model('AUTO', True) == 'NANO'
          and krea.pick_model('AUTO', False) == 'PRECISE')
    bpy.ops.studio.krea_frame(target="zoom", value='CLOSER')
    first = wm.studio_krea_zoom
    bpy.ops.studio.krea_frame(target="zoom", value='CLOSER')
    check("krea: camera buttons switch on and off", first == 'CLOSER' and wm.studio_krea_zoom == 'KEEP')


@section
def shots_presets_check():
    scene = bpy.context.scene
    rs = scene.studio
    io = mod("studio.presets_io")
    env = mod("studio.environment")
    env.save_scene(scene, "IO Scene")
    data = io.collect()
    check("all presets in one file", "IO Scene" in data["scene"] and isinstance(data["light"], dict))
    env.delete_scene("IO Scene")
    counts = io.merge(data)
    check("import brings them back", "IO Scene" in env.saved_scenes() and counts.get("scene"), counts)
    try:
        io.merge({"something": 1})
        check("a foreign file is refused", False)
    except ValueError:
        check("a foreign file is refused", True)
    env.delete_scene("IO Scene")
    shots = mod("studio.shots")
    problems = [t for t, _i in shots.render_problems(scene)]
    check("render check runs", isinstance(problems, list), problems)
    img = bpy.data.images.new("gone", 4, 4)
    img.source = 'FILE'
    img.filepath = "/nowhere/gone.png"
    img.use_fake_user = True
    check("render check: missing texture", any("gone" in t for t, _i in shots.render_problems(scene)))
    bpy.data.images.remove(img)
    cam = next(o for o in scene.objects if o.type == 'CAMERA')
    scene.camera = cam
    bpy.ops.studio.shot_add()
    shot = rs.shots[-1]
    shot.scene_preset, shot.variant, shot.quality = "Studio White", "", 'LOW'
    before = len(rs.items)
    runner = mod("studio.runner")
    studio_prefs = runner.prefs()
    auto, start = studio_prefs.auto_render, runner.RUN.start
    studio_prefs.auto_render = False
    runner.RUN.start = lambda *a, **k: None   # only the hand-over is tested here, not the render
    try:
        shots.render_shots(scene, [len(rs.shots) - 1])
        shots._next_shot()
    finally:
        studio_prefs.auto_render = auto
        runner.RUN.start = start
    check("shot: preset applied and camera queued", rs.scene_preset == "Studio White" and len(rs.items) == before + 1
          and rs.items[-1].camera == cam and rs.items[-1].quality == 'LOW', (rs.scene_preset, len(rs.items)))
    runner.RUN.cancel()
    shots._run["todo"].clear()
    shots._run["busy"] = False
    while len(rs.items) > before:
        rs.items.remove(len(rs.items) - 1)
    rs.shots.remove(len(rs.shots) - 1)
    for o in mod("studio.lights").scene_lights(scene):
        bpy.data.objects.remove(o)
    rs.floor_mode = 'NONE'
    rs.background_mode = 'NORMAL'
    rs.look_glow = rs.look_contrast = rs.look_vignette = 0.0


@section
def other_addons():
    addons = mod("studio.addons")
    real = addons._enabled_modules
    addons._enabled_modules = lambda: ["physical-starlight-atmosphere", "bl_ext.blender_org.tri_lighting",
                                       "bl_ext.user_default.light_wrangler", "something_else"]
    try:
        kinds = {a["key"]: a["kind"] for a in addons.found()}
        check("other add-ons: used and covered ones found", kinds == {"psa": "use", "tri": "covered",
                                                                      "lightwrangler": "use"}, kinds)
        names = [s["name"] for s in addons.summary()]
        check("other add-ons: listed for Claude", "Light Wrangler" in names and len(names) == 3, names)
        text = mod("studio").claude_tools._context({"kind": "light"})
        check("other add-ons: in the light context", "Other add-ons" in text and "psa.add_atmosphere" in text)
    finally:
        addons._enabled_modules = real
    check("other add-ons: none here, nothing listed", isinstance(addons.found(), list))
    check("refactor: ui and core still export their names",
          all(hasattr(mod("ui"), n) for n in ("sync_skill_operators", "_setup_visible", "CLAUDE_PT_chat", "classes"))
          and all(hasattr(mod("core"), n) for n in ("new_chat", "load_chat", "versions", "goto_version", "approve")))


@section
def queue_wait_tool():
    tools = mod("studio").claude_tools
    text = tools._queue({"action": "wait"})
    check("queue 'wait' answers at once when idle", isinstance(text, str) and text.startswith("Queue done"), text)


@section
def settings_for_earlier_users():
    settings, prefs = mod("settings"), mod("preferences").get_prefs()
    keep = (prefs.settings_version, prefs.backup_dir, prefs.main_suffix, prefs.studio.low_dir)
    chats = os.path.join(mod("core").workdir(), "chats.json")
    made = not os.path.exists(chats)
    if made:
        open(chats, "w").write("[]")
    try:
        prefs.settings_version = 0
        settings.apply_once()
        check("earlier users keep their folder names", (prefs.backup_dir, prefs.main_suffix, prefs.studio.low_dir)
              == ("autosave", "_now", "preview") and prefs.settings_version == settings.VERSION)
        prefs.property_unset("project_folders")
        prefs.autosave = False   # chosen by the user: stays
        prefs.settings_version = 1
        settings.apply_once()
        check("earlier users keep project folders, a choice stays", prefs.project_folders and not prefs.autosave,
              (prefs.project_folders, prefs.autosave))
        prefs.property_unset("project_folders")
        prefs.property_unset("autosave")
        check("new users: no project folders, backups on (good defaults, fewer questions)",
              not prefs.project_folders and prefs.autosave)
    finally:
        prefs.settings_version, prefs.backup_dir, prefs.main_suffix, prefs.studio.low_dir = keep
        if made:
            os.remove(chats)


@section
def prompt_from_studio_tab():
    core = mod("core")
    before = len(core._prompt_queue)
    sent = core.studio_prompt('LIGHT', "softer key light")
    item = core._prompt_queue[-1] if len(core._prompt_queue) > before else None
    check("tab wish goes out with the tab's skills", sent and item and item[0] == "softer key light"
          and {"light", "world"} <= set(item[1]), item)
    core._prompt_queue[:] = core._prompt_queue[:before]
    core.studio_prompts.clear()


@section
def camera_back():
    import time
    cameras = mod("studio").cameras
    scene = bpy.context.scene
    cam = scene.camera
    cam.pop(cameras.HISTORY_KEY, None)
    cameras._end_moves()         # earlier tests moved the camera; their move timer never ran
    cameras.seed(scene)          # in Blender a timer does this at start
    start = cam.location.copy()
    cam.location.x += 1          # an accidental move …
    bpy.context.view_layer.update()
    cam.location.x += 1          # … in several steps
    bpy.context.view_layer.update()
    time.sleep(cameras.MOVE_PAUSE)
    cameras._end_moves()         # the timer that ends a move (no timers in background mode)
    steps = len(cameras.history(cam))
    cameras.go_back(cam)
    check("camera back after moving", steps == 1 and (cam.location - start).length < 1e-4,
          (steps, tuple(cam.location), tuple(start)))


@section
def draft_before_the_render():
    runner = mod("studio").runner
    scene = small_scene()
    scene.render.resolution_x, scene.render.resolution_y, scene.cycles.samples = 1280, 960, 40
    log_path = os.path.join(runner.jobs_dir(), "..", "queue.log")
    mark = log_mark(runner)
    rq = scene.studio
    rq.items.clear()
    rq.quality = 'HIGH'
    bpy.ops.studio.add(quality='HIGH')
    seen, live = [], []
    runner.on_render_saved.append(lambda path, *_a: seen.append(path))
    runner.on_live_image.append(live.append)
    try:
        runner.RUN.start(scene)
    finally:
        runner.on_render_saved.clear()
        runner.on_live_image.remove(live.append)
    log = log_since(log_path, mark)
    check("draft first, then the image", "draft 0 after" in log and seen and os.path.exists(seen[0])
          and not [n for n in os.listdir(runner.jobs_dir()) if n.startswith("draft_")], log[-600:])
    check("live preview: the draft, then the image", len(live) >= 2 and "draft_" in live[0]
          and live[-1] == seen[0], live)
    worker_log = open(runner.worker_log_path(), encoding="utf-8").read()
    check("worker on the GPU without set-up devices", "W devices: METAL" in worker_log, worker_log[:300])
    image = runner.show_in_preview(seen[0])
    size = tuple(image.preview.image_size)
    check("sharp preview, not Blender's 256 px", 256 < max(size) <= runner.PREVIEW_SIZE and size[0] * 3 == size[1] * 4,
          size)
    rq.quality = 'LOW'
    small_scene()


@section
def worker_matches_blender():
    """The queue's image is Blender's own render: color management (view transform, look,
    exposure), Cycles settings and the file format come through the worker unchanged."""
    import numpy as np
    runner = mod("studio").runner
    scene = small_scene()
    scene.cycles.samples, scene.cycles.use_denoising = 64, True
    scene.render.film_transparent = False
    rq = scene.studio
    keep = (scene.view_settings.view_transform, scene.view_settings.look, scene.view_settings.exposure,
            rq.still_format, rq.quality)
    rq.quality, rq.background_mode = 'HIGH', 'NORMAL'

    def mean(path):
        image = bpy.data.images.load(path)
        pixels = np.empty(len(image.pixels), dtype=np.float32)
        image.pixels.foreach_get(pixels)
        bpy.data.images.remove(image)
        return pixels.reshape(-1, 4)[:, :3].mean(axis=0)

    try:
        for view, look, exposure, fmt in (("Standard", "None", 0.0, 'PNG'), ("AgX", "AgX - Punchy", 0.7, 'PNG'),
                                          ("AgX", "None", 0.0, 'EXR')):
            scene.view_settings.view_transform, scene.view_settings.look = view, look
            scene.view_settings.exposure = exposure
            rq.still_format = fmt
            ext = ".exr" if fmt == 'EXR' else ".png"
            local = os.path.join(TMP, "blender_%s_%s%s" % (view, fmt, ext))
            image_settings = scene.render.image_settings
            image_settings.file_format = 'OPEN_EXR' if fmt == 'EXR' else 'PNG'
            image_settings.color_mode = 'RGB'
            scene.render.filepath = local
            bpy.ops.render.render(write_still=True)
            rq.items.clear()
            bpy.ops.studio.add(quality='HIGH')
            seen = []
            runner.on_render_saved.append(lambda path, *_a: seen.append(path))
            try:
                runner.RUN.start(scene)
            finally:
                runner.on_render_saved.clear()
            ok = seen and seen[0].endswith(ext)
            diff = float(np.abs(mean(local) - mean(seen[0])).max()) if ok else 1.0
            check("queue = Blender's render: %s %s %s %s" % (view, look, exposure, fmt), ok and diff < 0.01,
                  (diff, seen))
    finally:
        (scene.view_settings.view_transform, scene.view_settings.look, scene.view_settings.exposure,
         rq.still_format, rq.quality) = keep
        small_scene()


@section
def english_by_default():
    """The add-on speaks English, also in a German Blender, unless German Interface is on."""
    i18n, prefs = mod("i18n"), mod("preferences").get_prefs()
    view = bpy.context.preferences.view
    keep = (view.language, view.use_translate_interface, prefs.language)
    try:
        view.language, view.use_translate_interface = 'de_DE', True
        prefs.language = 'EN'
        check("English in a German Blender", i18n.tr("Set Up Everything") == "Set Up Everything"
              and bpy.app.translations.pgettext_iface("Renders", "AI_Studio") == "Renders")
        prefs.language = 'DE'
        check("Deutsch: German", i18n.tr("Set Up Everything") == "Alles einrichten")
        view.language = 'en_US'
        check("Deutsch also in an English Blender (the add-on's own texts)",
              i18n.tr("Set Up Everything") == "Alles einrichten")
    finally:
        prefs.language = keep[2]
        view.language, view.use_translate_interface = keep[0], keep[1]


@section
def smooth_progress():
    """Cycles reports only every 30-80 s in the background: the bar moves on in between."""
    runner = mod("studio").runner
    run = runner.Runner()
    now = time.monotonic()
    run.current = {"uid": 1, "camera": "x", "path": "", "frames": 1, "started": now - 20, "sampling": now - 10}
    run.sample_factor = 0.2
    run.current["est"] = (now - 10, 0.2, 20.0)     # 20 % ten seconds ago, Cycles said 20 s left
    first = run.render_progress
    check("bar moves on between reports", 0.45 < first < 0.65, first)
    run.current["est"] = (time.monotonic(), 0.3, 40.0)   # a slower report: never back
    run.sample_factor = 0.3
    check("bar never goes back", run.render_progress >= first, run.render_progress)
    run.current["est"] = (now - 1000, 0.3, 5.0)
    check("bar waits short of the end", run.render_progress <= 0.99)
    run.sample_factor = 1.0
    check("done: full", run.render_progress == 1.0)
    run.current.update(est=(time.monotonic(), 0.5, 1.0), shown=0.0)
    run.sample_factor = 0.5
    left = run.current_left()
    check("time left from Cycles' estimate", abs(left - 3.0) < 0.5, left)


@section
def short_names():
    import importlib
    naming, core = importlib.import_module(PKG + ".naming"), mod("core")
    check("first one gets _01", naming.numbered("Ortho Top", ["Cube"]) == "Ortho Top_01")
    check("next free after the highest", naming.numbered("Ortho Top", ["Ortho Top_01", "Ortho Top_03"])
          == "Ortho Top_04")
    check("Blender's .001 and our own number count", naming.numbered("Key_02", ["Key", "Key.004"]) == "Key_05")
    check("three digits only past 99", naming.numbered("A", ["A_99"]) == "A_100")
    cams = mod("studio").cameras
    scene = bpy.context.scene
    prefs_studio = mod("studio").runner.prefs()
    prefs_studio.name_scheme = True
    cam = cams._new_camera(scene, bpy.context.view_layer, "Ortho Top")
    check("new camera: in the scheme", cam.name == "P_top_01" and cam.data.name == "P_top_01", cam.name)
    bpy.data.objects.remove(cam)
    check("chat title: a few words", core.short_title("Mach bitte die Kamera frontal und rendere eine Vorschau")
          == "Mach bitte die Kamera frontal", core.short_title("Mach bitte die Kamera frontal und rendere"))
    check("chat title: a number when taken", core.short_title("Licht weich", {"Licht weich"}) == "Licht weich_02")
    studio_core = mod("studio").core
    folder = tempfile.mkdtemp(dir=TMP)
    check("render files with _01", studio_core.next_path(folder, "Stuhl_Front").endswith("Stuhl_Front_01.png"))

    # Names of older versions and Blender's ".001" are made short (on load and after Claude's answers).
    check("scheme: words", naming.role_of("AS_camera Ortho Oben.001", ["AS_camera"]) == "top"
          and naming.role_of("Packshot Front") == "packshot-front" and naming.role_of("") == "view")
    check("tidy: prefix and .001 go", naming.tidy(["Ortho Front", "AS_camera View", "AS_camera View.001", "AS_1",
                                                  "AS_camera View Alt"], ["AS_camera", "AS"], "Camera")
          == {"AS_camera View": "View_01", "AS_camera View.001": "View_02", "AS_1": "Camera_01",
              "AS_camera View Alt": "View Alt_01"})
    before = {o: (o.name, o.data.name if o.data else None) for o in bpy.data.objects}   # for the other sections
    made = []
    for name, kind in (("AS_camera View", 'PERSP'), ("AS_camera View.001", 'PERSP'), ("Ortho Oben", 'ORTHO'),
                       ("detail pod", 'PERSP'), ("O_front_01", 'ORTHO')):
        data = bpy.data.cameras.new(name)
        data.type = kind
        made.append(bpy.data.objects.new(name, data))
        scene.collection.objects.link(made[-1])
    renamed = cams.tidy_names()
    check("scheme in the file: P_/O_, the view, a number; names you gave stay", [o.name for o in made] ==
          ["P_top_01", "P_top_02", "Ortho Oben", "detail pod", "O_front_01"]
          and made[1].data.name == "P_top_02", [o.name for o in made])
    made[4].data.type = 'PERSP'
    cams.tidy_names()
    check("camera switched to perspective: O_ becomes P_", made[4].name == "P_front_01", made[4].name)
    check("rename in the studio: the word is enough", cams.rename(made[3], "Seite") == "P_side_01"
          and made[3].data.name == "P_side_01")
    check("rename: a name in the scheme stays", cams.rename(made[3], "P_side_07") == "P_side_07")
    made[3].studio_camera.short_name = "detail"
    check("rename field of the Render tab", made[3].name == "P_detail_01", made[3].name)
    scene.camera = made[1]
    bpy.ops.studio.duplicate_camera()
    copy = scene.camera
    check("duplicate camera: next number, not .001", copy.name == "P_top_03" and copy.data.name == "P_top_03",
          copy.name)
    key = bpy.data.objects.new("Key", bpy.data.lights.new("Key", 'AREA'))
    light = mod("studio").lights.duplicate_light(scene, key)
    check("duplicate light: A_key_01, not .001", light.name == "A_key_01", light.name)
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", 'SUN'))
    scene.collection.objects.link(sun)
    cams.tidy_names()
    check("sun: D_", sun.name == "D_sun_01", sun.name)
    made.append(sun)
    prefs_studio.name_scheme = False
    plain = bpy.data.objects.new("View.001", bpy.data.cameras.new("View.001"))
    scene.collection.objects.link(plain)
    cams.tidy_names()
    check("scheme off: only .001 is fixed", plain.name == "View_01" and made[3].name == "P_detail_01", plain.name)
    made.append(plain)
    # collections of older versions lose their prefix
    for old in ("AS_camera", "AS_camera | Packshot", "AS_archiv V2", "AS_set"):
        bpy.data.collections.new(old)
    cams.rename_old_collections()
    check("collections without AS_", all(n in bpy.data.collections for n in
                                         ("Camera", "Camera | Packshot", "Archiv V2", "Set")),
          [c.name for c in bpy.data.collections])
    for name in ("Camera | Packshot", "Archiv V2", "Set"):
        bpy.data.collections.remove(bpy.data.collections[name])
    # 1 mm steps
    rna = bpy.types.STUDIO_Settings.bl_rna.properties if hasattr(bpy.types, "STUDIO_Settings") else scene.studio.bl_rna.properties
    check("camera lengths in 1 mm steps", all(abs(rna[k].step - 0.05) < 1e-6 for k in
          ("orbit_distance", "orbit_height", "orbit_side_offset", "cam_ortho_scale", "cam_focus_distance",
           "cam_clip_start", "cam_clip_end", "light_orbit_distance")))
    scene.camera = made[2]
    scene.studio.cam_ortho_scale = 1.234
    check("ortho scale field sets the camera", abs(made[2].data.ortho_scale - 1.234) < 1e-6)
    for obj in made + [copy, light, key]:
        bpy.data.objects.remove(obj)
    prefs_studio.name_scheme = False
    for obj, (name, data_name) in before.items():
        obj.name = name
        if obj.data is not None:
            obj.data.name = data_name


@section
def product_render_setup():
    rs = mod("studio").render_setup
    scene = bpy.context.scene
    before = (scene.render.engine, scene.cycles.samples, scene.view_settings.view_transform,
              scene.unit_settings.length_unit, scene.cycles.max_bounces)
    cprefs = bpy.context.preferences.addons["cycles"].preferences
    device_before = cprefs.compute_device_type
    scene.render.engine = 'BLENDER_EEVEE' if 'BLENDER_EEVEE' in [i.identifier for i in
                                                                 scene.render.bl_rna.properties["engine"].enum_items] else 'BLENDER_WORKBENCH'
    start = (scene.render.engine, scene.cycles.samples, scene.view_settings.view_transform,
             scene.unit_settings.length_unit, scene.cycles.max_bounces)
    said = rs.apply(scene)
    check("product setup: Cycles, GPU, AgX, mm, denoise", scene.render.engine == 'CYCLES'
          and scene.cycles.device == 'GPU' and scene.view_settings.view_transform == 'AgX'
          and scene.cycles.use_denoising
          and scene.cycles.samples == 512 and scene.cycles.max_bounces == 12, said)
    check("product setup: Metal for Cycles on this Mac", cprefs.compute_device_type == 'METAL'
          and any(d.use and d.type != 'CPU' for d in cprefs.devices), cprefs.compute_device_type)
    check("product setup explains itself", len(said) >= 6, said)
    rs.apply(scene)   # pressed twice: Undo still goes back to the very first state
    undone = rs.undo(scene)
    now = (scene.render.engine, scene.cycles.samples, scene.view_settings.view_transform,
           scene.unit_settings.length_unit, scene.cycles.max_bounces)
    check("undo: everything as before", undone and now == start and cprefs.compute_device_type == device_before
          and not rs.is_set(scene), (undone, now, start, cprefs.compute_device_type, device_before))
    scene.render.engine, scene.cycles.samples, scene.view_settings.view_transform, \
        scene.unit_settings.length_unit, scene.cycles.max_bounces = before


@section
def worker_reloads_the_file():
    import time
    runner = mod("studio").runner
    scene = small_scene()
    worker = runner.Worker()
    try:
        worker.start(scene, 901, "Camera")
        pid = worker.proc.pid
        worker.start(scene, 902, "Camera")   # the scene changed: the same Blender opens a fresh copy
        end = time.monotonic() + 60
        text = ""
        while time.monotonic() < end and text.count("W loaded") < 2:
            time.sleep(0.3)
            text = open(runner.worker_log_path(), encoding="utf-8", errors="replace").read()
        check("worker opens new copies itself", text.count("W loaded") == 2 and worker.proc.pid == pid
              and worker.alive(), text[-400:])
    finally:
        worker.stop()


@section
def material_groups():
    tab = mod("studio").tab_material
    material = bpy.data.materials.new("Group Test")
    objects = []
    for i in range(3):
        mesh = bpy.data.meshes.new("gt%d" % i)
        obj = bpy.data.objects.new("gt%d" % i, mesh)
        bpy.context.scene.collection.objects.link(obj)
        mesh.materials.append(material)
        objects.append(obj)
    for obj in objects[:2]:
        obj[tab.GROUP_KEY] = "g1"   # linked with the eyedropper
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.studio.material_duplicate()
    a, b, c = (o.active_material for o in objects)
    check("duplicate changes the whole group", a is b and a is not material and c is material)
    bpy.ops.studio.material_unlink()
    check("unlink takes one object out", objects[0].active_material is not b
          and tab.GROUP_KEY not in objects[0] and objects[1].active_material is b)
    for obj in objects:
        bpy.data.objects.remove(obj)


@section
def library_click_switches_everywhere():
    old, new = bpy.data.materials.new("Holz A"), bpy.data.materials.new("Holz B")
    other = bpy.data.materials.new("Alu")
    objects = []
    for i in range(3):
        obj = bpy.data.objects.new("sw%d" % i, bpy.data.meshes.new("sw%d" % i))
        bpy.context.scene.collection.objects.link(obj)
        obj.data.materials.append(other if i == 2 else old)
        objects.append(obj)
    objects[1].data.materials.append(old)   # a second slot with the same material
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.studio.material_assign(name=new.name)
    slots = [s.material for o in objects for s in o.material_slots]
    check("library click switches every object with the old material",
          slots == [new, new, new, other] and old.use_fake_user, slots)
    bpy.ops.studio.material_assign(name=old.name)
    check("and back", [s.material for s in objects[1].material_slots] == [old, old])
    bpy.ops.studio.material_assign(name=other.name, only_selected=True)
    check("shift: only the active object", objects[0].active_material == other
          and objects[1].active_material == old)
    # Without an active object the last material stays the current one.
    bpy.context.view_layer.objects.active = None
    bpy.ops.object.select_all(action='DESELECT')
    tab = mod("studio").tab_material
    tab.remember(old)
    bpy.ops.studio.material_assign(name=new.name)
    check("switch without selecting an object", objects[1].active_material == new
          and tab.current_material(bpy.context) == new)
    bpy.ops.studio.material_variant()
    variant = objects[1].active_material
    check("color variant replaces it everywhere", variant.name == "Holz B V2"
          and [s.material for s in objects[1].material_slots] == [variant, variant] and new.use_fake_user,
          variant.name)
    bpy.ops.studio.material_select(name=variant.name)
    check("select the objects of a material", {o.name for o in bpy.context.selected_objects} == {"sw1"}
          and bpy.context.active_object == objects[1],
          ([o.name for o in bpy.context.selected_objects], bpy.context.active_object, bpy.context.mode))
    tab.toggle_favorite("Holz A")
    groups = dict(tab._library_groups("holz"))
    check("search and favorites", [m.name for m, _s in groups.get(tab.FAVORITES_GROUP, [])] == ["Holz A"]
          and "Alu" not in {m.name for items in groups.values() for m, _s in items}, list(groups))
    unused = {m.name for m in tab.unused_materials()}
    bpy.ops.studio.material_clean_unused()
    check("remove unused keeps favorites", "Holz B" in unused and "Holz A" not in unused
          and "Holz B" not in bpy.data.materials and "Holz A" in bpy.data.materials, unused)
    tab.toggle_favorite("Holz A")
    for obj in objects:
        bpy.data.objects.remove(obj)


@section
def material_under_the_eyedropper():
    tab = mod("studio").tab_material
    cube = bpy.data.objects["Cube"]
    material, obj = tab.material_at_ray(bpy.context, Vector((0, -10, 0)), Vector((0, 1, 0)))
    check("eyedropper finds the material", obj == cube and material == cube.active_material, (material, obj))
    material, obj = tab.material_at_ray(bpy.context, Vector((0, -10, 50)), Vector((0, 1, 0)))
    check("eyedropper misses empty space", material is None and obj is None)


@section
def base_material_saved():
    studio = mod("studio")
    tab, baselib = studio.tab_material, studio.baselib
    library = os.path.join(TMP, "Base Materials.blend")
    glass = bpy.data.materials.new("Glass")
    glass.asset_mark()
    bpy.data.libraries.write(library, {glass}, fake_user=True)
    bpy.data.materials.remove(glass)
    studio.runner.prefs().material_library = library
    mine = bpy.data.materials.new("my glass")
    mine.diffuse_color = (0.1, 0.9, 0.2, 1.0)
    mine[tab.BASE_KEY] = "Glass"   # a copy of the base material, changed here
    name = baselib.save(mine)
    new = bpy.data.materials.new("Rubber new")
    baselib.save(new, "Rubber")
    with bpy.data.libraries.load(library, assets_only=True) as (source, target):
        names = sorted(source.materials)
        target.materials = ["Glass"]
    saved = target.materials[0]
    check("base material replaced in the library", name == "Glass" and names == ["Glass", "Rubber"]
          and abs(saved.diffuse_color[1] - 0.9) < 1e-4, names)
    baselib.remove("Rubber")
    with bpy.data.libraries.load(library, assets_only=True) as (source, _target):
        check("base material removed", sorted(source.materials) == ["Glass"], list(source.materials))
    studio.runner.prefs().material_library = ""



@section
def expired_login():
    core = mod("core")
    opened = []
    core.claude_cli.open_login = opened.append
    core._login_opened = False
    count = len(core.messages)
    core._handle_event({"type": "result", "is_error": True, "session_id": core.session_id,
                        "result": "Failed to authenticate: OAuth session expired and could not be refreshed"})
    core._handle_done(1, "")
    hint = [m for m in core.messages[count:] if m["role"] == "info"]
    check("expired login: hint and login window", core.login_needed and len(opened) == 1 and hint,
          (core.login_needed, opened, core.messages[count:]))
    core._handle_done(1, "Not logged in · Please run /login")
    check("login window opens only once on its own", len(opened) == 1, opened)
    core._handle_done(0, "")
    check("login hint gone after a good run", not core.login_needed)
    del core.messages[count:]
    # The start check / polling: `claude auth status` answers in a thread, the timer applies it.
    for answer, needed in ((False, True), (True, False)):
        thread = core.threading.Thread(target=lambda: None)
        thread.start()
        thread.join()
        core._login_check.update(thread=thread, result=answer)
        core._poll_login()
        check("auth status %s -> button %s" % (answer, needed), core.login_needed == needed)


@section
def onboarding():
    """Online access off (a new Blender), the login button going away, terminals closing."""
    core, ui, cli = mod("core"), mod("ui"), mod("claude_cli")
    system = bpy.context.preferences.system
    was = system.use_online_access
    prefs = core.get_prefs()
    backend = prefs.backend
    prefs.backend = 'CLOUD'
    system.use_online_access = False
    core._setup_cache["state"] = None
    check("online access off is seen", core.online_state() == 'OFF' and core.setup_state()["online"] == 'OFF')
    prefs.setup_done = True
    check("setup card comes back while offline", ui._setup_visible(bpy.context, prefs))
    count = len(core.messages)
    core._start_run([("hi", "u1")], prefs)
    text = " ".join(m["text"] for m in core.messages[count:])
    check("offline: no run, says what to click", core.current_run is None and "'Allow'" in text, text)
    del core.messages[count:]
    check("Allow button turns it on", core.allow_online() and bpy.app.online_access
          and core.setup_state()["online"] == 'ON')
    prefs.setup_asked = True
    check("setup card hides again once online", not ui._setup_visible(bpy.context, prefs) or
          not core.setup_state()["cli"])
    prefs.setup_done, prefs.setup_asked = False, False
    check("new user: steps and questions", ui._setup_steps_open(bpy.context, prefs)
          and ui._setup_questions_open(bpy.context, prefs))
    core._chat_works()
    state = core.setup_state()
    check("chat works: the steps go, the questions stay", prefs.setup_done
          and (not ui._setup_steps_open(bpy.context, prefs) or not state["cli"] or state["login"] is False)
          and ui._setup_questions_open(bpy.context, prefs))
    bpy.ops.claude.setup_choice(key="code", value="auto")
    bpy.ops.claude.setup_choice(key="backups", value="on")
    check("questions set the preferences", prefs.auto_approve and prefs.autosave)
    bpy.ops.claude.setup_choice(key="code", value="ask")
    bpy.ops.claude.setup_choice(key="backups", value="off")
    bpy.ops.claude.setup_done()
    check("Done: questions answered", prefs.setup_asked and not ui._setup_questions_open(bpy.context, prefs))
    system.use_online_access = was
    prefs.backend = backend

    # Log in: checks every few seconds right away (not only after the next 30 s check)
    core._login_check.update(next=core.time.monotonic() + 30)
    core.open_login()
    check("login button starts fast checks", core._login_check["next"] == 0.0)
    core._set_login_needed(True)
    thread = core.threading.Thread(target=lambda: None)
    thread.start()
    thread.join()
    count = len(core.messages)
    core._login_check.update(thread=thread, result=True)
    core._poll_login()
    said = [m["text"] for m in core.messages[count:]]
    check("logged in: button gone, info shown, next check soon",
          not core.login_needed and said and core._login_check["next"] - core.time.monotonic() <= core.LOGIN_POLL + 0.5,
          said)
    del core.messages[count:]

    # The terminal window closes itself when the command worked, and stays on an error.
    script = cli.terminal_script("claude auth login", "Logged in.", "/tmp/close.applescript")
    ok_part, error_part = script.split("fi\n", 1)
    check("terminal closes only after success", "osascript /tmp/close.applescript" in ok_part
          and "$status -eq 0" in ok_part and "osascript" not in error_part, script)
    import subprocess, tempfile
    with tempfile.TemporaryDirectory() as folder:
        closer = os.path.join(folder, "closer")
        with open(closer, "w") as f:
            f.write("#!/bin/sh\necho closed > %s/closed\n" % folder)
        os.chmod(closer, 0o755)
        for command, closes in (("true", True), ("false", False)):
            path = os.path.join(folder, "t.sh")
            with open(path, "w") as f:
                f.write(cli.terminal_script(command, "ok", closer).replace("/usr/bin/osascript ", "")
                        .replace("sleep 2", "true").replace(" >/dev/null 2>&1 &", ""))
            subprocess.run(["sh", path], capture_output=True, timeout=10)
            for _ in range(50):
                if os.path.exists(os.path.join(folder, "closed")):
                    break
                core.time.sleep(0.05)
            closed = os.path.exists(os.path.join(folder, "closed"))
            check("terminal script with %s: closes %s" % (command, closes), closed == closes)
            if closed:
                os.remove(os.path.join(folder, "closed"))


@section
def render_defaults():
    """HQ samples 512 instead of Blender's 4096; depth of field shown while it is on."""
    cameras, tab_camera = mod("studio.cameras"), mod("studio.tab_camera")
    scene = bpy.data.scenes.new("Samples Test")
    check("a new scene has Blender's 4096", scene.cycles.samples == cameras.BLENDER_SAMPLES)
    cameras.default_samples(scene)
    check("becomes 512", scene.cycles.samples == 512, scene.cycles.samples)
    scene.cycles.samples = 4096
    cameras.default_samples(scene)
    check("a 4096 set by hand stays", scene.cycles.samples == 4096)
    other = bpy.data.scenes.new("Own Samples")
    other.cycles.samples = 300
    cameras.default_samples(other)
    check("own sample counts stay", other.cycles.samples == 300)
    bpy.data.scenes.remove(scene)
    bpy.data.scenes.remove(other)
    hdri = mod("studio.hdri")
    check("no HDRI folder set: one in the add-on's data folder",
          hdri.folder_path("") == os.path.join(mod("studio.paths").data_dir(), "HDRI"),
          hdri.folder_path(""))

    class Region:
        view_perspective = 'PERSP'

    class Shading:
        type = 'SOLID'

    class Space:
        type = 'VIEW_3D'
        shading = Shading()
        region_3d = Region()

    space = Space()
    solid = tab_camera.dof_hint(space)
    Shading.type = 'MATERIAL'
    outside = tab_camera.dof_hint(space)
    Region.view_perspective = 'CAMERA'
    check("depth of field hint: solid / outside the camera / none",
          "Material Preview" in solid and "camera view" in outside and tab_camera.dof_hint(space) == "",
          (solid, outside))



@section
def live_connection():
    import json, stat, subprocess
    core, live = mod("core"), mod("live")
    live.LIVE_FILE = os.path.join(TMP, "config", "live.json")   # not the real one of an open Blender
    prefs = core.get_prefs()
    auto = prefs.auto_approve
    check("no live server in a background Blender", not live.is_running())
    prefs.live_connection = True
    with open(live.LIVE_FILE) as f:
        info = json.load(f)
    mode = stat.S_IMODE(os.stat(live.LIVE_FILE).st_mode)
    check("live.json written, private", mode == 0o600 and info["pid"] == os.getpid() and info["port"]
          and info["version"] == bpy.app.version_string, (oct(mode), list(info)))

    def post(body, token=info["token"], on_wait=None):
        run = subprocess.Popen(["curl", "-s", "-X", "POST", "http://127.0.0.1:%d/live" % info["port"],
                                "-H", "Authorization: Bearer " + token, "-d", json.dumps(body)],
                               stdout=subprocess.PIPE, text=True)
        waited = False
        while run.poll() is None:
            core._tick()
            if on_wait and core.pending and not waited:
                waited = True
                on_wait()
            time.sleep(0.02)
        return json.loads(run.stdout.read())

    check("wrong token refused", post({"op": "ping"}, token="nope")["ok"] is False)
    answer = post({"op": "ping"})
    check("ping", answer["ok"] and answer["version"] == bpy.app.version_string, answer)
    check("info", "Objects" in post({"op": "info"}).get("text", "") or post({"op": "info"})["ok"])
    check("unknown object", post({"op": "object", "name": "nope"})["ok"] is False)
    prefs.auto_approve = True
    count = len(core.messages)
    answer = post({"op": "python", "code": "kept = 41\nprint(bpy.app.version[0] > 0, math.pi > 3)", "description": "Test"})
    check("python runs, output back", answer["ok"] and answer["output"] == "True True", answer)
    check("description in the chat", core.messages[-1]["text"] == "Cowork: Test" and len(core.messages) == count + 1)
    answer = post({"op": "python", "code": "print(kept + 1)", "description": ""})
    check("values stay between calls", answer["ok"] and answer["output"] == "42", answer)
    answer = post({"op": "python", "code": "1/0", "description": ""})
    check("exception comes back", answer["ok"] is False and "ZeroDivisionError" in answer["error"], answer)
    prefs.auto_approve = False
    answer = post({"op": "python", "code": "print('asked')", "description": ""},
                  on_wait=lambda: core.approve(0, True))
    check("waits for the OK in the chat", answer.get("output") == "asked" and not core.pending, answer)
    answer = post({"op": "python", "code": "print('no')", "description": ""},
                  on_wait=lambda: core.approve(0, False))
    check("rejected code", answer["ok"] is False and "rejected" in answer["error"], answer)
    check("screenshot answers JSON", "ok" in post({"op": "screenshot", "max_size": 256}))
    prefs.auto_approve = True

    class Busy:
        cancelled = False
    core.current_run = Busy()
    seen = []

    def chat_done():
        seen.append(core.live_status())
        core.current_run = None
    run = subprocess.Popen(["curl", "-s", "-X", "POST", "http://127.0.0.1:%d/live" % info["port"],
                            "-H", "Authorization: Bearer " + info["token"],
                            "-d", json.dumps({"op": "python", "code": "print('after')", "description": ""})],
                           stdout=subprocess.PIPE, text=True)
    while run.poll() is None:
        core._tick()
        if core._live_waiting and not seen:
            chat_done()
        time.sleep(0.02)
    answer = json.loads(run.stdout.read())
    check("live code waits for the chat's answer", answer.get("output") == "after"
          and seen and "waits" in seen[0], (answer, seen))
    check("connection text for other sessions", "live.json" in live.connection_text())
    state = core.setup_state()
    check("setup state", state["cli"] and state["live"] is False or state["live"] is True, state)
    prefs.auto_approve = auto
    del core.messages[count:]
    prefs.live_connection = False
    check("switched off: server closed, file gone", not live.is_running() and not os.path.exists(live.LIVE_FILE))



@section
def feedback_tools():
    studio = mod("studio")
    api, cadswap, runner, lights, look = studio.api, studio.cadswap, studio.runner, studio.lights, studio.look
    scene = bpy.context.scene
    # CAD version: names with importer endings, materials over, old parts archived
    mat = bpy.data.materials.new("Alu test")
    old = bpy.data.objects.new("Gabel", bpy.data.meshes.new("Gabel"))
    old.data.materials.append(mat)
    keep_coll = bpy.data.collections.new("Produkt")
    scene.collection.children.link(keep_coll)
    keep_coll.objects.link(old)
    new = bpy.data.objects.new("Gabel <1>", bpy.data.meshes.new("Gabel2"))
    scene.collection.objects.link(new)
    info = api.cad_take_over([new])
    archived = bpy.data.collections.get("Archiv")
    check("new CAD version takes materials and collections", info["pairs"] == [["Gabel <1>", "Gabel"]]
          and new.active_material == mat and keep_coll in new.users_collection, info)
    check("old part archived, hidden", archived is not None and old.name in archived.objects
          and archived.hide_render and old.get(cadswap.ARCHIVED_FROM) == "Produkt")
    # temp collection is emptied after an answer
    helper = bpy.data.objects.new("cutter", bpy.data.meshes.new("cutter"))
    api.temp_collection().objects.link(helper)
    mod("core")._empty_temp()
    check("temp collection emptied after the answer", "cutter" not in bpy.data.objects
          and "temp" not in bpy.data.collections)
    # section through two boxes 2 mm apart
    import bmesh
    boxes = []
    for name, size in (("Scan", 0.1), ("CAD", 0.104)):
        me = bpy.data.meshes.new(name)
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=size)
        bm.to_mesh(me)
        bm.free()
        obj = bpy.data.objects.new(name, me)
        scene.collection.objects.link(obj)
        boxes.append(obj)
    result = api.section(["Scan", "CAD"], axis="X", offset=0.0)
    check("section measures width and distance", result["Scan"]["width_mm"] == 100.0
          and abs(result["CAD"]["to_Scan_mm"]["max"] - 2.83) < 0.1 and "Section Scan" in bpy.data.objects, result)
    mod("core")._empty_temp()
    # light preset keeps the names of your lights
    mod("studio").runner.prefs().name_scheme = True
    created = lights.apply_setup(scene, None, {"name": "A", "lights": [{"name": "Key"}, {"name": "Fill"}]})
    check("setup lights in the scheme", [o.name for o in created] == ["A_key_01", "A_fill_01"],
          [o.name for o in created])
    mod("studio").cameras.rename(created[0], "back")
    again = lights.apply_setup(scene, None, {"name": "B", "lights": [{"name": "Area.Default"}]})
    check("preset keeps your light names", [o.name for o in again] == ["A_back_01"]
          and "A_fill_01" not in bpy.data.objects and again[0].users_collection[0].name == "Light",
          [o.name for o in again])
    mod("studio").runner.prefs().name_scheme = False
    # a new camera changes what the worker must load
    before = runner.scene_content(scene)
    cam = bpy.data.objects.new("New cam", bpy.data.cameras.new("New cam"))
    scene.collection.objects.link(cam)
    check("new camera reaches the render worker", runner.scene_content(scene) != before)
    moved = runner.scene_content(scene)
    cam.location.x += 1
    check("moving a camera does not reload", runner.scene_content(scene) == moved)
    # compositor look
    rq = scene.studio
    rq.look_glow, rq.look_vignette = 0.4, 0.5
    tree = studio.background.compositor_tree(scene)
    names = {n.name for n in tree.nodes}
    check("look nodes in the compositor", look.GLOW in names and look.VIGNETTE + " Mix" in names, names)
    rq.look_glow = rq.look_vignette = 0.0
    check("look off: nodes gone", not any(n.name.startswith(look.PREFIX) for n in tree.nodes))
    # room bake takes the as_env objects
    boxes[0]["as_env"] = True
    check("room objects by marker", api.room_objects(scene) == [boxes[0]])
    # reference folders
    root = os.path.join(TMP, "S2N")
    os.makedirs(os.path.join(root, "DATA", "2.2", "FORK"))
    os.makedirs(os.path.join(root, "refrence", "CMF", "holz"))
    open(os.path.join(root, "refrence", "CMF", "holz", "a.jpg"), "w").close()
    check("project root found", api.project_root(os.path.join(root, "DATA", "2.2", "FORK", "FORK_now.blend")) == root)
    # render folders only when needed
    core_mod = studio.core
    out = os.path.join(TMP, "renders_lazy")
    path = core_mod.job_path(out, "Cam", 'HIGH', "Lazy")
    check("render folders not made up front", not os.path.exists(os.path.dirname(path)), path)
    for obj in boxes + [new, cam] + list(again):
        bpy.data.objects.remove(obj)



@section
def release_1_0():
    """New id with migration, render folders next to the file, connecting other Claude apps."""
    import json, subprocess
    migrate, studio, api = mod("migrate"), mod("studio"), mod("studio.api")
    # data of the old id is copied once, paths inside point at the new folder
    user = os.path.join(TMP, "dotuser")
    old_dir = os.path.join(user, "user_default", "claude_assistant")
    new_dir = os.path.join(user, "ai_studio_repo", "AI_Studio")
    os.makedirs(os.path.join(old_dir, "studio"))
    os.makedirs(new_dir)
    with open(os.path.join(old_dir, "chats.json"), "w") as f:
        json.dump([{"image": os.path.join(old_dir, "attachments", "a.png")}], f)
    with open(os.path.join(old_dir, "studio", "light_presets.json"), "w") as f:
        f.write("{}")
    with open(os.path.join(new_dir, "usage.json"), "w") as f:
        f.write('{"new": 1}')
    found = migrate.copy_data(new_dir, user)
    with open(os.path.join(new_dir, "chats.json")) as f:
        chats = f.read()
    with open(os.path.join(new_dir, "usage.json")) as f:
        usage = f.read()
    check("old data copied, paths moved, new files kept",
          found == old_dir and new_dir in chats and old_dir not in chats and usage == '{"new": 1}'
          and os.path.exists(os.path.join(new_dir, "studio", "light_presets.json")), chats)
    check("copied only once", migrate.copy_data(new_dir, user) is None)
    prefs = mod("core").get_prefs()
    was = prefs.studio.low_resolution
    prefs.studio.low_resolution = 37
    saved = migrate.values(prefs)
    prefs.studio.low_resolution = 50
    migrate.apply(prefs, saved)
    check("preferences carried over (nested)", prefs.studio.low_resolution == 37 and "studio" in saved)
    prefs.studio.low_resolution = was

    # renders: right next to the .blend file when no output folder is set
    score = studio.core
    folder = score.quality_dir("", "Chair", 'HIGH')
    expected = (mod("autosave").main_folder(bpy.data.filepath) if bpy.data.filepath
                else os.path.expanduser(score.UNSAVED_ROOT))
    check("renders next to the file", folder.startswith(os.path.join(expected, "Chair" + score.folder_names()['SUFFIX'])), folder)
    check("an output folder set still wins", score.quality_dir("/tmp/x", "Chair", 'HIGH').startswith("/tmp/x"))

    # references: any reference folder next to or above the file
    root = os.path.join(TMP, "Chair project")
    os.makedirs(os.path.join(root, "Reference", "CMF"))
    os.makedirs(os.path.join(root, "files", "v2"))
    open(os.path.join(root, "Reference", "CMF", "oak.jpg"), "w").close()
    check("reference folder without DATA", api.project_root(os.path.join(root, "files", "v2", "Chair.blend")) == root)
    check("no reference folder: none", api.project_root(os.path.join(TMP, "lonely", "x.blend")) is None)

    # connecting Claude Code and the Claude app (temporary files, a fake claude)
    clients = mod("live_clients")
    clients.STATE_FILE = os.path.join(TMP, "cfg", "clients.json")
    clients.DESKTOP_CONFIG = os.path.join(TMP, "Claude", "claude_desktop_config.json")
    os.makedirs(os.path.dirname(clients.DESKTOP_CONFIG))
    with open(clients.DESKTOP_CONFIG, "w") as f:
        json.dump({"mcpServers": {"other": {"command": "x"}}, "keep": True}, f)
    clients.connect_desktop()
    with open(clients.DESKTOP_CONFIG) as f:
        config = json.load(f)
    entry = config["mcpServers"].get("ai-studio", {})
    check("Claude app: own entry added, the rest kept",
          config["keep"] and "other" in config["mcpServers"] and entry.get("args", [""])[-1].endswith("live_mcp.py")
          and clients.desktop_connected(), config)
    fake = os.path.join(TMP, "fake_claude")
    log = os.path.join(TMP, "fake_claude.log")
    with open(fake, "w") as f:
        f.write('#!/bin/sh\necho "$@" >> %s\n' % log)
    os.chmod(fake, 0o755)
    clients.connect_code(fake)
    with open(log) as f:
        calls = f.read()
    check("Claude Code: claude mcp add in the user scope", "mcp add -s user ai-studio -- " in calls
          and clients.code_connected(), calls)

    # the MCP bridge talks to the Live Connection
    live = mod("live")
    on = prefs.live_connection
    prefs.live_connection = True
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
             {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "blender_status"}}]
    result = subprocess.run(clients.bridge_command(), input="\n".join(json.dumps(m) for m in lines) + "\n",
                            capture_output=True, text=True, timeout=30,
                            env=dict(os.environ, AI_STUDIO_LIVE_FILE=live.LIVE_FILE))
    answers = [json.loads(x) for x in result.stdout.splitlines() if x.strip()]
    status = answers[2]["result"]["content"][0]["text"] if len(answers) == 3 else ""
    check("MCP bridge: initialize, 7 tools, status from Blender",
          len(answers) == 3 and answers[0]["result"]["serverInfo"]["name"] == "ai-studio"
          and len(answers[1]["result"]["tools"]) == 7 and status.startswith("Blender " + bpy.app.version_string[:3]),
          (result.stdout[-500:], result.stderr[-500:]))
    prefs.live_connection = on

    # two Blenders: the bridge works in the one worked in last, or the one asked for by file
    import importlib.util
    os.environ["AI_STUDIO_LIVE_FILE"] = live.LIVE_FILE
    spec = importlib.util.spec_from_file_location("live_mcp_test", os.path.join(
        os.path.dirname(live.__file__), "live_mcp.py"))
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    del os.environ["AI_STUDIO_LIVE_FILE"]
    folder = bridge.INSTANCES
    os.makedirs(folder, exist_ok=True)
    import subprocess
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])   # a second "Blender"
    for pid, name, active in ((os.getpid(), "/x/VELO_now.blend", 2.0), (other.pid, "/x/Lampe.blend", 1.0)):
        with open(os.path.join(folder, "%d.json" % pid), "w") as f:
            json.dump({"pid": pid, "port": 1, "token": "t", "file": name, "active": active}, f)
    last, count = bridge.pick()
    asked, _n = bridge.pick("lampe")
    check("two Blenders: the last one worked in, or the one named", count == 2 and last["file"].endswith("VELO_now.blend")
          and asked["file"].endswith("Lampe.blend"), (last, asked))
    other.kill()
    for name in os.listdir(folder):
        os.remove(os.path.join(folder, name))



@section
def default_model():
    """New chats start with the model and effort from the preferences."""
    core = mod("core")
    prefs = core.get_prefs()
    keep = (prefs.default_model, prefs.default_effort, prefs.backend, prefs.model, prefs.effort)
    prefs.default_model = 'opus'
    check("default model sets its usual effort", prefs.default_effort == 'high')
    prefs.default_effort = 'max'
    prefs.model, prefs.effort = 'haiku', 'low'
    core.new_chat()
    check("new chat: default model and effort", (prefs.backend, prefs.model, prefs.effort) == ('CLOUD', 'opus', 'max'),
          (prefs.backend, prefs.model, prefs.effort))
    prefs.default_model = 'LOCAL'
    core.new_chat()
    check("default Local: new chat on LM Studio", prefs.backend == 'LOCAL')
    prefs.default_model, prefs.default_effort = keep[0], keep[1]
    prefs.backend, prefs.model, prefs.effort = keep[2], keep[3], keep[4]


@section
def setup_in_one_click():
    """Install in the background with progress, then the login, without a terminal for the install."""
    core, cli, flow = mod("core"), mod("claude_cli"), mod("setup_flow")
    folder = tempfile.mkdtemp(prefix="install_", dir=TMP)
    with open(os.path.join(folder, "claude-x"), "wb") as f:
        f.write(b"x" * 500)
    inst = cli.Installer(command="echo hi", download_dir=folder)
    inst.size = 1000
    check("install progress from the growing download", inst.progress() == (0.5, 'DOWNLOAD'), inst.progress())
    inst = cli.Installer(command="echo 'Setting up Claude Code...'; echo done", download_dir=folder).start()
    inst._thread.join(20)
    check("installer ok, then the setup phase", inst.ok and inst.progress()[1] == 'SETUP', inst.lines)
    inst = cli.Installer(command="echo 'Download failed' >&2; exit 1", download_dir=folder).start()
    inst._thread.join(20)
    check("failed installer: not ok, message kept", not inst.ok and "Download failed" in inst.error(), inst.lines)
    inst = cli.Installer(command="curl -fsSL http://127.0.0.1:9/x | bash", download_dir=folder)
    inst.command = "set -o pipefail; " + inst.command
    inst.start()._thread.join(30)
    check("no internet: the pipe does not hide the error", not inst.ok, inst.lines)

    # the whole flow with a stand-in installer and login
    real = (cli.Installer, cli.find_claude, cli.logged_in, core.open_login)
    found = []
    logins = iter([False, False, True])
    opened = []

    class FakeInstaller(cli.Installer):
        def __init__(self):
            super().__init__(command="echo 'Setting up Claude Code...'", download_dir=folder)

        def _run(self):
            super()._run()
            found.append("/fake/claude")

    cli.Installer = FakeInstaller
    cli.find_claude = lambda configured="": found[0] if found else None
    cli.logged_in = lambda path: next(logins, True)
    core.open_login = lambda: opened.append(1) or True
    keep_poll = flow.LOGIN_POLL
    flow.LOGIN_POLL = 0.05
    count = len(core.messages)
    try:
        core._set_login_needed(True)
        started = flow.start(("install", "login"))
        flow.flow["thread"].join(30)
        flow.tick()
        said = " ".join(m["text"] for m in core.messages[count:])
        check("one click: installs, opens the login once, waits for it", started and opened == [1]
              and not core.login_needed and core.login_ok and "installed" in said and "Logged in" in said, said)
        del core.messages[count:]
        found.clear()

        class Broken(real[0]):
            def __init__(self):
                super().__init__(command="echo 'Failed to fetch version'; exit 1", download_dir=folder)

        cli.Installer = Broken
        flow.start(("install", "login"))
        flow.flow["thread"].join(30)
        flow.tick()
        said = " ".join(m["text"] for m in core.messages[count:])
        check("install error goes into the chat", "Failed to fetch version" in said and not flow.running(), said)
    finally:
        cli.Installer, cli.find_claude, cli.logged_in, core.open_login = real
        flow.LOGIN_POLL = keep_poll
        del core.messages[count:]


@section
def render_batches():
    """Several cameras from the chat: one numbered batch folder; A / B picks for compare."""
    core, shots, tab_queue = mod("studio.core"), mod("studio.shots"), mod("studio.tab_queue")
    out = os.path.join(TMP, "renders_batch")
    first = core.next_batch(out, "Batch")
    folder = os.path.join(core.quality_dir(out, "Batch", 'HIGH'), first)
    os.makedirs(folder)
    one = core.batch_path(out, "Front", 'HIGH', first, "Batch")
    open(one, "w").close()
    two = core.batch_path(out, "Side", 'HIGH', first, "Batch")
    check("batch folders numbered, images in it numbered", first == "Batch_01"
          and core.next_batch(out, "Batch") == "Batch_02" and os.path.basename(one).startswith("01_")
          and os.path.basename(two).startswith("02_") and os.path.dirname(two) == folder, (first, one, two))
    a, b = os.path.join(TMP, "cmp_a.png"), os.path.join(TMP, "cmp_b.png")
    for path in (a, b):
        open(path, "w").close()
    tab_queue._ab.update(A=a, B=b)
    try:
        check("compare takes A and B", shots.compare_pair(bpy.context.scene) == [a, b])
    finally:
        tab_queue._ab.update(A="", B="")


@section
def env_skill_back():
    """ENV is shipped again: a copy moved aside comes back once (with the user's changes)."""
    import json
    skills = mod("skills")
    folder = os.path.join(TMP, "skills_env_back")
    os.makedirs(os.path.join(folder, "_retired"))
    with open(os.path.join(folder, skills.INSTALLED), "w") as f:
        json.dump(["environment"], f)
    with open(os.path.join(folder, "_retired", "environment.md"), "w") as f:
        f.write("mine")
    skills.install_defaults(folder)
    back = open(os.path.join(folder, "environment.md")).read()
    check("ENV skill back with the user's copy", back == "mine" and not os.path.exists(
        os.path.join(folder, "_retired", "environment.md")), back)


@section
def render_review_stars():
    """Stars next to the images, sort by them; a refined version knows its render."""
    review = mod("studio.render_review")
    folder = os.path.join(TMP, "review")
    os.makedirs(folder, exist_ok=True)
    one, two = os.path.join(folder, "P_front_01.png"), os.path.join(folder, "P_front_01_krea_01.png")
    for path in (one, two):
        open(path, "w").close()
    review.set_rating(two, 4)
    review.set_rating(one, 2)
    review.set_rating(one, 2)          # the same again: none
    keep = dict(review.state)
    review.state.update(sort='BEST', filter='ALL')
    try:
        order = review.arrange([one, two])
        review.state.update(filter='RATED')
        rated = review.arrange([one, two])
    finally:
        review.state.update(keep)
    check("stars: kept, sorted, toggled off", review.rating(two) == 4 and review.rating(one) == 0
          and order == [two, one] and rated == [two], (order, rated))
    check("refined version: kind and its render", review.refined_kind(two) == "krea" and review.source_of(two) == one)


@section
def three_zero():
    """3.0: fixed collection names, a film's progress text."""
    cams, film = mod("studio.cameras"), mod("studio.film")
    check("collections: fixed names", (cams.camera_collection_name(), cams.archive_collection_name(),
                                       cams.object_collection_name(), cams.temp_collection_name())
          == ("Camera", "Archiv", "Object", "TEMP"))
    line = film.text({"label": "P_front"}, {"part": 2, "parts": 4, "done": 250, "total": 960, "per_frame": 2.0},
                     'RENDERING')
    check("film: part, frames, time left", "Part 2/4" in line and "250/960" in line and "left" in line, line)

@section
def orbit_keys():
    """Orbit keys: the camera on a pivot keeps its place, and between two keys it moves on an arc."""
    orbit, keys = mod("studio.orbit"), mod("studio.keys")
    scene = bpy.context.scene
    data = bpy.data.cameras.new("Arc Test")
    cam = bpy.data.objects.new("Arc Test", data)
    scene.collection.objects.link(cam)
    cam.matrix_world = orbit.orbit_matrix(Vector((0, 0, 0)), 0.0, 0.3, 2.0, Vector((1, 1, 1)))
    bpy.context.view_layer.update()
    before = cam.matrix_world.translation.copy()
    pivot = orbit.rig(cam, 2.0)
    bpy.context.view_layer.update()
    keep = scene.frame_current
    try:
        check("pivot: camera stays where it was", (cam.matrix_world.translation - before).length < 1e-4,
              cam.matrix_world.translation)
        keys.key_camera(scene, cam, 1)
        pivot.rotation_euler.z = 1.5
        keys.key_camera(scene, cam, 21)
        scene.frame_set(11)
        mid = cam.matrix_world.translation - pivot.matrix_world.translation
        check("keys: an arc between the keys", abs(mid.length - 2.0) < 1e-3 and 0.3 < pivot.rotation_euler.z < 1.2,
              (mid.length, pivot.rotation_euler.z))
    finally:
        scene.frame_set(keep)
        bpy.data.objects.remove(cam)
        bpy.data.objects.remove(pivot)


@section
def motion_sections():
    """Sections "NN Name" with their own camera; a longer section moves the next one with its keys;
    a preset with strength, a chain, a fade; the frames in the scene's own Video Editor."""
    motion, orbit, cut = mod("studio.motion"), mod("studio.orbit"), mod("studio.cut")
    scene = bpy.context.scene
    keep = (scene.frame_start, scene.frame_end, scene.frame_current, scene.camera, scene.view_settings.exposure)
    scene.frame_start, scene.frame_end = 1, 100
    cam = bpy.data.objects.new("Motion Test", bpy.data.cameras.new("Motion Test"))
    scene.collection.objects.link(cam)
    cam.matrix_world = orbit.orbit_matrix(Vector((0, 0, 0)), 0.0, 0.2, 1.5, Vector((1, 1, 1)))
    scene.camera = cam
    bpy.context.view_layer.update()
    made, markers = [cam], []
    try:
        scene.frame_set(1)
        markers.append(motion.new_section(scene, "Intro"))
        scene.frame_set(41)
        markers.append(motion.new_section(scene, "Hero"))
        hero = markers[1].camera
        made += [m.camera for m in markers]
        found = [(m.name, s, e) for m, s, e in motion.sections(scene)]
        check("sections: NN Name, own camera on a pivot, pass index", found == [("01 Intro", 1, 40), ("02 Hero", 41, 100)]
              and hero not in (None, cam) and hero.pass_index == 2 and orbit.pivot_of(hero) is not None, found)
        pivot = orbit.pivot_of(hero)
        z0 = pivot.rotation_euler.z
        motion.apply(scene, hero, 'TURNTABLE', 41, 100, 'LINEAR', amount=90)
        scene.frame_set(100)
        check("preset: 90 degrees", abs(pivot.rotation_euler.z - z0 - math.pi / 2) < 0.01, pivot.rotation_euler.z)
        check("preset: keys already there are seen", motion.conflicts(scene, hero, 'SWING', 41, 100) >= 2)
        motion.set_length(scene, "01 Intro", length=50)
        found = [(m.name, s, e) for m, s, e in motion.sections(scene)]
        scene.frame_set(110)
        check("longer section: the next one moves with its keys", found == [("01 Intro", 1, 50), ("02 Hero", 51, 110)]
              and abs(pivot.rotation_euler.z - z0 - math.pi / 2) < 0.01, (found, pivot.rotation_euler.z))
        rq = scene.studio
        for preset, frames, amount in (('HOLD', 10, 0.0), ('TURNTABLE', 0, 180.0)):
            step = rq.motion_steps.add()
            step.section, step.preset, step.frames, step.amount = "02 Hero", preset, frames, amount
        motion.apply_chain(scene, hero, "02 Hero", 51, 110, 'LINEAR')
        scene.frame_set(61)
        held = pivot.rotation_euler.z
        scene.frame_set(110)
        check("chain: hold, then half around", abs(held - z0) < 0.01 and abs(pivot.rotation_euler.z - z0 - math.pi) < 0.01,
              (held, pivot.rotation_euler.z))
        base = scene.view_settings.exposure
        motion.apply(scene, None, 'FADE_IN', 1, 50, amount=10)
        scene.frame_set(1)
        dark = scene.view_settings.exposure
        scene.frame_set(11)
        check("fade in from black", dark < base - 5 and abs(scene.view_settings.exposure - base) < 0.01,
              (dark, scene.view_settings.exposure))
        # rendered frames as the films write them (animation/Frames/<section>/<section>_0001.png, Low
        # Quality at a quarter of the width): the strips go into this scene's own Video Editor, 1:1
        anim = os.path.join(TMP, "anim")
        import shutil
        img = bpy.data.images.new("cut frame", 4, 4)
        for name, a, b in (("01 Intro", 1, 50), ("02 Hero", 51, 110)):
            folder = os.path.join(anim, "Frames", name)
            os.makedirs(os.path.join(folder, ".film"), exist_ok=True)
            first = os.path.join(folder, "%s_%04d.png" % (name, a))
            img.filepath_raw, img.file_format = first, 'PNG'
            img.save()
            for f in range(a + 1, b + 1):
                shutil.copyfile(first, os.path.join(folder, "%s_%04d.png" % (name, f)))
            old = time.time() - 60                  # done a minute ago (frames being written are not read)
            for f in range(a, b + 1):
                os.utime(os.path.join(folder, "%s_%04d.png" % (name, f)), (old, old))
            with open(os.path.join(folder, ".film", "job.json"), "w") as f:
                json.dump({"scene": scene.name, "quality": "LOW", "overrides": [
                    ["render.resolution_x", 4], ["render.resolution_percentage", 100]]}, f)
        bpy.data.images.remove(img)
        keep_folder, had_editor = cut._anim_folder, scene.sequence_editor is not None
        keep_seq = scene.render.use_sequencer
        cut._anim_folder = lambda: anim
        try:
            built, report = cut.build(scene)
            intro = cut.section_strip(scene, "01 Intro")
            ed = cut.editor(scene)
            here = scene.frame_current
            ed.frame_current = 37                     # scrubbing the cut
            check("strips in the film's own Video Editor scene (scrubbing leaves the 3D frame), Low Quality "
                  "scaled up, 3D still renders",
                  built is scene and ed is not None and intro is not None and intro.id_data == ed
                  and scene.frame_current == here and cut.film_scene(ed) == scene
                  and intro.frame_final_start == 1
                  and intro.frame_final_duration == 50 and not scene.render.use_sequencer
                  and abs(intro.transform.scale_x - scene.render.resolution_x / 4) < 1e-3 and not report["missing"],
                  (built.name, intro and intro.frame_final_start, report))
            st = cut.frame_status("02 Hero", 51, 110, fresh=True)
            check("a section's frames: done, LQ", st["done"] == 60 and st["quality"] == "LOW"
                  and cut.status_text(st) == "LQ", st)
            check("nothing new: the strips only read again", cut.sync(scene) == "synced")
            first, second = cut.settings(scene, "01 Intro"), cut.settings(scene, "02 Hero")
            first.transition, first.frames, second.hold = 'CROSS', 6, 4
            cut.build(scene)
            kinds = sorted(cut._get(s, cut.KIND_KEY) for s in cut.ours(scene))
            hero = cut.section_strip(scene, "02 Hero")
            check("a cross and a hold; a strip's frame is its section's frame",
                  kinds == ["CROSS", "HOLD", "SECTION", "SECTION"] and cut.strip_range(scene) == (1, 50 + 60 - 6 + 4)
                  and cut.scene_frame(scene, hero, hero.frame_final_start) == ("02 Hero", 51),
                  (kinds, cut.strip_range(scene)))
            rq.cut_export_split, rq.cut_export_skip = 'ONE', ""
            one, join = cut.export_plan(scene)
            rq.cut_export_skip = "01 Intro"
            only, _j = cut.export_plan(scene)
            rq.cut_export_split, rq.cut_export_skip = 'EACH', ""
            each, _j = cut.export_plan(scene)
            rq.cut_export_split = 'ONE'
            check("export: both sections in one range; one left out; one video per section",
                  len(one) == 1 and not join and (one[0][1], one[0][2]) == cut.strip_range(scene)
                  and [r[0] for r in only] == ["02 Hero"] and [r[0] for r in each] == ["01 Intro", "02 Hero"]
                  and each[0][3] != each[1][3], (one, only, each))
            aq = mod("studio.autoquality")
            key = aq.section_key("02 Hero")
            before = aq.next_quality(scene, key)
            aq.record(scene, key)
            after = aq.next_quality(scene, key)
            aq._edits[0] += 1                         # something changed in the scene
            check("quality by itself: LQ, then HQ, after a change LQ again",
                  (before, after, aq.next_quality(scene, key)) == ('LOW', 'HIGH', 'LOW'))
            film = mod("studio.film")
            folder = os.path.join(anim, "Frames", "02 Hero")
            film._remove_frames(folder, "02 Hero", (60, 62))
            left = cut.frame_status("02 Hero", 51, 110, fresh=True)
            check("render again from a frame: only those frames go", left["done"] == 57, left["done"])
            real = os.path.join(folder, "02 Hero_0051.png")
            for f in range(60, 63):                   # drafts (same size) fill the frames not rendered
                draft = os.path.join(folder, "02 Hero_draft_%04d.png" % f)
                shutil.copyfile(real, draft)
                os.utime(draft, (old, old))
            filled = cut.frame_status("02 Hero", 51, 110, fresh=True)
            check("drafts fill the gaps, marked per frame; a render keeps them",
                  filled["done"] == 57 and filled["codes"][9:12] == ['DRAFT'] * 3
                  and filled["shown"][9] == "02 Hero_draft_0060.png" and filled["codes"][0] == 'LOW'
                  and not any("draft" in n for n in film._frames_of(folder, "02 Hero")), filled["codes"][8:13])
            cut.sync(scene)
            check("an orange strip: rendered in Low Quality",
                  cut.section_strip(scene, "02 Hero").color_tag == cut.MARKS['LOW'])
            fe_look = mod("studio.film_edit")
            graded = cut.section_strip(scene, "02 Hero")
            fe_look.set_look(graded, exposure=1.0, shadows=0.4)
            balance = graded.modifiers.get(fe_look.COLOR_MOD)
            check("colour & light: +1 stop doubles the light, shadows lift; reset is neutral",
                  balance is not None and abs(balance.color_balance.gain[1] - 2.0) < 0.01
                  and abs(balance.color_balance.lift[0] - 1.1) < 1e-3, balance and tuple(balance.color_balance.gain))
            fe_look.reset_look(graded)
            tools = mod("studio.film_tools")
            no_cam = {m.name for m in scene.timeline_markers if m.camera is None}
            check("check: sections without a camera are found",
                  {p[0] for p in tools.problems(scene) if p[1] == 'NO_CAMERA'} == no_cam, tools.problems(scene))
            again = next((m for m in scene.timeline_markers if m.camera is not None), None)
            if again is not None:                     # render again though nothing changed
                span = next((s, e) for n, s, e, _m in cut.sections_of(scene) if n == again.name)
                was = cut.frame_status(again.name, span[0], span[1], fresh=True)["quality"] or 'HIGH'
                bpy.ops.studio.add(mode='ANIM', section=again.name, force=True)
                queued = [i for i in scene.studio.items if i.mode == 'ANIM' and i.section == again.name]
                check("force: the section is queued again in the quality it has",
                      len(queued) == 1 and queued[0].quality == was and queued[0].percentage == 100,
                      [(i.section, i.quality) for i in scene.studio.items])
                for index in reversed(range(len(scene.studio.items))):
                    if scene.studio.items[index].mode == 'ANIM':
                        scene.studio.items.remove(index)
            fe = mod("studio.film_edit")
            before = cut.strip_range(scene)[1]
            fe.set_hold(scene, "01 Intro", 10)        # in place: what follows moves along, the cross stays
            after_hold = cut.strip_range(scene)[1]
            fe.freeze(scene, 20, 5)
            parts = [s for s in cut.ours(scene) if cut._get(s, cut.SECTION_KEY) == "01 Intro"
                     and cut._get(s, cut.KIND_KEY) == "SECTION"]
            kinds = sorted(cut._get(s, cut.KIND_KEY) for s in cut.ours(scene))
            check("hold and freeze in place: the rest moves along, the cross stays",
                  after_hold == before + 10 and cut.strip_range(scene)[1] == after_hold + 5 and len(parts) == 2
                  and "CROSS" in kinds and "FREEZE" in kinds, (before, after_hold, cut.strip_range(scene), kinds))
            places = [(s.name, s.frame_final_start, s.channel) for s in cut.ours(scene)]
            check("after a render the strips stay where they are", cut.sync(scene) == "synced"
                  and [(s.name, s.frame_final_start, s.channel) for s in cut.ours(scene)] == places)
            fe.set_look(intro, contrast=20.0, warmth=0.5)
            fe.fade(scene, 'IN', 8)
            fe.film_look(scene)
            bars = fe.letterbox(scene, 2.39)
            look_mod = intro.modifiers.get(fe.LOOK_MOD)
            state = fe.state(scene)
            check("look, fade, film look, letterbox", look_mod is not None and abs(look_mod.contrast - 20) < 1e-3
                  and intro.modifiers.get(fe.COLOR_MOD) is not None and fe._ours(scene, "FADE")
                  and fe.film_look(scene, create=False) is not None and len(bars) == 2
                  and state["playhead"] == cut.playhead(scene), (look_mod, [s["name"] for s in state["strips"]]))
        finally:
            cut._remove_ours(scene)
            if cut.editor(scene) is not None:
                bpy.data.scenes.remove(cut.editor(scene))
            if not had_editor and scene.sequence_editor is not None:
                scene.sequence_editor_clear()
            scene.render.use_sequencer = keep_seq
            cut._anim_folder = keep_folder
    finally:
        for m in markers:
            scene.timeline_markers.remove(m)
        scene.studio.motion_steps.clear()
        scene.studio.cut_items.clear()
        bag = cut._channelbag(scene)
        for fc in list(bag.fcurves if bag is not None else []):
            if fc.data_path == "view_settings.exposure":
                bag.fcurves.remove(fc)
        scene.frame_start, scene.frame_end = keep[0], keep[1]
        scene.frame_set(keep[2])
        scene.camera, scene.view_settings.exposure = keep[3], keep[4]
        for obj in made:
            pivot = orbit.pivot_of(obj)
            bpy.data.objects.remove(obj)
            if pivot is not None:
                bpy.data.objects.remove(pivot)


@section
def film_fixes():
    """A film names its camera even if it is the scene's now; a new film moves older frames away."""
    core, film = mod("studio.core"), mod("studio.film")
    scene = bpy.context.scene
    plan = core.PlanOverrides(scene)
    plan.set(scene, "camera", scene.camera)
    check("plan: the camera always", any(p == "camera" for p, _v in plan.as_list()), plan.as_list())
    folder = os.path.join(TMP, "film_again")
    os.makedirs(folder, exist_ok=True)
    for n in (1, 2):
        open(os.path.join(folder, "Hero_%04d.png" % n), "w").write("old")
    moved = film._clear_old_frames(folder, "Hero")
    archived = os.listdir(os.path.join(TMP, "archiv"))
    check("new film: older frames go to archiv", moved == 2 and not any(n.endswith(".png") for n in os.listdir(folder))
          and len(archived) == 1, (moved, archived))
    # a finished section film: its video next to the films as "01 Intro.mp4", the one before into archiv
    anim = os.path.join(TMP, "anim_publish")
    film_dir = os.path.join(anim, "Film_low_01")
    os.makedirs(os.path.join(film_dir, ".film"), exist_ok=True)
    with open(os.path.join(film_dir, ".film", "job.json"), "w") as f:
        json.dump({"scene": "S", "out": os.path.join(film_dir, "Film"), "runs": [[1, 50, "01_Intro"]],
                   "titles": {"01_Intro": "01 Intro"}}, f)
    video = os.path.join(film_dir, "Film.mp4")
    for _round in (1, 2):
        open(video, "w").write("video %d" % _round)
        film._publish(film_dir, video, [])
    published = os.path.join(anim, "01 Intro.mp4")
    check("film video under the section's name, the older one in archiv", open(published).read() == "video 2"
          and len(os.listdir(os.path.join(anim, "archiv"))) == 1, os.listdir(anim))


@section
def explosion_view():
    """A group follows its handle only with the explosion open; closed, the parts are as in CAD."""
    explode = mod("studio.explode")
    scene = bpy.context.scene
    parts = []
    for x in (0.0, 0.3):
        obj = bpy.data.objects.new("Explode Part %.1f" % x, bpy.data.meshes.new("Explode Part"))
        scene.collection.objects.link(obj)
        obj.location = (x, 0, 0)
        parts.append(obj)
    bpy.context.view_layer.update()
    keep = scene.get(explode.PROP)
    try:
        handle = explode.make_group(scene, "Test Group", parts)
        explode.set_value(scene, 1.0)
        handle.location.z += 0.5
        explode.set_value(scene, 1.0)
        opened = parts[1].matrix_world.translation.z
        explode.set_value(scene, 0.0)
        closed = parts[1].matrix_world.translation.z
        check("explosion: open the group follows its handle, closed it is as in CAD",
              abs(opened - 0.5) < 1e-4 and abs(closed) < 1e-4 and not explode.moved_parts(scene), (opened, closed))
    finally:
        explode.remove_group(scene, "Test Group")
        for obj in parts:
            bpy.data.objects.remove(obj)
        if keep is None:
            scene.pop(explode.PROP, None)
        else:
            scene[explode.PROP] = keep


@section
def unfinished_frames_and_folders():
    """Strips never point at empty or unfinished frame files; new project folders never nest."""
    cut = mod("studio.cut")
    names = ["a_0001.png", "a_0002.png", "a_0003.png"]
    check("unfinished frames show the frame before (or none)",
          cut.shown_names(names, {"a_0001.png", "a_0003.png"}) == ["a_0001.png", "a_0001.png", "a_0003.png"]
          and cut.shown_names(names, {"a_0003.png"}) == ["a_0003.png"] * 3 and cut.shown_names(names, set()) is None)
    folder = os.path.join(TMP, "frames_settle")
    os.makedirs(folder, exist_ok=True)
    for n, size in (("a_0001.png", 10), ("a_0002.png", 0), ("a_0003.png", 10)):
        with open(os.path.join(folder, n), "wb") as f:
            f.write(b"x" * size)
    old = time.time() - 60
    os.utime(os.path.join(folder, "a_0001.png"), (old, old))
    check("an empty placeholder and a frame just written are not done",
          cut.finished_files(folder) == {"a_0001.png"}, cut.finished_files(folder))
    autosave = mod("autosave")
    root = os.path.join(TMP, "projects_root")
    inner = os.path.join(root, "Lampe")
    os.makedirs(inner, exist_ok=True)
    open(os.path.join(inner, "Lampe" + autosave.main_suffix() + ".blend"), "wb").close()
    check("a file saved inside a project folder gets its folder next to it", autosave.projects_root(inner) == root
          and autosave.projects_root(root) == root, autosave.projects_root(inner))
    topic = os.path.join(TMP, "projects_topic", "VELO-LUME")
    for sub in (autosave.backup_dir_name(), autosave.archive_dir_name()):
        os.makedirs(os.path.join(topic, sub), exist_ok=True)
    main = os.path.join(topic, "VELO" + autosave.main_suffix() + ".blend")
    open(main, "wb").close()
    check("a folder laid out as a project counts as one, whatever its name",
          autosave.in_project_folder(main) and autosave.project_folder_target(main) is None)


@section
def section_cameras():
    """Every section renders from its own camera: the camera is the job's last override, set after the
    timeline markers are unbound (Blender 5.3 jumps to another marker's camera when one is unbound)."""
    core = mod("studio.core")
    scene = bpy.context.scene
    made = []
    for n in range(4):
        cam = bpy.data.objects.new("T_section_cam_%d" % n, bpy.data.cameras.new("T_section_cam_%d" % n))
        scene.collection.objects.link(cam)
        marker = scene.timeline_markers.new("9%d T" % n, frame=5000 + n * 10)
        marker.camera = cam
        made.append((cam, marker))
    keep = [(m, m.camera) for m in scene.timeline_markers]
    before = scene.camera
    wrong = []
    try:
        for cam, _marker in made:
            plan = core.PlanOverrides(scene)
            core.prepare_job(scene, cam, 'HIGH', os.path.join(TMP, "x"), 'NORMAL', 50, 50, animation=True, ov=plan)
            pairs = plan.as_list()
            if pairs[-1] != ["camera", {"object": cam.name}]:
                wrong.append("camera not last for %s" % cam.name)
            for path, value in pairs:                 # like the film's process: markers unbound, then the camera
                if path.startswith("timeline_markers") or path == "camera":
                    head, _, attr = path.rpartition(".")
                    setattr(scene.path_resolve(head) if head else scene, attr,
                            bpy.data.objects.get(value["object"]) if isinstance(value, dict) else value)
            if scene.camera != cam:
                wrong.append("%s rendered from %s" % (cam.name, scene.camera.name if scene.camera else None))
            for marker, camera in keep:
                marker.camera = camera
        check("4 sections, 4 cameras: each renders from its own", not wrong, wrong)
    finally:
        scene.camera = before
        for cam, marker in made:
            scene.timeline_markers.remove(marker)
            bpy.data.objects.remove(cam)


@section
def cancel_stops_films():
    """Cancel: the queue of this Mac, the films that run and the film entries that wait."""
    import subprocess
    film, runner = mod("studio.film"), mod("studio.runner")
    scene = bpy.context.scene
    work = os.path.join(tempfile.mkdtemp(prefix="film_"), ".film")
    os.makedirs(work)
    proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
    with open(os.path.join(work, "status.json"), "w") as f:
        json.dump({"state": "rendering", "total": 10, "done": 1, "updated": time.time(), "pid": proc.pid}, f)
    film.register_process(work, os.path.dirname(work), "T film", proc.pid)
    item = scene.studio.items.add()
    item.mode = 'ANIM'
    stopped = runner.cancel_all(scene)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    check("cancel stops the running film and drops the film entries that wait",
          "T film" in stopped and proc.returncode is not None and proc.returncode != 0
          and not any(f["dir"] == work for f, _s, _k in film.films(fresh=True))
          and not any(i.mode == 'ANIM' for i in scene.studio.items), (stopped, proc.returncode))


@section
def render_farm_folder():
    """The render farm's folder protocol: jobs, frames (> 0 bytes), states, workers; .part is never a job."""
    farm = mod("studio.renderfarm")
    root = tempfile.mkdtemp(prefix="farm_")
    for sub in ("queue", "renders/a_job", "_worker/status"):
        os.makedirs(os.path.join(root, sub))
    with open(os.path.join(root, "queue", "a_job.json"), "w") as f:
        json.dump({"scene": "Scene", "start": 1, "end": 3, "format": "PNG", "sent_by": "t", "note": "n"}, f)
    open(os.path.join(root, "queue", "a_job.blend"), "wb").close()
    open(os.path.join(root, "queue", "b_job.blend.part"), "wb").close()
    with open(os.path.join(root, "renders", "a_job", "a_job_0001.png"), "wb") as f:
        f.write(b"x" * 10)
    open(os.path.join(root, "renders", "a_job", "a_job_0002.png"), "wb").close()      # being rendered
    with open(os.path.join(root, "_worker", "status", "MD-1-render.txt"), "w") as f:
        f.write("MD-1 | rendert a_job | 2026-11-16 18:00\n")
    view = farm.scan(root)
    job = view["jobs"][0] if view["jobs"] else {}
    check("farm: one job, 1 of 3 frames, rendering; the .part is no job; the worker is read",
          len(view["jobs"]) == 1 and job.get("done") == 1 and job.get("total") == 3
          and job.get("state") == 'rendering' and view["halves"] == ["b_job.blend.part"]
          and view["workers"] == [("MD-1", "rendert a_job", "2026-11-16 18:00")], view)
    open(os.path.join(root, "queue", "a_job.done"), "w").close()
    check("farm off: the queue renders on this Mac", not farm.takes(bpy.context.scene, None))
    check("farm: .done → done; job names are safe", farm.scan(root)["jobs"][0]["state"] == 'done'
          and farm.job_name("/x/VELO now.blend", "Szene 1", 0).startswith("VELO_now_19")
          and farm.job_name("/x/VELO now.blend", "Szene 1", 0).endswith("_Szene_1"))
    packed = os.path.join(root, "packed.blend")             # an upload that broke off goes on where it stopped
    with open(packed, "wb") as f:
        f.write(os.urandom(3 * farm.CHUNK + 1234))
    size = os.path.getsize(packed)
    with open(packed, "rb") as src, open(os.path.join(root, "queue", "c_job.blend.part"), "wb") as dst:
        dst.write(src.read(2 * farm.CHUNK + 99))
    up = {"root": root, "job": "c_job", "size": size, "scene": "Scene", "start": 1, "end": 2, "format": "PNG",
          "sent_by": "t", "note": "c", "kind": 'ANIM', "phase": "copying", "packed": packed, "progress": 0.0}
    keep_jobs = list(farm._jobs)
    farm._copy(up, packed)
    resumed = up["phase"] == "sent" and os.path.getsize(os.path.join(root, "queue", "c_job.blend")) == size
    gone = dict(up, job="d_job", root=os.path.join(root, "missing"), phase="copying")
    farm._copy(gone, packed)
    check("farm upload: goes on from the half file, checked; a broken one is tried again later",
          resumed and gone["phase"] == "retry" and gone["tries"] == 1 and gone["retry_at"] > time.time(),
          (up["phase"], up.get("error"), gone.get("phase"), gone.get("error")))
    farm._jobs[:] = keep_jobs
    farm._save_registry()
    # after a restart: the list of this .blend comes back; jobs of the file on the share are taken over
    blend = os.path.join(root, "Restart Test.blend")
    farm._jobs.append(dict(up, job="r_job", phase="sent", blend=blend))
    farm._save_registry()
    farm._jobs[:] = keep_jobs
    farm._load_registry(blend)
    back = [j["job"] for j in farm._jobs if j.get("blend") == blend]
    host = socket.gethostname().split(".")[0]
    name = farm.job_name(blend, "01 Intro")
    with open(os.path.join(root, "queue", name + ".json"), "w") as f:
        json.dump({"scene": "Scene", "start": 1, "end": 9, "format": "PNG", "sent_by": "me@" + host,
                   "note": "01 Intro · HQ"}, f)
    open(os.path.join(root, "queue", name.replace("01_Intro", "02_Hero") + ".blend.part"), "wb").close()
    farm._adopt(root, farm._file_prefix(blend), host, {"01 Intro": (os.path.join(root, "F"), "01_Intro")},
                set(back), os.path.join(root, "A"))
    taken = farm._restart["adopt"] or []
    check("after a restart: the file's list is back, its job on the share is taken over, a half upload found",
          back == ["r_job"] and [j["job"] for j in taken] == [name] and taken[0]["section"] == "01 Intro"
          and len(farm._restart["halves"]) == 1, (back, taken, farm._restart["halves"]))
    farm._jobs[:] = keep_jobs
    farm._restart.update(adopt=None, halves=[])
    try:
        os.remove(farm._registry_path(blend))
    except OSError:
        pass
    old = farm.job_name("/x/Clean.blend", "Old")                # a day after it was done: off the share
    for ext, body in ((".json", json.dumps({"sent_by": "me@" + socket.gethostname().split(".")[0]})),
                      (".done", "done"), (".blend", "x")):
        with open(os.path.join(root, "queue", old + ext), "w") as f:
            f.write(body)
    os.makedirs(os.path.join(root, "renders", old), exist_ok=True)
    gone_now = farm.clean_share(root, socket.gethostname().split(".")[0])
    gone_later = farm.clean_share(root, socket.gethostname().split(".")[0], now=time.time() + 2 * 86400)
    check("share: a finished job of this Mac goes a day after it was done",
          old not in gone_now and old in gone_later and not os.path.exists(os.path.join(root, "queue", old + ".blend"))
          and not os.path.isdir(os.path.join(root, "renders", old)), (gone_now, gone_later))
    keep_view = dict(farm._view)
    farm._view.update(busy=True, since=time.monotonic() - 60)
    hung = farm.stuck()
    farm.reconnect(mount=False)
    check("farm: a hung look shows, Reconnect gives it up and looks again", hung and farm._view["gen"] > keep_view["gen"],
          farm._view)
    farm._view.update(keep_view)
    jobs = [{"job": "a_2_x", "phase": "sent", "root": root, "kind": 'ANIM', "start": 1, "end": 3, "ext": "png"},
            {"job": "a_1_y", "phase": "sent", "root": root, "kind": 'ANIM', "start": 1, "end": 3, "ext": "png"}]
    for job in jobs:
        open(os.path.join(root, "queue", job["job"] + ".json"), "w").close()
    keep, farm._jobs[:] = list(farm._jobs), jobs
    try:
        farm._reconcile()                      # a_1_y is to render after a_2_x but sorts before it: it waits
        held = os.path.exists(os.path.join(root, "queue", "a_1_y.json.hold"))
        os.makedirs(os.path.join(root, "renders", "a_2_x"))
        farm.remove_from_share(root, "a_2_x", 1, 3, "png")
        farm._jobs[:] = jobs[1:]
        farm._reconcile()
        check("farm: order kept by holding a job; removing one frees the next and lets workers skip its frames",
              held and os.path.exists(os.path.join(root, "queue", "a_1_y.json"))
              and not os.path.exists(os.path.join(root, "queue", "a_2_x.json"))
              and len(os.listdir(os.path.join(root, "renders", "a_2_x"))) == 3)
    finally:
        farm._jobs[:] = keep
    old = {"job": "old_job", "phase": "sent", "root": root, "kind": 'ANIM', "start": 1, "end": 3, "ext": "png",
           "folder": os.path.join(root, "Frames", "S1"), "note": "S1"}
    open(os.path.join(root, "queue", "old_job.json"), "w").close()
    keep, farm._jobs[:] = list(farm._jobs), [old]
    try:
        farm.supersede(old["folder"])
        for _wait in range(40):                      # the share is written in a thread
            if os.path.exists(os.path.join(root, "queue", "old_job.done")):
                break
            time.sleep(0.05)
        frames = sorted(os.listdir(os.path.join(root, "renders", "old_job")))
        check("sent again: the older job skips its frames left and is done, nothing is deleted on the share",
              old["phase"] == "done" and len(frames) == 3 and os.path.exists(os.path.join(root, "queue", "old_job.done"))
              and os.path.exists(os.path.join(root, "queue", "old_job.json")), frames)
    finally:
        farm._jobs[:] = keep
    cut = mod("studio.cut")
    blank = cut.blank_frame(root, "S1", 64, 36)
    check("a frame not rendered yet shows an empty frame of the strip's size",
          blank and cut.png_size(os.path.join(root, blank)) == (64, 36), blank)
    now = time.time()
    keep_stamps = dict(farm._stamps)
    farm._stamps.clear()
    view = {"worker_rows": [("MD-1", "wartet", "", True, 10.0), ("MD-2", "rendert x", "", True, 1000.0),
                            ("MD-3", "rendert x", "", True, 60.0)]}
    states = {n: st for n, st, _j, _s in farm.worker_states(view)}
    view["last_frame"] = {"md-2": now - 30}          # its frames on the share show it renders
    alive = {n: st for n, st, _j, _s in farm.worker_states(view)}
    farm._stamps.update(keep_stamps)
    check("workers: waiting, offline (rendert, but no line and no frame for an hour), loading; frames on "
          "the share keep a worker online",
          states == {"MD-1": 'waiting', "MD-2": 'offline', "MD-3": 'loading'} and alive["MD-2"] == 'rendering',
          (states, alive))
    keep_events, keep_stamps = list(farm._events), dict(farm._stamps)
    farm._events[:] = [(now - 30 * i, "md-1", 60.0) for i in range(20)]    # one frame every 30 s
    farm._stamps.clear()
    farm._stamps["md-1"] = [(1000.0 + 72 * i, None) for i in range(10)]
    jobs = [{"job": "r", "phase": "sent", "kind": 'ANIM', "start": 1, "end": 100, "have": 40, "note": "A"},
            {"job": "w", "phase": "sent", "kind": 'ANIM', "start": 1, "end": 50, "have": 0, "note": "B"}]
    keep, farm._jobs[:] = list(farm._jobs), jobs
    try:
        rate = farm.farm_rate(now)
        have, total, secs, _sending = farm.summary()
        check("farm stats: frames per minute, a Mac's seconds per frame, when a waiting job starts, one total",
              abs(rate - 2.0) < 0.3 and farm.seconds_per_frame("md-1") == 72.0
              and abs(farm.starts_in(jobs[1]) - 60 * 30) < 200 and (have, total) == (40, 150) and secs,
              (rate, farm.seconds_per_frame("md-1"), farm.starts_in(jobs[1]), have, total, secs))
    finally:
        farm._jobs[:] = keep
        farm._events[:] = keep_events
        farm._stamps.clear()
        farm._stamps.update(keep_stamps)


@section
def animation_tracks():
    """Animation tab: what moves in the section, moved, faster, a curve preset, removed."""
    tr = mod("studio.anim_tracks")
    scene = bpy.context.scene
    obj = bpy.data.objects.new("T Magnet", None)
    scene.collection.objects.link(obj)
    keep = scene.frame_current, scene.frame_start, scene.frame_end
    try:
        scene.frame_start, scene.frame_end = 1, 100
        for frame, x in ((10, 0.0), (30, 1.0)):
            obj.location.x = x
            obj.keyframe_insert("location", index=0, frame=frame)
        tr.forget()
        found = {t["id"]: t for t in tr.tracks(scene, 1, 100)}
        t = found.get("OB:T Magnet")
        check("a keyed object is a track with its frames", t is not None and (t["first"], t["last"]) == (10, 30),
              list(found))
        tr.retime(t, 1, 100, 20, 10)
        tr.set_curve(t, 1, 100, 'SNAP')
        fc = obj.animation_data.action and tr.fcurves_of(obj)[0]
        xs = [round(p.co.x) for p in fc.keyframe_points]
        check("moved to 20, faster (10 frames), magnet snap curve", xs == [20, 30]
              and fc.keyframe_points[0].interpolation == 'EXPO' and tr.curve_of(t, 1, 100) == 'SNAP', xs)
        check("removed again", tr.clear(t, 1, 100) == 2
              and "OB:T Magnet" not in {x["id"] for x in tr.tracks(scene, 1, 100)})
    finally:
        bpy.data.objects.remove(obj)
        scene.frame_current, scene.frame_start, scene.frame_end = keep
        tr.forget()


@section
def tabs_without_switch():
    """Five tabs in the 3D sidebar: Camera, Environment, Material, Animation, Render."""
    ui = mod("studio.ui_common")
    wm = bpy.context.window_manager
    keep = wm.studio_tab
    wm.studio_tab = 'ANIM'
    check("five tabs, Animation between Material and Render",
          ui.studio_tab(bpy.context) == 'ANIM'
          and ui.visible_tabs() == ('RENDER', 'LIGHT', 'MATERIAL', 'ANIM', 'QUEUE'), ui.visible_tabs())
    anim = mod("studio.tab_anim")
    scene = bpy.context.scene
    scene["Test LED Lumen"] = 12.5
    check("the scene's own values show in the Animation tab", "Test LED Lumen" in anim.scene_values(scene))
    del scene["Test LED Lumen"]
    skills = mod("skills")
    found = {s["name"]: s for s in skills.list_skills(mod("core").workdir())}
    check("ANIM in the skill row, CUT comes by itself", ("anim" not in found or found["anim"]["in_chat"])
          and ("cut" not in found or not found["cut"]["in_chat"]))
    wm.studio_tab = keep


@section
def skills_switch_themselves():
    """A message about light turns LIGHT on for it; SORT switches itself off after one use."""
    skills = mod("skills")
    core = mod("core")
    names = [s["name"] for s in skills.auto_for(core.workdir(), "mach das Licht weicher und räum auf")]
    sort = skills.find(core.workdir(), "sort")
    check("skills: on by words, SORT once", "light" in names and "sort" in names
          and (sort is None or skills.is_once(sort)), names)


@section
def project_tools():
    """CAD rules give materials, a slider drives several materials, a file name template."""
    project, core_s = mod("studio.project"), mod("studio.core")
    mat = bpy.data.materials.new("TPU Rule")
    part = bpy.data.objects.new("Pod Huelle links", bpy.data.meshes.new("Pod Huelle links"))
    bpy.context.scene.collection.objects.link(part)
    keep = project.cad_rules()
    try:
        project.set_cad_rules([{"match": "huelle", "material": "TPU Rule"}])
        done = project.apply_cad_rules([part])
        check("CAD rules: material after the name", part.active_material == mat and done, done)
        slider = bpy.data.materials.new("Slider Test")
        slider.use_nodes = True
        changed = project.material_slider("Rauheit Test", [slider], "Roughness", 0.3)
        check("material slider: scene property drives the material", changed == ["Slider Test"]
              and bpy.context.scene["Rauheit Test"] == 0.3, changed)
        bpy.context.scene["Farbe"] = "Rot"
        stem = core_s.template_stem("{Farbe} - {camera} {unknown}", "P_front_01")
        check("name template", stem == "Rot - P_front_01", stem)
    finally:
        project.set_cad_rules(keep)
        bpy.data.objects.remove(part)
        for key in ("Rauheit Test", "Farbe"):
            for scene in bpy.data.scenes:
                if key in scene:
                    del scene[key]


@section
def reliability():
    """A long code step is stopped (Blender would hang); a Blender in the background leaves the crash
    marker alone; after a crash the newest copy of the file and its chat come back."""
    bt, autosave, chats, core = mod("blender_tools"), mod("autosave"), mod("chats"), mod("core")
    keep_limit, bt.CODE_TIME_LIMIT = bt.CODE_TIME_LIMIT, 0.5
    try:
        t0 = time.monotonic()
        out, ok = bt.execute_python("n = 0\nwhile True:\n    try:\n        n += 1\n    except Exception:\n        pass")
        check("a code step that runs too long is stopped", not ok and "Stopped after" in out
              and time.monotonic() - t0 < 5, out[-200:])
        out, ok = bt.execute_python("print(7 * 6)")
        check("the next step runs normally", ok and "42" in out, out)
    finally:
        bt.CODE_TIME_LIMIT = keep_limit
    marker = os.path.join(core.workdir(), "running.json")
    before = os.path.getmtime(marker) if os.path.exists(marker) else None
    autosave.write_marker()
    check("background Blender: the crash marker is not written",
          (os.path.getmtime(marker) if os.path.exists(marker) else None) == before)
    folder = tempfile.mkdtemp(prefix="newest_")
    main = os.path.join(folder, "Lamp_now.blend")
    os.makedirs(os.path.join(folder, autosave.backup_dir_name()))
    backup = os.path.join(folder, autosave.backup_dir_name(), "Lamp_2026-11-16_20-06.blend")
    for path, age in ((main, 600), (backup, 60)):
        open(path, "wb").close()
        os.utime(path, (time.time() - age, time.time() - age))
    check("after a crash: the newest copy of the file is opened", autosave.newest_copy(main) == backup,
          autosave.newest_copy(main))
    film = mod("studio.film")
    copies = tempfile.mkdtemp(prefix="copies_")
    first, second = os.path.join(copies, "a", "scene.blend"), os.path.join(copies, "b", "scene.blend")
    for path in (first, second):
        os.makedirs(os.path.dirname(path))
        film._scene_copy(path)
    check("two films without an edit share one copy of the file (no new GBs)",
          os.path.samefile(first, second) and not os.path.exists(first + "1"))
    film.drop_scene_copy(os.path.dirname(first))
    check("a film done: its copy of the file goes", not os.path.exists(first) and os.path.exists(second))
    temp_file = os.path.join(tempfile.gettempdir(), "VELO_now_123_autosave.blend")
    check("Blender's auto save in a temporary folder never gets a project folder there",
          autosave.is_temp(temp_file) and autosave.project_folder_target(temp_file) is None
          and not autosave.is_temp(os.path.join(tempfile.gettempdir(), "x", "Stuhl.blend")))
    keep = core._chats
    core._chats = [{"id": "c-old", "updated": 1, "file": chats._file_key(main), "messages": []},
                   {"id": "c-new", "updated": 2, "file": chats._file_key(backup),
                    "messages": [{"role": "user", "text": "hi"}]},
                   {"id": "c-other", "updated": 3, "file": "/x/other", "messages": []}]
    saved = list(core.messages), core.session_id
    try:
        core.messages.clear()
        got = chats.restore_for_file(main)
        check("the file's last chat comes back with it (a backup copy counts as the file)",
              got == "c-new" and core.session_id == "c-new", got)
    finally:
        core.messages[:], core.session_id = saved
        core._chats = keep
    menus, wm = mod("ui_menus"), bpy.context.window_manager
    old_draft = core._read_json(menus.DRAFT_FILE, None)
    try:
        menus.save_draft("noch nicht gesendet")
        menus._clearing = True
        wm.claude_prompt = ""
        menus._clearing = False
        menus.restore_draft()
        back = wm.claude_prompt
        menus._clear_prompt(wm)
        check("the message not sent yet comes back after a restart; sending clears it",
              back == "noch nicht gesendet" and core._read_json(menus.DRAFT_FILE, {}).get("text") == "", back)
    finally:
        menus._clearing = False
        if old_draft is not None:
            core._write_json(menus.DRAFT_FILE, old_draft)


ok = all(results)
print("FUNCTIONAL_OK" if ok else "FUNCTIONAL_FAIL", "%d/%d" % (sum(results), len(results)))

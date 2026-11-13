"""Headless functional checks of Claude Studio (never opens a window).

    BLENDER_USER_RESOURCES=<test profile> Blender -b --python tests/functional.py

The extension must be installed and enabled in that profile. Prints one line per check
and FUNCTIONAL_OK / FUNCTIONAL_FAIL at the end. Renders are tiny (Cycles, few samples).
"""

import math
import os
import sys
import tempfile
import time
import traceback

import bpy
from mathutils import Vector

PKG = next(n for n in sys.modules if n.startswith("bl_ext.") and n.endswith(".AI_Studio"))
mod = lambda name: sys.modules[PKG + "." + name]   # noqa: E731
TMP = tempfile.mkdtemp(prefix="claude_studio_test_")
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, detail))


def section(func):
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
    rq, cam = scene.render_studio, scene.camera
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
    bpy.ops.render_studio.toggle_ortho()
    sensor = cam.data.sensor_width
    check("to ortho: the framing stays (scale from the distance)", cam.data.type == 'ORTHO'
          and abs(cam.data.ortho_scale - 2.0 * sensor / cam.data.lens) < 1e-4, cam.data.ortho_scale)
    cam.data.ortho_scale *= 2
    bpy.ops.render_studio.toggle_ortho()
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
    bpy.ops.render_studio.material_new()
    check("new plain material", cube.active_material is not None and cube.active_material.name.startswith("Material"))
    bpy.ops.render_studio.material_assign(name="18DS115 | Gummi")
    check("assign from library", cube.active_material.name == "18DS115 | Gummi")
    check("recent: the assigned one first", tab.recent()[:1] == ["18DS115 | Gummi"], tab.recent()[:3])
    recent = dict(tab._library_groups()).get(tab.RECENT_GROUP, [])
    check("recent group on top", recent and recent[0][0].name == "18DS115 | Gummi")


@section
def material_presets():
    cube = bpy.data.objects["Cube"]
    bpy.context.view_layer.objects.active = cube
    bpy.ops.render_studio.material_new(preset="Chrome")
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
    check("shipped skills installed", {"sort", "camera", "render", "material"} <= set(listed), sorted(listed))
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
    rs = scene.render_studio
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
    rs = scene.render_studio
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
    rs = scene.render_studio
    items, paint, lmap = mod("studio.light_items"), mod("studio.light_paint"), mod("studio.light_map")
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
    own = key.render_studio_light
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
    # the map's geometry
    rect = lmap.rect_for(1200, 40, 300, 0, 1.0)
    check("light map: between toolbar and sidebar, three times as wide as high",
          rect[0] >= 40 and rect[0] + rect[2] <= 900 and abs(rect[3] * 3 - rect[2]) < 1e-6, rect)
    back = lmap.from_map(rect, *lmap.to_map(rect, 0.3, 0.4))
    check("light map: point and angles round trip", abs(back[0] - 0.3) < 1e-6 and abs(back[1] - 0.4) < 1e-6)
    boxes = [lmap.light_box(rect, 0.0, 0.3, 1.0, 0.6), lmap.light_box(rect, 0.05, 0.3, 0.1, 0.1)]
    check("light map: a small light in front of a big one can be picked",
          lmap.pick(rect, boxes, *lmap.to_map(rect, 0.05, 0.3)) == 1
          and lmap.pick(rect, boxes, *lmap.to_map(rect, -0.4, 0.3)) == 0
          and lmap.pick(rect, boxes, rect[0] + 2, rect[1] + 2) is None)
    edge = [lmap.light_box(rect, math.pi - 0.05, 0.3, 0.4, 0.3)]
    check("light map: a light at the edge is picked on both sides",
          lmap.pick(rect, edge, rect[0] + 3, lmap.to_map(rect, 0, 0.3)[1]) == 0)
    cut = lmap.clip(lmap.box_points(rect[0] - 50, rect[1] - 50, rect[0] + 50, rect[1] + 50), rect)
    check("light map: shapes are cut at the map's edge", len(cut) == 4 and len(lmap.fan(cut)) == 6
          and all(px >= rect[0] - 1e-6 and py >= rect[1] - 1e-6 for px, py in cut), cut)
    check("light map: nothing left of a shape outside",
          not lmap.clip(lmap.box_points(rect[0] - 90, rect[1], rect[0] - 50, rect[1] + 20), rect)
          and len(lmap.shape_tris(rect[0] + 100, rect[1] + 50, 20, 10, True, rect, edge=2.0)) == 28 * 6)
    check("light map: the picture behind lines up with the camera", abs(lmap.backdrop_u(-math.pi / 2, 0.0) - 0.25) < 1e-9)
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
    keys = paint.RS_WT_light_place.bl_keymap
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
def shots_presets_check():
    scene = bpy.context.scene
    rs = scene.render_studio
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
    bpy.ops.render_studio.shot_add()
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
          and {"light", "hdri"} <= set(item[1]), item)
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
    rq = scene.render_studio
    rq.items.clear()
    rq.quality = 'HIGH'
    bpy.ops.render_studio.add()
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
    check("worker on the GPU without set-up devices", "RS devices: METAL" in worker_log, worker_log[:300])
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
    rq = scene.render_studio
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
            bpy.ops.render_studio.add()
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
    check("scheme in the file: P_/O_, the view, a number", [o.name for o in made] ==
          ["P_top_01", "P_top_02", "O_top_01", "P_detail-pod_01", "O_front_01"]
          and made[1].data.name == "P_top_02", [o.name for o in made])
    made[4].data.type = 'PERSP'
    cams.tidy_names()
    check("camera switched to perspective: O_ becomes P_", made[4].name == "P_front_01", made[4].name)
    check("rename in the studio: the word is enough", cams.rename(made[3], "Seite") == "P_side_01"
          and made[3].data.name == "P_side_01")
    check("rename: a name in the scheme stays", cams.rename(made[3], "P_side_07") == "P_side_07")
    made[3].render_studio_camera.short_name = "detail"
    check("rename field of the Render tab", made[3].name == "P_detail_01", made[3].name)
    scene.camera = made[2]
    bpy.ops.render_studio.duplicate_camera()
    copy = scene.camera
    check("duplicate camera: next number, not .001", copy.name == "O_top_02" and copy.data.name == "O_top_02",
          copy.name)
    key = bpy.data.objects.new("Key", bpy.data.lights.new("Key", 'AREA'))
    light = mod("studio").lights.duplicate_light(scene, key)
    check("duplicate light: A_key_01, not .001", light.name == "A_key_01", light.name)
    sun = bpy.data.objects.new("Sonne", bpy.data.lights.new("Sonne", 'SUN'))
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
                                         ("Camera", "Camera | Packshot", "Archive V2", "Set")),
          [c.name for c in bpy.data.collections])
    for name in ("Camera | Packshot", "Archive V2", "Set"):
        bpy.data.collections.remove(bpy.data.collections[name])
    # 1 mm steps
    rna = bpy.types.RS_Settings.bl_rna.properties if hasattr(bpy.types, "RS_Settings") else scene.render_studio.bl_rna.properties
    check("camera lengths in 1 mm steps", all(abs(rna[k].step - 0.05) < 1e-6 for k in
          ("orbit_distance", "orbit_height", "orbit_side_offset", "cam_ortho_scale", "cam_focus_distance",
           "cam_clip_start", "cam_clip_end", "light_orbit_distance")))
    scene.camera = made[2]
    scene.render_studio.cam_ortho_scale = 1.234
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
          and scene.unit_settings.length_unit == 'MILLIMETERS' and scene.cycles.use_denoising
          and scene.cycles.samples == 512 and scene.cycles.max_bounces == 12, said)
    check("product setup: Metal for Cycles on this Mac", cprefs.compute_device_type == 'METAL'
          and any(d.use and d.type != 'CPU' for d in cprefs.devices), cprefs.compute_device_type)
    check("product setup explains itself", len(said) >= 8 and any("millimeters" in s for s in said))
    rs.apply(scene)   # pressed twice: Undo still goes back to the very first state
    check("undo: everything as before", rs.undo(scene) and (scene.render.engine, scene.cycles.samples,
          scene.view_settings.view_transform, scene.unit_settings.length_unit, scene.cycles.max_bounces) == start
          and cprefs.compute_device_type == device_before and not rs.is_set(scene))
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
        while time.monotonic() < end and text.count("RS loaded") < 2:
            time.sleep(0.3)
            text = open(runner.worker_log_path(), encoding="utf-8", errors="replace").read()
        check("worker opens new copies itself", text.count("RS loaded") == 2 and worker.proc.pid == pid
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
    bpy.ops.render_studio.material_duplicate()
    a, b, c = (o.active_material for o in objects)
    check("duplicate changes the whole group", a is b and a is not material and c is material)
    bpy.ops.render_studio.material_unlink()
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
    bpy.ops.render_studio.material_assign(name=new.name)
    slots = [s.material for o in objects for s in o.material_slots]
    check("library click switches every object with the old material",
          slots == [new, new, new, other] and old.use_fake_user, slots)
    bpy.ops.render_studio.material_assign(name=old.name)
    check("and back", [s.material for s in objects[1].material_slots] == [old, old])
    bpy.ops.render_studio.material_assign(name=other.name, only_selected=True)
    check("shift: only the active object", objects[0].active_material == other
          and objects[1].active_material == old)
    # Without an active object the last material stays the current one.
    bpy.context.view_layer.objects.active = None
    bpy.ops.object.select_all(action='DESELECT')
    tab = mod("studio").tab_material
    tab.remember(old)
    bpy.ops.render_studio.material_assign(name=new.name)
    check("switch without selecting an object", objects[1].active_material == new
          and tab.current_material(bpy.context) == new)
    bpy.ops.render_studio.material_variant()
    variant = objects[1].active_material
    check("color variant replaces it everywhere", variant.name == "Holz B V2"
          and [s.material for s in objects[1].material_slots] == [variant, variant] and new.use_fake_user,
          variant.name)
    bpy.ops.render_studio.material_select(name=variant.name)
    check("select the objects of a material", {o.name for o in bpy.context.selected_objects} == {"sw1"}
          and bpy.context.active_object == objects[1],
          ([o.name for o in bpy.context.selected_objects], bpy.context.active_object, bpy.context.mode))
    tab.toggle_favorite("Holz A")
    groups = dict(tab._library_groups("holz"))
    check("search and favorites", [m.name for m, _s in groups.get(tab.FAVORITES_GROUP, [])] == ["Holz A"]
          and "Alu" not in {m.name for items in groups.values() for m, _s in items}, list(groups))
    unused = {m.name for m in tab.unused_materials()}
    bpy.ops.render_studio.material_clean_unused()
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
    archived = bpy.data.collections.get("Archive")
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
    rq = scene.render_studio
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


ok = all(results)
print("FUNCTIONAL_OK" if ok else "FUNCTIONAL_FAIL", "%d/%d" % (sum(results), len(results)))

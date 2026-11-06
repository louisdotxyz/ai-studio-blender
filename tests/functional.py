"""Headless functional checks of Claude Studio (never opens a window).

    BLENDER_USER_RESOURCES=<test profile> Blender -b --python tests/functional.py

The extension must be installed and enabled in that profile. Prints one line per check
and FUNCTIONAL_OK / FUNCTIONAL_FAIL at the end. Renders are tiny (Cycles, few samples).
"""

import math
import os
import sys
import tempfile
import traceback

import bpy
from mathutils import Vector

PKG = next(n for n in sys.modules if n.startswith("bl_ext.") and n.endswith(".claude_assistant"))
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
    out, ok = bt.execute_python("print(g, tuple(v), twice(4), 'obj' in globals())", chat=1)
    check("plain values kept, Blender data not", ok and out.strip() == "5 (1.0, 2.0, 3.0) 8 False", out)
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
    material.use_nodes = True
    cube = bpy.data.objects["Cube"]
    cube.data.materials.clear()
    cube.data.materials.append(material)
    node = next(n for n in material.node_tree.nodes if n.bl_idname == 'ShaderNodeBsdfPrincipled')
    V.save({"targets": {"SUB": "Holz TH/" + node.name},
            "variants": {"rot": {"SUB": {"color": "#FF0000"}}, "blau": {"SUB": {"color": "#0000FF"}}}})
    before = tuple(node.inputs["Base Color"].default_value)
    log_path = os.path.join(runner.jobs_dir(), "..", "queue.log")
    mark = os.path.getsize(log_path) if os.path.exists(log_path) else 0
    V.queue(["rot", "blau"], camera="Camera", quality="LOW")
    log = open(log_path, encoding="utf-8").read()[mark:]
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
    check("component group", sorted(s for _m, s in groups.get("18DS115", [])) == ["Alu", "Gummi"], groups.keys())
    bpy.ops.render_studio.material_new()
    check("new plain material", cube.active_material is not None and cube.active_material.name.startswith("Material"))
    bpy.ops.render_studio.material_assign(name="18DS115 | Gummi")
    check("assign from library", cube.active_material.name == "18DS115 | Gummi")


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
    mark = os.path.getsize(log_path)
    rq = scene.render_studio
    rq.items.clear()
    rq.quality = 'HIGH'
    bpy.ops.render_studio.add()
    seen = []
    runner.on_render_saved.append(lambda path, *_a: seen.append(path))
    runner.RUN.start(scene)
    runner.on_render_saved.clear()
    log = open(log_path, encoding="utf-8").read()[mark:]
    check("draft first, then the image", "draft after" in log and seen and os.path.exists(seen[0])
          and not [n for n in os.listdir(runner.jobs_dir()) if n.startswith("draft_")], log[-600:])
    rq.quality = 'LOW'
    small_scene()


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


ok = all(results)
print("FUNCTIONAL_OK" if ok else "FUNCTIONAL_FAIL", "%d/%d" % (sum(results), len(results)))

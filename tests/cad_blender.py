"""Headless checks of the CAD hand-over in Blender (import with sidecar, take over, archive,
mounts, materials, variants, feedback). Loads the add-on straight from src/ (no install needed):

    Blender -b --factory-startup --python tests/cad_blender.py

Prints CAD_BLENDER_OK / CAD_BLENDER_FAIL at the end.
"""

import base64
import importlib
import importlib.util
import json
import os
import sys
import tempfile
import traceback

import bpy
from mathutils import Vector

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
spec = importlib.util.spec_from_file_location("ai_studio_src", os.path.join(SRC, "__init__.py"),
                                              submodule_search_locations=[SRC])
package = importlib.util.module_from_spec(spec)
sys.modules["ai_studio_src"] = package
spec.loader.exec_module(package)
mod = lambda name: importlib.import_module("ai_studio_src." + name)   # noqa: E731
cadswap, cadimport, cadfiles = mod("studio.cadswap"), mod("studio.cadimport"), mod("studio.cadfiles")
TMP = tempfile.mkdtemp(prefix="ai_studio_cad_blender_")
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, "" if ok else detail))


def box(name, size, location, collection=None):
    mesh = bpy.data.meshes.new(name)
    sx, sy, sz = (s / 2 for s in size)
    verts = [(x, y, z) for x in (-sx, sx) for y in (-sy, sy) for z in (-sz, sz)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    (collection or bpy.context.scene.collection).objects.link(obj)
    return obj


def export_onshape_like(objects, path):
    """Like Onshape: Z-up coordinates in the glTF, buffers as data URIs, saved as .glb."""
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj in objects)
    folder = tempfile.mkdtemp(dir=TMP)
    gltf = os.path.join(folder, "x.gltf")
    bpy.ops.export_scene.gltf(filepath=gltf, export_format='GLTF_SEPARATE', use_selection=True, export_yup=False)
    data = json.load(open(gltf))
    for buffer in data["buffers"]:
        raw = open(os.path.join(folder, buffer["uri"]), "rb").read()
        buffer["uri"] = "data:application/octet-stream;base64," + base64.b64encode(raw).decode()
    json.dump(data, open(path, "w"))


def bounds(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    return [[min(c[i] for c in corners) for i in range(3)], [max(c[i] for c in corners) for i in range(3)]]


def make_version(version, front_z, clip=True):
    """A CAD version in its own collection (exported, then removed from the scene)."""
    coll = bpy.data.collections.new("export " + version)
    bpy.context.scene.collection.children.link(coll)
    ids = ["Leuchte | Front", "Leuchte | Gehäuse"] + (["Leuchte | Clip"] if clip else [])
    parts = [box(ids[0], (0.04, 0.01, 0.03), (0, -0.02, front_z), coll),
             box(ids[1], (0.05, 0.03, 0.035), (0, 0.0, 0.1), coll)]
    if clip:
        parts.append(box(ids[2], (0.01, 0.01, 0.02), (0.03, 0, 0.12), coll))
    for i, p in enumerate(parts):   # one appearance per part, as Onshape exports them
        p.data.materials.append(bpy.data.materials.new("Appearance %d %s" % (i, version)))
    path = os.path.join(TMP, "Leuchte %s.glb" % version)
    bpy.context.view_layer.update()
    export_onshape_like(parts, path)
    sidecar = {"format": "ai-studio-cad/1", "version": version, "program": "onshape", "up": "Z", "floor": False,
               "source": "https://cad.onshape.com/documents/test",
               "parts": [{"id": i, "material": m, "bbox": bounds(p), "mass_kg": 0.01}
                         for i, p, m in zip(ids, parts, ("PETG milchig", "Aluminium eloxiert", "Federstahl"))],
               "mounts": [{"name": "MOUNT | Lenker", "origin": [0, 0.02, front_z + 0.05], "x": [1, 0, 0], "z": [0, 1, 0]}],
               "variants": {"Schwarz": {"Aluminium eloxiert": {"color": "#202020"}}}}
    json.dump(sidecar, open(os.path.join(TMP, "Leuchte %s.cad.json" % version), "w"))
    for p in parts:
        bpy.data.objects.remove(p)
    bpy.data.collections.remove(coll)
    return path


def section(func):
    try:
        func()
    except Exception:
        traceback.print_exc()
        check(func.__name__, False, "exception")
    return func


for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj)


@section
def take_over_with_sidecar():
    # The scene's current state: an old export with the material in the name, set up by the user.
    holder = bpy.data.objects.new("Halter", None)
    bpy.context.scene.collection.objects.link(holder)
    scene_coll = bpy.data.collections.new("Leuchte")
    bpy.context.scene.collection.children.link(scene_coll)
    front = box("Leuchte | Front PETG milchig", (0.04, 0.01, 0.03), (0, -0.02, 0.05), scene_coll)
    front.parent = holder
    mat = bpy.data.materials.new("PETG | Milchig Render")
    front.data.materials.append(mat)
    bevel = front.modifiers.new("Bevel", 'BEVEL')
    bevel.width = 0.0007
    front["finish"] = "matt"
    front.visible_shadow = False
    housing = box("Leuchte | Gehäuse", (0.05, 0.03, 0.035), (0, 0, 0.1), scene_coll)
    housing["cad_id"], housing["cad_version"] = "Leuchte | Gehäuse", "V2"
    housing.data.materials.append(bpy.data.materials.new("Alu | Eloxiert Render"))

    path = make_version("V3", 0.05)
    report = cadimport.cad_import(path)
    print(cadimport.summary(report))
    new_front = next(o for o in bpy.data.objects if o.get("cad_id") == "Leuchte | Front" and o.get("cad_version") == "V3")
    new_housing = next(o for o in bpy.data.objects if o.get("cad_id") == "Leuchte | Gehäuse" and o.get("cad_version") == "V3")
    clip = next(o for o in bpy.data.objects if o.get("cad_id") == "Leuchte | Clip")
    check("upright and in place (sizes match the CAD)", not report["size_off"], report["size_off"])
    check("all parts tagged", not report["untagged"] and not report["missing_parts"], report)
    check("legacy name paired with the id", [new_front.name, front.name] in report["taken_over"], report["taken_over"])
    check("id pairs with id", [new_housing.name, housing.name] in report["taken_over"])
    check("materials kept", new_front.active_material == mat and new_housing.active_material.name == "Alu | Eloxiert Render")
    check("collection kept", scene_coll in new_front.users_collection)
    check("modifier copied", new_front.modifiers and new_front.modifiers[0].type == 'BEVEL'
          and abs(new_front.modifiers[0].width - 0.0007) < 1e-9)
    check("parent kept, world position too", new_front.parent == holder
          and (new_front.matrix_world.translation - Vector((0, -0.02, 0.05))).length < 1e-4,
          new_front.matrix_world.translation)
    check("property and visibility kept", new_front.get("finish") == "matt" and not new_front.visible_shadow)
    archive = bpy.data.collections.get("Archive")
    v2 = bpy.data.collections.get("Archive V2")
    check("old parts archived by version", housing.name in v2.objects and front.name in archive.objects
          and archive.hide_render, [c.name for c in bpy.data.collections])
    check("new part: placeholder material named after the CAD", clip.active_material
          and clip.active_material.name == "Federstahl", report["materials"])
    check("floor check off for a part not on the floor", not any("not on the floor" in l for l in report["check"]),
          report["check"])
    mount = bpy.data.objects.get("MOUNT | Lenker")
    z = mount.matrix_world.to_3x3() @ Vector((0, 0, 1)) if mount else None
    check("mount placed with its axes", mount and mount.empty_display_type == 'ARROWS'
          and (mount.matrix_world.translation - Vector((0, 0.02, 0.1))).length < 1e-6 and (z - Vector((0, 1, 0))).length < 1e-6)
    text = bpy.data.texts.get("Varianten.json")
    data = json.loads(text.as_string()) if text else {}
    check("variants written", "Schwarz" in data.get("variants", {}) and
          data["targets"].get("ALUMINIUM_ELOXIERT", "").startswith("Alu | Eloxiert Render/"), data)
    check("unused imported materials removed", report["unused_imported_materials_removed"] >= 2, report)

    # Next version: the front moves up, the clip is gone; the mount follows.
    child = bpy.data.objects.new("Klingel", None)
    bpy.context.scene.collection.objects.link(child)
    child.parent = mount
    path = make_version("V4", 0.06, clip=False)
    report = cadimport.cad_import(path)
    bpy.context.view_layer.update()
    newest = next(o for o in bpy.data.objects if o.get("cad_id") == "Leuchte | Front" and o.get("cad_version") == "V4")
    v3 = bpy.data.collections.get("Archive V3")
    check("V3 archived, modifier still there", v3 and new_front.name in v3.objects and newest.modifiers
          and newest.active_material == mat, report)
    check("old part without partner reported", clip.name in report["old_without_partner"], report)
    check("mount moved, what hangs on it follows", abs(mount.matrix_world.translation.z - 0.11) < 1e-6
          and abs(child.matrix_world.translation.z - 0.11) < 1e-6)


@section
def feedback_next_to_export():
    part = next(o for o in bpy.data.objects if o.get("cad_version") == "V4")
    path, _note = cadimport.feedback(part.name, "Kollision 1.2 mm mit dem Lenker")
    check("feedback next to the export", os.path.dirname(path) == TMP and
          cadfiles.open_feedback(TMP)[0].endswith("· Kollision 1.2 mm mit dem Lenker (Blender)"), path)


@section
def comments_box():
    """The CAD comments of the chat's CAD box: add, list, tick off (only the file, no Onshape)."""
    folder = os.path.join(TMP, "from_onshape")
    os.makedirs(folder)
    part = bpy.data.objects.new("Deckel_01", None)
    bpy.context.scene.collection.objects.link(part)
    part["cad_file"] = os.path.join(folder, "Leuchte V5.glb")
    part["cad_source"] = "https://cad.onshape.com/documents/d9/w/w9/e/e9"
    image = os.path.join(TMP, "look.png")
    open(image, "wb").write(b"\x89PNG fake")
    try:
        path, note = cadimport.feedback(part, "Wand 2.4 mm statt 2.0 mm", image=image)
        check("feedback only into the file, with its image", note == "" and os.path.dirname(path) == folder
              and ("image: %s" % image) in cadfiles.open_feedback(folder)[0], cadfiles.open_feedback(folder))
        ops = mod("studio.onshape_ops")
        check("no Onshape keys or sync any more", not hasattr(ops, "cached_keys") and not hasattr(cadimport, "sync_feedback"))
        for cls in ops.classes:   # the add-on is loaded, not enabled
            bpy.utils.register_class(cls)
        before = cadimport._last["file"]
        cadimport._last["file"] = part["cad_file"]
        try:
            result = bpy.ops.render_studio.cad_comment(part="Deckel_01", text="  Fase   0.5 mm  ")
            state = ops.comment_state(bpy.context, force=True)
            mine = [p for p in state["open"] if "Fase 0.5 mm" in p]
            check("CAD comment from the box", result == {'FINISHED'} and mine, (result, state["open"]))

            class Layout:   # takes every drawing call
                def __getattr__(self, name):
                    return lambda *a, **k: self
                def __setattr__(self, name, value):
                    pass
            ops.draw_comments(Layout(), bpy.context)
            check("box lists the open points", len(state["open"]) == 2, state["open"])
            result = bpy.ops.render_studio.cad_comment_done(point=mine[0])
            state = ops.comment_state(bpy.context, force=True)
            check("done: ticked off", result == {'FINISHED'} and not any("Fase" in p for p in state["open"]),
                  state["open"])
            check("done in Blender noted in the file", "Fase 0.5 mm (Blender) · done in Blender" in
                  open(os.path.join(folder, cadfiles.FEEDBACK_FILE), encoding="utf-8").read())
        finally:
            cadimport._last["file"] = before
            for cls in ops.classes:
                bpy.utils.unregister_class(cls)
    finally:
        bpy.data.objects.remove(part)


@section
def cad_job_to_its_chat():
    """A CAD job goes to the chat the export came from (named in the sidecar), else to a new chat."""
    os.environ["AI_STUDIO_JOBS_DIR"] = os.path.join(TMP, "jobs")
    os.environ["AI_STUDIO_NO_OPEN"] = "1"   # never open the Claude app from a test
    cadjobs = mod("cadjobs")
    folder = os.path.join(TMP, "with_chat")
    os.makedirs(folder)
    part = bpy.data.objects.new("Griff", None)
    bpy.context.scene.collection.objects.link(part)
    part["cad_file"] = os.path.join(folder, "Leuchte V6.glb")
    part["cad_chat"] = json.dumps({"name": "Leuchte V22", "url": "https://claude.ai/chat/0b9e1c2a-1111-4222-8333-444455556666"})
    try:
        chat = cadimport.origin_chat(part)
        check("origin chat from the part", chat and chat["name"] == "Leuchte V22", chat)
        job, how = cadimport.send_job("Griff 5 mm länger", part, "origin", open_app=False)
        check("job saved with its chat and folder", job["chat"]["name"] == "Leuchte V22" and job["cad_folder"] == folder
              and job["part"] == "Griff" and "saved as" in how, (job, how))
        check("known chat: the job waits for it, nothing in the clipboard",
              "waits for the chat 'Leuchte V22'" in how and "clipboard" not in how, how)
        job, _how = cadimport.send_job("Fase 1 mm", part, "new", open_app=False)
        check("new chat: no chat in the job", job["chat"] is None)
        # a part whose export names no chat: the chat the user names is used and remembered
        lonely_folder = os.path.join(TMP, "no_chat")
        os.makedirs(lonely_folder)
        lonely = bpy.data.objects.new("Draht", None)
        bpy.context.scene.collection.objects.link(lonely)
        lonely["cad_file"] = os.path.join(lonely_folder, "Leuchte V22.glb")
        try:
            job, how = cadimport.send_job("Biegeradius 1.5 mm", lonely, "origin", open_app=False, chat_name=" CAD  5 ")
            check("named chat goes into the job", job["chat"]["name"] == "CAD 5", (job, how))
            job, how = cadimport.send_job("Enden flach", lonely, "origin", open_app=False)
            check("next job of that export goes to the remembered chat", (job["chat"] or {}).get("name") == "CAD 5",
                  (job, how))
            check("the dialog suggests the remembered chat",
                  (cadimport.job_chat(lonely, lonely_folder) or {}).get("name") == "CAD 5")
        finally:
            bpy.data.objects.remove(lonely)
        ops = mod("studio.onshape_ops")
        for cls in ops.classes:
            bpy.utils.register_class(cls)
        try:
            bpy.context.view_layer.objects.active = None
            result = bpy.ops.render_studio.cad_job(part="Griff", text="Radius 2 mm", target='NEW')
            check("Send CAD Job button", result == {'FINISHED'}
                  and any(j["text"] == "Radius 2 mm" and j["chat"] is None for j in cadjobs.jobs()), result)
            # the dropdown: the known chats, last active first; the export's own chat is always in it
            cadjobs.seen_chat("CAD 7")
            choices = ops.chat_choices("Leuchte V22")
            names = [c["name"] for c in choices]
            check("dropdown: the active chat first, the export's and the remembered ones in it", names[0] == "CAD 7"
                  and "Leuchte V22" in names and "CAD 5" in names, names)
            check("dropdown: an unknown default chat is added", ops.chat_choices("CAD 11")[0]["name"] == "CAD 11")
            ops._choices[:] = choices
            items = [i[0] for i in ops._job_targets(None, bpy.context)]
            check("dropdown items: new chat, the chats, other", items[0] == 'NEW' and items[-1] == 'OTHER'
                  and len(items) == len(choices) + 2, items)
            result = bpy.ops.render_studio.cad_job(part="Griff", text="Loch 4 mm", target='CHAT%d' % names.index("CAD 7"))
            job = next(j for j in cadjobs.jobs() if j["text"] == "Loch 4 mm")
            check("chosen chat from the dropdown", result == {'FINISHED'} and job["chat"]["name"] == "CAD 7", job)
            result = bpy.ops.render_studio.cad_job(part="Griff", text="Nut 1 mm", target='OTHER', other=" CAD  9 ")
            job = next(j for j in cadjobs.jobs() if j["text"] == "Nut 1 mm")
            check("other chat typed in", result == {'FINISHED'} and job["chat"]["name"] == "CAD 9", job)
            check("ago text", ops.ago_text("") == "not seen yet" and ops.ago_text(cadjobs.known_chats()[0]["last"])
                  == "active just now")
            # switched off: no button, no tool
            runner = mod("studio.runner")
            real_prefs = runner.prefs
            runner.prefs = lambda: type("P", (), {"cad_jobs": False})()
            try:
                check("switched off: Send CAD Job not available", not bpy.ops.render_studio.cad_job.poll())
                tools = {t["name"]: t for t in mod("studio.claude_tools").get_tools()}
                try:
                    tools["studio_cad_job"]["handler"]({"text": "x"})
                    check("switched off: the chat's tool says so", False)
                except ValueError as exc:
                    check("switched off: the chat's tool says so", "switched off" in str(exc), str(exc))
                check("switched off: listing still works", tools["studio_cad_job"]["handler"]({}).startswith("CAD jobs"))
            finally:
                runner.prefs = real_prefs
            check("switched on again", bpy.ops.render_studio.cad_job.poll())
        finally:
            for cls in ops.classes:
                bpy.utils.unregister_class(cls)
    finally:
        bpy.data.objects.remove(part)


@section
def check_views_place_cameras():
    runner = mod("studio.runner")
    shown = []
    png = os.path.join(TMP, "dummy.png")
    image = bpy.data.images.new("dummy", 8, 8)
    image.filepath_raw, image.file_format = png, 'PNG'
    image.save()
    runner.request_preview = lambda scene, cam, size, samples, callback: (shown.append(cam.name), callback(png, None))
    cadimport.check_folder = lambda: os.path.join(TMP, "cad-check")
    cams = cadimport.check_views(views=("Iso", "Front"))
    result = cadimport.check_result()
    front = bpy.data.objects["P_cad-front_01"]
    forward = front.matrix_world.to_3x3() @ Vector((0, 0, -1))
    check("check cameras look at the parts", cams == ["P_cad-iso_01", "P_cad-front_01"] and forward.y > 0.999,
          (cams, forward))
    check("check cameras reused, not numbered on", cadimport.check_views(views=("Front",)) == ["P_cad-front_01"])
    check("check images copied", result and sorted(result["images"]) == ["Front", "Iso"]
          and all(os.path.exists(p) for p in result["images"].values()), result)


ok = all(results)
print("CAD_BLENDER_OK" if ok else "CAD_BLENDER_FAIL", "%d/%d" % (sum(results), len(results)))

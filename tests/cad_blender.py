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
        check("no Onshape keys or sync any more", not hasattr(cadimport, "sync_feedback"))
        check("no CAD box, no import buttons", not hasattr(cadswap, "draw_chat")
              and not hasattr(cadswap, "RS_OT_cad_import"))
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
        # the CAD skill is the switch: off, Claude in Blender has no CAD job / feedback tools
        link, tools_mod, core = mod("studio.cad_link"), mod("studio.claude_tools"), mod("core")
        core.active_skills[:] = []
        names_off = [t["name"] for t in tools_mod.get_tools()]
        core.active_skills[:] = ["cad"]
        check("same tools with the skill on or off (a running chat keeps its tool list)",
              names_off == [t["name"] for t in tools_mod.get_tools()] and "studio_cad_job" in names_off)
        tools = {t["name"]: t for t in tools_mod.get_tools()}
        check("skill on: the CAD tools are there", "studio_cad_job" in tools and "studio_cad_feedback" in tools)
        cadjobs.seen_chat("CAD 7")
        listing = tools["studio_cad_job"]["handler"]({})
        chats = listing.partition("Known CAD chats")[2]
        check("listing: the known chats for ask_user, last active first", chats
              and chats.index("CAD 7") < chats.index("CAD 5") and "active just now" in chats, listing)
        text = tools["studio_cad_job"]["handler"]({"text": "Loch 4 mm", "part": "Griff", "chat": "CAD 7"})
        job = next(j for j in cadjobs.jobs() if j["text"] == "Loch 4 mm")
        check("job from the chat to the named chat", job["chat"]["name"] == "CAD 7" and "CAD 7" in text, text)
        text = tools["studio_cad_job"]["handler"]({"text": "Nut 1 mm", "target": "new"})
        check("job for a new chat", "(new chat)" in text, text)
        # the chat says when a CAD chat takes a job and finishes it
        seen = {}
        check("first look: nothing to say", link.job_news(cadjobs.jobs(), seen, started=False) == [])
        cadjobs.claim(job["id"], "CAD 7")
        news = link.job_news(cadjobs.jobs(), seen)
        check("taken: one line", news == ["CAD job taken by CAD 7: Loch 4 mm"], news)
        cadjobs.done(job["id"], "Loch sitzt jetzt bei 4 mm")
        news = link.job_news(cadjobs.jobs(), seen)
        check("done: with the note", news == ["CAD job done (CAD 7): Loch 4 mm – Loch sitzt jetzt bei 4 mm"], news)
        check("nothing new: nothing said", link.job_news(cadjobs.jobs(), seen) == [])
        before = len(core.messages)
        link._started[0] = False
        link._seen.clear()
        link._poll()
        cadjobs.claim(cadjobs.new_job("Fase 2 mm")["id"], "CAD 7")
        link._poll()
        check("the timer writes it into the chat", len(core.messages) == before + 1
              and "Fase 2 mm" in core.messages[-1]["text"], core.messages[before:])
        core.active_skills[:] = []
        try:
            tools["studio_cad_job"]["handler"]({"text": "x"})
            check("skill off: the tool says so", False)
        except ValueError as exc:
            check("skill off: the tool says so", "CAD skill is off" in str(exc), str(exc))
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

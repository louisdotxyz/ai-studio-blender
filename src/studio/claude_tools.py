# SPDX-License-Identifier: GPL-3.0-or-later
"""Render Studio's tools for the "Claude Assistant" chat.

Claude Assistant reads ``get_tools()`` of every module named ``claude_tools``
before each message and offers the tools to the model (cloud or local). The
model then calls them directly instead of writing Python against the API,
which costs fewer tokens and works much better with small local models.
Handlers run on Blender's main thread and return text.
"""

import json

import bpy

from . import api, runner

_FORMATS = {
    "light": ("light_setup", api.LIGHT_SETUP_FORMAT),
    "hdri": ("hdri_setup", api.HDRI_SETUP_FORMAT),
}


def _format(kind):
    """The setup format, phrased for a tool argument instead of a JSON answer."""
    tool, text = _FORMATS[kind]
    return text.replace("Answer with JSON only, in this format:",
                        f"Pass the setup as the 'setup' argument of studio_{tool}, in this format:")


def _context(args):
    kind = str(args.get("kind") or "light").lower()
    scene = bpy.context.scene
    if kind == "environment":
        return api.environment_prompt(scene)
    if kind not in _FORMATS:
        raise ValueError("kind must be light, hdri or environment")
    return _format(kind) + "\n\nCurrent scene:\n" + json.dumps(api.scene_context(scene), indent=1)


def _light_setup(args):
    created = api.light_setup(args.get("setup") or {})
    return "Light setup applied and saved as preset: " + ", ".join(o.name for o in created)


def _hdri_setup(args):
    path = api.hdri_setup(args.get("setup") or {})
    return f"HDRI generated and used as World: {path}"


def _environment_bake(args):
    world = args.get("world")
    path = api.environment_bake(
        str(args.get("name") or "Environment"), str(args.get("collection") or "") or None,
        world=bpy.data.worlds.get(world) if world else None,
        resolution=int(args.get("resolution") or 1024), samples=int(args.get("samples") or 32))
    return f"Environment baked and used as World: {path}"


def _section(args):
    names = args.get("objects") or []
    result = api.section(names, axis=str(args.get("axis") or "X"), offset=float(args.get("offset_m") or 0.0),
                         plane_object=args.get("plane_object") or None, keep=bool(args.get("keep")))
    return json.dumps(result, indent=1)


def _base_material(args):
    action = str(args.get("action") or "list").lower()
    if action == "list":
        from .tab_material import base_library_file, BASE_KEY
        path = base_library_file()
        if not path:
            return "No base material library (Preferences › Studio)."
        names = sorted(m.get(BASE_KEY) for m in bpy.data.materials if m.get(BASE_KEY))
        return "Library: %s\nBase materials copied into this file: %s" % (path, ", ".join(names) or "none yet")
    if action == "remove":
        return "Removed from the base materials: " + api.remove_base_material(str(args["name"]))
    material = args.get("material") or getattr(bpy.context.active_object, "active_material", None)
    if material is None:
        raise ValueError("No material given and the active object has none")
    name = api.save_base_material(material, args.get("name") or None)
    return "Saved to the base materials (all projects): " + name


def _camera(args):
    from . import cameras
    scene = bpy.context.scene
    view_layer = bpy.context.view_layer
    action = str(args.get("action") or "place").lower()
    name = str(args.get("name") or "Cam")
    if action == "from_view":
        space = bpy.context.space_data
        if space is None or space.type != 'VIEW_3D':
            raise ValueError("No 3D viewport")
        cam = cameras.camera_from_view(scene, space, view_layer, name)
    elif action == "place":
        cam = cameras.camera_around(scene, view_layer, name, float(args.get("horizontal") or 0),
                                    float(args.get("vertical") or 10), float(args.get("distance") or 4),
                                    float(args.get("focal_length") or 50))
    else:
        raise ValueError("action must be from_view or place")
    scene.camera = cam
    text = f"Camera '{cam.name}' created and made active"
    if args.get("add_to_queue"):
        bpy.ops.render_studio.add()
        text += " and added to the render queue"
    return text + "."


def _queue(args):
    from .ui_common import active_camera, ensure_uids
    scene = bpy.context.scene
    rq = scene.render_studio
    action = str(args.get("action") or "status").lower()
    if action == "add":
        name = args.get("camera")
        cam = bpy.data.objects.get(name) if name else active_camera(scene)
        if cam is None or cam.type != 'CAMERA':
            raise ValueError(f"No camera named {name!r}" if name else "No active camera")
        if args.get("quality") in ('LOW', 'HIGH'):
            rq.quality = args["quality"]
        scene.camera = cam
        ensure_uids(rq)
        bpy.ops.render_studio.add()
        return f"Added {cam.name} ({rq.quality}) to the queue; it renders automatically if enabled."
    if action == "start":
        if runner.RUN.busy:
            return "The queue is already rendering."
        runner.RUN.start(scene)
        return "Rendering started. Call action 'wait' to get an answer when it is done."
    if action == "cancel":
        runner.RUN.cancel()
        return "Queue cancelled."
    if action == "wait":
        # Answer only when the queue is done: no polling, Blender stays usable meanwhile.
        def done():
            return None if runner.RUN.busy else "Queue done: " + (runner.RUN.message or "nothing to render")
        return done() or {"wait": done, "label": "waiting for the render queue"}
    lines = [f"Rendering: {'yes' if runner.RUN.busy else 'no'}"
             + (f" – {runner.RUN.message}" if runner.RUN.message else "")]
    lines += [f"- {runner.item_label(scene, item)}" for item in rq.items if item.camera]
    cams = [o.name for o in scene.objects if o.type == 'CAMERA' and runner.TEMP_KEY not in o]
    lines.append("Cameras in the scene: " + (", ".join(cams) or "none"))
    return "\n".join(lines)


def _cad_import(args):
    from . import cadimport
    report = cadimport.cad_import(str(args["path"]), take_over=args.get("take_over", True) is not False,
                                  materials=args.get("materials", True) is not False,
                                  setup=args.get("setup", True) is not False,
                                  delete_old=bool(args.get("delete_old")),
                                  tolerance_mm=float(args.get("tolerance_mm") or 0.5))
    text = cadimport.summary(report)
    if args.get("details"):
        text += "\n\n" + cadimport.report_json(report)
    return text


def _cad_check(args):
    from . import cadimport
    views = args.get("views") or ["Iso", "Front"]
    cams = cadimport.check_views(args.get("objects") or None, tuple(views), int(args.get("size") or 900),
                                 int(args.get("samples") or 16))

    def done():
        result = cadimport.check_result()
        if result is None:
            return None
        lines = ["%s: %s" % (view, path) for view, path in sorted(result["images"].items())]
        lines += ["failed: " + e for e in result["errors"]]
        return "Check images (look at them with Read):\n" + "\n".join(lines)
    return done() or {"wait": done, "label": "rendering CAD check images (%s)" % ", ".join(cams)}


def _cad_feedback(args):
    from . import cadfiles, cadimport
    if args.get("problem"):
        path = cadimport.feedback(args.get("part") or "", str(args["problem"]), args.get("folder") or None)
        return "Noted for the CAD side in " + path
    folder = args.get("folder") or cadimport.feedback_folder(args.get("part") or None)
    points = cadfiles.open_feedback(folder)
    return "Open CAD feedback:\n" + ("\n".join("- " + p for p in points) if points else "none")


_SETUP = {"type": "object", "description": "The setup as described by studio_context"}


def get_tools():
    return [
        {"name": "studio_context", "label": "Render Studio: looked at the setup",
         "description": "Render Studio: get the setup format and the current scene (subject size, camera, "
                        "lights, World) before creating a light setup, an HDRI or an environment.",
         "inputSchema": {"type": "object", "properties": {
             "kind": {"type": "string", "enum": ["light", "hdri", "environment"]}},
             "required": ["kind"]},
         "handler": _context},
        {"name": "studio_light_setup", "label": "Render Studio: light setup",
         "description": "Render Studio: apply a studio light setup around the subject (replaces the previous "
                        "setup, keeps the user's own lights, saves it as a preset). Use the same name to "
                        "change it. Do not create lights by hand.",
         "inputSchema": {"type": "object", "properties": {"setup": _SETUP}, "required": ["setup"]},
         "handler": _light_setup},
        {"name": "studio_hdri_setup", "label": "Render Studio: generated HDRI",
         "description": "Render Studio: generate an HDRI environment (sky, sun, softboxes) and use it as "
                        "the World.",
         "inputSchema": {"type": "object", "properties": {"setup": _SETUP}, "required": ["setup"]},
         "handler": _hdri_setup},
        {"name": "studio_environment_bake", "label": "Render Studio: baked environment",
         "description": "Render Studio: bake a modelled room (the objects marked obj['as_env'] = True; "
                        "they render even when hidden) "
                        "into a 32-bit HDRI and use it as the World. Test: resolution 1024, samples 32; "
                        "final: 4096 / 256. Save a backup copy of the file first.",
         "inputSchema": {"type": "object", "properties": {
             "name": {"type": "string"},
             "collection": {"type": "string", "description": "Only for older files: a collection "
                            "'ENV | <Name>' instead of the as_env marker"},
             "world": {"type": "string", "description": "World that lights the room while baking"},
             "resolution": {"type": "integer"}, "samples": {"type": "integer"}},
             "required": ["name"]},
         "handler": _environment_bake},
        {"name": "studio_section", "label": "Render Studio: section",
         "description": "Cut objects (e.g. a 3D scan and the CAD part) with a plane and measure the cut: "
                        "width/height of each contour in mm, and how far the others lie from the first "
                        "(mean/max mm). Shows the contours as edge objects (temp collection, keep=true keeps them).",
         "inputSchema": {"type": "object", "properties": {
             "objects": {"type": "array", "items": {"type": "string"}, "description": "Object names, the reference (scan) first"},
             "axis": {"type": "string", "enum": ["X", "Y", "Z"], "description": "Plane normal (default X)"},
             "offset_m": {"type": "number", "description": "Plane position along the axis in meters"},
             "plane_object": {"type": "string", "description": "Instead of axis: an object whose local XY plane cuts"},
             "keep": {"type": "boolean"}},
             "required": ["objects"]},
         "handler": _section},
        {"name": "studio_base_material", "label": "Render Studio: base materials",
         "description": "Render Studio's base material library (generic materials shared by all projects, "
                        "the base group in the Material tab): 'list' them, 'save' a material of this file "
                        "into it (replaces the one of the same name – change the copy here first, then save), "
                        "or 'remove' one (ask the user first). Only when the user asks to change the base "
                        "materials.",
         "inputSchema": {"type": "object", "properties": {
             "action": {"type": "string", "enum": ["list", "save", "remove"]},
             "material": {"type": "string", "description": "Material in this file (default: active)"},
             "name": {"type": "string", "description": "Name in the library (default: the base name)"}},
             "required": ["action"]},
         "handler": _base_material},
        {"name": "studio_camera", "label": "Render Studio: camera",
         "description": "Render Studio: create a camera. 'from_view' saves the user's current viewport view; "
                        "'place' puts a camera around the subject like the lights (horizontal degrees, 0 = "
                        "where the active camera is, negative = left; vertical degrees above the subject; "
                        "distance in subject radii, typical 3-6; focal_length mm). Optionally add it to the "
                        "render queue.",
         "inputSchema": {"type": "object", "properties": {
             "action": {"type": "string", "enum": ["from_view", "place"]},
             "name": {"type": "string"},
             "horizontal": {"type": "number"}, "vertical": {"type": "number"},
             "distance": {"type": "number"}, "focal_length": {"type": "number"},
             "add_to_queue": {"type": "boolean"}},
             "required": ["action"]},
         "handler": _camera},
        {"name": "studio_cad_import", "label": "Render Studio: CAD import",
         "description": "Import a CAD export (GLB/glTF incl. Onshape's, STEP, OBJ, STL …) with its sidecar "
                        "<name>.cad.json and take it over from the old version in the scene: part ids, base "
                        "materials for new parts, materials/collections/modifiers/settings of old parts kept, "
                        "old parts archived by version, mounting empties, sizes checked against the CAD. "
                        "Use it for every CAD model instead of importing by hand.",
         "inputSchema": {"type": "object", "properties": {
             "path": {"type": "string", "description": "The export file"},
             "take_over": {"type": "boolean", "description": "Take over from the old version (default true)"},
             "materials": {"type": "boolean"}, "setup": {"type": "boolean"},
             "delete_old": {"type": "boolean", "description": "Only when the user asked to delete the old parts"},
             "tolerance_mm": {"type": "number"},
             "details": {"type": "boolean", "description": "Also the full report as JSON"}},
             "required": ["path"]},
         "handler": _cad_import},
        {"name": "studio_cad_check", "label": "Render Studio: CAD check images",
         "description": "Check images of the CAD parts (default: the last CAD import) from fixed views "
                        "(Iso, Front, Right, Top), rendered in the background; answers with the image paths "
                        "when they are done.",
         "inputSchema": {"type": "object", "properties": {
             "objects": {"type": "array", "items": {"type": "string"}},
             "views": {"type": "array", "items": {"type": "string", "enum": ["Iso", "Front", "Right", "Top"]}},
             "size": {"type": "integer"}, "samples": {"type": "integer"}}},
         "handler": _cad_check},
        {"name": "studio_cad_feedback", "label": "Render Studio: CAD feedback",
         "description": "Feedback for the CAD side: with 'problem', note an open point (collision, size, "
                        "a look that needs a geometry change) in CAD-Feedback.md next to the part's CAD "
                        "export; without, list the open points. Geometry problems go here, not into a "
                        "Blender fix of the CAD mesh.",
         "inputSchema": {"type": "object", "properties": {
             "part": {"type": "string", "description": "Object name of the part (or empty for the whole model)"},
             "problem": {"type": "string", "description": "One sentence with numbers, in the user's language"},
             "folder": {"type": "string", "description": "Folder of the CAD export, if no part is given"}}},
         "handler": _cad_feedback},
        {"name": "studio_render_queue", "label": "Render Studio: render queue",
         "description": "Render Studio's render queue: 'status' (entries, cameras, progress), 'add' a camera "
                        "(LOW = quick test, HIGH = final), 'start', 'wait' (answers when everything is "
                        "rendered – use it instead of asking for the status again and again) or 'cancel'. "
                        "Renders are saved to the output folder.",
         "inputSchema": {"type": "object", "properties": {
             "action": {"type": "string", "enum": ["status", "add", "start", "wait", "cancel"]},
             "camera": {"type": "string", "description": "Camera name for 'add' (default: active)"},
             "quality": {"type": "string", "enum": ["LOW", "HIGH"]}},
             "required": ["action"]},
         "handler": _queue},
    ]

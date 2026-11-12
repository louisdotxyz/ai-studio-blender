# SPDX-License-Identifier: GPL-3.0-or-later
"""The CAD hand-over in one call: import an export with its sidecar (cadfiles), tag the parts,
take a new version over from the old one, give new parts their base materials, place the
mounting points, check sizes against the CAD and write variants. Also the check images for the
CAD side and the feedback list it reads.

Used by api.cad_import / api.cad_check_views / api.cad_feedback, the studio_cad_* tools and the
Import CAD button.
"""

import json
import math
import os
import shutil

import bpy
from mathutils import Matrix, Vector

from . import cadfiles, cadswap

MOUNT_COLLECTION = "CAD Mounts"
MOUNT_KEY = "cad_mount"
CHECK_KEY = "as_cad_check"
CHECK_VIEWS = {   # direction from the subject to the camera (Onshape: front looks along +Y)
    "Iso": Vector((1.0, -1.0, 0.8)),
    "Front": Vector((0.0, -1.0, 0.0)),
    "Right": Vector((1.0, 0.0, 0.0)),
    "Top": Vector((0.0, -0.0001, 1.0)),
}
_last = {"objects": [], "file": "", "folder": ""}      # the last import (for the check views)
_check = {"pending": 0, "paths": {}, "errors": [], "folder": ""}


# ---------------------------------------------------------------- import

def _roots(objects):
    inside = set(objects)
    return [o for o in objects if o.parent not in inside]


def _bounds(objects):
    corners = [o.matrix_world @ Vector(c) for o in objects if o.type == 'MESH' for c in o.bound_box]
    if not corners:
        return None
    low = [min(c[i] for c in corners) for i in range(3)]
    high = [max(c[i] for c in corners) for i in range(3)]
    return [low, high]


def import_tagged(path):
    """Import `path` (GLB/glTF/STEP/…), turn a Z-up glTF upright, tag the parts from the sidecar
    and place the mounting points. Returns (objects, sidecar or None)."""
    path = os.path.abspath(os.path.expanduser(path))
    sidecar_file = cadfiles.sidecar_path(path)
    sidecar = cadfiles.load_sidecar(sidecar_file) if sidecar_file else None
    objects = cadswap.import_model(path)
    is_gltf = os.path.splitext(path)[1].lower() in (".glb", ".gltf")
    if sidecar and sidecar["up"] == "Z" and is_gltf:
        # The file's coordinates are Z up, the importer took them for glTF's Y up.
        turn = Matrix.Rotation(math.radians(-90.0), 4, 'X')
        for root in _roots(objects):
            root.matrix_world = turn @ root.matrix_world
    version = (sidecar or {}).get("version") or cadfiles.version_from_name(path)
    for obj in objects:
        if obj.type != 'MESH':
            continue
        part = cadfiles.part_for(sidecar, obj.name, getattr(obj.data, "name", ""),
                                 obj.parent.name if obj.parent else "")
        obj["cad_file"] = path
        if version:
            obj[cadswap.CAD_VERSION] = version
        if part is None:
            continue
        obj[cadswap.CAD_ID] = part["id"]
        if part.get("material"):
            obj["cad_material"] = str(part["material"])
        if part.get("mass_kg") is not None:
            obj["cad_mass_kg"] = float(part["mass_kg"])
        if sidecar.get("source"):
            obj["cad_source"] = str(sidecar["source"])
    if sidecar:
        place_mounts(sidecar["mounts"])
    _last.update(objects=[o.name for o in objects], file=path, folder=os.path.dirname(path))
    return objects, sidecar


def place_mounts(mounts, scene=None):
    """Empties "MOUNT | …" with arrows for the sidecar's mounting points (X = main direction,
    Z = into the part it is mounted on). An existing one moves, so what hangs on it follows.
    Returns their names."""
    scene = scene or bpy.context.scene
    if not mounts:
        return []
    coll = bpy.data.collections.get(MOUNT_COLLECTION) or bpy.data.collections.new(MOUNT_COLLECTION)
    if coll not in scene.collection.children_recursive:
        scene.collection.children.link(coll)
    names = []
    for mount in mounts:
        x = Vector(mount["x"]).normalized()
        z = Vector(mount["z"]).normalized()
        if x.length < 0.5 or z.length < 0.5 or abs(x.dot(z)) > 0.999:
            raise ValueError("Mount %s: x and z must be two different directions" % mount["name"])
        x = (x - z * x.dot(z)).normalized()
        y = z.cross(x)
        matrix = Matrix((x, y, z)).transposed().to_4x4()
        matrix.translation = Vector(mount["origin"])
        obj = bpy.data.objects.get(mount["name"])
        if obj is None or obj.type != 'EMPTY':
            obj = bpy.data.objects.new(mount["name"], None)
            obj.empty_display_type = 'ARROWS'
            obj.empty_display_size = 0.02
            coll.objects.link(obj)
        obj.matrix_world = matrix
        obj[MOUNT_KEY] = True
        obj.hide_render = True
        names.append(obj.name)
    return names


def _base_names():
    """Names of the base materials: in the file and in the library."""
    from .tab_material import BASE_KEY, base_library_file
    names = {m.get(BASE_KEY) for m in bpy.data.materials if m.get(BASE_KEY)}
    library = base_library_file()
    if library:
        with bpy.data.libraries.load(library, link=False, assets_only=True) as (source, _target):
            names.update(source.materials)
    return sorted(n for n in names if n), library


def base_material(name):
    """The base material `name` in this file (copied in from the library when needed)."""
    from .tab_material import BASE_KEY, base_library_file
    found = next((m for m in bpy.data.materials if m.get(BASE_KEY) == name), None)
    if found is not None:
        return found
    library = base_library_file()
    if not library:
        return None
    with bpy.data.libraries.load(library, link=False, assets_only=True) as (source, target):
        target.materials = [name] if name in source.materials else []
    material = target.materials[0] if target.materials else None
    if material is not None:
        material[BASE_KEY] = name
        material.use_fake_user = False
        if material.asset_data is not None:
            material.asset_clear()
    return material


def _map_folder():
    from .paths import data_dir
    return data_dir()


def map_material(cad_material, base):
    """Remember that the CAD material `cad_material` becomes the base material `base`."""
    mapping = cadfiles.load_material_map(_map_folder())
    mapping[str(cad_material)] = str(base)
    cadfiles.save_material_map(_map_folder(), mapping)
    return mapping


def assign_materials(objects, imported_materials=()):
    """New parts without a partner: the base material their CAD material maps to, else a material
    of the same name in this file, else the imported one renamed after the CAD material.
    Returns {"base": {part: material}, "same_name": […], "placeholder": {part: material}}."""
    report = {"base": {}, "same_name": [], "placeholder": {}}
    targets = [o for o in objects if o.type == 'MESH' and o.get("cad_material")]
    if not targets:
        return report
    try:
        base_names, _library = _base_names()
    except Exception:
        base_names = []
    mapping = cadfiles.load_material_map(_map_folder())
    made = {}   # CAD material -> placeholder made in this call (Onshape shares one appearance)
    for obj in targets:
        cad = str(obj["cad_material"])
        base = cadfiles.base_material_for(cad, base_names, mapping)
        material = base_material(base) if base else None
        if material is not None:
            report["base"][obj.name] = material.name
        elif cad in made:
            material = made[cad]
            report["placeholder"][obj.name] = material.name
        else:
            material = bpy.data.materials.get(cad)
            if material is not None and material not in imported_materials:
                report["same_name"].append(obj.name)
            else:
                slots = [s.material for s in obj.material_slots if s.material]
                source = slots[0] if slots else None
                # A copy of the imported one keeps the CAD color; the shared original is dropped later.
                material = source.copy() if source is not None else bpy.data.materials.new(cad)
                material.name = cad
                made[cad] = material
                report["placeholder"][obj.name] = material.name
        if not obj.material_slots:
            obj.data.materials.append(material)
        for slot in obj.material_slots:
            slot.material = material
    return report


def _drop_unused(materials):
    count = 0
    for material in list(materials):
        try:
            if material.users == 0:
                bpy.data.materials.remove(material)
                count += 1
        except ReferenceError:
            pass
    return count


def write_variants(sidecar_variants, objects):
    """The sidecar's variants into Varianten.json (same names are replaced, others kept)."""
    if not sidecar_variants:
        return []
    from .variants import V
    names = {}
    for obj in objects:
        if obj.type == 'MESH' and obj.get("cad_material") and obj.active_material is not None:
            names.setdefault(str(obj["cad_material"]), obj.active_material.name)
    targets, variants = cadfiles.variants_for(sidecar_variants, names)
    if not variants:
        return []
    data = V.data()
    data.setdefault("targets", {}).update(targets)
    data.setdefault("variants", {}).update(variants)
    V.save(data)
    return sorted(variants)


def size_check(objects, sidecar, tolerance_mm=0.5):
    """Bounding boxes against the sidecar: [{"part", "max_mm", …}] for the parts off by more
    than the tolerance, plus the parts of the sidecar that were not imported."""
    if not sidecar:
        return [], []
    off, seen = [], set()
    for obj in objects:
        part = sidecar["by_key"].get(cadfiles.part_name(obj.get(cadswap.CAD_ID, ""))) if obj.type == 'MESH' else None
        if part is None:
            continue
        seen.add(cadfiles.part_name(part["id"]))
        if not part.get("bbox"):
            continue
        deviation = cadfiles.bbox_deviation(part["bbox"], _bounds([obj]), tolerance_mm)
        if deviation:
            off.append(dict(deviation, part=obj.name))
    missing = [p["id"] for key, p in sidecar["by_key"].items() if key not in seen]
    return off, missing


def cad_import(path, take_over=True, materials=True, collections=True, setup=True, delete_old=False,
               tolerance_mm=0.5):
    """Import a CAD export with its sidecar and take it over from the version in the scene.
    Returns a report dict (see api.cad_import)."""
    before = set(bpy.data.materials)
    objects, sidecar = import_tagged(path)
    imported_materials = set(bpy.data.materials) - before
    meshes = [o for o in objects if o.type == 'MESH']
    report = {"file": _last["file"], "sidecar": (sidecar or {}).get("path", ""),
              "version": (sidecar or {}).get("version") or cadfiles.version_from_name(path),
              "parts": len(meshes), "untagged": [o.name for o in meshes if not o.get(cadswap.CAD_ID)] if sidecar else []}
    pairs, lonely_new, lonely_old = cadswap.match(objects)
    if take_over and pairs:
        cadswap.take_over(pairs, materials, collections, 'DELETE' if delete_old else 'ARCHIVE', setup=setup)
    report["taken_over"] = [[n.name, o.name] for n, o in pairs] if take_over else []
    report["new_without_partner"] = [o.name for o in lonely_new]
    report["old_without_partner"] = [o.name for o in lonely_old]
    new_parts = lonely_new if take_over else meshes
    report["materials"] = assign_materials(new_parts, imported_materials) if materials else {}
    report["unused_imported_materials_removed"] = _drop_unused(imported_materials)
    report["mounts"] = [m["name"] for m in (sidecar or {}).get("mounts", [])]
    report["check"] = cadswap.check(objects, floor=bool((sidecar or {}).get("floor", sidecar is None)))
    report["size_off"], report["missing_parts"] = size_check(objects, sidecar, tolerance_mm)
    report["variants"] = write_variants((sidecar or {}).get("variants"), meshes)
    folder = os.path.dirname(_last["file"])
    report["onshape"] = _sync_after_import(folder)
    report["feedback_open"] = cadfiles.open_feedback(folder)
    for obj in bpy.context.view_layer.objects:
        obj.select_set(obj in objects)
    if meshes:
        bpy.context.view_layer.objects.active = meshes[0]
    return report


# ---------------------------------------------------------------- check images for the CAD side

def _check_objects(objects=None):
    if objects:
        return [bpy.data.objects[o] if isinstance(o, str) else o for o in objects]
    last = [bpy.data.objects.get(n) for n in _last["objects"]]
    last = [o for o in last if o is not None and o.type == 'MESH' and not cadswap._archived(o)]
    if last:
        return last
    return [o for o in bpy.context.selected_objects if o.type == 'MESH']


def check_folder():
    """<name>_renders/cad-check next to the .blend file (or the output folder)."""
    from . import core, runner
    root = runner.settings_from_prefs()[0]
    rq = bpy.context.scene.render_studio
    return os.path.join(core.project_dir(root, core.project_name(None, rq.output_name)), "cad-check")


def check_views(objects=None, views=("Iso", "Front"), size=900, samples=16, focal_length=50.0):
    """Render the parts from fixed views (cameras "CAD <view>" in the camera collection) in the
    render worker; Blender stays usable. The images go to check_folder(); check_result() tells when
    they are done. Returns the camera names."""
    from . import cameras, runner
    objects = _check_objects(objects)
    bounds = _bounds(objects)
    if bounds is None:
        raise ValueError("No parts to show: import a CAD model or select its parts")
    center = (Vector(bounds[0]) + Vector(bounds[1])) / 2
    radius = max((Vector(bounds[1]) - Vector(bounds[0])).length / 2, 0.005)
    scene, view_layer = bpy.context.scene, bpy.context.view_layer
    folder = check_folder()
    os.makedirs(folder, exist_ok=True)
    _check.update(pending=0, paths={}, errors=[], folder=folder)
    names = []
    for view in views:
        if view not in CHECK_VIEWS:
            raise ValueError("view must be one of %s" % ", ".join(CHECK_VIEWS))
        name = "CAD " + view
        cam = next((o for o in bpy.data.objects if o.type == 'CAMERA' and o.get(CHECK_KEY) == view), None)
        if cam is None:   # cameras of earlier versions: CHECK_KEY was True, the view in the name
            from ..naming import role_of
            want = role_of(name)
            cam = next((o for o in bpy.data.objects if o.type == 'CAMERA' and o.get(CHECK_KEY) is not None
                        and role_of(o.name) == want), None)
        if cam is None:
            cam = cameras._new_camera(scene, view_layer, name)   # "P_cad-front_01"
        cam[CHECK_KEY] = view
        cam.data.lens = focal_length
        cam.data.clip_start = min(cam.data.clip_start, radius * 0.01)
        half = math.atan(cam.data.sensor_width / 2 / focal_length)
        direction = CHECK_VIEWS[view].normalized()
        location = center + direction * (radius / math.sin(half) * 1.08)
        turn = (-direction).to_track_quat('-Z', 'Y').to_matrix().to_4x4()
        cam.matrix_world = Matrix.Translation(location) @ turn
        names.append(cam.name)
        target = os.path.join(folder, "%s.png" % view)

        def done(path, error, view=view, target=target):
            _check["pending"] -= 1
            if path:
                shutil.copyfile(path, target)
                _check["paths"][view] = target
            else:
                _check["errors"].append("%s: %s" % (view, error))
        _check["pending"] += 1
        runner.request_preview(scene, cam, int(size), int(samples), done)
    return names


def check_result():
    """None while the check images render, else {"images": {view: path}, "errors": […], "folder"}."""
    if _check["pending"] > 0:
        return None
    return {"images": dict(_check["paths"]), "errors": list(_check["errors"]), "folder": _check["folder"]}


# ---------------------------------------------------------------- feedback for the CAD side

def feedback_folder(part=None):
    """Where the CAD export of `part` (or the last import) lies."""
    obj = bpy.data.objects.get(part) if isinstance(part, str) else part
    path = (obj.get("cad_file") if obj is not None else "") or _last["file"]
    if not path:
        cad = next((o.get("cad_file") for o in bpy.context.scene.objects if o.get("cad_file")), "")
        path = cad
    if not path:
        raise ValueError("No CAD import known: give the folder of the CAD export")
    return os.path.dirname(path)


def onshape_source(obj=None, folder=None):
    """The Onshape element the part (or the export in `folder`) came from, "" if none."""
    if obj is not None and obj.get("cad_source"):
        return str(obj["cad_source"])
    for other in bpy.data.objects:
        if other.get("cad_source") and (folder is None or os.path.dirname(other.get("cad_file", "")) == folder):
            return str(other["cad_source"])
    if folder and os.path.isdir(folder):   # the sidecars next to the export, newest first
        sidecars = sorted((os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".cad.json")),
                          key=os.path.getmtime, reverse=True)
        for path in sidecars:
            try:
                source = (cadfiles.load_sidecar(path) or {}).get("source")
            except (OSError, ValueError):
                continue
            if source:
                return str(source)
    return ""


def _send_to_onshape():
    from .runner import prefs
    p = prefs()
    return p is None or p.onshape_comments


def feedback(part, problem, folder=None, image=None, send=None):
    """Note an open point for the CAD side in CAD-Feedback.md next to the export and, when the
    export came from Onshape and API keys are set, as a comment there. Returns (path, note)."""
    from . import onshape
    obj = bpy.data.objects.get(part) if isinstance(part, str) else part
    label = (obj.get(cadswap.CAD_ID) or obj.name) if obj is not None else str(part or "")
    folder = folder or feedback_folder(obj)
    path = cadfiles.add_feedback(folder, label, problem)
    send = _send_to_onshape() if send is None else send
    source = onshape_source(obj, folder) if send else ""
    if not source or onshape.parse_source(source) is None:
        return path, ""
    if not onshape.keys():
        return path, "not in Onshape: no API keys (Preferences › Studio › Onshape)"
    point = cadfiles.open_feedback(folder)[-1]
    message = "AI Studio (Blender) · %s: %s" % (label or "general", " ".join(str(problem).split()))
    try:
        cid = onshape.post_comment(source, message, image=image if image and os.path.isfile(image) else None)
    except onshape.OnshapeError as exc:
        return path, "not in Onshape: %s" % exc
    cadfiles.remember_comment(folder, point, cid, onshape.parse_source(source)["base"])
    return path, "also as a comment in Onshape"


def _sync_after_import(folder):
    """A new version from Onshape: what was resolved there is ticked off (and missing points sent)."""
    from . import onshape
    if not _send_to_onshape() or not cadfiles.open_feedback(folder):
        return ""
    source = onshape_source(None, folder)
    if not source or onshape.parse_source(source) is None or not onshape.keys():
        return ""
    try:
        return sync_feedback(folder)
    except (onshape.OnshapeError, ValueError) as exc:
        return "Onshape: %s" % exc


def sync_feedback(folder=None):
    """Both ways with Onshape: open points that are not there yet become comments, points whose
    comment was resolved in Onshape are ticked off. Returns a short text."""
    from . import onshape
    folder = folder or feedback_folder()
    source = onshape_source(None, folder)
    if not source or onshape.parse_source(source) is None:
        return "The CAD export did not come from Onshape"
    if not onshape.keys():
        return "No Onshape API keys (Preferences › Studio › Onshape)"
    sent = cadfiles.feedback_comments(folder)
    posted = resolved = 0
    for point in cadfiles.open_feedback(folder):
        known = sent.get(point)
        if known:
            if onshape.comment_resolved(known["base"], known["cid"]):
                cadfiles.tick_feedback(folder, point)
                resolved += 1
            continue
        _day, _sep, rest = point.partition(" · ")
        cid = onshape.post_comment(source, "AI Studio (Blender) · " + rest)
        cadfiles.remember_comment(folder, point, cid, onshape.parse_source(source)["base"])
        posted += 1
    left = len(cadfiles.open_feedback(folder))
    return "Onshape: %d sent, %d resolved there and ticked off, %d open" % (posted, resolved, left)


def summary(report):
    """A short text of cad_import's report (for the tool and the button)."""
    lines = ["%d parts from %s%s" % (report["parts"], os.path.basename(report["file"]),
                                     " (%s)" % report["version"] if report.get("version") else "")]
    if report.get("taken_over"):
        lines.append("%d taken over from the old version" % len(report["taken_over"]))
    if report.get("new_without_partner"):
        lines.append("new: " + ", ".join(report["new_without_partner"][:8]))
    if report.get("old_without_partner"):
        lines.append("old parts not in this version: " + ", ".join(report["old_without_partner"][:8]))
    mats = report.get("materials") or {}
    if mats.get("base"):
        lines.append("base materials: " + ", ".join("%s → %s" % kv for kv in list(mats["base"].items())[:8]))
    if mats.get("placeholder"):
        lines.append("no base material (CAD color kept): " + ", ".join(sorted(set(mats["placeholder"].values()))))
    lines += report.get("check") or []
    for off in report.get("size_off") or []:
        lines.append("size off by %.2f mm: %s" % (off["max_mm"], off["part"]))
    if report.get("missing_parts"):
        lines.append("in the sidecar but not imported: " + ", ".join(report["missing_parts"][:8]))
    if report.get("untagged"):
        lines.append("not in the sidecar: " + ", ".join(report["untagged"][:8]))
    if report.get("mounts"):
        lines.append("mounts: " + ", ".join(report["mounts"]))
    if report.get("variants"):
        lines.append("variants: " + ", ".join(report["variants"]))
    if report.get("onshape"):
        lines.append(report["onshape"])
    if report.get("feedback_open"):
        lines.append("open CAD feedback: %d point(s)" % len(report["feedback_open"]))
    return "\n".join(lines)


def report_json(report):
    return json.dumps(report, indent=1, ensure_ascii=False)

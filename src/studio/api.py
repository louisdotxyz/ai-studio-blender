# SPDX-License-Identifier: GPL-3.0-or-later
"""Render Studio's functions for Claude (in execute_python as `api`, and behind the studio_* tools):
light setups, generated HDRIs, environments (baked rooms, HDRI files), the scene context they need.
"""

import json
import math
import re

import bpy
from mathutils import Vector

from . import core, hdri, hdri_gen, lights, runner

ENVIRONMENT_FOLDER = "Environment"   # subfolder of the HDRI folder for baked rooms
ENV_KEY = "as_env"                   # obj["as_env"] = True marks the objects of a modelled room


def room_objects(scene=None, collection=None):
    """The objects of the modelled room: marked with obj["as_env"], or those of `collection`
    (older files: collections "ENV | <Name>")."""
    scene = scene or bpy.context.scene
    if collection:
        coll = bpy.data.collections.get(collection) if isinstance(collection, str) else collection
        if coll is None:
            raise ValueError("Collection %r not found" % collection)
        return list(coll.all_objects)
    return [o for o in scene.objects if o.get(ENV_KEY)]

LIGHT_SETUP_FORMAT = """\
Answer with JSON only, in this format:
{"name": "<short preset name>",
 "lights": [
   {"name": "Key", "type": "AREA",
    "horizontal": -45, "vertical": 35, "distance": 3.0,
    "size": 1.2, "power": 1.0,
    "color": [1.0, 0.95, 0.9]}
 ]}
Fields per light (all optional except where noted):
- type: "AREA" (soft box, default), "POINT", "SPOT" or "SUN".
- horizontal: degrees around the subject. 0 = camera side, negative = left, positive = right, 180 = behind.
- vertical: degrees above (+) or below (-) the subject, -89.5..89.5.
- distance: in subject radii (half the subject's bounding box diagonal), 0.5..50. Typical 2.5..5.
- size: area size (or point/spot radius) in subject radii. Bigger = softer shadows.
- power: 1.0 = a standard key light; fill typically 0.2..0.5, rim 0.5..1.0. Scaled with distance automatically.
- color: sRGB 0..1, or instead "kelvin": 2000..12000 (3200 warm tungsten, 5600 daylight, 7500 cool shade).
- shape (AREA): "RECTANGLE", "SQUARE", "DISK", "ELLIPSE".
- spot_angle, spot_blend (SPOT): degrees, 0..1.  sun_angle (SUN): softness in degrees.
At most 16 lights. The setup replaces the previous setup; the user's own lights stay."""

HDRI_SETUP_FORMAT = """\
Answer with JSON only, in this format:
{"name": "<short name>",
 "sky": {"top": [0.85, 0.9, 1.0], "horizon": [1.0, 1.0, 1.0], "ground": [0.3, 0.3, 0.3]},
 "strength": 1.0,
 "sun": {"horizontal": -30, "vertical": 45, "size": 2.0, "strength": 50, "kelvin": 5600},
 "softboxes": [{"horizontal": -60, "vertical": 25, "width": 40, "height": 30, "strength": 8,
                "color": [1, 1, 1]}],
 "resolution": 2048}
- sky: colors of the zenith, the horizon and the ground (sRGB 0..1). strength scales the sky.
- sun (optional): a bright disk. size in degrees (0.5 sharp .. 10 soft), strength 10..1000.
- softboxes (optional, up to 16): bright rectangles like studio lights, width/height in degrees,
  strength 1..50. Leave out the sun for a pure studio look.
- horizontal/vertical: degrees as seen from the subject. 0 = camera side, negative = left,
  positive = right, 180 = behind; vertical above (+) or below (-) the horizon.
- colors may use "kelvin" instead of "color" (sun and softboxes).
- resolution: width in pixels (height is half), 1024..4096; 2048 is enough for lighting."""


def _light_info(obj, center, radius, front):
    data = obj.data
    offset = obj.matrix_world.translation - center
    d = offset.length
    info = {"name": obj.name, "type": data.type, "power_watts": round(data.energy, 2)}
    if d > 1e-6:
        info["horizontal"] = round((math.degrees(math.atan2(offset.y, offset.x) - front) + 180) % 360 - 180, 1)
        info["vertical"] = round(math.degrees(math.atan2(offset.z, offset.xy.length)), 1)
        info["distance"] = round(d / radius, 2)
    info["color"] = [round(lights.linear_to_srgb(c), 3) for c in data.color]
    return info


def scene_context(scene):
    """What Claude should know about the scene, as a dict."""
    rs = scene.render_studio
    target = rs.light_orbit_target
    center, radius = lights.subject(scene, target)
    front = lights.front_angle(scene, center)
    cam = scene.camera
    current = hdri.current_path(scene)
    return {
        "subject": {
            "name": target.name if target else None,
            "radius_m": round(radius, 3),
            "note": "positions are relative to this subject and the active camera",
        },
        "camera": {"name": cam.name, "focal_length_mm": round(cam.data.lens, 1)}
        if cam is not None and cam.type == 'CAMERA' else None,
        "render": {"engine": scene.render.engine, "view_transform": scene.view_settings.view_transform,
                   "background": rs.background_mode},
        "lights": [_light_info(o, center, radius, front) for o in lights.scene_lights(scene)],
        "world": {"hdri": current.rsplit("/", 1)[-1] if current else None,
                  "name": scene.world.name if scene.world else None},
        "saved_light_presets": sorted(lights.saved_presets()),
    }


def extract_json(text):
    """Takes the JSON object out of an answer (```json fences or text around it)."""
    if not isinstance(text, str):
        return text
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("No JSON object found in the answer")
    return text[start:end + 1]


def light_setup(spec, scene=None, save=True):
    """Applies a light setup (dict or JSON text) and, with save=True, stores it as a preset.
    Returns the created light objects."""
    scene = scene or bpy.context.scene
    parsed = lights.parse_setup(extract_json(spec))
    created = lights.apply_setup(scene, scene.render_studio.light_orbit_target, parsed)
    if save:
        lights.save_preset(parsed)
    if created:
        scene.render_studio.active_light = created[0]
    return created


def hdri_setup(spec, scene=None):
    """Generates an HDRI file from a world setup, uses it and returns its path."""
    scene = scene or bpy.context.scene
    p = runner.prefs()
    return hdri_gen.generate(scene, extract_json(spec), p.hdri_folder if p else "", scene.render_studio)


def environment_context(scene):
    """What Claude needs to build a room around the subject, as a dict."""
    center, radius = lights.subject(scene, scene.render_studio.light_orbit_target)
    floor = None
    for obj in scene.objects:
        if obj.type != 'MESH' or obj.hide_render or not obj.visible_get():
            continue
        if obj.get(ENV_KEY) or any(c.name.startswith("ENV |") for c in obj.users_collection):
            continue
        z = min((obj.matrix_world @ Vector(c)).z for c in obj.bound_box)
        floor = z if floor is None else min(floor, z)
    p = runner.prefs()
    cam = scene.camera
    return {
        "subject": {"center_m": [round(v, 3) for v in center], "radius_m": round(radius, 3),
                    "floor_z_m": round(floor if floor is not None else center.z - radius, 3)},
        "camera": {"name": cam.name, "location_m": [round(v, 3) for v in cam.matrix_world.translation]}
        if cam is not None and cam.type == 'CAMERA' else None,
        "hdri_folder": hdri.folder_path(p.hdri_folder if p else ""),
        "environment_folder": ENVIRONMENT_FOLDER,
        "room_objects": len(room_objects(scene)),   # marked with obj["as_env"]
        "environments": sorted(c.name for c in bpy.data.collections if c.name.startswith("ENV |")),
        "worlds": [w.name for w in bpy.data.worlds],
    }


def environment_prompt(scene):
    return ("You build a real room around the subject and bake it into an HDRI.\n"
            "Current scene:\n" + json.dumps(environment_context(scene), indent=1))


def use_hdri(path, scene=None):
    """Use an existing HDRI/EXR (e.g. a baked room) as the World. Brighter/darker and rotation:
    scene.render_studio.world_strength / world_rotation, never bake again for that."""
    scene = scene or bpy.context.scene
    hdri.apply(scene, path, scene.render_studio)
    return path


def save_base_material(material, name=None):
    """Put a material of this file into the base material library (the group with the base
    materials in the Material tab), replacing one of the same name. `material` is a Material or
    its name; `name` the name in the library (default: the base name or the material's name)."""
    from . import baselib
    if isinstance(material, str):
        material = bpy.data.materials[material]
    return baselib.save(material, name)


def remove_base_material(name):
    """Take a material out of the base material library (ask the user first)."""
    from . import baselib
    baselib.remove(name)
    return name


def environment_bake(name, collection=None, location=None, world=None, resolution=4096, samples=256,
                     scene=None, use=True):
    """Renders a modelled room as a 32-bit equirectangular EXR into <HDRI folder>/Environment and,
    with use=True, uses it as the World. Returns the file path. The room: the objects marked
    obj["as_env"] (or those of `collection`); they are rendered even if hidden in renders.

    location: panorama position (default: center of the subject). world: World that lights the
    room while baking (default: the scene's World). Everything else is hidden while rendering;
    camera, render settings, World and hide_render states are restored."""
    import os
    from .. import autosave
    autosave.save_now("before environment bake", force=True)   # long Cycles render
    scene = scene or bpy.context.scene
    keep = set(room_objects(scene, collection))
    if not keep:
        raise ValueError('No room objects: mark them with obj["as_env"] = True')
    p = runner.prefs()
    root = hdri.folder_path(p.hdri_folder if p else "")
    if not root:
        raise ValueError("Set an HDRI folder in the preferences first")
    folder = os.path.join(root, ENVIRONMENT_FOLDER)
    os.makedirs(folder, exist_ok=True)
    path = core.next_path(folder, core.safe_name(name), ".exr")
    if location is None:
        location, _radius = lights.subject(scene, scene.render_studio.light_orbit_target)

    render, cycles, image = scene.render, scene.cycles, scene.render.image_settings
    saved_render = {k: getattr(render, k) for k in ("engine", "resolution_x", "resolution_y",
                    "resolution_percentage", "filepath", "film_transparent", "use_compositing", "use_sequencer")}
    saved_cycles = {k: getattr(cycles, k) for k in ("samples", "use_denoising", "sample_clamp_indirect")}
    saved_image = {k: getattr(image, k) for k in ("media_type", "file_format", "color_depth", "color_mode")
                   if hasattr(image, k)}
    saved_camera, saved_world = scene.camera, scene.world
    hidden = {obj: obj.hide_render for obj in scene.objects}

    data = bpy.data.cameras.new("RS Environment Bake")
    data.type = 'PANO'
    data.panorama_type = 'EQUIRECTANGULAR'
    cam = bpy.data.objects.new("RS Environment Bake", data)
    cam.location = location
    cam.rotation_euler = (math.pi / 2, 0.0, -math.pi / 2)   # image center = +X, as the World maps it
    scene.collection.objects.link(cam)
    try:
        for obj in hidden:
            obj.hide_render = obj not in keep
        if world is not None:
            scene.world = world
        scene.camera = cam
        render.engine = 'CYCLES'
        render.resolution_x, render.resolution_y = int(resolution), int(resolution) // 2
        render.resolution_percentage = 100
        render.film_transparent = False
        render.use_compositing = render.use_sequencer = False
        cycles.samples = int(samples)
        cycles.use_denoising = True
        cycles.sample_clamp_indirect = 10.0
        if hasattr(image, "media_type"):
            image.media_type = 'IMAGE'
        image.file_format = 'OPEN_EXR'
        image.color_depth = '32'
        image.color_mode = 'RGB'
        render.filepath = path
        bpy.ops.render.render(write_still=True)
    finally:
        for obj, state in hidden.items():
            obj.hide_render = state
        scene.camera, scene.world = saved_camera, saved_world
        for k, v in saved_render.items():
            setattr(render, k, v)
        for k, v in saved_cycles.items():
            setattr(cycles, k, v)
        for k, v in saved_image.items():
            try:
                setattr(image, k, v)
            except (TypeError, ValueError):
                pass
        bpy.data.objects.remove(cam)
        bpy.data.cameras.remove(data)
    hdri._cache["folder"] = None   # scan again, the new file shows up in the gallery
    if use:
        hdri.apply(scene, path, scene.render_studio)
        hdri.set_rotation(None, 0.0)   # baked in world axes
    return path


# ---------------------------------------------------------------- objects, CAD versions, references

def unique_name(name, kind="objects"):
    """A short name with the next free two-digit number: "Ortho Top" → "Ortho Top_01"
    ("Ortho Top_02" when _01 exists). kind: the bpy.data collection to check (objects,
    materials, collections, cameras, lights …)."""
    from ..naming import numbered
    return numbered(name, getattr(bpy.data, kind).keys())


def rename(obj, name):
    """Name a camera or light in the scheme: rename(cam, "side") → "P_side_01" (P/O from the
    camera's type, A/P/S/D from the light's), the next free number. Returns the name."""
    from . import cameras
    obj = bpy.data.objects.get(obj) if isinstance(obj, str) else obj
    if obj is None:
        raise ValueError("No such object")
    return cameras.rename(obj, name)


def archive(objects):
    """Move objects (or names) into the hidden archive collection instead of deleting them; the user
    deletes them there. Returns how many were moved."""
    from . import cadswap
    return cadswap.archive([bpy.data.objects[o] if isinstance(o, str) else o for o in objects])


def temp_collection():
    """The collection for helper objects; it is deleted after Claude's answer."""
    from . import cadswap
    return cadswap.temp_collection()


def cad_take_over(new_objects, materials=True, collections=True, delete=False, setup=True):
    """A new CAD version takes over from the old one: by CAD id or part name ("Gabel", "Gabel.001",
    "Gabel <1>" are the same part) the new objects get the materials, collections and (setup)
    modifiers, smooth shading, parent, visibility and custom properties of the old ones; the old
    ones go into the archive, by CAD version (delete=True only when the user asked for it).
    Returns {"pairs": [[new, old], …], "new_without_partner": […], "old_without_partner": […]}."""
    from . import cadswap
    objects = [bpy.data.objects[o] if isinstance(o, str) else o for o in new_objects]
    pairs, lonely_new, lonely_old = cadswap.match(objects)
    cadswap.take_over(pairs, materials, collections, 'DELETE' if delete else 'ARCHIVE', setup=setup)
    return {"pairs": [[n.name, o.name] for n, o in pairs],
            "new_without_partner": [o.name for o in lonely_new],
            "old_without_partner": [o.name for o in lonely_old]}


def cad_import(path, take_over=True, materials=True, setup=True, delete_old=False, tolerance_mm=0.5):
    """The whole CAD hand-over in one call. Imports a CAD export (GLB/glTF incl. Onshape's glTF
    text, STEP through OCP, OBJ, STL …) with its sidecar <name>.cad.json (see cadfiles): turns a
    Z-up glTF upright, tags parts (obj["cad_id"], "cad_material", "cad_version", "cad_file"), takes
    over from the old version (materials, collections, modifiers, settings; old parts into
    "Archive <version>"), gives new parts the base material their CAD material maps to, places
    the mounting empties "MOUNT | …", checks the sizes against the CAD (tolerance_mm) and writes the
    sidecar's variants into Varianten.json. Returns a report dict; print(api.cad_summary(report))."""
    from . import cadimport
    return cadimport.cad_import(path, take_over=take_over, materials=materials, setup=setup,
                                delete_old=delete_old, tolerance_mm=tolerance_mm)


def cad_summary(report):
    """Short text of a cad_import report."""
    from . import cadimport
    return cadimport.summary(report)


def cad_material_map(cad_material=None, base=None):
    """Without arguments: the mapping CAD material -> base material (all projects). With both:
    remember one ("PETG milchig" -> "PETG translucent") for the next imports."""
    from . import cadfiles, cadimport
    if cad_material and base:
        return cadimport.map_material(cad_material, base)
    return cadfiles.load_material_map(cadimport._map_folder())


def cad_check_views(objects=None, views=("Iso", "Front"), size=900, samples=16):
    """Check images of the CAD parts (default: the last import) from fixed views, rendered by the
    worker into <name>_renders/cad-check/<view>.png. Views: Iso, Front, Right, Top. Poll
    api.cad_check_result() until it is not None, then look at the images (Read)."""
    from . import cadimport
    return cadimport.check_views(objects, tuple(views), size, samples)


def cad_check_result():
    """None while the check images render, else {"images": {view: path}, "errors", "folder"}."""
    from . import cadimport
    return cadimport.check_result()


def cad_feedback(part, problem, folder=None, image=None):
    """An open point for the CAD side (collision, wrong size, a change to make) in CAD-Feedback.md
    next to the part's CAD export (with the image it refers to). Returns the file's path. Open
    points: api.cad_feedback_open(). For a change a chat should make now: studio_cad_job."""
    from . import cadimport
    return cadimport.feedback(part, problem, folder, image=image)[0]


def cad_feedback_open(folder=None):
    """The open points of CAD-Feedback.md (next to the last CAD import, or in `folder`)."""
    from . import cadfiles, cadimport
    return cadfiles.open_feedback(folder or cadimport.feedback_folder())


REFERENCE_DIRS = ("refrence", "reference", "references", "Referenz")
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".heic", ".jxl")


def _reference_dir(folder):
    """The reference folder directly in `folder` (any spelling of REFERENCE_DIRS), or None."""
    import os
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    wanted = {d.lower() for d in REFERENCE_DIRS}
    return next((os.path.join(folder, n) for n in sorted(names)
                 if n.lower() in wanted and os.path.isdir(os.path.join(folder, n))), None)


def project_root(path=None):
    """The nearest folder at or above the .blend file that has a reference folder
    ("reference", "references", "Referenz", "refrence"), e.g. Chair/reference next to
    Chair/Chair.blend or Project/reference above Project/DATA/Chair/Chair.blend. Stops at home."""
    import os
    filepath = path or bpy.data.filepath
    if not filepath:
        return None
    home = os.path.expanduser("~")
    folder = os.path.dirname(os.path.abspath(filepath))
    while folder and os.path.dirname(folder) != folder:
        if _reference_dir(folder):
            return folder
        if os.path.normcase(folder) == os.path.normcase(home):
            break
        folder = os.path.dirname(folder)
    return None


def references(topic=None):
    """Without topic: the topics (subfolders) of the project's reference folder. With a topic
    ("CMF", case does not matter): the image paths in it (subfolders too). HEIC/JXL: convert a
    copy with `sips -s format jpeg` into a temp folder before reading. Never write into the folder."""
    import os
    root = project_root()
    if root is None:
        raise ValueError("No reference folder next to or above this file (make one named 'reference')")
    base = _reference_dir(root)
    topics = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)))
    if topic is None:
        return topics
    name = next((t for t in topics if t.lower() == str(topic).strip().lower()), None)
    if name is None:
        raise ValueError("No reference topic %r (there are: %s)" % (topic, ", ".join(topics)))
    found = []
    for folder, _dirs, files in os.walk(os.path.join(base, name)):
        found += [os.path.join(folder, f) for f in sorted(files) if f.lower().endswith(IMAGE_EXTS)]
    return found


# ---------------------------------------------------------------- section through scan and model

def section(objects, axis="X", offset=0.0, plane_object=None, keep=False, compare=True):
    """Cut objects (or names, e.g. a scan and the CAD part) with a plane and measure the cut.

    Plane: `axis` "X"/"Y"/"Z" at `offset` meters along it, or the local XY plane of
    `plane_object`. The contours become edge objects "Section <name>" in the temp collection
    (keep=True: in the collection "Sections"). Returns per object width/height in the plane (mm),
    the center and the number of cut edges; with compare and two or more objects also how far the
    contours of the others lie from the first one (mean/max mm), e.g. CAD against the scan."""
    import bmesh
    from mathutils import kdtree
    from . import cadswap
    objects = [bpy.data.objects[o] if isinstance(o, str) else o for o in objects]
    if plane_object is not None:
        plane_object = bpy.data.objects[plane_object] if isinstance(plane_object, str) else plane_object
        co = plane_object.matrix_world.translation.copy()
        normal = (plane_object.matrix_world.to_3x3() @ Vector((0, 0, 1))).normalized()
    else:
        normal = Vector({"X": (1, 0, 0), "Y": (0, 1, 0), "Z": (0, 0, 1)}[str(axis).upper()])
        co = normal * float(offset)
    u = normal.orthogonal().normalized()
    v = normal.cross(u).normalized()
    if str(axis).upper() == "Z" and plane_object is None:
        u, v = Vector((1, 0, 0)), Vector((0, 1, 0))
    elif str(axis).upper() in ("X", "Y") and plane_object is None:
        v = Vector((0, 0, 1))
        u = v.cross(normal).normalized()
    if keep:
        coll = bpy.data.collections.get("Sections") or bpy.data.collections.new("Sections")
        if coll not in bpy.context.scene.collection.children_recursive:
            bpy.context.scene.collection.children.link(coll)
    else:
        coll = cadswap.temp_collection()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    result, contours = {}, []
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        bm = bmesh.new()
        bm.from_mesh(mesh)
        evaluated.to_mesh_clear()
        bm.transform(obj.matrix_world)
        cut = bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=co,
                                     plane_no=normal, dist=1e-6)["geom_cut"]
        verts = [vert for vert in cut if isinstance(vert, bmesh.types.BMVert)]
        index = {vert: i for i, vert in enumerate(verts)}
        points = [vert.co.copy() for vert in verts]
        pairs = [(index[e.verts[0]], index[e.verts[1]]) for e in cut
                 if isinstance(e, bmesh.types.BMEdge) and e.verts[0] in index and e.verts[1] in index]
        bm.free()
        name = "Section " + obj.name
        if not points:
            result[obj.name] = {"cut": False}
            contours.append([])
            continue
        old = bpy.data.objects.get(name)
        if old is not None:
            bpy.data.objects.remove(old)
        data = bpy.data.meshes.new(name)
        data.from_pydata([p - co for p in points], pairs, [])
        section_obj = bpy.data.objects.new(name, data)
        section_obj.location = co
        coll.objects.link(section_obj)
        us = [p.dot(u) for p in points]
        vs = [p.dot(v) for p in points]
        center = sum(points, Vector()) / len(points)
        result[obj.name] = {"cut": True, "width_mm": round((max(us) - min(us)) * 1000, 2),
                            "height_mm": round((max(vs) - min(vs)) * 1000, 2),
                            "center_m": [round(c, 4) for c in center], "edges": len(pairs),
                            "object": section_obj.name}
        contours.append(points)
    if compare and len(contours) > 1 and contours[0]:
        tree = kdtree.KDTree(len(contours[0]))
        for i, p in enumerate(contours[0]):
            tree.insert(p, i)
        tree.balance()
        for obj, points in zip(objects[1:], contours[1:]):
            if points:
                dists = [tree.find(p)[2] for p in points]
                result[obj.name]["to_" + objects[0].name + "_mm"] = {
                    "mean": round(sum(dists) / len(dists) * 1000, 2), "max": round(max(dists) * 1000, 2)}
    return result


# ---------------------------------------------------------------- Environment tab

def scene_preset(name, scene=None):
    """Applies a scene preset (built-in: "Studio Softboxes", "Daylight", "Golden Hour", "Night", "Studio White",
    "Dark Moody", or a saved one).
    Returns notes (e.g. a missing HDRI)."""
    from . import environment
    return environment.apply_scene(scene or bpy.context.scene, name)


def save_scene_preset(name, scene=None):
    from . import environment
    return environment.save_scene(scene or bpy.context.scene, name)


def floor(mode="COVE", color=None, roughness=None, size=None, curve=None, shadow_catcher=None, scene=None):
    """Studio floor under the product: mode NONE / FLOOR / COVE; color as sRGB (0-1). Returns the object."""
    from . import environment
    scene = scene or bpy.context.scene
    rs = scene.render_studio
    for prop, value in (("floor_color", color), ("floor_roughness", roughness), ("floor_size", size),
                        ("floor_curve", curve), ("floor_shadow_catcher", shadow_catcher)):
        if value is not None:
            setattr(rs, prop, value)
    rs.floor_mode = mode
    return environment.build_floor(scene)


def sun_from_hdri(scene=None):
    """A sun light where the HDRI is brightest. Returns the light object."""
    from . import environment
    return environment.sun_from_hdri(scene or bpy.context.scene)


def flag(light, pattern="FLAG", speed=None, scale=None, scene=None):
    """Flag or gobo in front of a light: FLAG, BLINDS, WINDOW, and the moving ones DAPPLE (leaves), BRANCHES (tree),
    WATER, CLOUDS. speed: how fast a moving one moves when the animation plays (0 = still, 1 = normal);
    scale: how fine its pattern is. pattern=None removes it."""
    from . import environment
    light = bpy.data.objects[light] if isinstance(light, str) else light
    if pattern is None:
        environment.remove_flag(light)
        return None
    if pattern not in {k for k, _n, _d in environment.FLAG_PATTERNS}:
        raise ValueError("pattern must be one of %s" % ", ".join(k for k, _n, _d in environment.FLAG_PATTERNS))
    obj = environment.add_flag(scene or bpy.context.scene, light, pattern)
    values = {k: v for k, v in (("speed", speed), ("scale", scale)) if v is not None}
    if values:
        environment.set_gobo_values(obj, values)
    return obj


def only_product(light, on=True, scene=None):
    """Light linking: the light lights only the product (True) or everything (False)."""
    from . import environment
    light = bpy.data.objects[light] if isinstance(light, str) else light
    if environment.linked_to_product(light) != on:
        environment.toggle_product_link(scene or bpy.context.scene, light)


def world(mode, time=None, pins=None, ambient=None, scene=None):
    """The World: mode "IMAGE" (an HDRI file), "STUDIO" (lights painted into an HDRI) or "SKY" (by time of day).
    time: hours for SKY (14 = afternoon, 17.2 = golden hour, 23 = night). pins: for STUDIO a list of
    {"name", "horizontal", "vertical", "width", "height" (degrees), "strength", "softness" 0-1, "shape" RECT/ROUND,
    "color"}; 0° horizontal = from the camera, negative = left."""
    from . import world_modes
    scene = scene or bpy.context.scene
    rs = scene.render_studio
    if time is not None:
        rs["sky_time"] = float(time)
    if ambient is not None:
        rs["studio_ambient"] = float(ambient)
    if pins is not None:
        rs.hdri_pins.clear()
        for i, item in enumerate(pins):
            pin = rs.hdri_pins.add()
            pin["name"] = str(item.get("name") or "Light %d" % (i + 1))
            for key, default in (("horizontal", 0), ("vertical", 30), ("width", 40), ("height", 30)):
                pin[key] = math.radians(float(item.get(key, default)))
            pin["strength"], pin["softness"] = float(item.get("strength", 8)), float(item.get("softness", 0.35))
            pin["shape"] = 1 if str(item.get("shape", "RECT")).upper() == "ROUND" else 0
            pin["color"] = [float(c) for c in item.get("color", [1, 1, 1])][:3]
    rs.world_mode = mode
    return world_modes.studio_spec(scene) if mode == 'STUDIO' else mode


def place_light(light, on_object, mode="REFLECTION", distance=None, scene=None):
    """Place a light by a spot on the product, as seen from the active camera: the middle of `on_object` that
    faces the camera. mode: REFLECTION (highlight there), ILLUMINATION (lit straight on), RIM (from behind),
    SHADE (that spot falls into shadow), SHADOW (the product's shadow falls there: use the floor as on_object)."""
    from . import light_paint
    scene = scene or bpy.context.scene
    light = bpy.data.objects[light] if isinstance(light, str) else light
    target = bpy.data.objects[on_object] if isinstance(on_object, str) else on_object
    cam = scene.camera
    if cam is None:
        raise ValueError("No active camera")
    eye = cam.matrix_world.translation
    center = sum((target.matrix_world @ Vector(c) for c in target.bound_box), Vector()) / 8
    view = (center - eye).normalized()
    ok, hit, normal, _i, _o, _m = scene.ray_cast(bpy.context.evaluated_depsgraph_get(), eye, view)
    if not ok:
        raise ValueError("The camera does not see %s" % target.name)
    from . import light_items
    product = light_items.center_of(scene, light)
    direction = light_paint.light_direction(view, normal, mode, hit, product)
    if distance is None:
        distance = max((light.matrix_world.translation - hit).length, 0.05)
    light_paint.place_light(light, product if mode == 'SHADOW' else hit, direction, distance)
    return [round(v, 4) for v in light.matrix_world.translation]


def _light_item(light, scene):
    """A light of the Lights list: a scene light (object or name) or a studio World light (its index or name)."""
    from . import light_items
    rs = scene.render_studio
    if isinstance(light, int):
        if not 0 <= light < len(rs.hdri_pins):
            raise ValueError("No studio World light %d" % light)
        return ('PIN', light)
    if isinstance(light, str):
        obj = bpy.data.objects.get(light)
        if obj is not None and obj.type == 'LIGHT':
            return ('LIGHT', obj)
        index = next((i for i, pin in enumerate(rs.hdri_pins) if pin.name == light), None)
        if index is None or not light_items.pins_shown(scene):
            raise ValueError("No light %r" % light)
        return ('PIN', index)
    return ('LIGHT', light)


def lights_list(scene=None):
    """Every light of the Lights list with its place on the light map: "around" in degrees (0 = from the
    camera, positive = the camera's right, 180 = from behind) and "height" (0 = horizon, 90 = from above)."""
    from . import light_items
    scene = scene or bpy.context.scene
    result = []
    for item in light_items.all_items(scene):
        around, height = light_items.angles(scene, item)
        result.append({"name": light_items.name_of(scene, item), "kind": "world" if item[0] == 'PIN' else "scene",
                       "on": light_items.is_on(scene, item), "around": round(math.degrees(around), 1),
                       "height": round(math.degrees(height), 1)})
    return result


def move_light(light, around=None, height=None, scene=None):
    """Moves a light on the light map (degrees, see lights_list); its distance, size and target stay."""
    from . import light_items
    scene = scene or bpy.context.scene
    item = _light_item(light, scene)
    now = light_items.angles(scene, item)
    light_items.set_angles(scene, item, now[0] if around is None else math.radians(around),
                           now[1] if height is None else math.radians(height))
    return lights_list(scene)


def mirror_light(light=None, copy=False, scene=None):
    """Flips a light to the other side of the product (left / right as the camera sees it); copy=True keeps
    it and puts a copy there. light=None flips the whole lighting."""
    from . import light_items, looks
    scene = scene or bpy.context.scene
    if light is None:
        looks.mirror_all(scene)
    else:
        light_items.mirror(scene, _light_item(light, scene), copy=copy)
    return lights_list(scene)


def look(name=None, new=False, scene=None):
    """Looks: several lighting moods in this file (lights, World, background, floor, haze). new=True keeps the
    lighting so far and goes on in a copy; name switches to that look ("A", "B", …). Returns the looks and
    the open one."""
    from . import looks
    scene = scene or bpy.context.scene
    notes = []
    if new:
        looks.add(scene)
    if name is not None:
        notes = looks.switch_name(scene, str(name))
    index = looks.active(scene)
    names = looks.names(scene)
    return {"looks": names, "open": names[index] if index >= 0 else None, "notes": notes}

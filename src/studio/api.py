# SPDX-License-Identifier: GPL-3.0-or-later
"""AI Studio's functions for Claude (in execute_python as `api`, and behind the studio_* tools):
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
    rs = scene.studio
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
    created = lights.apply_setup(scene, scene.studio.light_orbit_target, parsed)
    if save:
        lights.save_preset(parsed)
    if created:
        scene.studio.active_light = created[0]
    return created


def hdri_setup(spec, scene=None):
    """Generates an HDRI file from a world setup, uses it and returns its path."""
    scene = scene or bpy.context.scene
    p = runner.prefs()
    return hdri_gen.generate(scene, extract_json(spec), p.hdri_folder if p else "", scene.studio)


def environment_context(scene):
    """What Claude needs to build a room around the subject, as a dict."""
    center, radius = lights.subject(scene, scene.studio.light_orbit_target)
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
    scene.studio.world_strength / world_rotation, never bake again for that."""
    scene = scene or bpy.context.scene
    hdri.apply(scene, path, scene.studio)
    return path


def save_base_material(material, name=None):
    """Put a material of this file into the base material library (the group with the base
    materials in the Material tab), replacing one of the same name. `material` is a Material or
    its name; `name` the name in the library (default: the base name or the material's name)."""
    from . import baselib
    if isinstance(material, str):
        material = bpy.data.materials[material]
    return baselib.save(material, name)


def material_structure(kind, name=None, objects=None, **values):
    """A new procedural material to go on from. kind: WORN_METAL (tumbled metal, scratches, patina),
    LAYERED_EARTH (rammed earth, layers along Z), SPECKLED (recycled plastic, flecks), RESIN (amber, acetate:
    clear, color deepens with thickness), HAMMERED (polished metal with dents), DARK_ANODIZED (dark metal,
    long scratches, wipe marks). objects: objects or names it is put on. values: the structure's inputs by
    name (color, roughness, scale, scratches, patina, color_a, color_b, layer_height, grain, fleck_size,
    amount, dent_size, depth, smudges, bands, band_size …). Returns the material."""
    from . import matpresets, matstructures
    material = matstructures.create(str(kind).upper(), name)
    if values:
        matstructures.set_values(material, values)
    if objects:
        found = [bpy.data.objects[o] if isinstance(o, str) else o for o in objects]
        matpresets.assign(material, found)
    return material


def library_material(name, structure=None, kind=None, wear=None, **values):
    """Builds one material and puts it straight into the library (the Library of the Material tab, for
    every project); one of the same name is replaced. For a batch from reference images: one call per
    image. structure: WORN_METAL, LAYERED_EARTH, SPECKLED, RESIN, HAMMERED, DARK_ANODIZED (values: the
    structure's inputs, see material_structure) or None for a plain material. kind: Metal, Plastic,
    Glass, Rubber, Wood, Fabric, Light (decides what is fixed, e.g. metallic, and the controls in the
    tab). values of a plain material: Principled inputs by name (color, roughness, ior, coat_weight,
    sheen_weight, subsurface_weight, emission_strength …). wear: {"scratches": 0.3, "fingerprints": 0.2,
    "dust": 0.1} or None. Returns the name in the library."""
    from . import baselib, matkinds, matpresets, matstructures
    material_values = dict(values)
    if structure:
        material = matstructures.create(str(structure).upper(), name)
        matstructures.set_values(material, material_values)
    else:
        material = bpy.data.materials.new(name)
    node = matpresets._principled(material)
    if kind:
        matkinds.apply(material, node, str(kind).title())
    if node is not None and not structure:
        matkinds.set_values(node, material_values)
        color = node.inputs["Base Color"].default_value
        material.diffuse_color = tuple(color)
        material.roughness = node.inputs["Roughness"].default_value
    if wear:
        matstructures.add_wear(material, **wear)
    return baselib.save(material, name)


def camera_view(view):
    """A new camera for a standard view of the product: FRONT, BACK, LEFT, RIGHT, TOP, ISO (orthographic),
    HERO (three-quarter, 85 mm), DETAIL (close, 100 mm). Returns the camera."""
    from . import cameras
    scene = bpy.context.scene
    camera = cameras.camera_preset(scene, bpy.context.view_layer, str(view).upper())
    scene.camera = camera
    return camera


def krea_context():
    """The render shown in the Refine tab and what Krea must know about it: file, pixel size, camera, the
    product's true size in cm, how much of the picture it fills and the scale sentence for the prompt."""
    from . import krea_scene, tab_finish
    scene = bpy.context.scene
    source = tab_finish.current_image(scene)
    if not source:
        raise ValueError("Render an image first")
    return krea_scene.describe(scene, source)


def krea_prepare(prompt, upload_url, model=None, environment=None, zoom=None, view=None, protect=None):
    """Krea with the user's plan (Krea connector): builds the full prompt (your English prompt + the product's
    true scale + keep the product), renders the mask that keeps the product untouched (Precise, protect) and
    uploads render, mask and the user's pictures to upload_url (from Krea's get_upload_url). None = the Finish
    tab's choice. model: PRECISE, NANO or AUTO; environment: pictures (paths, None = the ones added in the tab;
    given here they count as the place); zoom CLOSER/KEEP/WIDER; view (camera height) LOWER/KEEP/HIGHER.
    The product is what the user marked (Refine tab), else everything visible.
    Returns {"model": Krea model id, "input": pass as generate_image's input unchanged, "credits_about", ...}."""
    from . import tab_finish
    return tab_finish.prepare(bpy.context.scene, prompt, upload_url, model, environment, zoom, view, protect)


def krea_quote(credits, balance=None):
    """Show Krea's price for the next picture (the preflight's compute units, and the plan's balance) in the
    Refine tab before generating."""
    from . import krea, tab_finish
    krea.set_quote(tab_finish._plan.get("model", ""), credits, balance)
    return "Krea: %s credits%s" % (credits, " (%s left)" % balance if balance is not None else "")


def krea_save(url, credits=None, balance=None):
    """Krea's result image URL → saved next to the render (…_krea_01.png) at exactly its size, logged with
    what it cost, shown in the Refine and Render tabs. Returns the path (it downloads in the background)."""
    from . import krea, tab_finish
    plan = tab_finish._plan
    if not plan:
        raise ValueError("Call api.krea_prepare first")
    return krea.save_from(url, plan["source"], plan["model"], credits, balance)


def krea_failed(reason):
    """Krea could not make the picture: show why in the Refine tab."""
    from . import krea
    krea.fail(reason)
    return reason


def krea_refine(prompt, model="AUTO", environment=None, zoom="KEEP", view="KEEP", protect=None):
    """Without Claude's Krea connector: refines the render shown in the Refine tab with the user's Krea API
    token (its own balance; in the background, the Refine tab shows it). Same arguments as krea_prepare."""
    from . import tab_finish
    target, used, width, height = tab_finish.refine(bpy.context.scene, prompt, str(model).upper(), environment,
                                                    str(zoom).upper(), str(view).upper(), protect)
    return {"result": target, "model": used, "size": [width, height]}


def wear(material, remove=False, **values):
    """Scratches, fingerprints and dust over any material with a Principled BSDF. values: scratches,
    fingerprints, dust (0-1 each, the strength); their kind: scratch_length, one_direction (0 = all directions,
    1 = drag marks in one direction), scratch_depth, print_size, smear (0-1), dust_on_top (0 = everywhere,
    1 = only faces that look up), dust_size, dust_color; where: from_ and to_ are not names, use "from" / "to"
    in a dict: api.wear(mat, **{"from": 0.0, "to": 0.4}) = scratches and prints only on the lower 40 % (where
    a hand holds it); scale. remove=True takes the layer off again."""
    from . import matstructures
    if isinstance(material, str):
        material = bpy.data.materials[material]
    if remove:
        matstructures.remove_wear(material)
        return None
    matstructures.add_wear(material, **values)
    return {s.name: (tuple(s.default_value) if s.type == 'RGBA' else s.default_value)
            for s in material.node_tree.nodes[matstructures.NODE_WEAR].inputs if s.type != 'VECTOR' and not s.is_linked}


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
        location, _radius = lights.subject(scene, scene.studio.light_orbit_target)

    render, cycles, image = scene.render, scene.cycles, scene.render.image_settings
    saved_render = {k: getattr(render, k) for k in ("engine", "resolution_x", "resolution_y",
                    "resolution_percentage", "filepath", "film_transparent", "use_compositing", "use_sequencer")}
    saved_cycles = {k: getattr(cycles, k) for k in ("samples", "use_denoising", "sample_clamp_indirect")}
    saved_image = {k: getattr(image, k) for k in ("media_type", "file_format", "color_depth", "color_mode")
                   if hasattr(image, k)}
    saved_camera, saved_world = scene.camera, scene.world
    hidden = {obj: obj.hide_render for obj in scene.objects}

    data = bpy.data.cameras.new("Studio Environment Bake")
    data.type = 'PANO'
    data.panorama_type = 'EQUIRECTANGULAR'
    cam = bpy.data.objects.new("Studio Environment Bake", data)
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
        hdri.apply(scene, path, scene.studio)
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
    rs = scene.studio
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
    rs = scene.studio
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
    rs = scene.studio
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


# ------------------------------------------------------------------ project (several scenes, CAD, archive)

def sync_scenes(source=None, targets=None, look=True, props=True, lights=True):
    """Copy the look, the scene's own properties (Glow …) and light values from `source` (default: open
    scene) into the other scenes; lights matched by name without the scene's word ("light present").
    Returns one line per scene."""
    from . import project
    return project.sync_scenes(source, targets, look, props, lights)


def copy_scene(word, scene=None):
    """A full copy of the scene named "<name> <word>" ("present"), its objects, collections and materials
    ending with " <word>" instead of ".001". Returns the new scene's name."""
    from . import project
    return project.copy_scene(word, scene).name


def cad_rules(rules=None):
    """The file's CAD rules: [{"match": "Huelle", "material": "TPU", "parent": "Empty Pod"}, …] (match is a
    regex on the part name). With `rules` they are replaced. Every CAD import applies them. Returns them."""
    from . import project
    return project.set_cad_rules(rules) if rules is not None else project.cad_rules()


def archive_overview():
    """[(collection, objects, triangles)] of the archive and its versions."""
    from . import project
    return project.archive_overview()


def export_archive(names, remove=False):
    """Write archive collections (with their objects) into <file>_archiv.blend; remove=True then takes them
    out of this file (ask the user first). Returns the path."""
    from . import project
    return project.export_archive(list(names), remove=remove)


def light_link(light, targets, name=None):
    """`light` (object or name) lights only `targets` (objects/collections or names); nested collections
    are made flat. Returns the receiver collection's name."""
    from . import project
    light = bpy.data.objects[light] if isinstance(light, str) else light
    resolved = [bpy.data.collections.get(t) or bpy.data.objects.get(t) if isinstance(t, str) else t for t in targets]
    return project.light_link(light, [t for t in resolved if t is not None], name).name


def material_slider(name, materials, socket, value=0.5, minimum=0.0, maximum=1.0):
    """One slider for several materials: a property `name` on every scene drives `socket` ("Roughness") of
    each material through an Attribute node. Returns the materials changed."""
    from . import project
    return project.material_slider(name, materials, socket, value, minimum, maximum)


# ---------------------------------------------------------------- animation: sections, motion, cut

def section_add(name, frame=None, from_view=False):
    """A section from `frame` (default: the current one): marker "NN name" with its own camera
    P_<name>_01 on a pivot in Camera, starting from the active camera (or the viewport). Returns the
    marker's name."""
    from . import motion
    return motion.new_section(bpy.context.scene, name, frame, from_view=from_view).name


def section_frames(name, start=None, length=None, move_keys=True):
    """Move a section and / or give it `length` frames; the sections after it (and their keys) move along.
    Returns how many keys moved."""
    from . import motion
    return motion.set_length(bpy.context.scene, name, start, length, move_keys)


def motion_preset(preset, start=None, end=None, amount=0.0, reverse=False, replace=True, ease='SMOOTH',
           camera=None, drop_focus=False, in_render=False):
    """A motion preset (TURNTABLE degrees, SWING ± degrees, PUSH_IN / PULL_OUT % of the distance, CRANE
    degrees, FOCUS start %, FADE_IN / FADE_OUT frames, HOLD) on the camera's pivot over the frames
    (default: the current section). replace: keys of this kind in the frames go first. Returns the keys set.
    FADE_IN / FADE_OUT make a fade in the cut (on the section's strip: it can be changed later without
    rendering; returns {"cut_fade": strip}); in_render=True keys the exposure instead (baked into the
    render), only when the user asks for exactly that."""
    from . import cameras, motion as m
    scene = bpy.context.scene
    a, b, _name = m.current_range(scene)
    if preset in m.FADES and not in_render:
        section = _name
        if start is not None:
            section = next((mk.name for mk, s, e in m.sections(scene) if s <= start <= e), _name)
        frames = int(m.amount_of(preset, amount))
        try:
            made = cut_fade("IN" if preset == 'FADE_IN' else "OUT", frames, "WHITE" if reverse else "BLACK",
                            section or None)
        except ValueError as ex:
            return {"cut_fade": None, "note": "%s: render the section first, then the fade goes on its strip "
                                              "(in_render=True keys the exposure instead)" % ex}
        return {"cut_fade": made, "frames": frames}
    cam = bpy.data.objects[camera] if isinstance(camera, str) else camera or cameras.active_camera(scene)
    return m.apply(scene, cam, preset, a if start is None else start, b if end is None else end, ease, amount,
                   reverse, replace, drop_focus)


def motion_chain(section, steps, ease='SMOOTH', drop_focus=False):
    """Steps of a section one after the other: [("PULL_OUT", 24, 140), ("HOLD", 12), ("TURNTABLE", 0, 90)]
    (preset, frames (0 = to the end), amount, reverse). Replaces the section's steps and applies them on
    the section's camera. Returns the keys set."""
    from . import motion as m
    scene = bpy.context.scene
    found = m.find(scene, section)
    if found is None:
        raise ValueError("No section %s" % section)
    items = scene.studio.motion_steps
    for i in reversed(range(len(items))):
        if items[i].section == section:
            items.remove(i)
    for spec in steps:
        step = items.add()
        step.section, step.preset = section, spec[0]
        step.frames = int(spec[1]) if len(spec) > 1 else 0
        step.amount = float(spec[2]) if len(spec) > 2 else 0.0
        step.reverse = bool(spec[3]) if len(spec) > 3 else False
    marker, start, end = found
    return m.apply_chain(scene, marker.camera or scene.camera, section, start, end, ease, drop_focus)


def key_pose(from_view=True):
    """The viewport's view (or where the camera is) as a key on the active camera at this frame, with the lens."""
    from . import cameras, motion as m
    scene = bpy.context.scene
    return m.key_pose(scene, cameras.active_camera(scene), from_view=from_view)


def assembly_check(collection=None):
    """Loose parts: in a product collection (or `collection`), mesh objects without a parent while most of
    its parts hang on one assembly object (they stay behind when the product is moved).
    Returns [{collection, root, loose: [names]}]; empty when all is well."""
    import collections as _c
    scene = bpy.context.scene
    groups = [bpy.data.collections[collection]] if collection else \
        [c for c in scene.collection.children_recursive if any(o.type == 'MESH' for o in c.objects)]
    out = []
    for coll in groups:
        meshes = [o for o in coll.all_objects if o.type == 'MESH']
        if len(meshes) < 3:
            continue
        roots = _c.Counter()
        for obj in meshes:
            top = obj
            while top.parent is not None:
                top = top.parent
            if top is not obj:
                roots[top.name] += 1
        if not roots:
            continue
        root, count = roots.most_common(1)[0]
        loose = [o.name for o in meshes if o.parent is None and o.name != root and o.name not in roots]
        if loose and count >= 0.6 * len(meshes):
            out.append({"collection": coll.name, "root": root, "loose": loose})
    return out


HELPER_WORDS = ("ref", "hilfe", "helper", "proxy", "guide", "hilfs")


def helper_offsets(min_mm=10.0, max_mm=500.0):
    """Helper meshes (hidden in renders, shown as wire/bounds, or named REF / Hilfe / helper …) that are the
    same shape as a visible part but sit beside it: [{helper, part, offset_mm: (x, y, z), distance_mm}].
    A helper 100 mm off its part means whatever follows the helper (a constraint, a boolean, a measurement)
    is off too."""
    from mathutils import Vector
    scene = bpy.context.scene
    unit = (scene.unit_settings.scale_length or 1.0) * 1000.0

    def box(obj):
        corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
        lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
        hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
        return (lo + hi) / 2, hi - lo

    meshes = [o for o in scene.objects if o.type == 'MESH']
    helpers = [o for o in meshes if o.hide_render or o.display_type in {'WIRE', 'BOUNDS'}
               or any(w in o.name.lower() for w in HELPER_WORDS)]
    parts = [o for o in meshes if o not in helpers]
    out = []
    for helper in helpers:
        centre, size = box(helper)
        if size.length < 1e-6:
            continue
        stem = helper.name.lower().split(".")[0]
        for w in HELPER_WORDS:
            stem = stem.replace(w, "")
        stem = stem.strip(" _-|")
        best = None
        for part in parts:
            same_data = part.data == helper.data
            named = stem and stem in part.name.lower()
            if not (same_data or named):
                continue
            c2, s2 = box(part)
            if any(abs(a - b) > 0.05 * max(a, b, 1e-6) for a, b in zip(size, s2)):
                continue                              # another shape: not its twin
            gap = (c2 - centre) * unit
            if best is None or gap.length < best.length:
                best, twin = gap, part
        if best is not None and min_mm <= best.length <= max_mm:
            out.append({"helper": helper.name, "part": twin.name, "offset_mm": tuple(round(v, 1) for v in best),
                        "distance_mm": round(best.length, 1)})
    return out


def farm_share_status():
    """The render farm's jobs of this file, from what was last looked at (never waits for the share):
    [{job, note, phase, total, on_share (rendered there), fetched (here), open}]."""
    from . import renderfarm
    out = []
    for job in renderfarm._jobs:
        total = renderfarm.total_of(job)
        fetched = job.get("have", 0)
        on_share = max(fetched, job.get("on_share", fetched))
        out.append({"job": job["job"], "note": job.get("note", ""), "phase": job["phase"], "total": total,
                    "on_share": on_share, "fetched": fetched, "open": max(0, total - on_share)})
    return out


def section_render(section=None, quality=None, frames=None, force=False, resolution=None, samples=None, version=None):
    """Render a section's frames (None: every section that needs it) into animation/Frames/<section>/;
    they come into the Video Editor by themselves. quality None = by itself (Low first, High once nothing
    changed), else 'LOW' / 'HIGH'. frames=(a, b): only those frames again; else frames there in that quality
    stay (a longer section renders only its new frames). force=True: every frame again though nothing changed
    (e.g. after frames from a wrong camera): each older frame stays until its new one comes. Sending a section
    again stops its older render. resolution=(w, h) / samples=N: only for this job, the scene stays as it is
    (e.g. one section in 4K with 512 samples; implies force for that section). version="4K": a version of its
    own into Frames/<section> 4K (the section's frames and its strip in the film stay; nothing is stopped).
    With the render farm on (scene.studio.farm_on) the sections render there. Returns what was queued."""
    scene = _cut_scene()                          # the film's scene, also when the Video Editor's is active
    before = {i.uid for i in scene.studio.items}
    result = bpy.ops.studio.add(mode='ANIM', section=section or "", quality=quality or 'DEFAULT',
                                frames="%d-%d" % tuple(frames) if frames else "",
                                force=bool(force or resolution or samples or version), folder=version or "")
    for item in scene.studio.items:
        if item.uid not in before and item.mode == 'ANIM':
            if resolution:
                item.job_width, item.job_height = int(resolution[0]), int(resolution[1])
            if samples:
                item.job_samples = int(samples)
    return [{"section": i.section, "quality": i.quality, "frames": [i.frame_start, i.frame_end]}
            for i in scene.studio.items if i.mode == 'ANIM'] if 'FINISHED' in result else []


def cut_build(transitions=None):
    """The sections as strips in this scene's Video Editor. transitions: {section: (kind, frames, hold)} with
    kind CUT / CROSS / BLACK / WHITE. Returns {"scene", "sections", "missing"}."""
    from . import cut
    scene = bpy.context.scene
    for section, spec in (transitions or {}).items():
        item = cut.settings(scene, section)
        item.transition = spec[0]
        if len(spec) > 1:
            item.frames = int(spec[1])
        if len(spec) > 2:
            item.hold = int(spec[2])
    made, report = cut.build(scene)
    return dict(report, scene=made.name)


def cut_export(edl=False, format=None, quality=None, size=None, range=None, music=None, name=None, sections=None,
               split=None):
    """The film as video (or PNG frames) in the background into animation/Export, with the Video Editor's
    Export settings; given values change them first: sections (names; None = as chosen there, [] = all),
    split 'ONE' (one video, sections with a gap between them are joined) / 'EACH' (one video per section),
    format 'MP4' / 'H265' / 'PRORES' / 'WEBM' / 'PNG', quality 'PERC_LOSSLESS' / 'HIGH' / 'MEDIUM' / 'LOW',
    size 100 / 50 / 25, range 'ALL' / 'PREVIEW' / 'CLIP' (PREVIEW / CLIP instead of the sections),
    music True/False, name (file name). edl=True also writes an EDL. Returns the paths."""
    from . import cut
    scene = _cut_scene()
    rq = scene.studio
    for prop, value in (("cut_export_format", format), ("cut_export_quality", quality),
                        ("cut_export_size", None if size is None else str(int(size))),
                        ("cut_export_range", range), ("cut_export_music", music), ("cut_export_name", name),
                        ("cut_export_split", split)):
        if value is not None:
            setattr(rq, prop, value)
    if sections is not None:
        every = [n for n, _s, _e, _m in cut.sections_of(scene)]
        rq.cut_export_skip = "\n".join(n for n in every if sections and n not in sections)
    video, log = cut.export(scene)
    out = {"video": video, "log": log}
    if edl:
        out["edl"] = cut.edl(scene)
    return out


def farm_report(hours=24):
    """When the render farm worked well and when not, and why Macs dropped out: hour by hour (share
    reachable, Macs online / rendering, frames), the events, each studio Mac's session ends with the reason
    from its log book (Ctrl+C, logout, shutdown, update, no clean end + what macOS says), its rules (log out
    automatically after …, scheduled shutdown) and the patterns learned (kept for next time). Returns text;
    call it first when the farm misbehaves."""
    from . import farm_log, renderfarm
    return farm_log.report(hours, renderfarm.share_root())["text"]


def cut_export_when_ready(sections=None, split=None, format=None, quality=None, size=None, folder=None,
                          name=None, frames='HIGH'):
    """Export by itself once every frame of these sections (names; None = as chosen in Export) is rendered
    and up to date in the quality `frames` ('HIGH' / 'ANY'): the farm or this Mac may still be rendering.
    split / format / quality / size like cut_export (None: the Export settings); folder + name: the result
    is copied there under that name (e.g. a FINAL folder). Survives a restart; one export at a time; tried
    again twice if it fails; done and failed are told in the chat. Use this, never an own timer.
    Returns the wish (its id cancels it: cut_export_cancel)."""
    from . import cut_auto
    return cut_auto.add(_cut_scene(), sections, split, format, quality, size, folder, name, frames)


def cut_export_waiting():
    """The exports waiting for their frames (and the ones of the last day): id, sections, state
    WAITING / EXPORTING / DONE / FAILED, frames there / all, outputs, error."""
    from . import cut_auto
    scene = _cut_scene()
    out = []
    for item in cut_auto.load():
        have, total = cut_auto.progress(scene, item)
        out.append(dict(item, frames_there=have, frames_all=total))
    return out


def cut_export_cancel(item_id):
    """Cancel a waiting export (cut_export_waiting gives the ids)."""
    from . import cut_auto
    cut_auto.cancel(item_id)
    return cut_export_waiting()


def section_proof(section=None):
    """A quick viewport image from the section's camera (None: every section) at its middle frame, into
    animation/Frames/<section>/.proof.png (shown in the Video Editor's Sections tab). Returns the paths:
    look at them to check camera and framing before rendering."""
    from . import film_tools
    made = film_tools.make_proofs(bpy.context.scene, [section] if section else None)
    return {name: film_tools.proof_path(name) for name in made}


def section_draft(section=None):
    """The section (None: every section) as a quick Workbench draft in the background, straight into the
    Video Editor (frames not rendered yet show their draft). Returns the sections queued."""
    from . import film_tools
    return film_tools.queue_drafts(bpy.context.scene, [section] if section else None)


def film_check():
    """What would go wrong in a render, and the estimate: {"problems": [[section, code, text]] (code
    NO_CAMERA blocks that section's render; CAMERA: its frames are from another camera than the section's,
    render it again with force=True; SHARED; OUTDATED; EXPOSURE: a fade keyed into the render; SAMPLES), "sections": [{"section", "camera", "locked",
    "state", "next", "seconds"}], "total_seconds"}."""
    from . import cut, film_tools
    scene = bpy.context.scene
    items = {c.section: c for c in scene.studio.cut_items}
    sections = []
    for name, start, end, marker in cut.sections_of(scene):
        st = cut.frame_status(name, start, end, fresh=True)
        quality = film_tools.next_quality(scene, name, st)
        secs = film_tools.estimate(scene, name, quality, st) if quality else 0
        cam = marker.camera if marker is not None else scene.camera
        sections.append({"section": name, "camera": cam.name if cam else None,
                         "locked": bool(getattr(items.get(name), "camera_lock", False)),
                         "state": film_tools.label_of(st) or "nothing", "next": quality,
                         "seconds": round(secs) if secs else None})
    total = film_tools.total_estimate(scene)
    return {"problems": [list(p) for p in film_tools.problems(scene)], "sections": sections,
            "total_seconds": round(total) if total else None}


def section_camera(section, own=False, lock=None):
    """own=True: the section gets its own camera (where its camera is at its start). lock True/False: the
    section keeps its camera (set back when the marker's camera changes). Returns the camera's name."""
    from . import cut, film_tools, motion
    scene = bpy.context.scene
    found = motion.find(scene, section)
    if found is None:
        raise ValueError("No section %s" % section)
    if own:
        film_tools.own_camera(scene, section)
    if lock is not None and bool(lock) != cut.settings(scene, section).camera_lock:
        film_tools.toggle_lock(scene, section)
    cam = found[0].camera
    return cam.name if cam else None


def _cut_scene():
    from . import cut
    return cut.film_scene(getattr(bpy.context, "sequencer_scene", None) or bpy.context.scene)


def _clip(scene, target):
    """A strip by its name or its section's name (the section's first strip)."""
    from . import cut
    sed = cut._sed_of(scene)
    if sed is None:
        raise ValueError("Nothing in the Video Editor yet")
    strip = sed.strips_all.get(target) if hasattr(sed, "strips_all") else sed.sequences_all.get(target)
    strip = strip or cut.section_strip(scene, target)
    if strip is None:
        raise ValueError("No strip or section %r" % target)
    return strip


def cut_state():
    """What the Video Editor shows: {"scene", "playhead", "active", "active_3d_frame", "strips": [{"name",
    "type", "channel", "frames", "kind", "section", "look"}]} (strips in time order)."""
    from . import film_edit
    return film_edit.state(_cut_scene())


def cut_look(target, brightness=None, contrast=None, saturation=None, warmth=None, exposure=None, shadows=None,
             highlights=None, tint=None):
    """A clip's look; target = strip or section name, "all" (every clip) or "film" (an adjustment layer over
    everything). Light: exposure -4..4 stops, shadows / highlights -1..1, brightness / contrast -100..100.
    Colour: saturation 0..2 (1 = as rendered), warmth -1..1 (cooler/warmer), tint -1..1 (green/magenta)."""
    from . import film_edit
    scene = _cut_scene()
    if target == "film":
        strips = [film_edit.film_look(scene)]
    elif target == "all":
        strips = film_edit.clips(scene)
    else:
        strips = [_clip(scene, target)]
    for strip in strips:
        film_edit.set_look(strip, brightness, contrast, saturation, warmth, exposure, shadows, highlights, tint)
    return {s.name: film_edit.look_of(s) for s in strips}


def cut_fade(side, frames=12, color="BLACK", target=None):
    """A fade in ("IN") from or out ("OUT") to black or white; target strip / section, None = the whole film."""
    from . import film_edit
    scene = _cut_scene()
    made = film_edit.fade(scene, side.upper(), int(frames), color.upper(), _clip(scene, target) if target else None)
    return made.name if made is not None else None


def cut_freeze(frame, frames=24):
    """The image at this Video Editor frame stands still for `frames`; everything after it moves along."""
    from . import film_edit
    return film_edit.freeze(_cut_scene(), int(frame), int(frames)).name


def cut_transition(section, kind, frames=12):
    """CUT / CROSS / BLACK / WHITE from `section` into the next one, in place."""
    from . import cut
    item = cut.settings(_cut_scene(), section)
    item.transition, item.frames = kind.upper(), int(frames)
    from . import film_edit
    film_edit.apply_section(_cut_scene(), section)
    return {"section": section, "transition": item.transition, "frames": item.frames}


def cut_hold(section, frames):
    """The section's last image stands still for `frames` after it (0 = none), in place."""
    from . import cut, film_edit
    item = cut.settings(_cut_scene(), section)
    item.hold = int(frames)
    film_edit.apply_section(_cut_scene(), section)
    return {"section": section, "hold": item.hold}


def cut_letterbox(ratio=2.39):
    """Black bars for this aspect ratio (0 = none)."""
    from . import film_edit
    return [s.name for s in film_edit.letterbox(_cut_scene(), ratio)]


def cut_title(text, frame=None, length=48, place="CENTER"):
    """A title at a Video Editor frame (default: the playhead), centred or "LOWER" third."""
    from . import film_edit
    return film_edit.title(_cut_scene(), text, frame, int(length), place.upper()).name


def render_status():
    """What renders and what is ready, for checking renders without own processes:
    {"busy", "message", "queue": [labels], "current": {"label", "path", "progress"} or None,
     "films": [{"label", "state", "part", "parts", "frame", "done", "total", "seconds_left", "text", "folder"}],
     "finished": [{"label", "video", "sections": [[name, video]], "folder"}],
     "sections": [{"section", "frames", "done", "total", "quality", "folder"}]} (films of this file)."""
    from . import film, runner
    scene = bpy.context.scene
    run = runner.RUN
    current = None
    if run.current:
        current = {"label": run.current.get("camera"), "path": run.current.get("path"),
                   "progress": round(runner.progress_fraction(), 3) if hasattr(runner, "progress_fraction") else None}
    films = []
    for entry, st, kind in film.films_here():
        if kind == 'DONE':
            continue
        left = film.time_left(st)
        films.append({"label": entry.get("label", ""), "state": kind, "part": st.get("part"), "parts": st.get("parts"),
                      "frame": st.get("frame"), "done": st.get("done", 0), "total": st.get("total", 0),
                      "seconds_left": round(left) if left else None, "text": film.text(entry, st, kind),
                      "folder": entry.get("folder", "")})
    finished = [{"label": f["label"], "video": f["video"], "sections": [list(x) for x in f["sections"]],
                 "folder": f["folder"]} for f in film.FINISHED if film.mine(f)]
    from . import cut
    sections = []
    for name, start, end, _marker in cut.sections_of(scene):
        st = cut.frame_status(name, start, end, fresh=True)
        sections.append({"section": name, "frames": [start, end], "done": st["done"], "total": st["total"],
                         "quality": st["quality"], "folder": st["folder"]})
    return {"busy": run.busy, "message": run.message, "current": current,
            "queue": [runner.item_label(scene, i) for i in scene.studio.items if i.camera],
            "films": films, "finished": finished, "sections": sections}


# ---------------------------------------------------------------- explosion view

def explode_group(name, objects):
    """A group of parts with a handle "Griff | <name>"; the parts follow it at scene["Explosion"] > 0
    (0 = exactly as in CAD). objects: names or objects. Returns the handle's name."""
    from . import explode
    objs = [bpy.data.objects[o] if isinstance(o, str) else o for o in objects]
    return explode.make_group(bpy.context.scene, name, objs).name


def explode_arrange(names=None, flat=True, upright=False, in_row=True, camera=None):
    """Arrange groups for the camera: each flat to it (upright: longest side up), then side by side."""
    from . import cameras, explode
    scene = bpy.context.scene
    cam = bpy.data.objects[camera] if isinstance(camera, str) else camera or cameras.active_camera(scene)
    names = names or list(explode.groups(scene))
    if flat or upright:
        for name in names:
            explode.face_camera(scene, name, cam, upright=upright)
    return explode.row(scene, names, cam) if in_row else len(names)


def explode_key_section(section=None, open_frames=24):
    """scene["Explosion"] keys in a section: closed at its start, open after open_frames, closed after it."""
    from . import explode, motion
    scene = bpy.context.scene
    if section:
        found = motion.find(scene, section)
        if found is None:
            raise ValueError("No section %s" % section)
        _m, start, end = found
    else:
        start, end, _n = motion.current_range(scene)
    explode.key_section(scene, start, end, open_frames)


def realign_hooks(objects=None):
    """Hooks point at their targets again (after a part or its target got a new origin)."""
    from . import explode
    objs = [bpy.data.objects[o] if isinstance(o, str) else o for o in (objects or bpy.context.scene.objects)]
    return explode.realign_hooks(objs)

# SPDX-License-Identifier: GPL-3.0-or-later
"""CAD hand-over files, without bpy (testable outside Blender).

- The sidecar `<model>.cad.json` the CAD side writes next to its export: one entry per part
  with a stable id (the part name without material), the material, mass, bounding box, plus
  mounting points, parameters and variants. Format (all lengths in meters, coordinates of the
  CAD program with Z up, which is Blender's world after the import):

      {"format": "ai-studio-cad/1", "version": "V3", "program": "onshape",
       "source": "https://cad.onshape.com/documents/…", "element": "Leuchte V3",
       "up": "Z",            # axis that is up in the exported glTF's own coordinates
                             # (Onshape with yAxisIsUp=false: "Z"; a normal glTF: "Y")
       "floor": false,       # true: the model should stand on Z = 0 (checked)
       "parts": [{"id": "Leuchte | Front", "material": "PETG milchig", "mass_kg": 0.012,
                  "bbox": [[x0, y0, z0], [x1, y1, z1]], "part_id": "JHD"}],
       "mounts": [{"name": "MOUNT | Lenker", "origin": [x, y, z], "x": [1, 0, 0], "z": [0, 0, 1]}],
       "parameters": {"Gehaeuse Breite": "42 mm"},
       "variants": {"Schwarz": {"PETG milchig": {"color": "#202020"}}}}

- Onshape's glTF export is glTF text with the buffers inside (data URIs), sometimes saved as
  .glb: `importable_copy` gives Blender a .gltf it can read.
- STEP → GLB through a Python that has OCP (build123d / CadQuery), names and colors kept.
- The feedback list `CAD-Feedback.md` next to the export: what Blender found for the CAD side.
"""

import datetime
import json
import os
import re
import shutil
import subprocess
import tempfile

FORMAT = "ai-studio-cad/1"
SIDECAR_SUFFIX = ".cad.json"
FEEDBACK_FILE = "CAD-Feedback.md"
MATERIAL_MAP_FILE = "cad_materials.json"
STEP_EXTS = (".step", ".stp")

_ENDINGS = re.compile(r"(\.\d{3}|\s*<\d+>|\s*\(\d+\))$")


def part_name(name):
    """The name without the endings importers and Blender add, lower case:
    "Gabel.001", "Gabel <1>", "Gabel (2)" -> "gabel"."""
    name = str(name).strip()
    while True:
        stripped = _ENDINGS.sub("", name)
        if stripped == name or not stripped:
            return " ".join(name.lower().split())
        name = stripped


# ---------------------------------------------------------------- sidecar

def sidecar_path(model_path):
    """The sidecar of an export: `Leuchte V3.cad.json` for `Leuchte V3.glb` (or
    `Leuchte V3.glb.cad.json`). None if there is none."""
    stem = os.path.splitext(model_path)[0]
    for candidate in (stem + SIDECAR_SUFFIX, model_path + SIDECAR_SUFFIX):
        if os.path.isfile(candidate):
            return candidate
    return None


def _vec(value, name):
    if not (isinstance(value, (list, tuple)) and len(value) == 3):
        raise ValueError("%s must be [x, y, z]" % name)
    return [float(v) for v in value]


def load_sidecar(path):
    """The sidecar as a checked dict; parts keyed by part_name(id) under "by_key"."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("%s: expected a JSON object" % os.path.basename(path))
    fmt = data.get("format", FORMAT)
    if not str(fmt).startswith("ai-studio-cad/"):
        raise ValueError("%s: unknown format %r" % (os.path.basename(path), fmt))
    up = str(data.get("up", "Y")).upper()
    if up not in ("Y", "Z"):
        raise ValueError("up must be 'Y' or 'Z'")
    data["up"] = up
    parts = []
    for i, part in enumerate(data.get("parts") or []):
        if not isinstance(part, dict) or not str(part.get("id", "")).strip():
            raise ValueError("part %d has no id" % (i + 1))
        part = dict(part)
        part["id"] = str(part["id"]).strip()
        if part.get("bbox") is not None:
            box = part["bbox"]
            if not (isinstance(box, (list, tuple)) and len(box) == 2):
                raise ValueError("%s: bbox must be [[min], [max]]" % part["id"])
            part["bbox"] = [_vec(box[0], "bbox min"), _vec(box[1], "bbox max")]
        parts.append(part)
    data["parts"] = parts
    data["by_key"] = {part_name(p["id"]): p for p in parts}
    mounts = []
    for m in data.get("mounts") or []:
        if not isinstance(m, dict) or not str(m.get("name", "")).strip():
            raise ValueError("every mount needs a name")
        mounts.append({"name": str(m["name"]).strip(), "origin": _vec(m.get("origin", [0, 0, 0]), "origin"),
                       "x": _vec(m.get("x", [1, 0, 0]), "x"), "z": _vec(m.get("z", [0, 0, 1]), "z")})
    data["mounts"] = mounts
    data["variants"] = data.get("variants") or {}
    data["path"] = path
    return data


def part_for(sidecar, *names):
    """The sidecar part for an imported object: tried by its name, then e.g. its mesh's name."""
    if not sidecar:
        return None
    for name in names:
        if name:
            part = sidecar["by_key"].get(part_name(name))
            if part is not None:
                return part
    return None


def version_from_name(path):
    """'V3' from 'Leuchte V3.glb' / 'leuchte_v3.glb', else ''."""
    match = re.search(r"(?:^|[\s_\-])[vV](\d+)(?=$|[\s_\-.])", os.path.splitext(os.path.basename(path))[0])
    return "V" + match.group(1) if match else ""


def bbox_deviation(expected, actual, tolerance_mm=0.5):
    """How far an imported bounding box lies from the sidecar's, in mm: None when within the
    tolerance, else {"min": [dx, dy, dz], "max": [...], "max_mm": worst}."""
    dev_min = [round((a - e) * 1000, 2) for a, e in zip(actual[0], expected[0])]
    dev_max = [round((a - e) * 1000, 2) for a, e in zip(actual[1], expected[1])]
    worst = max(abs(v) for v in dev_min + dev_max)
    return None if worst <= tolerance_mm else {"min": dev_min, "max": dev_max, "max_mm": worst}


# ---------------------------------------------------------------- files for the importer

def is_gltf_text(path):
    """True for a glTF JSON file (whatever its extension)."""
    try:
        with open(path, "rb") as f:
            head = f.read(64).lstrip()
    except OSError:
        return False
    return head.startswith(b"{")


def importable_copy(path, folder=None):
    """A path Blender's glTF importer reads: a .glb that is really glTF text (Onshape) is copied
    as .gltf into a temporary folder; everything else is returned as it is."""
    if os.path.splitext(path)[1].lower() == ".glb" and is_gltf_text(path):
        folder = folder or tempfile.mkdtemp(prefix="ai_studio_cad_")
        target = os.path.join(folder, os.path.splitext(os.path.basename(path))[0] + ".gltf")
        shutil.copyfile(path, target)
        return target
    return path


# ---------------------------------------------------------------- STEP through OCP

STEP_SCRIPT = r'''
import math, sys
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.IFSelect import IFSelect_RetDone
from OCP.Message import Message_ProgressRange
from OCP.RWGltf import RWGltf_CafWriter
from OCP.RWMesh import RWMesh_CoordinateSystem
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_AsciiString, TCollection_ExtendedString
from OCP.TColStd import TColStd_IndexedDataMapOfStringString
from OCP.TDF import TDF_LabelSequence
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import XCAFDoc_DocumentTool

src, dst, deflection_mm, angle_deg = sys.argv[1], sys.argv[2], float(sys.argv[3]), float(sys.argv[4])
app = XCAFApp_Application.GetApplication_s()
doc = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
app.InitDocument(doc)
reader = STEPCAFControl_Reader()
reader.SetNameMode(True)
reader.SetColorMode(True)
reader.SetLayerMode(True)
if reader.ReadFile(src) != IFSelect_RetDone:
    sys.exit("cannot read the STEP file")
reader.Transfer(doc)
unit_m = 0.001   # the STEP reader works in millimeters (OCCT's default unit)
tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
labels = TDF_LabelSequence()
tool.GetFreeShapes(labels)
for i in range(1, labels.Length() + 1):
    shape = tool.GetShape_s(labels.Value(i))
    BRepMesh_IncrementalMesh(shape, deflection_mm * 0.001 / unit_m, False, math.radians(angle_deg), True)
writer = RWGltf_CafWriter(TCollection_AsciiString(dst), True)
converter = writer.ChangeCoordinateSystemConverter()
converter.SetInputLengthUnit(unit_m)
converter.SetInputCoordinateSystem(RWMesh_CoordinateSystem.RWMesh_CoordinateSystem_Zup)
if not writer.Perform(doc, TColStd_IndexedDataMapOfStringString(), Message_ProgressRange()):
    sys.exit("cannot write the GLB")
print("STEP_OK", unit_m)
'''

_ocp_python = []   # cache: [path or ""]


def _candidates():
    env = os.environ.get("AI_STUDIO_OCP_PYTHON")
    found = [env] if env else []
    import glob
    for pattern in ("~/miniforge3/envs/*/bin/python", "~/miniconda3/envs/*/bin/python",
                    "~/anaconda3/envs/*/bin/python", "~/.conda/envs/*/bin/python", "~/mambaforge/envs/*/bin/python",
                    "~/.venvs/*/bin/python", "~/venvs/*/bin/python", "~/.virtualenvs/*/bin/python"):
        found += sorted(glob.glob(os.path.expanduser(pattern)))
    for path in ("~/miniforge3/bin/python", "~/miniconda3/bin/python", "~/anaconda3/bin/python",
                 "/opt/homebrew/bin/python3", "/usr/local/bin/python3"):
        found.append(os.path.expanduser(path))
    which = shutil.which("python3")
    if which:
        found.append(which)
    seen, out = set(), []
    for path in found:
        # /usr/bin/python3 on macOS can open the "install developer tools" dialog: never try it.
        if path and path not in seen and os.path.isfile(path) and not path.startswith("/usr/bin/"):
            seen.add(path)
            out.append(path)
    return out


def find_ocp_python(refresh=False):
    """A Python with OCP (build123d / CadQuery), or None. AI_STUDIO_OCP_PYTHON picks one."""
    if _ocp_python and not refresh:
        return _ocp_python[0] or None
    _ocp_python[:] = [""]
    for path in _candidates():
        try:
            result = subprocess.run([path, "-c", "import OCP.RWGltf"], capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            _ocp_python[0] = path
            break
    return _ocp_python[0] or None


def step_to_glb(step_path, glb_path=None, deflection_mm=0.05, angle_deg=12.0, timeout=600):
    """Mesh a STEP file into a GLB (part names and colors kept, meters, glTF Y up).
    Returns the GLB path. Raises RuntimeError with a sentence when it is not possible."""
    python = find_ocp_python()
    if python is None:
        raise RuntimeError("STEP needs a Python with OCP (e.g. `pip install build123d`); none found. "
                           "Set AI_STUDIO_OCP_PYTHON or export GLB from the CAD program.")
    glb_path = glb_path or os.path.join(tempfile.mkdtemp(prefix="ai_studio_step_"),
                                        os.path.splitext(os.path.basename(step_path))[0] + ".glb")
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(STEP_SCRIPT)
        script = f.name
    try:
        result = subprocess.run([python, script, step_path, glb_path, str(deflection_mm), str(angle_deg)],
                                capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError("STEP conversion took longer than %d s" % timeout)
    finally:
        os.remove(script)
    if "STEP_OK" not in result.stdout or not os.path.isfile(glb_path):
        raise RuntimeError("STEP conversion failed: " + (result.stderr or result.stdout)[-400:])
    return glb_path


# ---------------------------------------------------------------- materials

def load_material_map(folder):
    """{CAD material: base material} from cad_materials.json in `folder` ({} if none)."""
    try:
        with open(os.path.join(folder, MATERIAL_MAP_FILE), encoding="utf-8") as f:
            data = json.load(f)
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_material_map(folder, mapping):
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, MATERIAL_MAP_FILE), "w", encoding="utf-8") as f:
        json.dump(dict(sorted(mapping.items())), f, indent=1, ensure_ascii=False)


def base_material_for(cad_material, base_names, mapping=None):
    """The base material a CAD material becomes: the mapping, the same name (any case), or the
    one base name contained in it / containing it ("Aluminium eloxiert" -> "Aluminium").
    None if there is no clear match."""
    if not cad_material:
        return None
    mapping = mapping or {}
    if cad_material in mapping:
        return mapping[cad_material]
    low = " ".join(str(cad_material).lower().split())
    for key, value in mapping.items():
        if key.lower() == low:
            return value
    names = list(base_names)
    exact = [n for n in names if n.lower() == low]
    if exact:
        return exact[0]
    words = set(re.split(r"[\s|/,_\-]+", low))
    inside = [n for n in names if set(re.split(r"[\s|/,_\-]+", n.lower())) <= words]
    if len(inside) == 1:
        return inside[0]
    if len(inside) > 1:   # the most specific: the one with the most words
        inside.sort(key=lambda n: -len(n.split()))
        if len(inside[0].split()) > len(inside[1].split()):
            return inside[0]
    return None


def variants_for(sidecar_variants, material_names):
    """Varianten.json pieces from the sidecar's variants ({variant: {CAD material: values}}):
    (targets {alias: "Material/Principled BSDF"}, variants {name: {alias: values}}) for the
    materials that exist (material_names: {CAD material: Blender material name})."""
    targets, variants = {}, {}
    for variant, by_material in (sidecar_variants or {}).items():
        values = {}
        for cad_material, settings in (by_material or {}).items():
            blender = material_names.get(cad_material)
            if not blender or not isinstance(settings, dict):
                continue
            alias = re.sub(r"[^A-Z0-9]+", "_", cad_material.upper()).strip("_") or "MAT"
            targets[alias] = blender + "/Principled BSDF"
            values[alias] = settings
        if values:
            variants[str(variant)] = values
    return targets, variants


# ---------------------------------------------------------------- feedback for the CAD side

_FEEDBACK_HEADER = ("# CAD-Feedback\n\n"
                    "Found in Blender (AI Studio) for the CAD model next to this file. The CAD side reads the "
                    "open points before it changes the model and ticks them off when they are done.\n\n")


def add_feedback(folder, part, problem, source="Blender", today=None):
    """Append an open point to CAD-Feedback.md in `folder`. Returns the file's path."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, FEEDBACK_FILE)
    day = today or datetime.date.today().isoformat()
    line = "- [ ] %s · %s · %s (%s)\n" % (day, " ".join(str(part).split()) or "allgemein",
                                         " ".join(str(problem).split()), source)
    new = not os.path.exists(path)
    with open(path, "a", encoding="utf-8") as f:
        if new:
            f.write(_FEEDBACK_HEADER)
        f.write(line)
    return path


ONSHAPE_FILE = ".cad_feedback_onshape.json"   # point → its comment in Onshape


def feedback_comments(folder):
    """{point text: {"cid", "base"}} of the points that went to Onshape as comments."""
    try:
        with open(os.path.join(folder, ONSHAPE_FILE), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember_comment(folder, point, cid, base):
    data = feedback_comments(folder)
    data[point] = {"cid": cid, "base": base}
    with open(os.path.join(folder, ONSHAPE_FILE), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)


def tick_feedback(folder, point, note="resolved in Onshape"):
    """Mark an open point done ("- [x] … · resolved in Onshape"). True when it was found."""
    path = os.path.join(folder, FEEDBACK_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return False
    for i, line in enumerate(lines):
        if line.startswith("- [ ] ") and line[6:].rstrip() == point:
            lines[i] = "- [x] %s · %s\n" % (point, note)
            with open(path, "w", encoding="utf-8") as f:
                f.writelines(lines)
            return True
    return False


def open_feedback(folder):
    """The open points (lines '- [ ] …') of CAD-Feedback.md in `folder`."""
    try:
        with open(os.path.join(folder, FEEDBACK_FILE), encoding="utf-8") as f:
            return [line[6:].rstrip() for line in f if line.startswith("- [ ] ")]
    except OSError:
        return []

"""Checks of the CAD hand-over without Blender (plain Python 3):

    python3 tests/cad_unit.py

Covers cadfiles (sidecar, names, glTF text, materials, variants, feedback) and the /cad upload
of mcp_server (targets, CORS, a real upload). Prints CAD_UNIT_OK / CAD_UNIT_FAIL.
"""

import importlib.util
import json
import os
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
results = []


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(name, ok, detail=""):
    results.append(bool(ok))
    print("%s %s %s" % ("PASS" if ok else "FAIL", name, "" if ok else detail))


cf = load("cadfiles", "studio/cadfiles.py")
ms = load("mcp_server", "mcp_server.py")
TMP = tempfile.mkdtemp(prefix="cad_unit_")

# names
check("part_name strips importer endings", cf.part_name("Gabel.001") == cf.part_name("Gabel <1>")
      == cf.part_name(" gabel  (2) ") == "gabel")
check("part_name keeps the id", cf.part_name("Leuchte | Front") == "leuchte | front")
check("version from file name", cf.version_from_name("/x/Leuchte V3.glb") == "V3"
      and cf.version_from_name("leuchte_v12.glb") == "V12" and cf.version_from_name("Velo.glb") == "")

# sidecar
model = os.path.join(TMP, "Leuchte V3.glb")
open(model, "wb").write(b'  {"asset": {"version": "2.0"}}')
sidecar = {"format": "ai-studio-cad/1", "version": "V3", "up": "z",
           "parts": [{"id": "Leuchte | Front", "material": "PETG milchig", "bbox": [[0, 0, 0], [0.04, 0.01, 0.03]]},
                     {"id": "Leuchte | Gehäuse", "material": "Aluminium eloxiert"}],
           "mounts": [{"name": "MOUNT | Lenker", "origin": [0, 0, 0.1], "x": [1, 0, 0], "z": [0, 0, -1]}],
           "variants": {"Schwarz": {"Aluminium eloxiert": {"color": "#202020"}}}}
json.dump(sidecar, open(os.path.join(TMP, "Leuchte V3.cad.json"), "w"))
path = cf.sidecar_path(model)
data = cf.load_sidecar(path)
check("sidecar found next to the export", path and path.endswith("Leuchte V3.cad.json"), path)
check("sidecar: up normalized, parts by key", data["up"] == "Z" and "leuchte | front" in data["by_key"])
check("part_for by object or mesh name", cf.part_for(data, "Mesh 3", "Leuchte | Front.001")["id"] == "Leuchte | Front")
bad = os.path.join(TMP, "bad.cad.json")
json.dump({"parts": [{"material": "x"}]}, open(bad, "w"))
try:
    cf.load_sidecar(bad)
    check("part without id refused", False)
except ValueError:
    check("part without id refused", True)
check("bbox within tolerance", cf.bbox_deviation([[0, 0, 0], [1, 1, 1]], [[0.0002, 0, 0], [1, 1, 1]]) is None)
dev = cf.bbox_deviation([[0, 0, 0], [1, 1, 1]], [[0, 0, 0], [1.002, 1, 1]])
check("bbox off by 2 mm", dev and abs(dev["max_mm"] - 2.0) < 1e-6, dev)

# Onshape's glTF text saved as .glb
check("glTF text detected", cf.is_gltf_text(model))
os.makedirs(os.path.join(TMP, "imp"))
copy = cf.importable_copy(model, os.path.join(TMP, "imp"))
check("copied as .gltf for the importer", copy.endswith("Leuchte V3.gltf") and open(copy, "rb").read() == open(model, "rb").read())
real = os.path.join(TMP, "real.glb")
open(real, "wb").write(b"glTF\x02\x00\x00\x00")
check("a real GLB stays", cf.importable_copy(real) == real)

# materials
base = ["Aluminium", "Aluminium brushed", "PETG translucent", "Rubber black", "Glass"]
check("material: exact name", cf.base_material_for("glass", base) == "Glass")
check("material: base contained in CAD name", cf.base_material_for("Aluminium eloxiert", base) == "Aluminium")
check("material: most specific", cf.base_material_for("Aluminium brushed natur", base) == "Aluminium brushed")
check("material: mapping wins", cf.base_material_for("PETG milchig", base, {"PETG milchig": "PETG translucent"})
      == "PETG translucent")
check("material: unknown stays None", cf.base_material_for("Eiche geölt", base) is None)
cf.save_material_map(TMP, {"PETG milchig": "PETG translucent"})
check("material map saved and loaded", cf.load_material_map(TMP) == {"PETG milchig": "PETG translucent"})

# variants
targets, variants = cf.variants_for(data["variants"], {"Aluminium eloxiert": "Gehäuse | Alu"})
check("variants for Varianten.json", targets == {"ALUMINIUM_ELOXIERT": "Gehäuse | Alu/Principled BSDF"}
      and variants == {"Schwarz": {"ALUMINIUM_ELOXIERT": {"color": "#202020"}}}, (targets, variants))

# feedback
fb = cf.add_feedback(TMP, "Leuchte | Front", "Kollision 1.2 mm mit dem Lenker", today="2026-11-10")
cf.add_feedback(TMP, "", "Radius an der Kante zu klein für den Druck", today="2026-11-10")
text = open(fb, encoding="utf-8").read()
check("feedback file with header", text.startswith("# CAD-Feedback") and text.count("- [ ] ") == 2, text)
with open(fb, "a", encoding="utf-8") as f:
    f.write("- [x] 2026-11-01 · alt · erledigt (Blender)\n")
check("open points only", cf.open_feedback(TMP) == ["2026-11-10 · Leuchte | Front · Kollision 1.2 mm mit dem Lenker (Blender)",
                                                   "2026-11-10 · allgemein · Radius an der Kante zu klein für den Druck (Blender)"],
      cf.open_feedback(TMP))
if cf.find_ocp_python() is None:
    try:
        cf.step_to_glb(os.path.join(TMP, "x.step"))
        check("STEP without OCP: clear message", False)
    except RuntimeError as exc:
        check("STEP without OCP: clear message", "OCP" in str(exc), str(exc))

# /cad upload
home = os.path.expanduser("~")
ms.live_state["file"] = os.path.join(home, "Projekt", "Velo.blend")
check("upload: default folder CAD next to the .blend",
      ms.cad_upload_target("Leuchte V3.glb") == os.path.join(home, "Projekt", "CAD", "Leuchte V3.glb"))
for name, folder in (("../x.glb", ""), ("a/b.glb", ""), (".hidden.glb", ""), ("x.exe", ""), ("x.glb", "/etc"),
                     ("claude_desktop_config.json", "~/Library/Application Support/Claude"),
                     ("x.cad.json", "~/Library/Application Support/Claude"), ("clients.json", ""),
                     ("x.glb", "~/.config/ai-studio"), ("x.glb", "~/Projekt/.git")):
    try:
        ms.cad_upload_target(name, folder)
        check("upload refused: %s %s" % (name, folder), False)
    except ValueError:
        check("upload refused: %s %s" % (name, folder), True)
check("upload: sidecar and folders in the home folder still work",
      ms.cad_upload_target("Leuchte V3.cad.json", "~/Projekt/CAD").endswith("Projekt/CAD/Leuchte V3.cad.json"))
check("origins", ms.cad_origin_allowed("https://cad.onshape.com") and ms.cad_origin_allowed("https://acme.onshape.com")
      and not ms.cad_origin_allowed("https://evil.com") and not ms.cad_origin_allowed("http://cad.onshape.com")
      and not ms.cad_origin_allowed("https://onshape.com.evil.com"))

ms.start(0)
ms.set_live_token("tok")
real_home = os.environ.get("HOME", "")
os.environ["HOME"] = os.path.realpath(TMP)     # the upload test writes nothing into the real home
target_dir = os.path.join(os.path.realpath(TMP), "Projekt", "CAD")
url = "http://127.0.0.1:%d/cad?name=%s&folder=%s" % (ms.port(), "Leuchte%20V3.glb", urllib.parse.quote(target_dir))
body = b"glTF" + os.urandom(3000)
request = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": "Bearer tok", "Origin": "https://cad.onshape.com"})
with urllib.request.urlopen(request, timeout=10) as response:
    answer = json.loads(response.read())
    cors = response.headers.get("Access-Control-Allow-Origin")
saved = os.path.join(target_dir, "Leuchte V3.glb")
check("upload written", answer.get("ok") and open(saved, "rb").read() == body, answer)
check("upload answers with CORS for Onshape", cors == "https://cad.onshape.com", cors)
pre = urllib.request.Request(url, method="OPTIONS", headers={"Origin": "https://cad.onshape.com",
                                                              "Access-Control-Request-Private-Network": "true"})
with urllib.request.urlopen(pre, timeout=10) as response:
    check("preflight allows private network", response.status == 204
          and response.headers.get("Access-Control-Allow-Private-Network") == "true")
try:
    urllib.request.urlopen(urllib.request.Request(url, data=body, method="POST",
                                                  headers={"Authorization": "Bearer wrong"}), timeout=10)
    check("wrong token refused", False)
except urllib.error.HTTPError as exc:
    check("wrong token refused", exc.code == 401)
ms.stop()
os.environ["HOME"] = real_home
os.remove(saved)
os.rmdir(target_dir)

ok = all(results)
print("CAD_UNIT_OK" if ok else "CAD_UNIT_FAIL", "%d/%d" % (sum(results), len(results)))
sys.exit(0 if ok else 1)

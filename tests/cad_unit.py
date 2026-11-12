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

# ticking off a feedback point
folder = os.path.join(TMP, "fb_tick")
cf.add_feedback(folder, "Front", "zu dick", today="2026-11-11")
point = cf.open_feedback(folder)[0]
check("tick off a point", cf.tick_feedback(folder, point) and cf.open_feedback(folder) == []
      and "- [x] " + point + " · done" in open(os.path.join(folder, cf.FEEDBACK_FILE)).read())

# the chat an export came from
side = os.path.join(TMP, "Chat V1.cad.json")
json.dump({"format": "ai-studio-cad/1", "chat": {"name": "Leuchte V22", "url": "https://evil.example/x",
                                                 "session": "cad-leuchte"}}, open(side, "w"))
chat = cf.load_sidecar(side)["chat"]
check("sidecar chat: name kept, foreign link and session dropped", chat == {"name": "Leuchte V22", "url": ""}, chat)

# CAD jobs (files) and the links into the Claude app
jobs = load("cadjobs", "cadjobs.py")
jobs_dir = os.path.join(TMP, "jobs")
uuid_ = "0b9e1c2a-1111-4222-8333-444455556666"
job = jobs.new_job("  Wand  2.4 mm  ", part="Deckel", chat={"name": "Leuchte", "url": "https://claude.ai/chat/" + uuid_},
                   jobs_dir=jobs_dir)
check("job written, text tidy", job["text"] == "Wand 2.4 mm" and os.path.isfile(job["path"])
      and jobs.jobs("open", jobs_dir)[0]["id"] == job["id"])
check("origin chat opens by its link", jobs.chat_link(job)[0] == "claude://claude.ai/chat/" + uuid_)
check("no link, only a name: Recents", jobs.chat_link(dict(job, chat={"name": "Leuchte"}))[0]
      == jobs.RECENTS)
link = jobs.chat_link(job, "new")[0]
check("new chat with the text", link.startswith("claude://claude.ai/new?q=CAD%20job") and "Wand%202.4%20mm" in link)
text = jobs.message(dict(job, path=job["path"]))
check("message names its chat and works without blender_jobs", "for the chat 'Leuchte'" in text
      and "no blender_jobs tool" in text and "blender_python" in text and "cadjobs.claim(%r" % job["id"] in text
      and "linked to the Mac" in text, text)
check("export goes straight into Blender", "OS.exportPackage" in jobs.message(dict(job, cad_folder="/x/CAD")))
here = os.path.dirname(os.path.abspath(jobs.__file__))
ns = {}
exec("import sys; sys.path.insert(0, %r); import cadjobs as fallback" % here, ns)
check("the fallback snippet imports cadjobs on its own", hasattr(ns["fallback"], "claim"))
temp_image = os.path.join(TMP, "tool-result.png")
open(temp_image, "wb").write(b"\x89PNG fake")
with_image = jobs.new_job("Radius 2 mm", images=[temp_image, os.path.join(TMP, "missing.png")], jobs_dir=jobs_dir)
os.remove(temp_image)   # the temporary original is gone, the job keeps its copy
check("images copied next to the job", len(with_image["images"]) == 1
      and with_image["images"][0].startswith(os.path.join(jobs_dir, with_image["id"]))
      and os.path.isfile(with_image["images"][0]), with_image["images"])
check("no chat remembered yet", jobs.remembered_chat("/x/VELO/files/CAD", jobs_dir) is None)
jobs.remember_chat("/x/VELO/files/CAD/", {"name": "CAD 5"}, jobs_dir)
check("chat remembered per CAD folder", jobs.remembered_chat("/x/VELO/files/CAD", jobs_dir)
      == {"name": "CAD 5", "url": ""}, jobs.remembered_chat("/x/VELO/files/CAD", jobs_dir))
jobs.done(with_image["id"], jobs_dir=jobs_dir)
jobs.claim(job["id"], "Leuchte", jobs_dir=jobs_dir)
check("claimed", jobs.load(job["id"], jobs_dir)["status"] == "claimed")
jobs.done(job["id"], "Wand ist 2.4", jobs_dir=jobs_dir)
check("done with a note", jobs.load(job["id"], jobs_dir)["note"] == "Wand ist 2.4" and not jobs.jobs("open", jobs_dir))
try:
    jobs.claim(job["id"], jobs_dir=jobs_dir)
    check("a done job cannot be claimed again", False)
except ValueError:
    check("a done job cannot be claimed again", True)
check("no Claude Code sessions any more", not hasattr(jobs, "notify_session"))

# chats announce themselves through blender_jobs; Blender lists them for the next job
import datetime
names = [c["name"] for c in jobs.known_chats(jobs_dir)]
check("known chats: the ones jobs went to", "Leuchte" in names and "CAD 5" in names, names)
os.environ["AI_STUDIO_JOBS_DIR"] = jobs_dir
mcp = load("live_mcp", "live_mcp.py")
answer = mcp.run_tool("blender_jobs", {"action": "list", "chat": "  CAD   7 "})
check("list with a chat name works", not answer.get("isError"), answer)
seen = jobs.known_chats(jobs_dir)
check("a chat that called blender_jobs comes first, with its time", seen[0]["name"] == "CAD 7" and seen[0]["last"]
      and jobs.ago(seen[0]["last"]) == (0, "now"), seen)
new = jobs.new_job("Bohrung 5 mm", chat={"name": "CAD 5"}, jobs_dir=jobs_dir)
mcp.run_tool("blender_jobs", {"action": "claim", "id": new["id"], "by": "CAD 5"})
check("claim notes the chat too", jobs.known_chats(jobs_dir)[0]["name"] == "CAD 5"
      and jobs.load(new["id"], jobs_dir)["claimed_by"] == "CAD 5", jobs.known_chats(jobs_dir))
mcp.run_tool("blender_jobs", {"action": "list", "chat": "cad 5"})
check("same chat, other spelling: one entry", [c["name"].lower() for c in jobs.known_chats(jobs_dir)].count("cad 5") == 1)
del os.environ["AI_STUDIO_JOBS_DIR"]
now = datetime.datetime(2026, 11, 12, 12, 0, 0)
check("ago", jobs.ago("2026-11-12T11:57:00", now) == (3, "min") and jobs.ago("2026-11-12T07:00:00", now) == (5, "h")
      and jobs.ago("2026-11-09T12:00:00", now) == (3, "d") and jobs.ago("", now) is None)
check("instructions ask the chat for its name", "chat's name" in mcp.INSTRUCTIONS)

# the CAD skill: an unchanged old copy gets the new text, a changed one stays; the switch is kept
import subprocess
sk = load("skills", "skills.py")
old = subprocess.run(["git", "show", "9d2a4b0:src/skills_default/cad.md"], cwd=os.path.join(ROOT, ".."),
                     capture_output=True).stdout
if old:
    for case, text in (("unchanged", old), ("changed", old + b"\nmeine Notiz\n")):
        work = os.path.join(TMP, "work_" + case)
        os.makedirs(os.path.join(work, "skills"))
        open(os.path.join(work, "skills", "cad.md"), "wb").write(text)
        json.dump(sorted(n[:-3] for n in os.listdir(sk.DEFAULTS_DIR) if n.endswith(".md")),
                  open(os.path.join(work, "skills", sk.INSTALLED), "w"))
        body = sk.find(work, "cad")["body"]
        check("old CAD skill %s: %s" % (case, "new text" if case == "unchanged" else "kept"),
              ("studio_cad_job" in body) == (case == "unchanged"), body[:80])
work = os.path.join(TMP, "work_active")
sk.save_active(work, ["cad", "gone"])
check("checked skills kept, deleted ones dropped", sk.load_active(work) == ["cad"], sk.load_active(work))

ok = all(results)
print("CAD_UNIT_OK" if ok else "CAD_UNIT_FAIL", "%d/%d" % (sum(results), len(results)))
sys.exit(0 if ok else 1)

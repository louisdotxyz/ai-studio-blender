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

# Onshape: comments for the CAD side, against a stand-in for Onshape's API
import base64
import http.server
import threading
osh = load("onshape", "studio/onshape.py")
seen = {"comments": {}, "auth": set(), "attachments": []}


class FakeOnshape(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _answer(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        seen["auth"].add(self.headers.get("Authorization"))
        if self.path == "/api/v6/users/sessioninfo":
            return self._answer(200, {"name": "Test User"})
        cid = self.path.rsplit("/", 1)[-1]
        if cid in seen["comments"]:
            return self._answer(200, seen["comments"][cid])
        self._answer(404, {"message": "not found"})

    def do_POST(self):
        seen["auth"].add(self.headers.get("Authorization"))
        data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/api/v6/comments":
            body = json.loads(data)
            cid = "c%d" % (len(seen["comments"]) + 1)
            seen["comments"][cid] = dict(body, id=cid)
            return self._answer(200, {"id": cid})
        if self.path.endswith("/attachment"):
            seen["attachments"].append((self.path, self.headers.get("Content-Type"), data))
            return self._answer(200, {})
        self._answer(404, {})


fake = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeOnshape)
threading.Thread(target=fake.serve_forever, daemon=True).start()
base = "http://127.0.0.1:%d" % fake.server_address[1]
creds = ("AKEY", "SECRET")
source = base + "/documents/d1/w/w1/e/e1"
check("source parsed (also company domains)", osh.parse_source(source)["eid"] == "e1"
      and osh.parse_source("https://acme.onshape.com/documents/a/w/b/e/c")["base"] == "https://acme.onshape.com"
      and osh.parse_source("https://evil.com/documents/a/w/b/e/c") is None)
check("keys tested with Basic auth", osh.whoami(base, creds) == "Test User"
      and "Basic " + base64.b64encode(b"AKEY:SECRET").decode() in seen["auth"])
png = os.path.join(TMP, "check.png")
open(png, "wb").write(b"\x89PNG fake")
cid = osh.post_comment(source, "AI Studio (Blender) · Front: 1.2 mm collision", image=png, credentials=creds)
made = seen["comments"][cid]
check("comment on the element of the export", (made["documentId"], made["workspaceId"], made["elementId"],
                                               made["objectId"]) == ("d1", "w1", "e1", "d1")
      and "collision" in made["message"])
check("check image attached", seen["attachments"] and b"PNG fake" in seen["attachments"][0][2]
      and seen["attachments"][0][1].startswith("multipart/form-data"))
check("open comment is not resolved", osh.comment_resolved(base, cid, creds) is False)
seen["comments"][cid]["resolvedAt"] = "2026-11-11T10:00:00Z"
check("resolved in Onshape", osh.comment_resolved(base, cid, creds) is True)
seen["comments"][cid]["reopenedAt"] = "2026-11-11T11:00:00Z"
check("reopened after resolving: open again", osh.comment_resolved(base, cid, creds) is False)
check("deleted comment: None", osh.comment_resolved(base, "gone", creds) is None)
try:
    osh.post_comment(base + "/documents/d1/v/v1/e/e1", "x", credentials=creds)
    check("version of a document refused", False)
except osh.OnshapeError:
    check("version of a document refused", True)
folder = os.path.join(TMP, "fb_onshape")
cf.add_feedback(folder, "Front", "zu dick", today="2026-11-11")
point = cf.open_feedback(folder)[0]
cf.remember_comment(folder, point, cid, base)
check("comment remembered per point", cf.feedback_comments(folder)[point]["cid"] == cid)
check("tick off a point", cf.tick_feedback(folder, point) and cf.open_feedback(folder) == []
      and "- [x] " + point + " · resolved in Onshape" in open(os.path.join(folder, cf.FEEDBACK_FILE)).read())
fake.shutdown()


class Done:
    def __init__(self, code=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


real_security = osh._security
osh._security = lambda *a: Done(0, 'keychain: "x"\n    "acct"<blob>="AKEY"\n    "svce"<blob>="%s"\n' % osh.SERVICE,
                                 'password: "SE\\"CRET"\n')
check("keys read from the keychain", osh.keys() == ("AKEY", 'SE\\"CRET'), osh.keys())
osh._security = lambda *a: Done(44, "", "security: SecKeychainSearchCopyNext: The specified item could not be found")
check("no keys in the keychain: None", osh.keys() is None)
osh._security = real_security

# friendly sentences instead of errors (CAD box, sync)
real_whoami = osh.whoami
for error, expect in (("Onshape refused the keys (401): …", "does not accept the saved API keys"),
                      ("Onshape does not answer: timed out", "cannot be reached")):
    def boom(*a, _e=error, **k):
        raise osh.OnshapeError(_e)
    osh.whoami = boom
    ok_, text = osh.check(credentials=("A", "B"))
    check("keys check: %s" % expect, not ok_ and expect in text, text)
osh.whoami = lambda *a, **k: "Louis"
check("keys check: works", osh.check(credentials=("A", "B")) == (True, "Onshape keys work (Louis)"))
osh.whoami = real_whoami

# the chat an export came from
side = os.path.join(TMP, "Chat V1.cad.json")
json.dump({"format": "ai-studio-cad/1", "chat": {"name": "Leuchte V22", "url": "https://evil.example/x",
                                                 "session": "cad-leuchte"}}, open(side, "w"))
chat = cf.load_sidecar(side)["chat"]
check("sidecar chat: name and session kept, foreign link dropped",
      chat == {"name": "Leuchte V22", "url": "", "session": "cad-leuchte"}, chat)

# CAD jobs (files) and the links into the Claude app
jobs = load("cadjobs", "cadjobs.py")
jobs_dir = os.path.join(TMP, "jobs")
uuid_ = "0b9e1c2a-1111-4222-8333-444455556666"
job = jobs.new_job("  Wand  2.4 mm  ", part="Deckel", chat={"name": "Leuchte", "url": "https://claude.ai/chat/" + uuid_},
                   jobs_dir=jobs_dir)
check("job written, text tidy", job["text"] == "Wand 2.4 mm" and os.path.isfile(job["path"])
      and jobs.jobs("open", jobs_dir)[0]["id"] == job["id"])
check("origin chat opens by its link", jobs.chat_link(job)[0] == "claude://claude.ai/chat/" + uuid_)
check("no link, only a name: Recents and paste", jobs.chat_link(dict(job, chat={"name": "Leuchte"}))[0]
      == jobs.RECENTS)
link = jobs.chat_link(job, "new")[0]
check("new chat with the text", link.startswith("claude://claude.ai/new?q=CAD%20job") and "Wand%202.4%20mm" in link)
text = jobs.message(dict(job, path=job["path"]))
check("message names its chat and works without blender_jobs", "for the chat 'Leuchte'" in text
      and "no blender_jobs tool" in text and job["path"] in text and "linked to the Mac" in text, text)
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
      == {"name": "CAD 5", "url": "", "session": ""}, jobs.remembered_chat("/x/VELO/files/CAD", jobs_dir))
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
calls = []


def fake_run(cmd, **k):
    calls.append(cmd)
    if cmd[1] == "agents":
        return Done(0, json.dumps([{"name": "cad-leuchte", "sessionId": "abc", "status": "idle", "cwd": "/x"}]))
    return Done(0)


found = jobs.find_session(jobs.code_sessions("claude", run=fake_run), "CAD-Leuchte")
check("open Claude Code session found by name", found and found["sessionId"] == "abc")
check("message goes out with only SendMessage", jobs.notify_session("claude", "cad-leuchte", job, run=fake_run)
      and calls[-1][1] == "-p" and calls[-1][-2:] == ["SendMessage", "ListAgents"]
      and "cad-leuchte" in calls[-1][2] and job["id"] in calls[-1][2])

ok = all(results)
print("CAD_UNIT_OK" if ok else "CAD_UNIT_FAIL", "%d/%d" % (sum(results), len(results)))
sys.exit(0 if ok else 1)

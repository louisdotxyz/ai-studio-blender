"""End-to-end check of the add-on's own updates (headless, nothing of yours is touched):

    python3 tests/update_e2e.py

Builds the add-on twice (an "old" 0.9.0 and the current version), serves the current one as an
extension repository on 127.0.0.1, installs the old one from that repository into a fresh
Blender profile, then lets the old version find and install the update. Prints UPDATE_OK.
"""

import http.server
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
BLENDER = "/Applications/Blender.app/Contents/MacOS/Blender"
TMP = os.path.realpath(tempfile.mkdtemp(prefix="ai_studio_update_"))   # /var is a link: Blender would see two folders


def blender(*args, env=None, check=True):
    """Blender without a window; with a profile (env) its preferences count, else factory settings."""
    result = subprocess.run([BLENDER, "-b"] + ([] if env else ["--factory-startup"]) + list(args),
                            capture_output=True, text=True, env=env, timeout=300)
    if check and (result.returncode != 0 or "not found in remote" in result.stdout):
        print(result.stdout[-2000:], result.stderr[-2000:])
        print("TMP", TMP)
        raise SystemExit("UPDATE_FAIL blender " + " ".join(args[:4]))
    return result.stdout + result.stderr


def build(version, out_dir):
    src = os.path.join(TMP, "src_" + version)
    shutil.copytree(os.path.join(ROOT, "src"), src, ignore=shutil.ignore_patterns("__pycache__"))
    manifest = os.path.join(src, "blender_manifest.toml")
    text = open(manifest, encoding="utf-8").read()
    open(manifest, "w", encoding="utf-8").write(re.sub(r'(?m)^version = ".*"', 'version = "%s"' % version, text, count=1))
    os.makedirs(out_dir, exist_ok=True)
    blender("--command", "extension", "build", "--source-dir", src, "--output-dir", out_dir)
    return os.path.join(out_dir, "AI_Studio-%s.zip" % version)


current = re.search(r'(?m)^version = "(.*)"', open(os.path.join(ROOT, "src", "blender_manifest.toml")).read()).group(1)
repo = os.path.join(TMP, "repo")
new_zip = build(current, os.path.join(TMP, "new"))
old_zip = build("0.9.0", repo)          # the repository serves the old version first
blender("--command", "extension", "server-generate", "--repo-dir", repo)

with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=repo, **k)   # noqa: E731
http.server.ThreadingHTTPServer.allow_reuse_address = True
server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
threading.Thread(target=server.serve_forever, daemon=True).start()

profile = os.path.join(TMP, "profile")
os.makedirs(profile)   # Blender saves its preferences only into an existing folder
env = dict(os.environ, BLENDER_USER_RESOURCES=profile)
url = "http://127.0.0.1:%d/index.json" % port
blender("--online-mode", "--command", "extension", "repo-add", "--name", "studio", "--url", url, "studio", env=env)
blender("--online-mode", "--command", "extension", "sync", env=env)
blender("--online-mode", "--command", "extension", "install", "--enable", "AI_Studio", env=env)
# Now the repository has the new version (as after a release).
os.remove(old_zip)
shutil.copy(new_zip, repo)
blender("--command", "extension", "server-generate", "--repo-dir", repo)

script = os.path.join(TMP, "run_update.py")
open(script, "w").write('''
import sys, time, bpy
from bl_ext.studio.AI_Studio import updates
print("BEFORE", updates.installed_version(), updates.own_repo() is not None)
updates.check(force=True)
updates.state["thread"].join(30)
print("AVAILABLE", updates.state["available"], updates.state["error"])
print("INSTALLED", updates.install())
import importlib
fresh = importlib.import_module("bl_ext.studio.AI_Studio.updates")
print("AFTER", fresh.installed_version())
fresh.announce()
core = importlib.import_module("bl_ext.studio.AI_Studio.core")
print("ANNOUNCE", any("1.2" in m["text"] for m in core.messages))
''')
out = blender("--online-mode", "--python", script, env=env, check=False)
lines = {line.split(" ", 1)[0]: line for line in out.splitlines() if line.split(" ", 1)[0] in
         ("BEFORE", "AVAILABLE", "INSTALLED", "AFTER", "ANNOUNCE")}
manifest = open(os.path.join(profile, "extensions", "studio", "AI_Studio", "blender_manifest.toml")).read()
installed = re.search(r'(?m)^version = "(.*)"', manifest).group(1)
server.shutdown()
ok = (lines.get("BEFORE", "").startswith("BEFORE 0.9.0 True")
      and lines.get("AVAILABLE", "").startswith("AVAILABLE " + current)
      and installed == current and lines.get("ANNOUNCE") == "ANNOUNCE True")
for key in ("BEFORE", "AVAILABLE", "INSTALLED", "AFTER", "ANNOUNCE"):
    print(lines.get(key, key + " ?"))
print("installed on disk:", installed)
if not ok:
    print(out[-3000:])
shutil.rmtree(TMP, ignore_errors=True)
print("UPDATE_OK" if ok else "UPDATE_FAIL")
sys.exit(0 if ok else 1)

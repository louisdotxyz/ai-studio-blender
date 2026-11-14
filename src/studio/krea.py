# SPDX-License-Identifier: GPL-3.0-or-later
"""Refine a finished render with Krea (Finish tab): a sentence ("a hand holds the product"), the render
goes to Krea's image editing, the result is saved next to the render, at exactly the render's size.

Krea's public REST API, with a personal API token (krea.ai/app/api/tokens; billed from the workspace's
API balance, not the plan's compute units):
    POST /assets                         multipart "file" → the uploaded image's URL
    POST /generate/image/<model path>    JSON → {"job_id", "status"}
    GET  /jobs/<id>                      → {"status", "result": {"urls": [...]}, "error"}
Network work runs in a thread; Blender only reads the state (`job`) and loads the image at the end.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid

import bpy

API = "https://api.krea.ai"
TOKENS_URL = "https://www.krea.ai/app/api/tokens"
USAGE_URL = "https://www.krea.ai/app/api/usage"
# key: (model path, label, what it is good for)
MODELS = (
    ('AUTO', "", "Auto", "Precise when something is added to the picture as it is; Free for a new environment, "
     "zooming out or another perspective"),
    ('PRECISE', "ideogram/ideogram-4.5-precise", "Precise",
     "Ideogram 4.5 Precise: edits the picture in place; keeps the product, the framing and the size exactly. "
     "Best for a hand, props, a person next to it"),
    ('NANO', "google/nano-banana-pro", "Free",
     "Nano Banana Pro: paints the picture anew; can change the environment, zoom out or the perspective. "
     "The product can shift a little; the result is scaled back to the render's size"),
)
ZOOMS = (('KEEP', "Keep", "The same framing"), ('SOME', "Wider", "Zoom out a little: more of the surroundings"),
         ('FAR', "Far", "Zoom out a lot: a wide shot, the product small in the scene"))
VIEWS = (('KEEP', "Keep", "The same perspective"), ('EYE', "Eye Level", "Seen from eye level"),
         ('ABOVE', "From Above", "Seen from above"), ('LOW', "Low Angle", "Seen from a low angle, looking up"))
ZOOM_TEXT = {'SOME': " Zoom out a little so that more of the surroundings are visible and the product is smaller "
                     "in the frame.",
             'FAR': " Zoom out to a wide shot: the product is small in the scene and the surroundings fill the frame."}
VIEW_TEXT = {'EYE': " Show the scene from eye level.", 'ABOVE': " Show the scene from above.",
             'LOW': " Show the scene from a low angle, looking up at the product."}
ENV_TEXT = (" Place the product into the environment of the reference image: its space, surfaces, light and colors, "
            "with matching shadows and reflections.")
# What Krea charges for one picture (its own price list, krea.ai/app/api/pricing); None = not published
PRICES = {'PRECISE': None, 'NANO': 0.15}
IDEAS = (
    ("Hand", "Add a realistic adult hand that holds the product naturally, matching the existing light."),
    ("In Use", "Show the product in use by a person, cropped so the product stays the hero of the image."),
    ("Setting", "Place the product in a fitting real-world setting with matching light and soft shadows."),
    ("Props", "Add a few tasteful props around the product, out of focus, matching the existing light."),
)
KEEP = " Keep the product exactly as it is: same shape, proportions, materials, colors, details and text."
KEEP_FRAME = " Keep the camera angle and the framing."
POLL = 2.0
LIMIT = 300.0
job = {"state": "", "text": "", "source": "", "result": "", "started": 0.0, "thread": None}


def token():
    from . import runner
    p = runner.prefs()
    return (getattr(p, "krea_token", "") or os.environ.get("KREA_API_KEY", "")).strip() if p else ""


def model_path(key):
    return next((m[1] for m in MODELS if m[0] == key), MODELS[1][1])


def pick_model(key, environment="", zoom='KEEP', view='KEEP'):
    """Auto: Precise edits in place (product and size exact); a new environment, zooming out or another
    perspective needs the whole picture anew: Free."""
    if key != 'AUTO':
        return key
    return 'NANO' if environment or zoom != 'KEEP' or view != 'KEEP' else 'PRECISE'


def full_prompt(prompt, environment="", zoom='KEEP', view='KEEP'):
    text = prompt.strip().rstrip(".") + "." if prompt.strip() else ""
    if environment:
        text += ENV_TEXT
    text += ZOOM_TEXT.get(zoom, "") + VIEW_TEXT.get(view, "")
    text += KEEP + (KEEP_FRAME if zoom == 'KEEP' and view == 'KEEP' else "")
    return text.strip()


# ------------------------------------------------------------------ what it cost (Krea has no balance API)

def _log_file():
    from .paths import data_dir
    return os.path.join(data_dir(), "krea_log.json")


def log():
    try:
        with open(_log_file(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _add_log(model, width, height):
    entries = log()[-500:]
    entries.append({"time": time.strftime("%Y-%m-%d %H:%M"), "model": model, "size": [width, height]})
    try:
        with open(_log_file(), "w", encoding="utf-8") as f:
            json.dump(entries, f)
    except OSError:
        pass


def month_summary():
    """(pictures this month, known cost in $, pictures without a published price)."""
    month = time.strftime("%Y-%m")
    entries = [e for e in log() if str(e.get("time", "")).startswith(month)]
    known = sum(PRICES.get(e.get("model")) or 0.0 for e in entries)
    unknown = sum(1 for e in entries if PRICES.get(e.get("model")) is None)
    return len(entries), known, unknown


def price_text(model):
    price = PRICES.get(model)
    return ("about $%.2f per picture" % price) if price else "price per picture: see krea.ai/app/api/pricing"


def busy():
    return job["state"] in {"upload", "working", "download"}


def result_path(source):
    """<render>_krea_01.png, _02 … next to the render (never overwrites)."""
    base, _ext = os.path.splitext(source)
    base = re.sub(r"_krea_\d+$", "", base)
    for n in range(1, 1000):
        path = "%s_krea_%02d.png" % (base, n)
        if not os.path.exists(path):
            return path
    raise RuntimeError("Too many Krea versions of %s" % os.path.basename(source))


def nearest_ratio(width, height):
    """The aspect ratio Krea's models know that is closest to width : height."""
    ratios = ("21:9", "16:9", "3:2", "4:3", "5:4", "1:1", "4:5", "3:4", "2:3", "9:16")
    want = width / max(height, 1)
    return min(ratios, key=lambda r: abs(int(r.split(":")[0]) / int(r.split(":")[1]) - want))


def request_body(key, prompt, image_url, width, height, environment_url="", zoom='KEEP', view='KEEP'):
    """The JSON for one model; the render's size goes with it where the model takes one, an environment
    picture as the second image (Free) or as a reference (Precise)."""
    text = full_prompt(prompt, bool(environment_url), zoom, view)
    if key == 'NANO':
        return {"prompt": text, "image_urls": [image_url] + ([environment_url] if environment_url else []),
                "width": width, "height": height, "aspect_ratio": nearest_ratio(width, height),
                "resolution": "2K" if max(width, height) > 1100 else "1K"}
    body = {"prompt": text, "image_url": image_url, "quality": "high"}
    if environment_url:
        body["reference_images"] = [environment_url]
    return body


# ------------------------------------------------------------------ HTTP (in the thread)

def _call(method, path, key, data=None, headers=None, timeout=60):
    req = urllib.request.Request(path if path.startswith("http") else API + path, data=data, method=method,
                                 headers=dict({"Authorization": "Bearer " + key, "Accept": "application/json"},
                                              **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 401:
            raise RuntimeError("Krea does not accept the API token (Preferences › Studio)") from None
        if exc.code == 402:
            raise RuntimeError("Krea: the API balance is empty (top it up at krea.ai)") from None
        raise RuntimeError("Krea %s: %s" % (exc.code, detail)) from None
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError:
        return body.decode("utf-8", "replace").strip()


def _first_url(data):
    if isinstance(data, str):
        return data if data.startswith("http") else ""
    if isinstance(data, dict):
        for k in ("image_url", "url", "asset_url"):
            if isinstance(data.get(k), str) and data[k].startswith("http"):
                return data[k]
        values = list(data.values())
    elif isinstance(data, list):
        values = data
    else:
        return ""
    for value in values:
        found = _first_url(value)
        if found:
            return found
    return ""


def upload(key, path):
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        data = f.read()
    body = (("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\nContent-Type: image/png\r\n\r\n"
             % (boundary, os.path.basename(path))).encode() + data + ("\r\n--%s--\r\n" % boundary).encode())
    url = _first_url(_call("POST", "/assets", key, body, {"Content-Type": "multipart/form-data; boundary=" + boundary},
                           timeout=120))
    if not url:
        raise RuntimeError("Krea did not return the uploaded image")
    return url


def _work(key, model, prompt, source, width, height, target, environment, zoom, view):
    try:
        job["state"] = "upload"
        image_url = upload(key, source)
        environment_url = upload(key, environment) if environment else ""
        job["state"] = "working"
        body = request_body(model, prompt, image_url, width, height, environment_url, zoom, view)
        started = _call("POST", "/generate/" + "image/" + model_path(model), key, json.dumps(body).encode(),
                        {"Content-Type": "application/json"})
        job_id = started.get("job_id") if isinstance(started, dict) else ""
        if not job_id:
            raise RuntimeError("Krea did not start the job: %s" % str(started)[:200])
        deadline = time.monotonic() + LIMIT
        while True:
            time.sleep(POLL)
            state = _call("GET", "/jobs/" + job_id, key)
            status = state.get("status") if isinstance(state, dict) else ""
            if status == "completed":
                urls = (state.get("result") or {}).get("urls") or []
                if not urls:
                    raise RuntimeError("Krea finished without an image")
                break
            if status in {"failed", "cancelled"}:
                error = state.get("error") or {}
                raise RuntimeError("Krea: %s %s" % (error.get("code", status), error.get("message", "")))
            if time.monotonic() > deadline:
                raise RuntimeError("Krea took longer than %d s" % LIMIT)
        job["state"] = "download"
        urllib.request.urlretrieve(urls[0], target + ".download")
        _add_log(model, width, height)
        job.update(state="loaded", result=target)
    except Exception as exc:         # everything goes to the panel as text
        job.update(state="error", text=str(exc))


def start(model, prompt, source, width, height, environment="", zoom='KEEP', view='KEEP'):
    """Starts refining `source` (a render file) in the background. Returns (result path, model used)."""
    key = token()
    if not key:
        raise ValueError("Add your Krea API token in Preferences › Studio")
    if busy():
        raise ValueError("Krea is still working on the last image")
    if not source or not os.path.isfile(source):
        raise ValueError("Render an image first")
    environment = bpy.path.abspath(environment) if environment else ""
    if environment and not os.path.isfile(environment):
        raise ValueError("The environment picture is missing: %s" % environment)
    if not prompt.strip() and not environment and zoom == 'KEEP' and view == 'KEEP':
        raise ValueError("Write what Krea should change")
    model = pick_model(model, environment, zoom, view)
    target = result_path(source)
    job.update(state="upload", text="", source=source, result="", started=time.monotonic(), model=model)
    thread = threading.Thread(target=_work, args=(key, model, prompt, source, width, height, target, environment,
                                                  zoom, view), daemon=True)
    job["thread"] = thread
    thread.start()
    if not bpy.app.timers.is_registered(_finish):
        bpy.app.timers.register(_finish, first_interval=1.0)
    return target, model


def fit_size(path, width, height):
    """Scales the downloaded image to exactly width × height (the render's pixels) and saves it as PNG."""
    image = bpy.data.images.load(path + ".download", check_existing=False)
    try:
        if tuple(image.size) != (width, height):
            image.scale(width, height)
        image.filepath_raw = path
        image.file_format = 'PNG'
        image.save()
    finally:
        bpy.data.images.remove(image)
        try:
            os.remove(path + ".download")
        except OSError:
            pass


def image_size(path):
    """(width, height) of a render file: from the PNG header, otherwise by loading it for a moment."""
    import struct
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
        return struct.unpack(">II", head[16:24])
    image = bpy.data.images.load(path, check_existing=False)
    try:
        return tuple(image.size)
    finally:
        bpy.data.images.remove(image)


def _finish():
    """Timer: when the thread is done, fit the image to the render's size and show it."""
    if busy():
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()
        return 1.0
    if job["state"] == "loaded":
        try:
            width, height = image_size(job["source"])
            fit_size(job["result"], width, height)
            job.update(state="done", text=os.path.basename(job["result"]))
            from . import runner, tab_queue
            runner._images_cache["time"] = 0.0      # list the new image right away
            tab_queue._view["path"] = job["result"]
        except Exception as exc:
            job.update(state="error", text="Could not save the Krea image: %s" % exc)
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()
    return None

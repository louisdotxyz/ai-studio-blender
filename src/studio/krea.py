# SPDX-License-Identifier: GPL-3.0-or-later
"""Refine a finished render with Krea (Finish tab): a sentence ("a hand holds the product"), the render
goes to Krea's image editing, the result is saved next to the render, at exactly the render's size.

Two ways:
- With Claude (default): the chat gets the user's claude.ai Krea connector for that run and works with the
  Krea plan's compute units. It calls api.krea_prepare (prompt with the product's true scale, the render,
  a mask that keeps the product untouched, uploads), asks Krea for the price (preflight), shows it here,
  generates and hands the result to api.krea_save.
- ▶ without Claude: Krea's public REST API with a personal API token (krea.ai/app/api/tokens; billed from
  the workspace's API balance, not the plan's compute units):
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
ZOOMS = (('CLOSER', "Closer", "Closer to the product: it fills more of the picture"),
         ('KEEP', "Same", "The same framing"),
         ('WIDER', "Wider", "Further away: more of the surroundings, the product smaller"))
VIEWS = (('LOWER', "Lower", "The camera lower, looking up at the product a little"),
         ('KEEP', "Same", "The same camera height"),
         ('HIGHER', "Higher", "The camera higher, looking down at the product"))
# older names (Claude, saved files) → the current ones
_ZOOM_ALIASES = {'SOME': 'WIDER', 'FAR': 'WIDER', 'NEAR': 'CLOSER'}
_VIEW_ALIASES = {'EYE': 'KEEP', 'ABOVE': 'HIGHER', 'LOW': 'LOWER', 'HIGH': 'HIGHER'}
ZOOM_TEXT = {'CLOSER': " Move the camera closer: the product is larger in the frame, cropped tighter.",
             'WIDER': " Zoom out so that more of the surroundings are visible and the product is smaller in the frame."}
VIEW_TEXT = {'LOWER': " Show the scene from a lower camera height, looking slightly up at the product.",
             'HIGHER': " Show the scene from a higher camera height, looking down at the product."}


def zoom_key(zoom):
    zoom = str(zoom or 'KEEP').upper()
    return _ZOOM_ALIASES.get(zoom, zoom if zoom in ZOOM_TEXT else 'KEEP')


def view_key(view):
    view = str(view or 'KEEP').upper()
    return _VIEW_ALIASES.get(view, view if view in VIEW_TEXT else 'KEEP')


ENV_TEXT = (" Place the product into the environment of the reference image: its space, surfaces, light and colors, "
            "with matching shadows and reflections.")
REF_TEXT = (" Use the reference images for what the request describes (for example a hand pose, props or a style); "
            "do not copy anything else from them.")
MAX_REFERENCES = 4


def _refs(references):
    """A list of reference files or URLs from a list, a single string or newline-separated text."""
    if not references:
        return []
    if isinstance(references, str):
        references = references.split("\n")
    return [r for r in (str(x).strip() for x in references) if r]


# The toggles of the Finish tab: (key, label, sentence for Krea, keep the product untouched?)
SCENE = (
    ('hand', "Hand", "Add a realistic adult hand that holds the product naturally, matching the existing light.", False),
    ('inuse', "In Use", "Show the product in use by a person, cropped so the product stays the hero of the image.",
     False),
    ('room', "Room", "Place the product in a fitting real-world setting with matching light and soft shadows.", True),
    ('props', "Props", "Add a few tasteful props around the product, out of focus, matching the existing light.", True),
)
# What Krea charges for one picture (its own price list, krea.ai/app/api/pricing); None = not published
PRICES = {'PRECISE': None, 'NANO': 0.15}
# Compute units of the plan for one picture (Krea's quotes, Nov 2026); the real price comes from the preflight
UNITS = {'PRECISE': 163, 'NANO': 111}
KEEP = " Keep the product exactly as it is: same shape, proportions, materials, colors, details and text."
KEEP_FRAME = " Keep the camera angle and the framing."
POLL = 2.0
LIMIT = 300.0
job = {"state": "", "text": "", "source": "", "result": "", "started": 0.0, "thread": None, "quote": None}


def token():
    from . import runner
    p = runner.prefs()
    return (getattr(p, "krea_token", "") or os.environ.get("KREA_API_KEY", "")).strip() if p else ""


def model_path(key):
    return next((m[1] for m in MODELS if m[0] == key), MODELS[1][1])


def pick_model(key, environment="", zoom='KEEP', view='KEEP'):
    """environment: True when the product goes into a new place (references with "Room")."""
    """Auto: Precise edits in place (product and size exact); a new environment, zooming out or another
    perspective needs the whole picture anew: Free."""
    if key != 'AUTO':
        return key
    return 'NANO' if environment or zoom_key(zoom) != 'KEEP' or view_key(view) != 'KEEP' else 'PRECISE'


def full_prompt(prompt, environment="", zoom='KEEP', view='KEEP', scale="", room=True):
    """The user's sentence plus what the tab's choices mean, the product's true scale and "keep it".
    environment: reference images go with it; room: they show the place (else hand, props, style …)."""
    text = prompt.strip().rstrip(".") + "." if prompt.strip() else ""
    if environment:
        text += ENV_TEXT if room else REF_TEXT
    zoom, view = zoom_key(zoom), view_key(view)
    text += ZOOM_TEXT.get(zoom, "") + VIEW_TEXT.get(view, "")
    if scale:
        text += " " + scale.strip()
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


def _add_log(model, width, height, credits=None, balance=None, via="api"):
    """via: "plan" (Claude with the Krea connector: compute units) or "api" (API token: dollars)."""
    entries = log()[-500:]
    entry = {"time": time.strftime("%Y-%m-%d %H:%M"), "model": model, "size": [width, height], "via": via}
    if credits is not None:
        entry["credits"] = round(float(credits), 1)
    if balance is not None:
        entry["balance"] = round(float(balance), 1)
    entries.append(entry)
    try:
        with open(_log_file(), "w", encoding="utf-8") as f:
            json.dump(entries, f)
    except OSError:
        pass


def month_summary():
    """(pictures this month, known API cost in $, API pictures without a published price, plan credits)."""
    month = time.strftime("%Y-%m")
    entries = [e for e in log() if str(e.get("time", "")).startswith(month)]
    api = [e for e in entries if e.get("via", "api") == "api"]
    known = sum(PRICES.get(e.get("model")) or 0.0 for e in api)
    unknown = sum(1 for e in api if PRICES.get(e.get("model")) is None)
    credits = sum(e.get("credits") or 0.0 for e in entries if e.get("via") == "plan")
    return len(entries), known, unknown, credits


def last_balance():
    """The plan's compute units left after the last run with Claude (from Krea's quote), or None."""
    for entry in reversed(log()):
        if entry.get("balance") is not None:
            return entry["balance"]
    return None


def price_text(model, plan=True):
    if plan:
        return "about %d credits per picture" % UNITS.get(model, 150)
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


def request_body(key, prompt, image_url, width, height, environment_url="", zoom='KEEP', view='KEEP',
                 scale="", mask_url="", room=True):
    """The JSON for one model; the render's size goes with it where the model takes one, an environment
    picture as the second image (Free) or as a reference (Precise); the mask (white = Krea may change it,
    the product black) only for Precise, which edits in place."""
    refs = _refs(environment_url)
    text = full_prompt(prompt, bool(refs), zoom, view, scale, room)
    if key == 'NANO':
        return {"prompt": text, "image_urls": [image_url] + refs[:MAX_REFERENCES],
                "width": width, "height": height, "aspect_ratio": nearest_ratio(width, height),
                "resolution": "2K" if max(width, height) > 1100 else "1K"}
    body = {"prompt": text, "image_url": image_url, "quality": "high"}
    if mask_url:
        body["mask_url"] = mask_url
    if refs:
        body["reference_images"] = refs[:3 if mask_url else MAX_REFERENCES]
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


_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def multipart(path, fields=None):
    """(body, content type) of a multipart/form-data POST with the file as "file" (and extra form fields)."""
    boundary = uuid.uuid4().hex
    with open(path, "rb") as f:
        data = f.read()
    parts = b"".join(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                      % (boundary, k, v)).encode() for k, v in (fields or {}).items())
    kind = _TYPES.get(os.path.splitext(path)[1].lower(), "application/octet-stream")
    head = ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\nContent-Type: %s\r\n\r\n"
            % (boundary, os.path.basename(path), kind)).encode()
    return parts + head + data + ("\r\n--%s--\r\n" % boundary).encode(), "multipart/form-data; boundary=" + boundary


def upload(key, path):
    body, kind = multipart(path)
    url = _first_url(_call("POST", "/assets", key, body, {"Content-Type": kind}, timeout=120))
    if not url:
        raise RuntimeError("Krea did not return the uploaded image")
    return url


def upload_to(upload_url, path, fields=None):
    """Uploads a file to a presigned Krea upload URL (from the connector's get_upload_url): no token needed.
    Returns the asset URL Krea answers with."""
    body, kind = multipart(path, fields)
    req = urllib.request.Request(upload_url, data=body, method="POST",
                                 headers={"Content-Type": kind, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            answer = r.read().decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as exc:
        raise RuntimeError("Krea upload %s: %s" % (exc.code, exc.read().decode("utf-8", "replace")[:200])) from None
    try:
        url = _first_url(json.loads(answer))
    except ValueError:
        url = _first_url(answer)
    if not url:
        raise RuntimeError("Krea did not return the uploaded file: %s" % answer[:200])
    return url


def _work(key, model, prompt, source, width, height, target, environment, zoom, view, scale="", mask="", room=True):
    try:
        job["state"] = "upload"
        image_url = upload(key, source)
        environment_url = [upload(key, path) for path in _refs(environment)]
        mask_url = upload(key, mask) if mask and model == 'PRECISE' else ""
        job["state"] = "working"
        body = request_body(model, prompt, image_url, width, height, environment_url, zoom, view, scale, mask_url,
                            room)
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


def start(model, prompt, source, width, height, environment="", zoom='KEEP', view='KEEP', scale="", mask="",
          room=True):
    """Starts refining `source` (a render file) in the background with the API token. scale: the sentence
    about the product's true size; mask: a mask file (Precise only). Returns (result path, model used)."""
    key = token()
    if not key:
        raise ValueError("Add your Krea API token in Preferences › Studio")
    if busy():
        raise ValueError("Krea is still working on the last image")
    if not source or not os.path.isfile(source):
        raise ValueError("Render an image first")
    environment = [bpy.path.abspath(p) for p in _refs(environment)]
    for path in environment:
        if not os.path.isfile(path):
            raise ValueError("The picture is missing: %s" % path)
    if not prompt.strip() and not environment and zoom_key(zoom) == 'KEEP' and view_key(view) == 'KEEP':
        raise ValueError("Write what Krea should change")
    model = pick_model(model, bool(environment) and room, zoom, view)
    target = result_path(source)
    job.update(state="upload", text="", source=source, result="", started=time.monotonic(), model=model, quote=None)
    thread = threading.Thread(target=_work, args=(key, model, prompt, source, width, height, target, environment,
                                                  zoom, view, scale, mask, room), daemon=True)
    job["thread"] = thread
    thread.start()
    if not bpy.app.timers.is_registered(_finish):
        bpy.app.timers.register(_finish, first_interval=1.0)
    return target, model


def _download(url, target, model, width, height, credits, balance):
    try:
        urllib.request.urlretrieve(url, target + ".download")
        _add_log(model, width, height, credits, balance, via="plan")
        job.update(state="loaded", result=target)
    except Exception as exc:
        job.update(state="error", text="Could not download the Krea image: %s" % exc)


def save_from(url, source, model, credits=None, balance=None):
    """A Krea result (made through the connector): downloads it next to `source` as …_krea_01.png at exactly
    the render's size, logs what it cost and shows it in the Finish and Render tabs. Returns the path."""
    if not str(url).startswith("http"):
        raise ValueError("Not an image URL: %r" % url)
    if not source or not os.path.isfile(source):
        raise ValueError("The render is missing: %s" % source)
    width, height = image_size(source)
    target = result_path(source)
    job.update(state="download", text="", source=source, result="", started=time.monotonic(), model=model)
    thread = threading.Thread(target=_download, args=(url, target, model, width, height, credits, balance),
                              daemon=True)
    job["thread"] = thread
    thread.start()
    if not bpy.app.timers.is_registered(_finish):
        bpy.app.timers.register(_finish, first_interval=0.5)
    return target


def set_quote(model, credits, balance=None):
    """What Krea said the next picture costs (preflight), shown in the Finish tab while it is made."""
    job.update(state="working", text="", started=time.monotonic(), model=model, thread=None,
               quote=[credits, balance])
    if not bpy.app.timers.is_registered(_finish):
        bpy.app.timers.register(_finish, first_interval=1.0)


def fail(text):
    """Claude could not finish a run: the Finish tab shows why (and is free for the next one)."""
    job.update(state="error", text=str(text)[:200], thread=None)


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
    if busy() and job.get("thread") is None and time.monotonic() - job["started"] > LIMIT + 60:
        job.update(state="error", text="Krea did not answer (no result came back)")
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

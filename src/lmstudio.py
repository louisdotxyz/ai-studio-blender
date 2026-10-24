# SPDX-License-Identifier: GPL-3.0-or-later
"""Local models through LM Studio.

Claude Code talks to LM Studio's Anthropic-compatible endpoint
(``ANTHROPIC_BASE_URL``) in a stripped-down mode (``--bare``, no built-in
tools, own system prompt), so a request carries only the Blender tools and a
short prompt instead of ~15k tokens of Claude Code overhead.

Models are the ones LM Studio knows, stored in ``~/.lmstudio/models``. The
selected model is loaded in the background as soon as it is chosen, so the
first message does not wait for it. This module does not import ``bpy``.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request

HOST = "http://127.0.0.1:1234"
MODELS_DIR = os.path.expanduser("~/.lmstudio/models")
MIN_CONTEXT = 16384   # below this an already loaded model is reloaded
OUTPUT_TOKENS = 8192  # max answer length per request (reserved from the context)

_cache = None  # [{"key", "name", "size", "vision", "max_context"}]
_lock = threading.Lock()
# Shown in the sidebar: idle / starting / loading / ready / error
status = {"state": "idle", "model": "", "message": "", "since": 0.0, "context": 0}
# Polled in the background: server up?, loaded models {key: context}, RAM used by LM Studio (bytes)
live = {"server": None, "loaded": {}, "ram": 0, "polled": 0.0}
TOTAL_RAM = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") if hasattr(os, "sysconf") else 0
GB = 1024 ** 3  # memory sizes in GiB, like Activity Monitor and footprint
POLL_INTERVAL = 5.0
_polling = False


def find_lms():
    path = os.path.expanduser("~/.lmstudio/bin/lms")
    if os.path.isfile(path) and os.access(path, os.X_OK):
        return path
    return shutil.which("lms")


def _lms(*args, timeout=60):
    lms = find_lms()
    if not lms:
        raise RuntimeError("LM Studio CLI 'lms' not found (~/.lmstudio/bin/lms)")
    proc = subprocess.run([lms, *args], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "").strip()[-500:] or f"lms {args[0]} failed")
    return proc.stdout


def _json_from(output):
    # lms may print status lines ("Waking up LM Studio service...") before the JSON.
    start = min((i for i in (output.find("["), output.find("{")) if i >= 0), default=-1)
    return json.loads(output[start:]) if start >= 0 else []


def _get(path, timeout=2.0):
    with urllib.request.urlopen(HOST + path, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _server_models():
    """LM Studio's REST view of all models with load state, or None if the server is down."""
    try:
        return _get("/api/v0/models").get("data") or []
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Model list

def list_models(refresh=False):
    """LLMs installed in LM Studio (cached)."""
    global _cache
    if _cache is not None and not refresh:
        return _cache
    models = []
    try:
        for m in _json_from(_lms("ls", "--json", timeout=30)):
            if m.get("type") != "llm":
                continue
            size = (m.get("sizeBytes") or 0) / 1e9
            models.append({"key": m["modelKey"], "name": m.get("displayName") or m["modelKey"],
                           "size": f"{size:.1f} GB" if size else "", "vision": bool(m.get("vision")),
                           "max_context": int(m.get("maxContextLength") or 0)})
    except Exception:
        models = []
    _cache = sorted(models, key=lambda m: m["name"].lower())
    return _cache


def prefetch():
    """Fill the model list in the background so the menu opens instantly."""
    threading.Thread(target=list_models, daemon=True, name="lmstudio-ls").start()


def display_name(key):
    for m in _cache or []:
        if m["key"] == key:
            return m["name"]
    return key


def has_vision(key):
    m = next((m for m in _cache or [] if m["key"] == key), None)
    return True if m is None else m["vision"]


# ---------------------------------------------------------------------------
# Server and loading

def _set(state, model="", message="", context=0):
    status.update(state=state, model=model, message=message, context=context, since=time.monotonic())


def _ensure_loaded(model, context_length):
    """Start the server and load ``model``. Returns the loaded context length."""
    models = _server_models()
    if models is None:
        _set("starting", model)
        _lms("server", "start", timeout=60)
        for _ in range(40):
            models = _server_models()
            if models is not None:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("the server did not start")

    entry = next((m for m in models if m.get("id") == model), None)
    if entry is None:
        raise RuntimeError(f"model '{model}' is not installed")
    max_ctx = int(entry.get("max_context_length") or context_length)
    wanted = min(context_length, max_ctx)
    loaded_ctx = int(entry.get("loaded_context_length") or 0)
    if entry.get("state") == "loaded" and loaded_ctx >= min(MIN_CONTEXT, wanted):
        return loaded_ctx  # use it as it is: reloading costs more than it gains

    _set("loading", model)
    # Free the memory of other loaded LLMs first (one big model at a time).
    for m in models:
        if m.get("state") == "loaded" and m.get("type") in ("llm", "vlm"):
            try:
                _lms("unload", m["id"], timeout=60)
            except Exception:
                pass
    _lms("load", model, "--context-length", str(wanted), "--gpu", "max", "--parallel", "1", "-y",
         timeout=900)
    return wanted


def prepare(model, context_length):
    """Make sure the model is loaded (blocking; thread-safe).

    Returns (context_length, None) or (0, error message).
    """
    if not model:
        return 0, "No local model selected."
    with _lock:
        try:
            ctx = _ensure_loaded(model, context_length)
        except subprocess.TimeoutExpired:
            _set("error", model, "LM Studio did not respond in time.")
            return 0, "LM Studio: did not respond in time."
        except Exception as exc:
            _set("error", model, str(exc))
            return 0, f"LM Studio: {exc}"
        _set("ready", model, context=ctx)
    _poll()
    return ctx, None


def preload(model, context_length):
    """Load the model in the background (when it is chosen or Blender starts)."""
    if model and not _lock.locked():
        threading.Thread(target=prepare, args=(model, context_length),
                         daemon=True, name="lmstudio-load").start()


def busy():
    return status["state"] in ("starting", "loading")


def unload(model):
    """Unload the model to free its memory (blocking; call from a thread)."""
    with _lock:
        try:
            _lms("unload", model, timeout=60)
        except Exception as exc:
            _set("error", model, str(exc))
            return
        _set("idle", model)
    _poll()


def unload_async(model):
    threading.Thread(target=unload, args=(model,), daemon=True, name="lmstudio-unload").start()


# ---------------------------------------------------------------------------
# Live status (load state and memory), polled in a background thread

def _lmstudio_ram():
    """Physical memory of LM Studio's model workers (MLX memory is not in RSS; footprint sees it)."""
    try:
        pids = subprocess.run(["pgrep", "-f", "lmstudio/.internal/utils/node"], capture_output=True,
                              text=True, timeout=5).stdout.split()
    except Exception:
        return 0
    total = 0
    for pid in pids:
        try:
            out = subprocess.run(["footprint", "-p", pid], capture_output=True, text=True, timeout=5).stdout
        except Exception:
            continue
        m = re.search(r"phys_footprint:\s*([\d.]+)\s*([KMG]B)", out)
        if m:
            total += float(m.group(1)) * {"KB": 1024, "MB": 1024 ** 2, "GB": GB}[m.group(2)]
    return int(total)


def _poll():
    global _polling
    try:
        models = _server_models()
        loaded = {}
        for m in models or []:
            if m.get("state") == "loaded" and m.get("type") in ("llm", "vlm"):
                loaded[m["id"]] = int(m.get("loaded_context_length") or 0)
        live.update(server=models is not None, loaded=loaded,
                    ram=_lmstudio_ram() if loaded else 0, polled=time.monotonic())
        # Unloaded elsewhere (LM Studio app, TTL): no longer "ready".
        if status["state"] == "ready" and status["model"] not in loaded:
            _set("idle", status["model"])
    finally:
        _polling = False


def poll_if_due():
    """Refresh ``live`` in the background at most every POLL_INTERVAL seconds."""
    global _polling
    if _polling or time.monotonic() - live["polled"] < POLL_INTERVAL:
        return
    _polling = True
    threading.Thread(target=_poll, daemon=True, name="lmstudio-poll").start()


# ---------------------------------------------------------------------------
# Claude Code settings for local models

def cli_args(system_prompt):
    """Replace Claude Code's own prompt and built-in tools: only the Blender tools remain."""
    return ["--bare", "--tools", "", "--system-prompt", system_prompt]


# Local effort: LM Studio maps Claude Code's adaptive thinking to the model's reasoning mode, but
# hardly differentiates effort levels; "off" disables reasoning (by far the fastest).
LOCAL_EFFORTS = {"off": None, "medium": "medium", "high": "high"}


def env(model, context_length, effort="off"):
    """Environment for the claude CLI so that it uses LM Studio."""
    out = {
        "ANTHROPIC_BASE_URL": HOST,
        "ANTHROPIC_API_KEY": "lmstudio",  # --bare reads only this; LM Studio ignores it
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_SMALL_FAST_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        # Not CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: with --resume it breaks every follow-up
        # message ("Invalid URL", Claude Code 2.1).
        "ENABLE_TOOL_SEARCH": "false",  # tools directly in the request, no extra round trip
        # Compact the chat before it overflows the loaded context.
        "CLAUDE_CODE_MAX_CONTEXT_TOKENS": str(context_length),
        "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(OUTPUT_TOKENS),
        # Local generation is slow; don't let the CLI give up early.
        "API_TIMEOUT_MS": str(30 * 60 * 1000),
    }
    if LOCAL_EFFORTS.get(effort) is None:
        out.update(MAX_THINKING_TOKENS="0", CLAUDE_CODE_DISABLE_THINKING="1",
                   CLAUDE_CODE_DISABLE_ADAPTIVE_THINKING="1")
    return out

# SPDX-License-Identifier: GPL-3.0-or-later
"""Chat state, main-thread timer, tool dispatch and chat history."""

import base64
import json
import os
import queue
import re
import sys
import tempfile
import threading
import time
import traceback
import uuid

import bpy

from . import attachments as att_mod
from . import autosave, blender_tools, claude_cli, lessons, lmstudio, mcp_server, memory, skills
from .i18n import tr
from .preferences import get_prefs

TIMER_INTERVAL = 0.1
MAX_SAVED_CHATS = 50

SYSTEM_PROMPT = """\
You are a Blender {version} assistant in the sidebar of the 3D viewport. You change the user's scene \
directly with the tools of the MCP server "blender":
- execute_python(code, description): run bpy code in the running Blender; one undo step per call. \
bpy, bmesh, math, mathutils (Vector, Matrix, Euler), os, sys, re, json, time, np are imported. \
Plain values (numbers, strings, lists, vectors) and your functions stay for the next call of this chat; \
Blender data (objects, materials) does not: look it up again by name. print() returns values to you. \
Skill helpers are predefined: T (checks), M (photo match), V (render variants), api (Render Studio). \
Never call bpy.ops.render.render yourself (it freezes Blender): use render_preview or the render queue.
- get_scene_info / get_object_info(name): inspect the scene or one object in detail.
- get_viewport_screenshot: see the viewport as the user sees it. render_preview(camera): quick camera render.
- ask_user(question, options): ask with answer buttons. remember(note, scope): save a lasting preference.
- save_lesson(title, rule, error, solution): after fixing an error, record what worked.
- Tools of other add-ons may follow (e.g. studio_* from Render Studio). For their tasks use them \
instead of writing code.

How to work:
- Act, don't explain how. For a change, call execute_python right away; don't paste code into the chat.
- Small, focused steps. After visible changes, check the result (screenshot, or print values).
- Prefer bpy.data and direct property access over bpy.ops. Reuse existing materials/objects \
instead of creating duplicates. Never delete or overwrite user data or files without asking.
- If something is unclear or there are real alternatives, call ask_user (one question, 2-5 short options) \
instead of guessing or asking in plain text.

Context in user messages (added automatically, not typed by the user):
- [Blender context] = current scene state; [Selection] = selected objects – "this/these/die Auswahl" \
means them. "[… unchanged]" = same as before; "[… only changes]" lists just the objects that changed, \
all others are as last described.
- [Skill: …] = task instructions picked by the user; follow them. [Note] = info from the add-on, e.g. undone steps.
- [Attachment: name – path] = attached file; import non-image files with execute_python via the path.
- The user may send more messages while you work; take them into account.

Answers: the panel is narrow plain text. Be brief: 1-3 sentences on what you did or found, no markdown \
tables or headings. Reply in the language of the user's latest message (also tool descriptions and \
ask_user questions/options).
If your answer changed the scene, end it with one extra last line "Version: <one word>" naming the \
change in the user's language (e.g. "Version: Beize"). The add-on shows it as the version name.
"""

LOCAL_PREFIX = """\
Use the tools by calling them, never by writing JSON or code in your answer. Call one tool at a time \
and wait for its result. Run each change only once.
Screenshots are slow on this computer: check results by printing values in execute_python; take a \
screenshot only when the user asks about the look or you really need to see it.
Never open windows, editors, dialogs or renders with a window (no window_new, no 'INVOKE_DEFAULT').

"""

# Chat state (module level).
messages = []        # {"role": "user"|"assistant"|"tool"|"error"|"info", "text": str, "attachments": [...]}
pending = []         # mcp_server.Task objects waiting for code approval
active_skills = []   # checked skills (names); they stay checked until unchecked
_skills_sent = set() # skills whose instructions were already sent in this chat
notes = []           # hints for Claude added to the next message (e.g. undone steps)
_step = 0            # last undo marker used for Claude's code steps
history_rev = 0      # bumped when old messages change (undo, step links)
questions = []       # mcp_server.Task objects from ask_user waiting for an answer
queued = []          # user messages sent while Claude works, answered after the current turn
attachments = []     # attachments for the next message (dicts from attachments.make)
session_id = None
current_run = None
streaming_text = ""
usage = {}           # last rate_limit_info from the CLI (+ "updated")
_events = queue.Queue()
_prompt_queue = []   # prompts from the UI, sent by the timer
_followups = []      # (content, uid) to send once the current process has ended
_dirty = True
_started = 0.0
_chats = None
tick_hooks = []      # UI callbacks run on every timer tick
_screenshot_warned = False
pending_title = ""    # name typed for a chat that has no id yet
# What the model is doing right now (shown in the chat while it works).
activity = {"kind": "idle", "detail": "", "chars": 0, "since": 0.0}
context = {"used": 0, "window": 0}  # tokens in the current chat / context window of the model
compacting = False   # a /compact run is active
lessons_count = 0    # lessons of the current model (shown in the sidebar)
_turn = {"start": 0.0, "util": None, "code_calls": 0}  # for the per-answer statistics
COMPACT_COMMAND = "/compact"
_last_sent = {}      # kind -> hash of the context last sent in this session (to skip repeats)
login_needed = False  # the CLI's login is missing or expired (the panel shows a Log in button)
_login_opened = False  # the login window was opened once on its own already
_login_check = {"thread": None, "result": None, "next": 0.0}  # background `claude auth status`
LOGIN_POLL = 3.0      # seconds between checks while logged out and the login window is open


def mark_dirty():
    global _dirty
    _dirty = True


def add_message(role, text, **extra):
    text = (text or "").strip()
    if not text and not extra.get("attachments"):
        return
    # Merge consecutive assistant text chunks into one block.
    if role == "assistant" and messages and messages[-1]["role"] == "assistant":
        messages[-1]["text"] += "\n\n" + text
    else:
        messages.append({"role": role, "text": text, **extra})
    mark_dirty()


def is_busy():
    return current_run is not None


def elapsed():
    return int(time.monotonic() - _started) if current_run else 0


def workdir():
    try:
        path = bpy.utils.extension_path_user(__package__, path="", create=True)
    except Exception:
        path = os.path.join(tempfile.gettempdir(), "claude_blender")
    os.makedirs(path, exist_ok=True)
    return path


def _write_json(name, data):
    path = os.path.join(workdir(), name)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def _read_json(name, default):
    try:
        with open(os.path.join(workdir(), name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Attachments

def add_attachment(path):
    att = att_mod.make(path)
    if any(a["path"] == att["path"] for a in attachments):
        return att
    attachments.append(att)
    mark_dirty()
    return att


def remove_attachment(index):
    if 0 <= index < len(attachments):
        attachments.pop(index)
        mark_dirty()


# ---------------------------------------------------------------------------
# Sending

def queue_prompt(text, skill_names=()):
    """Called from UI code (main thread). Answers an open question or queues a message
    (with extra skills, e.g. from a Studio tab).

    The actual send happens in the timer, with a clean context for the screenshot.
    """
    text = (text or "").strip()
    if questions and text and not attachments and not skill_names:
        answer_question(0, text)
        return True
    if not text and not attachments:
        return False
    _prompt_queue.append((text, list(skill_names)))
    return True


# Wishes sent from a Studio tab: tab -> index in `messages` where its answer starts.
studio_prompts = {}


def studio_prompt(tab, text):
    """Send a wish typed in a Studio tab (Render, Light, Material) with that tab's skills."""
    names = [s["name"] for s in skills.list_skills(workdir()) if s.get("studio_tab") == tab]
    if not queue_prompt(text, names):
        return False
    studio_prompts[tab] = len(messages)
    return True


def studio_reply(tab):
    """("working" | "done", first line of the answer) for the last wish from `tab`, or (None, "")."""
    start = studio_prompts.get(tab)
    if start is None or start > len(messages):
        return None, ""
    answer = next((m for m in reversed(messages[start:]) if m["role"] == "assistant"), None)
    if is_busy():
        return "working", activity_text()
    if answer is None:
        return None, ""
    line = next((l for l in plain_text(answer["text"]).splitlines()
                 if l.strip() and not l.startswith("Version:")), "")
    return "done", line.strip()


def is_local(prefs=None):
    prefs = prefs or get_prefs()
    return bool(prefs and prefs.backend == 'LOCAL')


# ---------------------------------------------------------------------------
# Tools from other add-ons
#
# Any add-on can offer tools to the chat with a module named "claude_tools" that has
#     def get_tools():  return [{"name": "studio_light_setup", "description": "...",
#                               "inputSchema": {...}, "handler": func, "label": "Set up lights"}]
# handler(arguments: dict) runs on Blender's main thread and returns a text, or a dict
# {"text": str, "image_png": bytes} (an image for the model to look at). Raising an
# exception returns the error to the model. The list is read again before every run.

_ext_tools = {}


def extension_tools():
    tools = []
    for name, module in list(sys.modules.items()):
        if name.endswith(".claude_tools") and name != __name__ and callable(getattr(module, "get_tools", None)):
            try:
                tools.extend(t for t in module.get_tools() if t.get("name") and callable(t.get("handler")))
            except Exception:
                traceback.print_exc()
    return tools


def _refresh_extension_tools():
    _ext_tools.clear()
    builtin = {t["name"] for t in mcp_server.TOOLS}
    for tool in extension_tools():
        if tool["name"] not in builtin:
            _ext_tools[tool["name"]] = tool
    mcp_server.extra_tools[:] = [
        {"name": t["name"], "description": t.get("description", ""),
         "inputSchema": t.get("inputSchema") or {"type": "object", "properties": {}}}
        for t in _ext_tools.values()]


# Tool calls that wait for something (e.g. the render queue) without blocking Blender:
# [(task, check, deadline)]; check() returns None while waiting, else the result text.
_waiting = []
WAIT_LIMIT = 2 * 60 * 60


def _run_extension_tool(tool, args, task=None):
    result = tool["handler"](args or {})
    if isinstance(result, dict) and callable(result.get("wait")) and task is not None:
        set_activity("tool_running", result.get("label") or tr("waiting…"))
        _waiting.append((task, result["wait"], time.monotonic() + WAIT_LIMIT))
        return mcp_server.DEFERRED
    if isinstance(result, dict):
        content = []
        if result.get("text"):
            content.append({"type": "text", "text": str(result["text"])})
        if result.get("image_png"):
            content.append({"type": "image", "mimeType": "image/png",
                            "data": base64.b64encode(result["image_png"]).decode("ascii")})
        return {"content": content or [{"type": "text", "text": "Done."}], "isError": False}
    return mcp_server.text_result(str(result) if result is not None else "Done.")


def model_label(prefs=None):
    """Name of the model in use, for texts like "Sonnet is working"."""
    prefs = prefs or get_prefs()
    if prefs is None:
        return "Claude"
    if is_local(prefs):
        return lmstudio.display_name(prefs.local_model) if prefs.local_model else tr("Local model")
    from .preferences import MODELS
    return next((name for key, name, _d in MODELS if key == prefs.model), "Claude")


def set_activity(kind, detail=""):
    if activity["kind"] != kind or activity["detail"] != detail:
        activity.update(kind=kind, detail=detail, chars=0, since=time.monotonic())
        mark_dirty()


def activity_text():
    """Short description of what the model is doing, e.g. 'writing · 120 tokens · 14 tok/s'."""
    kind, detail = activity["kind"], activity["detail"]
    tokens = activity["chars"] // 4  # rough estimate while streaming
    secs = max(0.1, time.monotonic() - activity["since"])
    rate = f" · {tokens / secs:.0f} tok/s" if tokens > 20 else ""
    if lmstudio.busy():
        return tr("loading the model in LM Studio…")
    if kind == "compacting":
        return tr("summarizing the chat…")
    if kind == "thinking":
        return tr("thinking… {} tokens").format(tokens) + rate
    if kind == "writing":
        return tr("writing… {} tokens").format(tokens) + rate
    if kind == "tool_call":
        if detail == "execute_python":
            return tr("writing code… {} tokens").format(tokens) + rate
        return tr("preparing {}…").format(detail)
    if kind == "tool_running":
        return detail
    if kind == "waiting":
        return tr("waiting for you")
    if kind == "rendering":
        return tr("rendering a preview in the background…")
    if is_local():
        return tr("reading the prompt…")  # prompt processing: the slow part on a local model
    return tr("thinking…")


def context_fraction():
    return context["used"] / context["window"] if context["window"] else 0.0


def _changed(kind, data):
    """True if this context differs from what was last sent in this session."""
    digest = hash(data)
    if session_id and _last_sent.get(kind) == digest:
        return False
    _last_sent[kind] = digest
    return True


def _scene_text(header, objects, total, max_objects):
    """Full scene summary on the first message, afterwards only what changed (saves tokens)."""
    header_text = "\n".join(header)
    previous = _last_sent.get("objects") if session_id else None
    current = dict(objects)
    _last_sent["objects"] = current
    if previous is None:
        lines = [line for _name, line in objects] + blender_tools._more_line(total, max_objects)
        return "[Blender context]\n" + header_text + "\n" + "\n".join(lines)
    changed = [line for name, line in objects if previous.get(name) != line]
    removed = [name for name in previous if name not in current]
    if not changed and not removed:
        if not _changed("scene_header", header_text):
            return "[Blender context unchanged]"
        return "[Blender context – objects unchanged]\n" + header_text
    parts = ["[Blender context – only changes since the last message]", header_text]
    if changed:
        parts.append("Changed or new objects:\n" + "\n".join(changed))
    if removed:
        parts.append("Removed objects: " + ", ".join(removed[:50]))
    return "\n".join(parts)


def model_slug(prefs=None):
    """Folder name for the model's lessons (each model learns separately)."""
    prefs = prefs or get_prefs()
    if prefs is None:
        return "claude"
    if is_local(prefs):
        return lessons.slug(prefs.local_model or "local")
    return "claude-" + prefs.model


def lessons_root(prefs=None):
    prefs = prefs or get_prefs()
    return lessons.project_dir(prefs.lessons_dir if prefs else "", workdir())


def _build_content(text, atts, prefs):
    local = is_local(prefs)
    max_img = prefs.screenshot_max_size if prefs else 1568
    # Automatic screenshots stay in the chat history: keep them small (~800 tokens at 1024 px).
    # On a local model images also cost a lot of prompt processing.
    shot_size = min(max_img, 768 if local else 1024)
    content = []
    if prefs is None or prefs.send_scene_context:
        try:
            max_objects = 25 if local else 80
            with blender_tools.viewport_context():
                header, objects, total = blender_tools.scene_summary_parts(max_objects)
            content.append({"type": "text", "text": _scene_text(header, objects, total, max_objects)})
        except Exception as exc:
            add_message("info", tr("Could not describe the scene: {}").format(exc))
    send_shot = prefs.local_send_screenshot if local else (prefs is not None and prefs.auto_screenshot)
    if local and not lmstudio.has_vision(prefs.local_model):
        send_shot = False
    if send_shot:
        try:
            png = blender_tools.capture_viewport(shot_size)
            if _changed("screenshot", png):
                content.append({"type": "text", "text": "[Viewport screenshot]"})
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": "image/png",
                    "data": base64.b64encode(png).decode("ascii")}})
            else:
                content.append({"type": "text", "text": "[Viewport unchanged]"})
        except Exception as exc:
            global _screenshot_warned
            if not _screenshot_warned:  # say it once, not with every message
                _screenshot_warned = True
                add_message("info", tr("Viewport screenshot not possible: {}").format(exc))
    wm = bpy.context.window_manager
    if (prefs is None or prefs.send_selection) and getattr(wm, "claude_use_selection", True):
        try:
            info = blender_tools.selection_info(max_objects=3 if local else 5, max_chars=600 if local else 1200)
            if info and _changed("selection", info):
                content.append({"type": "text", "text": "[Selection]\n" + info})
            elif info:
                content.append({"type": "text", "text": "[Selection unchanged]"})
        except Exception:
            traceback.print_exc()
    if notes:
        content.append({"type": "text", "text": "[Note]\n" + "\n".join(notes)})
        notes.clear()
    for att in atts:
        try:
            content.extend(att_mod.content_blocks(att, max_img))
        except Exception as exc:
            add_message("error", tr("Could not read attachment '{}': {}").format(att["name"], exc))
    content.append({"type": "text", "text": text or "(see attachment)"})
    return content


def _send(text, extra_skills=()):
    prefs = get_prefs()
    atts = list(attachments)
    attachments.clear()
    chosen = []
    name, rest = skills.split_command(text)
    if name:
        skill = skills.find(workdir(), name)
        if skill:
            text = rest
            chosen.append(skill)
    for name in list(active_skills):
        skill = skills.find(workdir(), name)
        if skill is None:
            active_skills.remove(name)  # file was deleted
        elif all(s["name"] != skill["name"] for s in chosen):
            chosen.append(skill)
    for name in extra_skills:   # e.g. the skills of the Studio tab the wish came from
        skill = skills.find(workdir(), name)
        if skill and all(s["name"] != skill["name"] for s in chosen):
            chosen.append(skill)
    if chosen:
        if not session_id:
            _skills_sent.clear()
        new = [s for s in chosen if s["name"] not in _skills_sent]
        known = [s for s in chosen if s["name"] in _skills_sent]
        prompt = skills.build_multi(new, known, text)
        _skills_sent.update(s["name"] for s in chosen)
    else:
        prompt = text

    msg = {"role": "user", "text": text,
           "attachments": [{"name": a["name"], "kind": a["kind"], "path": a["path"]} for a in atts]}
    if chosen:
        msg["skill"] = ", ".join(s["title"] for s in chosen)
    content = _build_content(prompt, atts, prefs)
    uid = str(uuid.uuid4())

    if current_run is not None:
        # Interjection: same session. Shown as waiting until the CLI picks it up.
        queued.append(dict(msg, uid=uid))
        if not current_run.send(content, uid):
            _followups.append((content, uid))
        mark_dirty()
        return

    messages.append(msg)
    mark_dirty()
    _start_run([(content, uid)], prefs)


def _start_run(outgoing, prefs, single=False):
    global current_run, _started, streaming_text
    local = bool(prefs and prefs.backend == 'LOCAL')
    if local and not prefs.local_model:
        add_message("error", tr("Choose a local model first (LM Studio models in ~/.lmstudio/models)."))
        return
    if not local and not bpy.app.online_access:
        add_message("error", tr("Online access is disabled, but Claude Code needs the internet. Enable "
                                "it in Preferences › System › Network › Allow Online Access."))
        return
    claude_path = claude_cli.find_claude(prefs.claude_path if prefs else "")
    if not claude_path:
        add_message("error", tr("Claude CLI not found. Install Claude Code and log in once by running "
                                "'claude' in a terminal, or set the path in the add-on preferences."))
        return

    if not mcp_server.is_running():
        mcp_server.start()
    _refresh_extension_tools()

    config_path = os.path.join(workdir(), "mcp_config.json")
    fd = os.open(config_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(mcp_server.mcp_config(), f)

    system_prompt = SYSTEM_PROMPT.format(version=bpy.app.version_string)
    if local:
        system_prompt = LOCAL_PREFIX + system_prompt
    if prefs and prefs.custom_instructions.strip():
        system_prompt += "\nAdditional instructions from the user:\n" + prefs.custom_instructions.strip()
    system_prompt += memory.system_prompt_section(workdir())
    if prefs and prefs.learn_from_errors:
        try:
            global lessons_count
            lessons_count = lessons.count(lessons_root(prefs), model_slug(prefs))
            system_prompt += lessons.prompt_section(lessons_root(prefs), model_slug(prefs),
                                                    max_chars=1000 if local else 2000)
        except OSError:
            traceback.print_exc()

    if local:
        model, wanted, effort = prefs.local_model, prefs.local_context, prefs.local_effort

        def prepare(run):  # in the CLI thread: load the model, then size the context to it
            ctx, error = lmstudio.prepare(model, wanted)
            if not error:
                run.env.update(lmstudio.env(model, ctx, effort))
            return error

        kwargs = dict(model=model, env_extra=lmstudio.env(model, wanted, effort), prepare=prepare,
                      local_args=lmstudio.cli_args(system_prompt))
        if lmstudio.LOCAL_EFFORTS.get(effort):
            kwargs["effort"] = lmstudio.LOCAL_EFFORTS[effort]
    else:
        kwargs = dict(model=prefs.model if prefs else "", effort=prefs.effort if prefs else "")
    current_run = claude_cli.ClaudeRun(
        claude_path, _events,
        cwd=workdir(),
        mcp_config_path=config_path,
        system_prompt=system_prompt,
        session_id=session_id,
        extra_args=prefs.extra_args if prefs else "",
        single=single,
        **kwargs,
    )
    for content, uid in outgoing:
        current_run.send(content, uid)
    set_activity("loading" if local else "prompt")
    _turn.update(start=time.monotonic(), util=_session_utilization(), code_calls=0)
    streaming_text = ""
    _started = time.monotonic()
    current_run.start()


def _session_utilization():
    window = ((usage or {}).get("unifiedWindows") or {}).get("five_hour") or {}
    value = window.get("utilization")
    if value is None:
        return None
    value = float(value)
    return value / 100 if value > 1.5 else value


def _record_stats(obj):
    """Duration, tokens and usage share of this answer, kept on the last assistant message."""
    prefs = get_prefs()
    if prefs and not prefs.show_stats:
        return
    target = next((m for m in reversed(messages) if m["role"] == "assistant"), None)
    if target is None or target.get("stats"):
        return
    u = obj.get("usage") or {}
    # usage.input_tokens of a result adds up every model call of the turn (cache reads included),
    # so it is not a useful size; show the context the chat has now and what was written.
    tokens_out = int(u.get("output_tokens") or 0)
    secs = int(time.monotonic() - _turn["start"]) if _turn["start"] else int(obj.get("duration_ms", 0) / 1000)
    parts = [f"{secs}s", tr("{} written").format(_k(tokens_out))]
    if context["used"]:
        parts.append(tr("context {}").format(_k(context["used"])))
    if not is_local(prefs):
        after, before = _session_utilization(), _turn.get("util")
        if after is not None and before is not None and after >= before:
            parts.append(tr("session +{:.1f}%").format((after - before) * 100))
    target["stats"] = " · ".join(parts)


def _k(n):
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def stop():
    if current_run:
        current_run.cancel()
    for task in pending + questions:
        task.finish(mcp_server.text_result("Cancelled: the user stopped the request.", True))
    pending.clear()
    questions.clear()
    for msg in queued:  # keep what the user wrote
        msg.pop("uid", None)
        messages.append(msg)
    queued.clear()
    _followups.clear()
    mark_dirty()


# ---------------------------------------------------------------------------
# Chat history (saved in the extension's user directory)

def list_chats():
    global _chats
    if _chats is None:
        _chats = _read_json("chats.json", [])
        if not isinstance(_chats, list):
            _chats = []
    return _chats


def save_current_chat():
    if not session_id or not messages:
        return
    chats = list_chats()
    entry = next((c for c in chats if c.get("id") == session_id), None)
    if entry is None:
        entry = {"id": session_id}
        chats.insert(0, entry)
    first_user = next((m for m in messages if m["role"] == "user"), None)
    title = (first_user["text"] if first_user and first_user["text"] else
             (first_user["attachments"][0]["name"] if first_user and first_user.get("attachments") else "Chat"))
    global pending_title
    if pending_title and not entry.get("renamed"):
        entry.update(title=pending_title, renamed=True)
        pending_title = ""
    if not entry.get("renamed"):
        entry["title"] = " ".join(title.split())[:70]
    entry.update(updated=time.time(), messages=messages[-400:], context=dict(context))
    prefs = get_prefs()
    if prefs:  # the chat continues with the model it was used with
        entry["model"] = {key: getattr(prefs, key) for key in MODEL_KEYS}
    chats.sort(key=lambda c: -c.get("updated", 0))
    del chats[MAX_SAVED_CHATS:]
    try:
        _write_json("chats.json", chats)
    except OSError:
        traceback.print_exc()


_chat_serial = [0]   # changes with every new/loaded chat: code values are kept per chat


def new_chat():
    global session_id
    if is_busy():
        return
    _chat_serial[0] += 1
    save_current_chat()
    messages.clear()
    attachments.clear()
    session_id = None
    global pending_title
    pending_title = ""
    _last_sent.clear()
    _skills_sent.clear()
    context.update(used=0, window=0)
    _restore_model(DEFAULT_MODEL)  # every new chat starts with the standard model
    mark_dirty()


def load_chat(chat_id):
    global session_id
    if is_busy():
        return
    _chat_serial[0] += 1
    entry = next((c for c in list_chats() if c.get("id") == chat_id), None)
    if entry is None:
        return
    save_current_chat()
    messages[:] = [dict(m) for m in entry.get("messages", [])]
    session_id = chat_id
    _last_sent.clear()
    _skills_sent.clear()
    _restore_model(entry.get("model"))
    context.update((entry.get("context") or {"used": 0, "window": 0}))
    mark_dirty()


def current_title():
    entry = next((c for c in list_chats() if c.get("id") == session_id), None) if session_id else None
    return entry.get("title", "") if entry else pending_title


def set_current_title(title):
    global pending_title
    title = " ".join((title or "").split())[:70]
    if session_id and any(c.get("id") == session_id for c in list_chats()):
        if title:
            rename_chat(session_id, title)
    else:
        pending_title = title
    mark_dirty()


MODEL_KEYS = ("backend", "model", "effort", "local_model", "local_effort")
DEFAULT_MODEL = {"backend": 'CLOUD', "model": 'sonnet', "effort": 'medium'}


def _restore_model(saved):
    prefs = get_prefs()
    if not prefs or not isinstance(saved, dict):
        return
    for key in MODEL_KEYS:  # model before effort: switching the model resets the effort
        if key in saved and getattr(prefs, key) != saved[key]:
            try:
                setattr(prefs, key, saved[key])
            except (TypeError, ValueError):
                pass  # e.g. a model that no longer exists


def rename_chat(chat_id, title):
    title = " ".join((title or "").split())[:70]
    entry = next((c for c in list_chats() if c.get("id") == chat_id), None)
    if entry is None or not title:
        return
    entry.update(title=title, renamed=True)
    try:
        _write_json("chats.json", list_chats())
    except OSError:
        traceback.print_exc()
    mark_dirty()


def delete_chat(chat_id):
    global session_id
    chats = list_chats()
    chats[:] = [c for c in chats if c.get("id") != chat_id]
    _write_json("chats.json", chats)
    if session_id == chat_id and not is_busy():
        messages.clear()
        session_id = None
    mark_dirty()


# ---------------------------------------------------------------------------
# Usage

def load_usage():
    global usage
    usage = _read_json("usage.json", {}) or {}


def _update_usage(info):
    global usage
    if not isinstance(info, dict):
        return
    usage = dict(info, updated=time.time())
    try:
        _write_json("usage.json", usage)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Approval of code

def _run_code_task(task):
    global _step, history_rev
    _step = max(_step, int(bpy.context.scene.get(blender_tools.UNDO_MARKER, 0))) + 1
    prefs = get_prefs()
    _turn["code_calls"] += 1
    autosave.before_risky("before code")
    autosave.note_step(tr("code: {}").format(task.description or "…"))
    output, ok = blender_tools.execute_python(task.code, "Claude: " + (task.description or "Code"), _step,
                                              max_output=3000 if is_local(prefs) else 6000,
                                              chat=_chat_serial[0])
    # Link the step to its action line in the chat (the latest code action without a step).
    for msg in reversed(messages):
        if msg["role"] == "tool" and msg.get("code") and "step" not in msg:
            msg["step"] = _step
            history_rev += 1
            break
    if getattr(task, "from_text", False):  # code the model only wrote: tell it what happened
        notes.append("The user ran the code you wrote in your answer. " + ("Output:\n" if ok else "It failed:\n")
                     + output[-1500:])
    if not ok:
        add_message("error", tr("Code error:") + "\n" + output[-1500:])
        if prefs and prefs.learn_from_errors:
            output += ("\n\n[After you have fixed this error, call save_lesson once with the problem and the "
                       "working solution.]")
    return mcp_server.text_result(output, not ok)


def undo_from(first_step):
    """Undo Claude's code steps from first_step on (main thread, object mode)."""
    global history_rev
    if bpy.context.mode != 'OBJECT':
        return tr("Undo only works in Object Mode.")
    count, problem = blender_tools.undo_claude_steps(first_step)
    if count:
        # Steps newer than the marker that is now in the scene are undone.
        current = int(bpy.context.scene.get(blender_tools.UNDO_MARKER, 0))
        undone = [m for m in messages if m["role"] == "tool" and m.get("step", 0) > current
                  and m.get("step", 0) >= first_step and not m.get("undone")]
        for m in undone:
            m["undone"] = True
        history_rev += 1
        names = ", ".join(m["text"] for m in undone) or tr("Claude's changes")
        add_message("info", tr("Undone: {}").format(names))
        notes.append(f"The user undid your changes: {names}. The scene is back in the state before them.")
    mark_dirty()
    return problem


# ---------------------------------------------------------------------------
# Versions: every code step of the model is a version; the timeline moves
# through them with Blender's undo/redo (the scene marker tells where we are).

_VERSION_LINE = re.compile(r"^[ \t]*Version:[ \t]*(.+?)[ \t]*$", re.M | re.I)


def turns():
    """The chat split into turns: a user message and everything until the next one.
    Answers to ask_user (a user message right after a "question") stay in the turn.
    [{"start", "end" (exclusive), "tools": [msg], "steps": [int], "word": str, "last_assistant": index}]"""
    out, cur = [], None
    for i, m in enumerate(messages):
        if m["role"] == "user" and not (i and messages[i - 1]["role"] == "question"):
            if cur:
                cur["end"] = i
            cur = {"start": i, "end": len(messages), "tools": [], "steps": [], "word": "",
                   "last_assistant": -1}
            out.append(cur)
            continue
        if cur is None:
            cur = {"start": i, "end": len(messages), "tools": [], "steps": [], "word": "", "last_assistant": -1}
            out.append(cur)
        if m["role"] == "tool":
            cur["tools"].append(m)
            if m.get("step"):
                cur["steps"].append(m["step"])
        elif m["role"] == "assistant":
            cur["last_assistant"] = i
            cur["word"] = m.get("version") or cur["word"]
    for t in out:
        if not t["word"] and t["tools"]:
            t["word"] = _fallback_word(t["tools"])
    return out


def _fallback_word(tools):
    """A one-word name when the model gave none: the first longer word of the first code step."""
    for m in tools:
        if m.get("code"):
            for word in re.findall(r"[\wÄÖÜäöüß-]{4,}", m["text"]):
                return word[:1].upper() + word[1:]
    return tr("Change")


def _take_version_word():
    """Moves "Version: <word>" lines of the answers into the messages (not shown as text)."""
    for msg in messages:
        if msg["role"] != "assistant" or "Version:" not in msg["text"] and "version:" not in msg["text"]:
            continue
        found = _VERSION_LINE.findall(msg["text"])
        if found:
            msg["version"] = found[-1].strip(" .\"'")[:20]
            msg["text"] = _VERSION_LINE.sub("", msg["text"]).rstrip()


def versions():
    """One version per turn that changed the scene: [{"step": last step of the turn, "text": word}]."""
    return [{"step": max(t["steps"]), "text": t["word"]} for t in turns() if t["steps"]]


SUMMARY_KINDS = (  # (key, icon, label, tools, keywords in code descriptions)
    ("material", 'MATERIAL', "Material", ("studio_base_material",), r"materi|beiz|farb|colou?r|textur|shader|rauheit|rough|glanz|holz"),
    ("camera", 'CAMERA_DATA', "Camera", ("studio_camera",), r"kamera|camera|\bcam\b|brennweite|focal"),
    ("light", 'LIGHT', "Light", ("studio_light_setup",), r"licht|light|lampe|schatten|shadow"),
    ("world", 'WORLD', "World", ("studio_hdri_setup", "studio_environment_bake"), r"hdri|world|umgebung|himmel|sky"),
    ("render", 'RENDER_STILL', "Render", ("render_preview", "studio_render_queue"), r"render"),
    ("look", 'HIDE_OFF', "Looked", ("get_viewport_screenshot", "get_scene_info", "get_object_info",
                                    "studio_context"), r"$^"),
    ("object", 'MESH_DATA', "Objects", (), r""),
)


def summarize(tools):
    """Counts per area for the icon row under an answer: [(icon, label, count)], steps, undone?"""
    counts = {}
    for m in tools:
        name, text = m.get("tool", ""), m["text"].lower()
        for key, _icon, _label, names, pattern in SUMMARY_KINDS:
            if name in names or (m.get("code") and pattern and re.search(pattern, text)) \
                    or (key == "object" and m.get("code")):
                counts[key] = counts.get(key, 0) + 1
                break
    rows = [(icon, tr(label), counts[key]) for key, icon, label, _n, _p in SUMMARY_KINDS if key in counts]
    steps = sum(1 for m in tools if m.get("step"))
    undone = any(m.get("undone") for m in tools)
    return rows, steps, undone


def current_version():
    try:
        return int(bpy.context.scene.get(blender_tools.UNDO_MARKER, 0))
    except Exception:
        return 0


def goto_version(step):
    """Move the scene to the state right after ``step`` (0 = before the first step)."""
    global history_rev
    if is_busy():
        return tr("Wait until the model is done.")
    if bpy.context.mode != 'OBJECT':
        return tr("Undo only works in Object Mode.")
    current = current_version()
    problem = ""
    if step < current:
        _count, problem = blender_tools.undo_claude_steps(step + 1)
    elif step > current:
        problem = blender_tools.redo_claude_steps(step)
    now = current_version()
    for m in messages:
        if m["role"] == "tool" and m.get("step"):
            m["undone"] = m["step"] > now
    history_rev += 1
    if now != current:
        names = ", ".join(m["text"] for m in messages if m["role"] == "tool" and m.get("step") == now) or "–"
        notes.append(f"The user moved the scene to version {now} ({names}); later steps are undone"
                     if now < current else f"The user restored the scene to version {now} ({names}).")
    mark_dirty()
    return problem


def approve(index, accept):
    if not 0 <= index < len(pending):
        return
    task = pending.pop(index)
    if task.done:
        return
    if accept:
        task.finish(_run_code_task(task))
    else:
        add_message("info", tr("Rejected: {}").format(task.description or tr("Code")))
        task.finish(mcp_server.text_result(
            "The user rejected this code. Do not run it again; ask what they want instead.", True))
    mark_dirty()


# ---------------------------------------------------------------------------
# Questions from Claude (ask_user)

def toggle_option(index, option):
    if 0 <= index < len(questions):
        task = questions[index]
        task.selected ^= {option}
        mark_dirty()


def answer_question(index, answer):
    if not 0 <= index < len(questions):
        return
    task = questions.pop(index)
    answer = (answer or "").strip()
    if task.done:
        return
    add_message("question", task.question)
    add_message("user", answer or tr("(no answer)"))
    task.finish(mcp_server.text_result(f"The user answered: {answer}" if answer
                                       else "The user did not choose anything."))
    mark_dirty()


# ---------------------------------------------------------------------------
# Tool dispatch (main thread)

def _dispatch(task):
    args = task.arguments
    name = task.name
    prefs = get_prefs()

    if name in _ext_tools:
        tool = _ext_tools[name]
        set_activity("tool_running", tool.get("label") or name)
        try:
            with blender_tools.viewport_context():  # so the add-on's bpy.ops work
                return _run_extension_tool(tool, args, task)
        except Exception as exc:
            add_message("error", tr("Tool error: {}").format(f"{name}: {exc}"))
            return mcp_server.text_result(f"{type(exc).__name__}: {exc}", True)
    set_activity("tool_running", tr(TOOL_LABELS.get(name, name)) if name in TOOL_LABELS else name)
    if name == "get_scene_info":
        with blender_tools.viewport_context():
            return mcp_server.text_result(blender_tools.scene_summary(max_objects=60 if is_local(prefs) else 200))

    if name == "save_lesson":
        if prefs and not prefs.learn_from_errors:
            return mcp_server.text_result("Learning from errors is turned off by the user.")
        try:
            page, new = lessons.save(lessons_root(prefs), model_slug(prefs), model_label(prefs),
                                     str(args.get("title", "")).strip() or "Lesson",
                                     " ".join(str(args.get("rule", "")).split()),
                                     str(args.get("error", "")), str(args.get("solution", "")))
        except OSError as exc:
            return mcp_server.text_result(f"Could not save the lesson: {exc}", True)
        add_message("info", tr("Learned: {}").format(str(args.get("title", "")).strip()))
        global lessons_count
        lessons_count += 1 if new else 0
        return mcp_server.text_result(("Saved" if new else "Updated") + f" lesson '{page}'.")

    if name == "get_object_info":
        obj, text = blender_tools.object_info(str(args.get("name", "")))
        return mcp_server.text_result(text, obj is None)

    if name == "get_viewport_screenshot":
        size = prefs.screenshot_max_size if prefs else 1568
        png = blender_tools.capture_viewport(min(size, 768) if is_local(prefs) else size)
        return {"content": [{"type": "image", "mimeType": "image/png",
                             "data": base64.b64encode(png).decode("ascii")}], "isError": False}

    if name == "render_preview":
        # Rendered by the Studio's render worker: Blender stays usable meanwhile.
        size = max(128, min(1600, int(args.get("max_size") or 800)))
        samples = max(1, min(1024, int(args.get("samples") or 16)))
        scene = bpy.context.scene
        camera = bpy.data.objects.get(args["camera"]) if args.get("camera") else scene.camera
        if camera is None or camera.type != 'CAMERA':
            return mcp_server.text_result(tr("No camera named {}").format(args.get("camera") or ""), True)

        def done(path, error):
            if error:
                task.finish(mcp_server.text_result(error, True))
                return
            with open(path, "rb") as f:
                png = f.read()
            os.remove(path)
            task.finish({"content": [{"type": "image", "mimeType": "image/png",
                                      "data": base64.b64encode(png).decode("ascii")}], "isError": False})
        from .studio import runner as studio_runner
        try:
            studio_runner.request_preview(scene, camera, size, samples, done)
        except Exception as ex:
            return mcp_server.text_result(str(ex), True)
        set_activity("rendering")
        return mcp_server.DEFERRED

    if name == "remember":
        ok, text = memory.remember(workdir(), args.get("note", ""), args.get("scope", "project"))
        if ok:
            add_message("info", tr("Remembered: {}").format(" ".join(str(args.get("note", "")).split())))
        return mcp_server.text_result(text, not ok)

    if name == "ask_user":
        task.question = str(args.get("question", "")).strip()
        opts = args.get("options") or []
        task.options = [str(o).strip() for o in opts if str(o).strip()][:8]
        task.multi_select = bool(args.get("multi_select")) and len(task.options) > 1
        if not task.question:
            return mcp_server.text_result("Parameter 'question' is required.", True)
        if not current_run:
            return mcp_server.text_result("No active chat request.", True)
        questions.append(task)
        set_activity("waiting")
        return mcp_server.DEFERRED

    if name == "execute_python":
        task.code = str(args.get("code", ""))
        task.description = str(args.get("description", "")).strip()
        if not task.code.strip():
            return mcp_server.text_result("Parameter 'code' is required.", True)
        if not current_run:
            return mcp_server.text_result("No active chat request.", True)
        set_activity("tool_running", tr("running: {}").format(task.description or tr("Code")))
        if prefs and prefs.auto_approve:
            return _run_code_task(task)  # reads the prefs itself
        set_activity("waiting")
        pending.append(task)
        mark_dirty()
        return mcp_server.DEFERRED

    return mcp_server.text_result(f"Unknown tool {name}", True)


# ---------------------------------------------------------------------------
# CLI events (main thread)

TOOL_LABELS = {
    "render_preview": "Looked at a render preview",
    "remember": "Remembered something",
    "save_lesson": "Saved a lesson",
    "get_scene_info": "Looked at the scene",
    "get_viewport_screenshot": "Looked at the viewport",
}


def _handle_event(obj):
    global session_id, streaming_text
    kind = obj.get("type")

    if kind == "stream_event":
        event = obj.get("event") or {}
        etype = event.get("type")
        if etype == "content_block_delta":
            delta = event.get("delta") or {}
            dtype = delta.get("type")
            if dtype == "text_delta":
                streaming_text += delta.get("text", "")
                activity["chars"] += len(delta.get("text", ""))
                mark_dirty()
            elif dtype in ("thinking_delta", "input_json_delta"):
                activity["chars"] += len(delta.get("thinking", "") or delta.get("partial_json", ""))
                mark_dirty()
        elif etype == "content_block_start":
            block = event.get("content_block") or {}
            btype = block.get("type")
            if btype in ("thinking", "redacted_thinking"):
                set_activity("thinking")
            elif btype == "text":
                set_activity("writing")
            elif btype == "tool_use":
                name = block.get("name", "")
                set_activity("tool_call", name.split("__")[-1] if name.startswith("mcp__") else name)
        elif etype == "message_start":
            streaming_text = ""
            set_activity("prompt")
            u = (event.get("message") or {}).get("usage") or {}
            used = sum(int(u.get(k) or 0) for k in
                       ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            if used:
                context["used"] = used

    elif kind == "system" and obj.get("subtype") == "status":
        if obj.get("status") == "compacting":
            set_activity("compacting")
        elif obj.get("compact_result") == "failed":
            add_message("error", tr("Summarizing the chat failed: {}").format(obj.get("compact_error") or ""))
        elif obj.get("compact_result") == "success":
            _last_sent.clear()  # the summary drops details: send the full Blender context again
            _skills_sent.clear()

    elif kind == "system" and obj.get("subtype") == "compact_boundary":
        meta = obj.get("compact_metadata") or {}
        pre, post = int(meta.get("pre_tokens") or 0), int(meta.get("post_tokens") or 0)
        if post:
            context["used"] = post
        add_message("info", tr("Chat summarized ({}k → {}k tokens), older details are condensed.").format(
            round(pre / 1000, 1), round(post / 1000, 1)))

    elif kind == "system" and obj.get("subtype") == "init":
        if activity["kind"] in ("loading", "prompt"):
            set_activity("prompt")  # model is ready, the prompt is being processed
        session_id = obj.get("session_id") or session_id
        for server in obj.get("mcp_servers") or []:
            if server.get("name") == "blender" and server.get("status") != "connected":
                add_message("error", tr("Could not connect to Blender (MCP status: {}).").format(server.get("status")))

    elif kind == "assistant":
        for block in (obj.get("message") or {}).get("content") or []:
            if block.get("type") == "text":
                streaming_text = ""
                add_message("assistant", block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool = block.get("name", "")
                tool = tool.split("__")[-1] if tool.startswith("mcp__") else tool
                if tool in ("ToolSearch", "TodoWrite", "ask_user", "remember", "save_lesson"):
                    continue
                inp = block.get("input") or {}
                if tool == "get_object_info":
                    label = tr("Looked at '{}'").format(inp.get("name", ""))
                elif tool == "execute_python":
                    add_message("tool", inp.get("description") or tr("Ran code"), code=True, tool=tool)
                    continue
                elif tool in _ext_tools:
                    label = _ext_tools[tool].get("label") or tool
                else:
                    label = tr(TOOL_LABELS[tool]) if tool in TOOL_LABELS else tool
                add_message("tool", label, tool=tool)

    elif kind == "user" and not obj.get("isReplay"):
        set_activity("prompt")
        content = (obj.get("message") or {}).get("content")
        if isinstance(content, list):
            for block in content:
                if block.get("type") == "tool_result" and block.get("is_error"):
                    text = block.get("content")
                    if isinstance(text, list):
                        text = " ".join(b.get("text", "") for b in text if isinstance(b, dict))
                    # Code errors and rejections are already shown.
                    if text and "rejected" not in text and "Traceback" not in text:
                        add_message("error", tr("Tool error: {}").format(str(text)[:500]))

    elif kind == "user" and obj.get("isReplay"):
        # The CLI picked up a message sent while Claude was working.
        uid = obj.get("uuid")
        for i, msg in enumerate(queued):
            if msg.get("uid") == uid:
                msg = queued.pop(i)
                msg.pop("uid", None)
                if streaming_text.strip():
                    add_message("assistant", streaming_text)
                    streaming_text = ""
                messages.append(msg)
                mark_dirty()
                break

    elif kind == "rate_limit_event":
        _update_usage(obj.get("rate_limit_info"))

    elif kind == "result":
        session_id = obj.get("session_id") or session_id
        if not compacting:
            _record_stats(obj)
        for info in (obj.get("modelUsage") or {}).values():
            if isinstance(info, dict) and info.get("contextWindow"):
                context["window"] = int(info["contextWindow"])
        if is_local() and lmstudio.status.get("context"):
            context["window"] = lmstudio.status["context"]
        if obj.get("is_error"):
            text = str(obj.get("result") or obj.get("subtype") or tr("Unknown error"))
            if claude_cli.is_auth_error(text):
                _set_login_needed(True)
            add_message("error", text)



def _handle_done(rc, stderr):
    global current_run, streaming_text
    run = current_run
    current_run = None
    if streaming_text.strip():  # cancelled mid-answer: keep what was there
        add_message("assistant", streaming_text)
    streaming_text = ""
    for task in pending + questions:
        task.finish(mcp_server.text_result("Request ended.", True))
    pending.clear()
    questions.clear()
    if _followups and not (run and run.cancelled):
        # Messages that arrived while the process was already closing.
        outgoing = list(_followups)
        _followups.clear()
        _start_run(outgoing, get_prefs())
        mark_dirty()
        return
    was_compacting = compacting
    _set_compacting(False)
    set_activity("idle")
    _take_version_word()
    _offer_written_code()
    if run and run.cancelled:
        add_message("info", tr("Stopped"))
    elif rc == -1 and (stderr or "").startswith("LM Studio"):
        add_message("error", stderr)
    elif rc != 0:
        msg = tr("Claude CLI exited with code {}.").format(rc)
        if stderr:
            msg += "\n" + stderr[-1200:]
        low = (stderr or "").lower()
        if claude_cli.is_auth_error(low):
            _set_login_needed(True)
        if "unknown option" in low:
            msg += "\n" + tr("Tip: update Claude Code with 'claude update' in a terminal.")
        add_message("error", msg)
        if login_needed:
            _login_hint()
    elif rc == 0 and not is_local():
        _set_login_needed(False)
    save_current_chat()
    prefs = get_prefs()
    if (rc == 0 and not was_compacting and not (run and run.cancelled) and prefs and prefs.auto_compact
            and context_fraction() >= prefs.compact_at / 100):
        compact(auto=True)
    mark_dirty()


def _set_login_needed(value):
    global login_needed
    login_needed = value


def _login_hint():
    """Say what to do, and open the login window the first time on its own."""
    global _login_opened
    add_message("info", tr("You are logged out of Claude. Log in with the button below "
                           "(it opens the browser), then send your message again."))
    if not _login_opened:
        _login_opened = True
        open_login()


def open_login():
    global _login_opened
    prefs = get_prefs()
    claude_path = claude_cli.find_claude(prefs.claude_path if prefs else "")
    if not claude_path:
        add_message("error", tr("Claude CLI not found – see the README"))
        return False
    try:
        claude_cli.open_login(claude_path)
    except OSError as exc:
        add_message("error", tr("Could not open the login: {}").format(exc))
        return False
    _login_opened = True   # check often now, so the button goes away right after the login
    return True


def check_login():
    """Ask `claude auth status` in a thread; the timer applies the answer (login_needed)."""
    thread = _login_check["thread"]
    if (thread and thread.is_alive()) or is_local():
        return
    prefs = get_prefs()
    claude_path = claude_cli.find_claude(prefs.claude_path if prefs else "")
    if not claude_path:
        return

    def run():
        _login_check["result"] = claude_cli.logged_in(claude_path)

    _login_check["result"] = None
    _login_check["thread"] = threading.Thread(target=run, daemon=True)
    _login_check["thread"].start()


def _poll_login():
    thread = _login_check["thread"]
    if thread and not thread.is_alive():
        _login_check["thread"] = None
        result = _login_check["result"]
        if result is not None and login_needed == result:
            _set_login_needed(not result)
            mark_dirty()
        _login_check["next"] = time.monotonic() + (LOGIN_POLL if _login_opened else 30.0)
    elif login_needed and not thread and time.monotonic() >= _login_check["next"]:
        check_login()


_CODE_FENCE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.S)


def _offer_written_code():
    """A local model sometimes writes the code into its answer instead of calling execute_python:
    offer to run it (Run/Reject card), so the turn is not lost."""
    if not is_local() or _turn["code_calls"] or not messages or messages[-1]["role"] != "assistant":
        return
    blocks = [b.strip() for b in _CODE_FENCE.findall(messages[-1]["text"]) if "bpy" in b or "import" in b]
    for code in blocks[:3]:
        task = mcp_server.Task("execute_python", {})
        task.code, task.description = code, tr("Code from the answer (not run by the model)")
        task.from_text = True
        pending.append(task)
    if blocks:
        add_message("info", tr("The model wrote code instead of running it – run it with the card below."))
    mark_dirty()


def _set_compacting(value):
    global compacting
    compacting = value


def compact(auto=False):
    """Summarize the chat (Claude Code's /compact) so it fits the context again."""
    if is_busy() or not session_id:
        return False
    prefs = get_prefs()
    if auto:
        add_message("info", tr("The chat uses {}% of the context – summarizing it…").format(
            round(context_fraction() * 100)))
    _set_compacting(True)
    _start_run([([{"type": "text", "text": COMPACT_COMMAND}], str(uuid.uuid4()))], prefs, single=True)
    if not current_run:
        _set_compacting(False)
        return False
    set_activity("compacting")
    return True


# ---------------------------------------------------------------------------
# Timer

def _tag_redraw():
    wm = bpy.context.window_manager
    if wm is None:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                for region in area.regions:
                    if region.type == 'UI':
                        region.tag_redraw()


_last_second = -1
_last_poll = [0.0]


def _tick():
    global _dirty, _last_second
    try:
        while _prompt_queue:
            text, extra = _prompt_queue.pop(0)
            try:
                _send(text, extra)
            except Exception:
                add_message("error", tr("Sending failed:") + "\n" + traceback.format_exc(limit=3))

        mcp_server.process_tasks(_dispatch)

        for entry in list(_waiting):
            task, check, deadline = entry
            try:
                text = check() if time.monotonic() < deadline else "Stopped waiting (time limit)."
            except Exception as exc:
                text = "Error while waiting: %s" % exc
            if task.cancelled or text is not None:
                _waiting.remove(entry)
                task.finish(mcp_server.text_result(text or "Cancelled."))
                _dirty = True

        # Drop approvals whose request timed out.
        for lst in (pending, questions):
            for task in [t for t in lst if t.done or t.cancelled]:
                lst.remove(task)
                _dirty = True

        while True:
            try:
                item = _events.get_nowait()
            except queue.Empty:
                break
            if item[0] == "event":
                _handle_event(item[1])
            else:
                _handle_done(item[1], item[2])

        for hook in tick_hooks:
            hook()

        _poll_login()

        if is_local():
            lmstudio.poll_if_due()  # load state and memory for the sidebar
            if lmstudio.live["polled"] != _last_poll[0]:
                _last_poll[0] = lmstudio.live["polled"]
                _dirty = True

        if lmstudio.busy() and int(time.monotonic()) != _last_second:  # loading timer in the sidebar
            _last_second = int(time.monotonic())
            _dirty = True

        if current_run and activity["chars"]:
            _dirty = True  # live token counter

        if current_run and elapsed() != _last_second:  # update the "thinking" timer
            _last_second = elapsed()
            _dirty = True

        if _dirty:
            _dirty = False
            _tag_redraw()
    except Exception:
        traceback.print_exc()
    return TIMER_INTERVAL


# ---------------------------------------------------------------------------
# Display helpers

_MD_PATTERNS = [
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),
    (re.compile(r"__(.+?)__"), r"\1"),
    (re.compile(r"`{3}[\w-]*"), ""),
    (re.compile(r"`([^`]+)`"), r"\1"),
    (re.compile(r"^#{1,6}\s*", re.M), ""),
    (re.compile(r"^(\s*)[-*] ", re.M), r"\1• "),
]


def plain_text(text):
    for pattern, repl in _MD_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def register():
    load_usage()
    crash = autosave.last_crash
    if crash:
        notes.append("Blender did not shut down cleanly last time (probably a crash)"
                     + (f" during: {crash['step']}" if crash.get("step") else "") + ". Be careful with "
                     "heavy operations; the user may have lost recent changes.")
    lmstudio.prefetch()
    prefs = get_prefs()
    if is_local(prefs) and prefs.local_model:
        lmstudio.preload(prefs.local_model, prefs.local_context)
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.5, persistent=True)
    check_login()   # show the Log in button before the first message if Claude is logged out


def unregister():
    if bpy.app.timers.is_registered(_tick):
        bpy.app.timers.unregister(_tick)
    if current_run:
        current_run.cancel()
    for task in list(pending):
        task.finish(mcp_server.text_result("Add-on disabled.", True))
    pending.clear()
    save_current_chat()
    mcp_server.stop()

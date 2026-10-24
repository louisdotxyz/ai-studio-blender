# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal MCP server (streamable HTTP transport, JSON responses only).

Runs in a background thread and only listens on 127.0.0.1. Every request must
carry a random bearer token. Tool calls are never executed in the HTTP thread:
they are queued as ``Task`` objects and handled on Blender's main thread by
``process_tasks`` (called from a ``bpy.app.timers`` callback).

This module does not import ``bpy`` so it can be tested outside Blender.
"""

import json
import queue
import secrets
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SERVER_NAME = "blender"
SERVER_VERSION = "1.0.0"
DEFAULT_PROTOCOL = "2025-06-18"
TASK_TIMEOUT = 2 * 60 * 60  # seconds; ask_user and code review wait for the user this long
TOOL_TIMEOUT_MS = TASK_TIMEOUT * 1000   # the CLI's own limit per tool call (default 300 s)

# Returned by a dispatcher when the task is finished later (e.g. after approval).
DEFERRED = object()

TOOLS = [
    {
        "name": "get_scene_info",
        "description": (
            "Detailed overview of the current Blender scene: file, render settings, "
            "mode, selection, collections and all objects with transforms, "
            "modifiers and materials."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_object_info",
        "description": (
            "Detailed information about one object: transforms, dimensions, mesh "
            "statistics, modifiers with settings, materials, custom properties, "
            "camera/light data."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Exact object name"}},
            "required": ["name"],
        },
    },
    {
        "name": "get_viewport_screenshot",
        "description": (
            "Screenshot of the 3D viewport as the user currently sees it. Use it to "
            "check the result of your changes visually."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "execute_python",
        "description": (
            "Execute Python code inside the running Blender instance. `bpy`, "
            "`bmesh`, `mathutils` and `math` are pre-imported. Use print() to "
            "return values. The code runs on the main thread with a 3D-viewport "
            "context override and creates one undo step. Depending on the user's "
            "settings the user has to approve the code first; if rejected, ask "
            "the user what they want instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python code to run"},
                "description": {
                    "type": "string",
                    "description": "Short description of what the code does, shown to the user (in the user's language)",
                },
            },
            "required": ["code", "description"],
        },
    },
    {
        "name": "render_preview",
        "description": (
            "Render a quick preview from the scene camera (reduced resolution and samples, "
            "the user's render settings otherwise) and return the image. Use it to judge "
            "lighting, materials and composition as they will look in the final render. "
            "Blocks Blender while rendering, so keep it small."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "max_size": {"type": "integer", "description": "Longest side in pixels (default 800, max 1600)"},
                "samples": {"type": "integer", "description": "Max. samples for Cycles/EEVEE (default 16)"},
                "camera": {"type": "string", "description": "Camera to render from (default: the scene camera)"},
            },
        },
    },
    {
        "name": "remember",
        "description": (
            "Save a durable note to the memory file (Markdown). Use it for lasting preferences "
            "and facts the user tells you (units, style, naming, project goals), not for "
            "temporary details. scope 'project' = this .blend file, 'global' = all projects."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "One short sentence, in the user's language"},
                "scope": {"type": "string", "enum": ["project", "global"]},
            },
            "required": ["note", "scope"],
        },
    },
    {
        "name": "save_lesson",
        "description": (
            "After you fixed an error (failed code, wrong result the user pointed out), save what you "
            "learned so you avoid it next time. Not for one-off typos."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short title of the problem, e.g. 'Material color via Principled BSDF'"},
                "rule": {"type": "string", "description": "One sentence: what to do (or avoid) in future"},
                "error": {"type": "string", "description": "The error message or wrong behaviour (short)"},
                "solution": {"type": "string", "description": "What fixed it, with the working code if short"},
            },
            "required": ["title", "rule", "error", "solution"],
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the user a question in the Blender sidebar and wait for the answer. "
            "Use it whenever you need a decision, a preference or missing information "
            "instead of guessing. Offer 2-5 short, concrete options; the user can also "
            "type their own answer. Returns the user's answer as text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question, short and clear, in the user's language"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2-5 short answer options (a few words each)",
                },
                "multi_select": {
                    "type": "boolean",
                    "description": "True if the user may pick several options",
                },
            },
            "required": ["question"],
        },
    },
]


def text_result(text, error=False):
    return {"content": [{"type": "text", "text": text}], "isError": bool(error)}


class Task:
    """A tool call waiting to be handled on Blender's main thread."""

    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments or {}
        self.event = threading.Event()
        self.result = None
        self.cancelled = False
        # Filled by the dispatcher for tasks that wait for the user.
        self.code = ""
        self.description = ""
        self.question = ""
        self.options = []
        self.multi_select = False
        self.selected = set()

    @property
    def done(self):
        return self.event.is_set()

    def finish(self, result):
        if not self.event.is_set():
            self.result = result
            self.event.set()


_tasks = queue.Queue()
_server = None
_thread = None
_token = None


def is_running():
    return _server is not None


def port():
    return _server.server_address[1] if _server else None


def token():
    return _token


def mcp_config():
    """Config dict for ``claude --mcp-config``."""
    return {
        "mcpServers": {
            SERVER_NAME: {
                "type": "http",
                "url": f"http://127.0.0.1:{port()}/mcp",
                "headers": {"Authorization": f"Bearer {_token}"},
                "timeout": TOOL_TIMEOUT_MS,
            }
        }
    }


def start(port_number=0):
    global _server, _thread, _token
    if _server:
        return
    _token = secrets.token_urlsafe(32)
    _server = ThreadingHTTPServer(("127.0.0.1", port_number), _Handler)
    _server.daemon_threads = True
    _thread = threading.Thread(target=_server.serve_forever, name="claude-mcp", daemon=True)
    _thread.start()


def stop():
    global _server, _thread, _token
    if _server:
        _server.shutdown()
        _server.server_close()
    _server = None
    _thread = None
    _token = None
    # Release anything still waiting.
    while True:
        try:
            task = _tasks.get_nowait()
        except queue.Empty:
            break
        task.finish(text_result("Blender add-on was stopped.", True))


def process_tasks(dispatch):
    """Run queued tool calls. Must be called on Blender's main thread."""
    handled = False
    while True:
        try:
            task = _tasks.get_nowait()
        except queue.Empty:
            return handled
        handled = True
        if task.cancelled:
            continue
        try:
            result = dispatch(task)
        except Exception:
            result = text_result(traceback.format_exc(), True)
        if result is not DEFERRED:
            task.finish(result)


# Tools of other add-ons (see core.extension_tools), set before each run.
extra_tools = []


def all_tools():
    return TOOLS + list(extra_tools)


def _resolve_tool(name):
    """The tool for a (possibly sloppy) name: prefixes, case, '-' vs '_', close matches."""
    tools = {t["name"]: t for t in all_tools()}
    if name in tools:
        return tools[name]
    import difflib
    clean = str(name or "").split("__")[-1].strip().lower().replace("-", "_").replace(" ", "_")
    if clean in tools:
        return tools[clean]
    match = difflib.get_close_matches(clean, list(tools), n=1, cutoff=0.75)
    return tools[match[0]] if match else None


def _coerce_arguments(tool, arguments):
    """Small models pass JSON as a string or numbers as text: fix what the schema makes obvious."""
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return {}
    if not isinstance(arguments, dict):
        return {}
    props = (tool.get("inputSchema") or {}).get("properties") or {}
    out = {}
    for key, value in arguments.items():
        spec = props.get(key) or {}
        kind = spec.get("type")
        if isinstance(value, str):
            if kind in ("object", "array"):
                try:
                    value = json.loads(value)
                except ValueError:
                    pass
            elif kind == "integer":
                try:
                    value = int(float(value))
                except ValueError:
                    pass
            elif kind == "number":
                try:
                    value = float(value)
                except ValueError:
                    pass
            elif kind == "boolean":
                value = value.strip().lower() in ("true", "1", "yes", "ja")
        out[key] = value
    return out


def _call_tool(name, arguments):
    tool = _resolve_tool(name)
    if tool is None:
        return text_result(f"Unknown tool: {name}. Available: " + ", ".join(t["name"] for t in all_tools()), True)
    name = tool["name"]
    arguments = _coerce_arguments(tool, arguments)
    missing = [k for k in (tool.get("inputSchema") or {}).get("required") or [] if k not in arguments]
    if missing:
        return text_result(f"Tool {name}: missing argument(s) {', '.join(missing)}.", True)
    task = Task(name, arguments)
    _tasks.put(task)
    if not task.event.wait(TASK_TIMEOUT):
        task.cancelled = True
        return text_result("Timeout: the user did not respond in time.", True)
    return task.result


def _handle_rpc(msg):
    """Handle one JSON-RPC message. Returns a response dict or None for notifications."""
    if not isinstance(msg, dict):
        return _error(None, -32600, "Invalid request")
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}

    if msg_id is None:  # notification (or a client response) – nothing to answer
        return None

    if method == "initialize":
        return _ok(msg_id, {
            "protocolVersion": params.get("protocolVersion") or DEFAULT_PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    if method == "ping":
        return _ok(msg_id, {})
    if method == "tools/list":
        return _ok(msg_id, {"tools": all_tools()})
    if method == "tools/call":
        return _ok(msg_id, _call_tool(params.get("name"), params.get("arguments")))
    if method in ("resources/list", "prompts/list"):
        key = method.split("/")[0]
        return _ok(msg_id, {key: []})
    return _error(msg_id, -32601, f"Method not found: {method}")


def _ok(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


class _Handler(BaseHTTPRequestHandler):
    server_version = "BlenderClaudeMCP/1.0"

    def log_message(self, fmt, *args):  # keep Blender's console quiet
        pass

    def _authorized(self):
        if self.path.split("?", 1)[0].rstrip("/") != "/mcp":
            self._send(404)
            return False
        expected = f"Bearer {_token}"
        if not _token or not secrets.compare_digest(self.headers.get("Authorization", ""), expected):
            self._send(401)
            return False
        return True

    def _send(self, status, body=None):
        data = b"" if body is None else json.dumps(body).encode("utf-8")
        try:
            self.send_response(status)
            if body is not None:
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            if data:
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass  # the CLI gave up on this request (e.g. it was stopped)

    def do_POST(self):
        if not self._authorized():
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"null")
        except (ValueError, json.JSONDecodeError):
            self._send(400, _error(None, -32700, "Parse error"))
            return

        if isinstance(payload, list):
            responses = [r for r in (_handle_rpc(m) for m in payload) if r is not None]
            self._send(200, responses) if responses else self._send(202)
        else:
            response = _handle_rpc(payload)
            self._send(200, response) if response is not None else self._send(202)

    def do_GET(self):
        # No server-initiated SSE stream.
        if self._authorized():
            self._send(405)

    def do_DELETE(self):
        if self._authorized():
            self._send(200)

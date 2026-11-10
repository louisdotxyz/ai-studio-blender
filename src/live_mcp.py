# SPDX-License-Identifier: GPL-3.0-or-later
"""MCP server (stdio) for other Claude apps: Claude Code, Claude Desktop / Cowork.

Started by those apps, not by Blender: ``<python> live_mcp.py``. It reads
~/.config/ai-studio/live.json (port and token of the Blender that was opened last,
written by live.py) on every call and forwards to Blender's Live Connection
(POST http://127.0.0.1:<port>/live). No bpy, no third-party modules.
"""

import json
import os
import sys
import urllib.error
import urllib.request

LIVE_FILE = os.environ.get("AI_STUDIO_LIVE_FILE") or os.path.expanduser("~/.config/ai-studio/live.json")
VERSION = "1.0.0"
INSTRUCTIONS = (
    "Tools for the Blender that is open with the AI Studio add-on (Live Connection). Start with "
    "blender_scene. blender_python runs bpy code in Blender as one undo step (print() returns output; "
    "bpy, bmesh, mathutils, math are imported; `api` is AI Studio's helper module). The user may have to "
    "approve the code in Blender. Never save the .blend file. Objects you replace go into the archive "
    "with api.archive(objects) instead of being deleted, unless the user says to delete them.")

TOOLS = [
    {"name": "blender_status",
     "description": "Is Blender open with the Live Connection? Which file and version.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "blender_scene",
     "description": "Overview of the open scene: objects, collections, materials, cameras, lights.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "blender_object",
     "description": "Details of one object: transform, dimensions, modifiers, materials.",
     "inputSchema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "blender_python",
     "description": "Run Python in the open Blender (one undo step). print() what you need to see.",
     "inputSchema": {"type": "object", "properties": {
         "code": {"type": "string"},
         "description": {"type": "string", "description": "One short line for the user: what it does"}},
         "required": ["code"]}},
    {"name": "blender_screenshot",
     "description": "Picture of the 3D viewport as the user sees it.",
     "inputSchema": {"type": "object", "properties": {
         "max_size": {"type": "integer", "description": "Longest side in pixels (default 1280)"}}}},
    {"name": "blender_image",
     "description": "An image file on this computer (e.g. a render or CAD check image from api.cad_check_views) "
                    "as a picture, scaled to max_size.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "PNG or JPEG file"},
         "max_size": {"type": "integer", "description": "Longest side in pixels (default 1280)"}},
         "required": ["path"]}},
]

OPS = {"blender_status": "ping", "blender_scene": "info", "blender_object": "object",
       "blender_python": "python", "blender_screenshot": "screenshot", "blender_image": "image"}


def call_blender(payload, timeout=7200):
    """One request to the Live Connection. Raises RuntimeError with a sentence for the model."""
    try:
        with open(LIVE_FILE, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        raise RuntimeError("Blender is not open with AI Studio (no ~/.config/ai-studio/live.json). "
                           "Ask the user to open Blender; the Live Connection is on by default.")
    request = urllib.request.Request(
        "http://127.0.0.1:%d/live" % int(info["port"]), data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + info["token"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError("The Live Connection is off in Blender (AI Studio preferences › Chat).")
        raise RuntimeError("Blender answered %d: %s" % (exc.code, exc.read()[:300].decode("utf-8", "replace")))
    except (urllib.error.URLError, OSError) as exc:
        raise RuntimeError("Blender does not answer (%s). Is it still open?" % exc)


def run_tool(name, arguments):
    """MCP tools/call result."""
    if name not in OPS:
        return {"content": [{"type": "text", "text": "Unknown tool " + name}], "isError": True}
    payload = dict(arguments or {}, op=OPS[name])
    try:
        answer = call_blender(payload, timeout=10 if name == "blender_status" else 7200)
    except RuntimeError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    if name in ("blender_screenshot", "blender_image") and answer.get("png"):
        return {"content": [{"type": "image", "data": answer["png"], "mimeType": "image/png"}]}
    text = answer.get("text") or answer.get("output") or answer.get("error") or ""
    if name == "blender_status" and answer.get("ok"):
        text = "Blender %s is open with %s" % (answer.get("version", "?"), answer.get("file") or "an unsaved file")
    if answer.get("file") and name == "blender_python":
        text = (text or "(no output)") + "\n[file: %s]" % answer["file"]
    return {"content": [{"type": "text", "text": text or "(no output)"}], "isError": not answer.get("ok", False)}


def handle(message):
    """The answer to one JSON-RPC message, or None for notifications."""
    method, msg_id = message.get("method"), message.get("id")
    if msg_id is None:
        return None
    params = message.get("params") or {}
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion") or "2025-06-18",
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "ai-studio", "version": VERSION},
                  "instructions": INSTRUCTIONS}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        result = run_tool(params.get("name"), params.get("arguments"))
    elif method == "ping":
        result = {}
    else:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": -32601, "message": "Unknown method " + str(method)}}
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        answer = handle(message)
        if answer is not None:
            sys.stdout.write(json.dumps(answer) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()

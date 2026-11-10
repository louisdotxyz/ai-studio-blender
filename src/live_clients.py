# SPDX-License-Identifier: GPL-3.0-or-later
"""Connect other Claude apps to the Live Connection (setup card).

Both get the MCP server live_mcp.py (run with Blender's own Python, so nothing has to be
installed):
- Claude Code: ``claude mcp add -s user ai-studio -- <python> live_mcp.py``
- Claude Desktop / Cowork: an "ai-studio" entry in claude_desktop_config.json (the app
  reads it at its next start).
What was connected is kept in ~/.config/ai-studio/clients.json; at every start Blender
fixes the command when Blender's Python moved (a new Blender version).
"""

import json
import os
import subprocess
import sys
import tempfile
import threading

NAME = "ai-studio"
STATE_FILE = os.path.expanduser("~/.config/ai-studio/clients.json")
DESKTOP_CONFIG = os.path.expanduser("~/Library/Application Support/Claude/claude_desktop_config.json")
DESKTOP_APP = "/Applications/Claude.app"


def bridge_command():
    return [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_mcp.py")]


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, data, mode=0o600):
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        pass
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".ai-studio-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _remember(key, command):
    state = _read(STATE_FILE)
    state[key] = command
    _write(STATE_FILE, state)


def desktop_installed():
    return os.path.isdir(DESKTOP_APP) or os.path.isfile(DESKTOP_CONFIG)


def desktop_connected():
    servers = _read(DESKTOP_CONFIG).get("mcpServers")
    return isinstance(servers, dict) and NAME in servers


def code_connected():
    return bool(_read(STATE_FILE).get("claude_code"))


def connect_desktop():
    """Add (or update) the "ai-studio" server, keeping everything else in the file."""
    config = _read(DESKTOP_CONFIG)
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        servers = config["mcpServers"] = {}
    command = bridge_command()
    servers[NAME] = {"command": command[0], "args": command[1:]}
    _write(DESKTOP_CONFIG, config)
    _remember("desktop", command)


def connect_code(claude_path):
    """`claude mcp add` in the user scope (all projects). Raises RuntimeError with the CLI's message."""
    command = bridge_command()
    subprocess.run([claude_path, "mcp", "remove", "-s", "user", NAME], capture_output=True, text=True,
                   timeout=60, stdin=subprocess.DEVNULL)
    result = subprocess.run([claude_path, "mcp", "add", "-s", "user", NAME, "--"] + command,
                            capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[-300:] or "claude mcp add failed")
    _remember("claude_code", command)


def refresh(claude_path=None):
    """At start: point connected apps at this Blender's Python again if it changed. In a thread."""
    state = _read(STATE_FILE)
    command = bridge_command()

    def run():
        try:
            if state.get("desktop") and state["desktop"] != command and desktop_connected():
                connect_desktop()
            if state.get("claude_code") and state["claude_code"] != command and claude_path:
                connect_code(claude_path)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            print("AI Studio: could not update the Live Connection for other apps:", exc)

    if state.get("desktop") or state.get("claude_code"):
        threading.Thread(target=run, daemon=True).start()

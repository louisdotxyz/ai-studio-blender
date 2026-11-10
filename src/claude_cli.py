# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs the local Claude Code CLI (``claude -p``) in a background thread.

One process per user message; the conversation continues via ``--resume``.
Output is read as stream-json and pushed onto a queue that the main thread
drains. This module does not import ``bpy``.
"""

import glob
import json
import os
import shlex
import shutil
import subprocess
import threading

# Blender started from the macOS Dock does not inherit the shell's PATH.
_EXTRA_PATHS = [
    "~/.local/bin",
    "~/.claude/local",
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "~/.npm-global/bin",
    "~/.bun/bin",
    "~/.volta/bin",
    "~/bin",
]


def _candidate_dirs():
    dirs = [os.path.expanduser(p) for p in _EXTRA_PATHS]
    # nvm installs, newest first
    dirs += sorted(glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin")), reverse=True)
    return dirs


def search_path():
    parts = os.environ.get("PATH", "").split(os.pathsep)
    for d in _candidate_dirs():
        if d not in parts:
            parts.append(d)
    return os.pathsep.join(p for p in parts if p)


def find_claude(configured=""):
    if configured:
        path = os.path.expanduser(configured)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
        return None
    return shutil.which("claude", path=search_path())


def build_env(claude_path):
    env = dict(os.environ)
    path = search_path()
    claude_dir = os.path.dirname(os.path.realpath(claude_path))
    env["PATH"] = os.pathsep.join([claude_dir, os.path.dirname(claude_path), path])
    # Don't let an outer Claude Code session (if Blender was started from one) leak into the child.
    for name in ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION"):
        env.pop(name, None)
    # Tool calls wait for the user (code approval, ask_user): the CLI's limits are much shorter,
    # also the one for a tool that sends nothing for a while (300 s, "no response or progress").
    from .mcp_server import TOOL_TIMEOUT_MS
    env["MCP_TOOL_TIMEOUT"] = str(TOOL_TIMEOUT_MS)
    env["CLAUDE_CODE_MCP_TOOL_IDLE_TIMEOUT"] = str(TOOL_TIMEOUT_MS)
    # Send the Blender tools directly instead of deferring them behind ToolSearch.
    env["ENABLE_TOOL_SEARCH"] = "false"
    return env


# Words in the CLI's output when the login is missing or has expired.
_AUTH_WORDS = ("failed to authenticate", "oauth", "not logged in", "please run /login",
               "invalid api key", "authentication_error")


def is_auth_error(text):
    low = (text or "").lower()
    return any(word in low for word in _AUTH_WORDS)


def logged_in(claude_path):
    """True/False from ``claude auth status``, None when it can't be told (old CLI, timeout)."""
    try:
        result = subprocess.run([claude_path, "auth", "status"], capture_output=True, text=True,
                                timeout=20, env=build_env(claude_path), stdin=subprocess.DEVNULL)
        return bool(json.loads(result.stdout)["loggedIn"])
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
        return None


INSTALL_COMMAND = {"posix": "curl -fsSL https://claude.ai/install.sh | bash",
                   "win32": "powershell -NoProfile -Command \"irm https://claude.ai/install.ps1 | iex\""}


_CLOSE_WINDOW = """on run argv
\tdelay 1
\ttell application "Terminal"
\t\trepeat with w in windows
\t\t\ttry
\t\t\t\tif tty of selected tab of w is (item 1 of argv) then
\t\t\t\t\tclose w
\t\t\t\t\texit repeat
\t\t\t\tend if
\t\t\tend try
\t\tend repeat
\tend tell
end run
"""


def terminal_script(command, note, closer):
    """The macOS .command file: runs the command, and when it worked closes its own window
    (a second later, after the shell is gone, so Terminal does not ask). On an error the
    window stays open so the message can be read."""
    return ("#!/bin/sh\n%s\nstatus=$?\necho\n"
            "if [ $status -eq 0 ]; then\n"
            "  echo %s\n  sleep 2\n"
            "  nohup /usr/bin/osascript %s \"$(tty)\" >/dev/null 2>&1 &\n  exit 0\nfi\n"
            "echo %s\n" % (command, shlex.quote(note), shlex.quote(closer),
                             shlex.quote("Something went wrong (see above). This window can be closed.")))


def open_terminal(command, note="Done. Back to Blender."):
    """Run a shell command in a new terminal window (the user may have to type there).
    The window closes by itself when the command worked."""
    import sys
    import tempfile
    if sys.platform == "darwin":
        folder = tempfile.gettempdir()
        closer = os.path.join(folder, "ai_studio_close_window.applescript")
        with open(closer, "w", encoding="utf-8") as f:
            f.write(_CLOSE_WINDOW)
        script = os.path.join(folder, "ai_studio_terminal.command")
        with open(script, "w", encoding="utf-8") as f:
            f.write(terminal_script(command, note, closer))
        os.chmod(script, 0o755)
        subprocess.Popen(["open", script])
    elif sys.platform == "win32":
        # /c closes the window; pause only after an error
        subprocess.Popen(["cmd", "/c", "start", "AI Studio", "cmd", "/c", command + " || pause"])
    else:
        terminal = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal")
        if terminal is None:
            raise OSError("No terminal found: run this yourself: " + command)
        subprocess.Popen([terminal, "--", "sh", "-c",
                          command + " || { echo; echo 'Something went wrong (see above).'; read x; }"])


def open_login(claude_path):
    """Run ``claude auth login`` in a terminal window: it opens the browser, and the
    terminal is there if the CLI asks to paste a code."""
    open_terminal(shlex.quote(claude_path) + " auth login",
                  "Logged in. Back to Blender: this window closes by itself.")


def open_update(claude_path):
    open_terminal(shlex.quote(claude_path) + " update", "Updated. Back to Blender: this window closes by itself.")


def open_install():
    import sys
    open_terminal(INSTALL_COMMAND["win32" if sys.platform == "win32" else "posix"],
                  "Installed. Back to Blender and log in there: this window closes by itself.")


class ClaudeRun:
    """One ``claude -p`` process for one or more user messages.

    stdin stays open while Claude works, so messages sent in between
    (interjections) go into the same session. The CLI picks them up at the
    next tool boundary (or starts a new turn) and echoes them back thanks to
    ``--replay-user-messages``. stdin is closed after a ``result`` once every
    sent message has been echoed, which ends the process.
    """

    def __init__(self, claude_path, events, *, cwd, mcp_config_path,
                 system_prompt, session_id=None, model="", effort="", extra_args="",
                 env_extra=None, prepare=None, local_args=None, single=False):
        self.events = events  # queue.Queue of ("event", dict) / ("done", rc, stderr)
        self.cmd = [
            claude_path, "-p",
            "--input-format", "stream-json",
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--replay-user-messages",
            "--mcp-config", mcp_config_path,
            "--strict-mcp-config",
        ]
        if local_args:  # local model: own prompt, no built-in tools (see lmstudio.cli_args)
            self.cmd += ["--allowedTools", "mcp__blender"] + local_args
        else:
            self.cmd += [
                # Only built-in tools that help in Blender; the rest is token overhead.
                "--tools", "Read,WebSearch,WebFetch",
                # Pre-approve web access and reading (renders, datasheets, photos anywhere):
                # in -p mode there is nobody to grant it. Writing stays off.
                "--allowedTools", "mcp__blender,WebSearch,WebFetch,Read",
                # Claude asks questions through the Blender tool ask_user instead.
                "--disallowedTools", "Bash,Edit,Write,NotebookEdit,AskUserQuestion",
                "--append-system-prompt", system_prompt,
            ]
        if session_id:
            self.cmd += ["--resume", session_id]
        if model:
            self.cmd += ["--model", model]
        if effort:
            self.cmd += ["--effort", effort]
        if extra_args:
            self.cmd += shlex.split(extra_args)
        self.cwd = cwd
        self.env = build_env(claude_path)
        if env_extra:  # local model: the CLI talks to LM Studio instead of Anthropic
            self.env.pop("ANTHROPIC_API_KEY", None)
            self.env.update(env_extra)
        self.prepare = prepare  # optional callable(run) run in the thread first; returns an error or None
        self.proc = None
        self.cancelled = False
        self._stderr = []
        self._lock = threading.Lock()
        self._outbox = []          # (content, uid) written once the process has started
        self._unconsumed = set()   # uids sent but not yet echoed by the CLI
        self._stdin_closed = False
        self.single = single  # one message only (e.g. /compact, which the CLI never echoes back)
        self._thread = threading.Thread(target=self._run, name="claude-cli", daemon=True)

    def start(self):
        self._thread.start()

    @property
    def running(self):
        return self._thread.is_alive()

    def send(self, content, uid):
        """Send a user message (uid: UUID string). False if the run is already ending."""
        with self._lock:
            if self._stdin_closed or self.cancelled:
                return False
            if self.proc is None:
                self._outbox.append((content, uid))
                return True
            return self._write(content, uid)

    def _write(self, content, uid):
        # Caller holds the lock.
        message = {"type": "user", "uuid": uid, "message": {"role": "user", "content": content}}
        try:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            return False
        self._unconsumed.add(uid)
        return True

    def _close_stdin(self):
        # Caller holds the lock.
        if not self._stdin_closed:
            self._stdin_closed = True
            try:
                self.proc.stdin.close()
            except (OSError, ValueError):
                pass

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except OSError:
                pass

    def _read_stderr(self):
        for line in self.proc.stderr:
            self._stderr.append(line)
            del self._stderr[:-40]

    def _run(self):
        if self.prepare is not None:
            error = self.prepare(self)
            if error or self.cancelled:
                self.events.put(("done", -1 if error else 0, error or ""))
                return
        try:
            proc = subprocess.Popen(
                self.cmd,
                cwd=self.cwd,
                env=self.env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            self.events.put(("done", -1, f"Could not start the Claude CLI: {exc}"))
            return

        err_thread = threading.Thread(target=self._read_stderr, args=(), daemon=True)
        with self._lock:
            self.proc = proc
            err_thread.start()
            for content, uid in self._outbox:
                self._write(content, uid)
            self._outbox.clear()
            if not self._unconsumed or self.single:
                self._close_stdin()

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                self._stderr.append(line + "\n")
                continue
            kind = obj.get("type")
            if kind == "user" and obj.get("isReplay"):
                with self._lock:
                    self._unconsumed.discard(obj.get("uuid"))
            elif kind == "result":
                with self._lock:
                    if not self._unconsumed:
                        self._close_stdin()
            self.events.put(("event", obj))

        rc = proc.wait()
        err_thread.join(timeout=2)
        self.events.put(("done", rc, "".join(self._stderr).strip()))

# SPDX-License-Identifier: GPL-3.0-or-later
"""Memory as Markdown files.

- Project memory: next to the .blend file, e.g. Chair.blend -> Chair.claude.md
- Global memory:  in the extension's user directory, applies to all projects
Both are plain Markdown and can be edited in any editor. Their content is
added to Claude's system prompt on every message.
"""

import os
import time

import bpy

MAX_CHARS = 8000

PROJECT_HEADER = "# Claude memory: {name}\n\nGuidelines and notes for this project. Claude reads this file with every message.\n\n"
GLOBAL_HEADER = "# Claude memory (all projects)\n\nGeneral guidelines, e.g. units, style, naming. Claude reads this file with every message.\n\n"


def project_path():
    """<main folder>/<project>.claude.md – the same for 2.2_now.blend, its backups in autosave/
    and archived versions (named like the project, without _now and time stamps)."""
    if not bpy.data.filepath:
        return None
    from . import autosave
    folder = autosave.main_folder(bpy.data.filepath)
    return os.path.join(folder, autosave.project_name(bpy.data.filepath) + ".claude.md")


def global_path(workdir):
    path = os.path.join(workdir, "memory.md")
    legacy = os.path.join(workdir, "gedaechtnis.md")  # name used by pre-1.0 builds
    if not os.path.exists(path) and os.path.isfile(legacy):
        os.replace(legacy, path)
    return path


def _read(path):
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
    except OSError:
        return ""
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n… (truncated)"
    return text


def ensure_file(path, header):
    if not os.path.isfile(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(header)
    return path


def system_prompt_section(workdir):
    parts = []
    glob = _read(global_path(workdir))
    if glob:
        parts.append("Global memory (all projects):\n" + glob)
    proj = _read(project_path())
    if proj:
        parts.append(f"Project memory ({os.path.basename(project_path())}):\n" + proj)
    if not parts:
        return ""
    return ("\n\nMemory files (Markdown, maintained by the user and by you via the remember tool). "
            "Follow them:\n\n" + "\n\n".join(parts))


def remember(workdir, note, scope):
    """Append a note. Returns (ok, message)."""
    note = " ".join((note or "").split())
    if not note:
        return False, "Empty note."
    if scope == "project":
        path = project_path()
        if path is None:
            return False, ("The .blend file is not saved yet, so there is no project memory. "
                           "Ask the user to save the file, or use scope 'global'.")
        ensure_file(path, PROJECT_HEADER.format(name=os.path.basename(bpy.data.filepath)))
    else:
        path = ensure_file(global_path(workdir), GLOBAL_HEADER)
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"- {note} _(Claude, {time.strftime('%d.%m.%Y')})_\n")
    return True, f"Saved to {os.path.basename(path)}."

# SPDX-License-Identifier: GPL-3.0-or-later
"""Skills: reusable instructions as Markdown files (/render, /material, ...).

Skills live in <extension user dir>/skills/<name>.md with a small front matter:

    ---
    title: CAM
    description: Camera, framing, depth of field
    icon: CAMERA_DATA
    studio_tab: RENDER        (optional: used by the chat while you come from that Studio tab)
    chat: yes                 (optional: also in the chat's skill row although bound to a tab)
    ---
    Instructions for Claude ...

The defaults in skills_default/ are written once; after that the files belong to the
user (edit them or add new ones, the skill row picks them up automatically).
"""

import os
import re

# Skills that ship with the add-on (skills_default/*.md): installed once each, then they
# belong to the user – never rewritten, and not brought back after being deleted.
DEFAULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills_default")
INSTALLED = ".installed.json"
_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)


def skills_dir(workdir):
    path = os.path.join(workdir, "skills")
    os.makedirs(path, exist_ok=True)
    install_defaults(path)
    return path


_checked = set()


def install_defaults(folder):
    """Write the shipped skills that were never installed into `folder` (once per session check)."""
    if folder in _checked:
        return
    _checked.add(folder)
    import json
    record = os.path.join(folder, INSTALLED)
    try:
        with open(record, encoding="utf-8") as f:
            installed = set(json.load(f))
    except (OSError, ValueError):
        installed = set()
    try:
        names = [n[:-3] for n in os.listdir(DEFAULTS_DIR) if n.endswith(".md")]
    except OSError:
        return
    for name in sorted(set(names) - installed):
        target = os.path.join(folder, name + ".md")
        if not os.path.exists(target):
            with open(os.path.join(DEFAULTS_DIR, name + ".md"), encoding="utf-8") as src, \
                    open(target, "w", encoding="utf-8") as dst:
                dst.write(src.read())
        installed.add(name)
    try:
        with open(record, "w", encoding="utf-8") as f:
            json.dump(sorted(installed), f)
    except OSError:
        pass


def _parse(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    meta = {}
    match = _FRONT.match(text)
    if match:
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                meta[key.strip().lower()] = value.strip()
        text = text[match.end():]
    name = os.path.splitext(os.path.basename(path))[0]
    return {
        "icon": meta.get("icon") or default_icon(name, meta.get("title") or name),
        "name": name,
        "title": meta.get("title") or name.capitalize(),
        "description": meta.get("description", ""),
        # Studio tab whose Claude chat uses this skill (RENDER, LIGHT, MATERIAL, QUEUE) …
        "studio_tab": meta.get("studio_tab", "").upper(),
        # … and whether it is in the chat's skill row anyway (else only via its tab)
        "in_chat": meta.get("chat", "").lower() in {"yes", "true", "1"} or not meta.get("studio_tab"),
        "body": text.strip(),
        "path": path,
    }


ICON_KEYWORDS = (  # first match wins: (keywords in name/title, Blender icon)
    (("sort", "clean", "ordn", "aufräum"), 'SORTALPHA'),
    (("foto", "photo", "bild", "image", "ref"), 'IMAGE_DATA'),
    (("cam", "kamera"), 'CAMERA_DATA'),
    (("hdri", "world", "umgebung", "environment", "env"), 'WORLD'),
    (("light", "licht", "lighting"), 'LIGHT'),
    (("material", "beiz", "shader", "textur"), 'MATERIAL'),
    (("render",), 'RENDER_STILL'),
    (("anim",), 'RENDER_ANIMATION'),
    (("model", "mesh", "geometr"), 'MESH_DATA'),
)
ICON_CHOICES = ('SHADERFX', 'SORTALPHA', 'IMAGE_DATA', 'CAMERA_DATA', 'WORLD', 'LIGHT', 'MATERIAL',
                'RENDER_STILL', 'RENDER_ANIMATION', 'MESH_DATA', 'MODIFIER', 'BRUSH_DATA', 'TEXT', 'CHECKMARK')


def default_icon(name, title):
    text = (name + " " + title).lower()
    for words, icon in ICON_KEYWORDS:
        if any(w in text for w in words):
            return icon
    return 'SHADERFX'


def list_skills(workdir):
    folder = skills_dir(workdir)
    out = []
    for entry in sorted(os.listdir(folder)):
        if entry.lower().endswith(".md"):
            try:
                out.append(_parse(os.path.join(folder, entry)))
            except OSError:
                pass
    return out


def find(workdir, name):
    name = (name or "").lower()
    return next((s for s in list_skills(workdir) if s["name"].lower() == name), None)


def split_command(text):
    """'/render bitte schneller' -> ('render', 'bitte schneller'); otherwise (None, text)."""
    match = re.match(r"^/([\w-]+)\s*(.*)$", text.strip(), re.S)
    if not match:
        return None, text
    return match.group(1), match.group(2)


def build_multi(new, known, task):
    """Prompt for several checked skills: full instructions only for skills not yet sent in
    this chat; the others are just named (the model has them from earlier messages)."""
    parts = [f"[Skill: {s['title']}]\n{s['body']}" for s in new]
    if known:
        parts.append("[Skills still active: " + ", ".join(s["title"] for s in known)
                     + " – follow their instructions from earlier in this chat]")
    task = task.strip() or "Apply the skill(s) to the current scene."
    return "\n\n".join(parts) + f"\n\nThe user's task: {task}"


def create(workdir, title, description, body, icon=""):
    """Write a new skill file. Returns its name (file name without .md)."""
    folder = skills_dir(workdir)
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "skill"
    name, n = base, 2
    while os.path.exists(os.path.join(folder, name + ".md")):
        name, n = f"{base}-{n}", n + 1
    body = body.strip() or "Describe here what the model should do when this skill is checked."
    with open(os.path.join(folder, name + ".md"), "w", encoding="utf-8") as f:
        icon_line = f"icon: {icon}\n" if icon else ""
        f.write(f"---\ntitle: {title.strip()}\ndescription: {description.strip()}\n{icon_line}---\n{body}\n")
    return name

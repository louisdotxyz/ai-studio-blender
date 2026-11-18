# SPDX-License-Identifier: GPL-3.0-or-later
"""Skills: reusable instructions as Markdown files (/render, /material, ...).

Skills live in <extension user dir>/skills/<name>.md with a small front matter:

    ---
    title: CAM
    description: Camera, framing, depth of field
    icon: CAMERA_DATA
    studio_tab: RENDER        (optional: used by the chat while you come from that Studio tab)
    chat: yes                 (optional: also in the chat's skill row although bound to a tab;
                               no: never in the row, it comes by itself)
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
ACTIVE = ".active.json"   # the checked skills, kept between Blender sessions
# In the chat's skill row also when an older copy of the file says only studio_tab (2.1: no
# Claude line in the Light and Material tabs any more, these skills are used from the chat).
IN_CHAT = {"camera", "light", "material"}
# Never in the skill row, they come by themselves (cut: in the Video Editor's chat).
HIDDEN = {"cut"}
# Earlier shipped versions (sha1 of the file): a copy the user never changed gets the new text.
UPGRADES = {"sort": {"b35e76dbacdeb8cbbbf0f5eb0167c16a63492077"},
            "cad": {"e7b3d80ae27d22995023a36ddcaab3b3761f1b97"},
            "photo-match": {"a34caebb3fd705b8973cab6c1019c6d541e46aa2"},
            "world": {"1a3ba7d58d06ff2b246bc978ab1c0c482f37f9a5", "076f978bd04040c0e0d75c2ed3f2afcb09636abc"},
            "camera": {"925c6fe6c1b6442c271e4c0547a76407d2d139b7"},
            "finish": {"2c8e2980a4e3960e845ceaa0933402ef1aa3a520", "3a398b13b482836e9cbd207528b050c89c18fe95", "9ea64e3d802313f63d87e93dd73bf6399efdb5d1",
                       "275623b5bedd1339797315e1bd0f302adc054bb4"},
            "material": {"6ce2e39e716956cfeadf82e2b4e65e6e6800d243", "0b5914cfc5e631ebaf7906c58f86cd84c29c8dba",
                         "8943cce70de0d1325a652aa55edc0fdaca5b1855", "37835b81a9da24889085115e41a96c6d2b89b5ba"},
            "anim": {"bfcafe4b6beb5a4ca4a9dc95402c8e6eba75b14d", "22546325844f52d85e227a4b5955fe30bce6ee8b", "962d4b78bc9b8c975496ed5299b42e54bb3d197b", "3296f0cb84fef37a8acfc0479f4998b22973ba91",
                     "ff82b3329bde9750aaa134aa3801619bd96e3348", "87bf7c97adb4defa26c244cd876b333d9088ab2a",
                     "f2034240a1592b8ec96e54729d44ae61279db009",
                     "734a01c2e9c5f01d604a057772962ebd7c9c123d",
                     "45f7f14e8f37d7b206cacab4cf35eab678c6c9d5"},
            "cut": {"428f06972d363deecc2326b9254e3ad47c843a72", "e611cbf7a99c7047c4fc188e99025411d8089812", "3ecd86857bce1a50c41f5481d86f50088964389a", "d6c9efad8c1d901562ae27e8900d3b3ae269013f", "6ae68ae9968b512cdaf837799388561fbc1243bf", "ee49b0c3eae03c514b6d6d3d64038e49014144e7", "811c1f2d106f7e45330f733ea3f4e5b96aff6bce",
                    "ac87ce4754241ab4af4197e7bbaa7044794c7314"},
            "light": {"836f400f42ede6f5a887089f90528bf1e29da481", "dd0b9b9a2a01ef17ccf380bfc53708180e61224e",
                      "f0af9312a69eafd23e268221be706be8fffab3ba", "b5d877c4c7bf2035cd52552db1f4d371071a4ca5", "aca7b2ea8e34e2ff04bae9046de24b9aff6ed4ef"}}
_FRONT = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.S)


def skills_dir(workdir):
    path = os.path.join(workdir, "skills")
    os.makedirs(path, exist_ok=True)
    install_defaults(path)
    return path


_checked = set()


# Skills that are no longer shipped: moved into skills/_retired/ (the hashes: the shipped versions, for reference)
RETIRED = {"render": {"5f74fb865a31eaa85050bf2874ed4cf4d8d40d11"},
           "hdri": {"898cdb56736a4d8f429c42ae8373147c552c4c63"}}
# Skills that were retired and are shipped again: the user's own copy comes back from skills/_retired/
RESTORED = ("environment",)


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
            _copy_default(name, target)
        installed.add(name)
    for name, old in UPGRADES.items():
        target = os.path.join(folder, name + ".md")
        if name in names and _sha1(target) in old:
            _copy_default(name, target)
    for name in RESTORED:     # back once: the copy moved aside before, else the shipped one
        target = os.path.join(folder, name + ".md")
        if "restored:" + name in installed or name not in names:
            continue
        installed.add("restored:" + name)
        if os.path.exists(target):
            continue
        old = os.path.join(folder, "_retired", name + ".md")
        try:
            if os.path.exists(old):
                os.replace(old, target)
            else:
                _copy_default(name, target)
        except OSError:
            pass
    for name in RETIRED:      # moved aside (also when changed by hand): skills/_retired/<name>.md
        target = os.path.join(folder, name + ".md")
        if os.path.exists(target):
            try:
                os.makedirs(os.path.join(folder, "_retired"), exist_ok=True)
                os.replace(target, os.path.join(folder, "_retired", name + ".md"))
            except OSError:
                pass
    try:
        with open(record, "w", encoding="utf-8") as f:
            json.dump(sorted(installed), f)
    except OSError:
        pass


def _copy_default(name, target):
    with open(os.path.join(DEFAULTS_DIR, name + ".md"), encoding="utf-8") as src, \
            open(target, "w", encoding="utf-8") as dst:
        dst.write(src.read())


def _sha1(path):
    import hashlib
    try:
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return ""


# Skills that switch themselves: (words in the message that turn it on for that message, does it turn
# itself off after one use). Off after one use: tasks done once (tidying up, a CAD version, a photo
# match …), so a forgotten SORT does not tidy every following answer. A skill's own front matter can
# set "when: <regex>" and "once: yes/no".
AUTO = {
    "sort": (r"aufr[äa]um|r[äa]um\w*( \w+){0,3} auf\b|tidy|clean ?up|sortier|ordnung", True),
    "cad": (r"\bcad\b|\bstep\b|onshape|neue version|cad-auftrag", True),
    "photo-match": (r"foto nachstell|wie auf dem (foto|bild)|in das foto|ins foto|match.*photo|photo match", True),
    "environment": (r"raum (bauen|erstellen)|room from|baue? (einen|den) raum|umgebung bauen", True),
    "build": (r"datenblatt|datasheet|nachbauen|kaufteil", True),
    "finish": (r"\bkrea\b|verfeiner|refine", True),
    "camera": (r"kamera|camera|brennweite|focal|bildausschnitt|framing|perspektive|orbit|tiefenschärfe", False),
    "light": (r"licht|light|lampe|schatten|shadow|beleucht|ausleucht", False),
    "material": (r"material|oberfläche|textur|glanz|metall|glas|lack|farbe des", False),
    "world": (r"\bhdri\b|\bworld\b|himmel|\bsky\b|umgebungslicht", False),
    "anim": (r"animation|animier|\bschnitt(szene)?\b(?!\s+durch)|kamerafahrt|turntable|keyframe|\bfilm\b|"
             r"überblend|fade|\bbpm\b|sequencer", False),
}


def auto_rule(skill):
    """(regex or "", once?) of a skill: its own front matter first, else AUTO."""
    when, once = AUTO.get(skill.get("name"), ("", False))
    when = skill.get("when") or when
    if "once" in skill:
        once = str(skill["once"]).strip().lower() in ("yes", "true", "1")
    return when, once


def auto_for(workdir, text):
    """Skills whose words are in the message (on for this message only)."""
    found = []
    low = (text or "").lower()
    for skill in list_skills(workdir):
        when, _once = auto_rule(skill)
        if when:
            try:
                if re.search(when, low):
                    found.append(skill)
            except re.error:
                pass
    return found


def is_once(skill):
    return auto_rule(skill)[1]


def load_active(workdir):
    """The skills that were checked when Blender closed (only the ones that still exist); None
    when nothing was ever saved (the caller picks defaults)."""
    import json
    path = os.path.join(skills_dir(workdir), ACTIVE)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            names = json.load(f)
    except (OSError, ValueError):
        return []
    known = {s["name"] for s in list_skills(workdir)}
    return [n for n in names if isinstance(n, str) and n in known] if isinstance(names, list) else []


def save_active(workdir, names):
    import json
    try:
        with open(os.path.join(skills_dir(workdir), ACTIVE), "w", encoding="utf-8") as f:
            json.dump(list(names), f)
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
        "in_chat": name.lower() not in HIDDEN and meta.get("chat", "").lower() not in {"no", "false", "0"} and (
            meta.get("chat", "").lower() in {"yes", "true", "1"} or not meta.get("studio_tab")
            or name.lower() in IN_CHAT),
        "body": text.strip(),
        "path": path,
        **({"when": meta["when"]} if meta.get("when") else {}),
        **({"once": meta["once"]} if meta.get("once") else {}),
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

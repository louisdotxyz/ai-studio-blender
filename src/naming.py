# SPDX-License-Identifier: GPL-3.0-or-later
"""Short names with a two-digit number: "Ortho Top_01", "Key_02" (_100 and up only past 99),
never Blender's "Ortho Top.001" or a collection prefix like "AS_camera Ortho Top". Cameras and
lights follow the scheme below ("P_side_01").
No bpy: the caller passes the names that are taken."""

import re

_NUMBER = re.compile(r"(?:_\d{2,}|\.\d{3})$")


def base_of(name):
    """"Ortho Top_01" / "Ortho Top.001" → "Ortho Top"."""
    return _NUMBER.sub("", (name or "").strip()).strip() or "Untitled"


def numbered(name, taken):
    """The next free "<base>_NN" after the highest number in use for this base."""
    base = base_of(name)
    pattern = re.compile(re.escape(base) + r"(?:_(\d{2,})|\.(\d{3}))?$")
    highest = 0
    for other in taken:
        match = pattern.match(other)
        if match:
            highest = max(highest, int(match.group(1) or match.group(2) or 0))
    return "%s_%02d" % (base, highest + 1)


def number_suffix(n):
    """1 → "01", 100 → "100"."""
    return "%02d" % n


def tidy(names, prefixes=(), fallback="Untitled"):
    """{old: new} for names an older version or Blender made badly: a collection prefix
    ("AS_camera View" → "View_01"), Blender's ".001" ("View.001" → "View_02") or nothing but
    a number after the prefix ("AS_1" → "<fallback>_01"). Other names stay as they are."""
    prefixes = sorted({p for p in prefixes if p}, key=len, reverse=True)
    names = list(names)
    broken = []
    for name in names:
        rest, cut = name, False
        for prefix in prefixes:
            if name.startswith(prefix) and name[len(prefix):len(prefix) + 1] in (" ", "_", "|"):
                rest, cut = name[len(prefix) + 1:].strip(" _|"), True
                break
        dotted = re.search(r"\.\d{3}$", rest) is not None
        if not cut and not dotted:
            continue
        base = base_of(rest)
        if not rest or base.isdigit() or base == "Untitled":
            base = fallback
        number = re.search(r"(?:_(\d{2,})|\.(\d{3}))$", rest)
        broken.append((base.lower(), int(number.group(1) or number.group(2)) if number else 0, name, base))
    taken = [n for n in names if n not in {b[2] for b in broken}]
    result = {}
    for _key, _n, name, base in sorted(broken):
        new = numbered(base, taken)
        taken.append(new)
        result[name] = new
    return result


# ---------------------------------------------------------------- the scheme: "P_side_01"
# Cameras: P (perspective) / O (orthographic), lights: A (area) / P (point) / S (spot) /
# D (sun, directional); then what it is (front, side, key …) in lower case, then the number.

CAMERA_LETTERS = {"PERSP": "P", "ORTHO": "O", "PANO": "P"}
LIGHT_LETTERS = {"AREA": "A", "POINT": "P", "SPOT": "S", "SUN": "D"}
# words that only said what kind it is (gone, the letter says it)
_KIND_WORDS = {"ortho", "orthographic", "orthografisch", "persp", "perspective", "perspektive", "perspektivisch",
               "camera", "cam", "kamera", "view", "ansicht", "light", "licht", "lamp", "lampe", "area",
               "point", "spot", "sun", "sonne", "as", "rs"}
# German (and a few English) view and light words → the scheme's word
_WORDS = {"vorne": "front", "vorn": "front", "vorder": "front", "vorderseite": "front", "frontal": "front",
          "hinten": "back", "rueck": "back", "rück": "back", "rückseite": "back", "rear": "back",
          "seite": "side", "seitlich": "side", "links": "left", "rechts": "right",
          "oben": "top", "aufsicht": "top", "draufsicht": "top", "unten": "bottom", "untersicht": "bottom",
          "isometrisch": "iso", "isometric": "iso", "führung": "key", "fuehrung": "key", "fülllicht": "fill",
          "fuell": "fill", "füll": "fill", "kante": "rim", "gegenlicht": "rim"}
_SCHEME = re.compile(r"^([A-Z])_([a-z0-9][a-z0-9-]*)_(\d{2,})$")


def role_of(name, prefixes=(), fallback="view"):
    """What a camera or light is, in the scheme's words: "AS_camera Ortho Oben.001" → "top",
    "P_side_02" → "side", "detail pod" → "detail-pod"."""
    text = (name or "").strip()
    match = _SCHEME.match(text)
    if match:
        return match.group(2)
    for prefix in sorted({p for p in prefixes if p}, key=len, reverse=True):
        if text.startswith(prefix) and text[len(prefix):len(prefix) + 1] in (" ", "_", "|"):
            text = text[len(prefix) + 1:]
            break
    text = _NUMBER.sub("", text).strip()
    text = re.sub(r"^[A-Z]_(?=[A-Za-z])", "", text)          # an old letter: "P_Front" → "Front"
    words = [w for w in re.split(r"[^0-9a-zäöüß]+", text.lower()) if w]
    words = [_WORDS.get(w, w) for w in words if w not in _KIND_WORDS]
    words = [w.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss") for w in words]
    role = "-".join(words)
    return role if role and not role.isdigit() else fallback


def scheme_base(letter, role):
    return "%s_%s" % (letter, role)


def in_scheme(name, letter, role):
    match = _SCHEME.match(name or "")
    return bool(match) and match.group(1) == letter and match.group(2) == role


def scheme_names(items, prefixes=()):
    """{old: new} for [(name, letter, fallback role)]: every name in the scheme, names that
    already are stay, the others get the next free number."""
    keep, change = [], []
    for name, letter, fallback in items:
        role = role_of(name, prefixes, fallback)
        (keep if in_scheme(name, letter, role) else change).append((name, letter, role))
    taken = [name for name, _l, _r in keep]
    seen = set()
    for name in list(taken):   # two with the same name in the scheme cannot be: keep the first
        if name in seen:
            change.append(next(i for i in keep if i[0] == name))
        seen.add(name)
    result = {}
    for name, letter, role in sorted(change, key=lambda i: (i[1], i[2], i[0].lower())):
        new = numbered(scheme_base(letter, role), taken)
        taken.append(new)
        result[name] = new
    return result

# SPDX-License-Identifier: GPL-3.0-or-later
"""Short names with a two-digit number: "Ortho Top_01", "Key_02" (_100 and up only past 99),
never Blender's "Ortho Top.001" or a collection prefix like "AS_camera Ortho Top".
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

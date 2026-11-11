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

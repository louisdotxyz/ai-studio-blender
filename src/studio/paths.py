# SPDX-License-Identifier: GPL-3.0-or-later
"""Where Render Studio keeps its files inside the combined extension."""

import os

import bpy

ADDON = __package__.rsplit(".", 1)[0]   # the extension's package (Studio is a sub-package)


def data_dir():
    """<extension user dir>/studio – presets, favorites, material presets."""
    try:
        base = bpy.utils.extension_path_user(ADDON, path="studio", create=True)
    except Exception:
        import tempfile
        base = os.path.join(tempfile.gettempdir(), "render_studio")
        os.makedirs(base, exist_ok=True)
    return base



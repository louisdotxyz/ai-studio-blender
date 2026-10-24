# SPDX-License-Identifier: GPL-3.0-or-later
"""Attachments: classify files, turn them into message content, paste from clipboard."""

import base64
import os
import subprocess
import sys
import tempfile
import time

from .i18n import tr

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
# Images Blender can read but the API can't: converted to PNG before sending.
CONVERT_IMAGE_EXT = {".tif", ".tiff", ".bmp", ".tga", ".exr", ".hdr", ".dpx", ".cin", ".jp2", ".j2c"}
TEXT_EXT = {
    ".txt", ".md", ".py", ".json", ".csv", ".tsv", ".xml", ".html", ".htm", ".css", ".js", ".ts",
    ".toml", ".yaml", ".yml", ".ini", ".cfg", ".log", ".osl", ".glsl", ".mtl", ".svg", ".sh", ".rst",
}
# Formats commonly dropped on Blender; sent as path so Claude can import them via execute_python.
FILE_EXT = {
    ".blend", ".obj", ".fbx", ".glb", ".gltf", ".stl", ".ply", ".abc", ".usd", ".usda", ".usdc",
    ".usdz", ".dxf", ".x3d", ".wrl", ".dae", ".3ds", ".step", ".stp", ".zip", ".mp4", ".mov",
}
ALL_EXT = sorted(set(IMAGE_TYPES) | CONVERT_IMAGE_EXT | TEXT_EXT | FILE_EXT | {".pdf"})

MAX_INLINE_TEXT = 200_000      # bytes; larger text files are sent as path only
MAX_IMAGE_BYTES = 3_500_000    # API limit is 5 MB base64
MAX_PDF_BYTES = 30_000_000

KIND_ICONS = {"image": 'IMAGE_DATA', "pdf": 'DOCUMENTS', "text": 'TEXT', "file": 'FILE_BLANK'}


def _looks_like_text(path):
    try:
        with open(path, "rb") as f:
            chunk = f.read(4096)
        if b"\0" in chunk:
            return False
        chunk.decode("utf-8")
        return True
    except (OSError, UnicodeDecodeError):
        return False


def classify(path):
    ext = os.path.splitext(path)[1].lower()
    size = os.path.getsize(path)
    if ext in IMAGE_TYPES or ext in CONVERT_IMAGE_EXT:
        return "image"
    if ext == ".pdf" and size <= MAX_PDF_BYTES:
        return "pdf"
    if size <= MAX_INLINE_TEXT and (ext in TEXT_EXT or (ext not in FILE_EXT and _looks_like_text(path))):
        return "text"
    return "file"


def make(path):
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(path):
        raise ValueError(tr("Not a file: {}").format(path))
    return {"path": path, "name": os.path.basename(path), "kind": classify(path), "size": os.path.getsize(path)}


def _size_label(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


def _image_bytes(path, max_size):
    """Return (bytes, media_type), converting/downscaling with Blender if needed."""
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_TYPES and os.path.getsize(path) <= MAX_IMAGE_BYTES:
        with open(path, "rb") as f:
            return f.read(), IMAGE_TYPES[ext]

    import bpy

    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        if w == 0 or h == 0:
            raise ValueError(tr("Could not read the image"))
        factor = min(1.0, max_size / max(w, h))
        if factor < 1.0:
            img.scale(max(1, int(w * factor)), max(1, int(h * factor)))
        out = os.path.join(tempfile.gettempdir(), f"claude_att_{os.getpid()}_{time.monotonic_ns()}.png")
        img.filepath_raw = out
        img.file_format = 'PNG'
        img.save()
    finally:
        bpy.data.images.remove(img)
    with open(out, "rb") as f:
        data = f.read()
    os.remove(out)
    return data, "image/png"


def content_blocks(att, max_image=2000):
    """Message content blocks for one attachment."""
    path, name, kind = att["path"], att["name"], att["kind"]
    header = f"[Attachment: {name} – {path} ({_size_label(att['size'])})]"
    if kind == "image":
        data, media = _image_bytes(path, max_image)
        return [
            {"type": "text", "text": header},
            {"type": "image", "source": {"type": "base64", "media_type": media,
                                         "data": base64.b64encode(data).decode("ascii")}},
        ]
    if kind == "pdf":
        with open(path, "rb") as f:
            data = f.read()
        return [
            {"type": "text", "text": header},
            {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                            "data": base64.b64encode(data).decode("ascii")}},
        ]
    if kind == "text":
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            body = f.read()
        return [{"type": "text", "text": f"{header}\n```\n{body}\n```"}]
    return [{"type": "text", "text": header + "\nThis file is available locally. If needed, import or use it "
                                               "in Blender with execute_python."}]


# ---------------------------------------------------------------------------
# Clipboard

def _run(cmd, timeout=10):
    return subprocess.run(cmd, capture_output=True, timeout=timeout, text=True)


def paste_from_clipboard(dest_dir):
    """Save the clipboard image (or copied file path) and return a list of file paths."""
    os.makedirs(dest_dir, exist_ok=True)
    target = os.path.join(dest_dir, time.strftime("Clipboard_%Y-%m-%d_%H-%M-%S.png"))

    if sys.platform == "darwin":
        # A file copied in Finder (Cmd+C)
        res = _run(["osascript", "-e", "POSIX path of (the clipboard as «class furl»)"])
        if res.returncode == 0 and res.stdout.strip() and os.path.isfile(res.stdout.strip()):
            return [res.stdout.strip()]
        # Image data (e.g. screenshot with Cmd+Ctrl+Shift+4)
        for cls, ext in (("PNGf", ".png"), ("TIFF", ".tiff")):
            out = os.path.splitext(target)[0] + ext
            script = [
                "-e", f'set f to open for access (POSIX file "{out}") with write permission',
                "-e", "set eof f to 0",
                "-e", f"write (the clipboard as «class {cls}») to f",
                "-e", "close access f",
            ]
            res = _run(["osascript", *script])
            if res.returncode == 0 and os.path.isfile(out) and os.path.getsize(out) > 0:
                return [out]
            if os.path.exists(out):
                os.remove(out)
        raise RuntimeError(tr("There is no image or file in the clipboard"))

    if sys.platform == "win32":
        ps = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$f=[System.Windows.Forms.Clipboard]::GetFileDropList();"
            "if($f.Count -gt 0){$f; exit 0};"
            "$i=[System.Windows.Forms.Clipboard]::GetImage();"
            f"if($i){{$i.Save('{target}',[System.Drawing.Imaging.ImageFormat]::Png); '{target}'; exit 0}};"
            "exit 1"
        )
        res = _run(["powershell", "-NoProfile", "-STA", "-Command", ps])
        paths = [p.strip() for p in res.stdout.splitlines() if p.strip() and os.path.isfile(p.strip())]
        if res.returncode == 0 and paths:
            return paths
        raise RuntimeError(tr("There is no image or file in the clipboard"))

    for cmd in (["wl-paste", "--no-newline", "--type", "image/png"],
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"]):
        try:
            res = subprocess.run(cmd, capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if res.returncode == 0 and res.stdout:
            with open(target, "wb") as f:
                f.write(res.stdout)
            return [target]
    raise RuntimeError(tr("No image in the clipboard (requires wl-paste or xclip)"))

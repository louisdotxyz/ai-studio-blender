"""Static check without Blender: undefined names and similar (pyflakes), with Blender's
property annotations (`name: EnumProperty(...)`) turned into assignments first, because
pyflakes would read the strings in them as type annotations.

    python3 tests/lint.py        (needs: pip install pyflakes)
"""
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")
ANNOTATION = re.compile(r"^(\s+)(\w+): (\w*Property\()", re.M)
IGNORE = ("imported but unused", "redefinition of unused", "unable to detect undefined names")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        for folder, _dirs, files in os.walk(ROOT):
            for name in files:
                if not name.endswith(".py"):
                    continue
                src = os.path.join(folder, name)
                dst = os.path.join(tmp, os.path.relpath(src, ROOT))
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                with open(src, encoding="utf-8") as f:
                    text = ANNOTATION.sub(r"\1\2 = \3", f.read())
                with open(dst, "w", encoding="utf-8") as f:
                    f.write(text)
        result = subprocess.run([sys.executable, "-m", "pyflakes", tmp], capture_output=True, text=True)
        out = result.stdout + result.stderr   # syntax errors come on stderr
    problems = [line.replace(tmp, "src") for line in out.splitlines() if not any(i in line for i in IGNORE)]
    print("\n".join(problems) or "LINT_OK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

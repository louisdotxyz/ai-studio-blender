# SPDX-License-Identifier: GPL-3.0-or-later
"""Writes docs/index.html: the install page of the extension repository (GitHub Pages).

    python3 tools/repo_page.py docs

Reads docs/index.json (made by `blender --command extension server-generate`). The big
button is a link Blender understands: dragged into Blender's window it adds this
repository and installs the add-on, and Blender then offers every new version.
"""

import html
import json
import os
import sys
import urllib.parse

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Studio · ID Toolkit</title>
<style>
  :root {{ --bg: #f6f6f4; --fg: #1d1d1b; --muted: #6b6b66; --card: #fff; --line: #e2e2dd; --accent: #e87d0d; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #1b1b1a; --fg: #ececea; --muted: #a2a29c; --card: #252524; --line: #393937; }}
  }}
  body {{ margin: 0; background: var(--bg); color: var(--fg);
         font: 16px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
  main {{ max-width: 640px; margin: 0 auto; padding: 48px 16px 64px; }}
  h1 {{ font-size: 30px; margin: 0 0 4px; }}
  .tag {{ color: var(--muted); margin: 0 0 32px; }}
  .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 20px 22px;
          margin-bottom: 16px; }}
  h2 {{ font-size: 17px; margin: 0 0 10px; }}
  ol {{ margin: 0; padding-left: 20px; }}
  li {{ margin: 4px 0; }}
  .button {{ display: inline-block; margin: 14px 0 6px; padding: 12px 20px; border-radius: 9px;
            background: var(--accent); color: #fff; font-weight: 600; text-decoration: none; cursor: grab; }}
  .small {{ color: var(--muted); font-size: 14px; }}
  code {{ font-size: 14px; background: var(--bg); padding: 1px 5px; border-radius: 4px; word-break: break-all; }}
  a {{ color: inherit; }}
</style>
</head>
<body>
<main>
  <h1>AI Studio for Blender</h1>
  <p class="small">Part of ID Toolkit</p>
  <p class="tag">{tagline} · version {version} · macOS · Blender {blender} or newer</p>

  <div class="card">
    <h2>Install with updates</h2>
    <ol>
      <li>In Blender: <em>Edit › Preferences › System › Network</em>: turn on <b>Allow Online Access</b>.</li>
      <li>Drag this button into the Blender window and confirm:</li>
    </ol>
    <a class="button" href="{drag}" draggable="true">AI Studio {version}</a>
    <p class="small">Blender adds this repository and installs the add-on. New versions then show up under
      <em>Preferences › Get Extensions</em>. Or add the repository by hand
      (<em>Get Extensions › Repositories › + › Add Remote Repository</em>):<br><code>{repo_url}</code></p>
  </div>

  <div class="card">
    <h2>Install without online access</h2>
    <ol>
      <li><a href="{zip}">Download the zip</a>.</li>
      <li>Drag it into the Blender window, or <em>Preferences › Get Extensions › ⌄ › Install from Disk…</em></li>
    </ol>
    <p class="small">This way Blender does not look for updates.</p>
  </div>

  <div class="card">
    <h2>Then</h2>
    <ol>
      <li>Open the sidebar in the 3D viewport (N): tabs <b>Claude</b> and <b>Studio</b>.</li>
      <li>The setup card checks online access, Claude Code and the login, with one button each, and
        connects Claude Code and the Claude app (Cowork) to the open Blender.</li>
    </ol>
    <p class="small">You need your own <a href="https://claude.com/claude-code">Claude Code</a> access (Claude
      subscription) or <a href="https://lmstudio.ai">LM Studio</a> for local models. Unofficial, not affiliated
      with Anthropic or the Blender Foundation. <a href="{website}">Source and README</a>.</p>
  </div>
</main>
</body>
</html>
"""


def main(folder):
    with open(os.path.join(folder, "index.json"), encoding="utf-8") as f:
        item = json.load(f)["data"][0]
    zip_name = item["archive_url"]
    query = urllib.parse.urlencode({"repository": "./index.json",
                                    "blender_version_min": item["blender_version_min"]})
    esc = html.escape
    page = PAGE.format(
        tagline=esc(item["tagline"]), version=esc(item["version"]),
        blender=esc(item["blender_version_min"].rsplit(".0", 1)[0]),
        drag=esc(zip_name + "?" + query), zip=esc(zip_name), website=esc(item["website"]),
        repo_url=esc(os.environ.get("REPO_URL", "https://louisdotxyz.github.io/ai-studio-blender/index.json")))
    with open(os.path.join(folder, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs")

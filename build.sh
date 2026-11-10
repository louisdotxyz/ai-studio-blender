#!/bin/zsh
# Builds the extension zip into dist/ (needs Blender 5.x in /Applications).
#   ./build.sh          the zip
#   ./build.sh --repo   also docs/: the extension repository for GitHub Pages (index.json, the
#                       newest zip, an install page). Pages serves it at
#                       https://louisdotxyz.github.io/ai-studio-blender/ — share that link: dragging its
#                       button into Blender installs the add-on, and Blender then offers updates.
set -e
cd "$(dirname "$0")"
BLENDER=/Applications/Blender.app/Contents/MacOS/Blender
mkdir -p dist
$BLENDER --factory-startup --command extension build \
  --source-dir src --output-dir dist 2>&1 | grep -E "created|ERROR|FATAL"
if [[ "$1" == "--repo" ]]; then
  version=$(grep -m1 '^version' src/blender_manifest.toml | cut -d'"' -f2)
  mkdir -p docs && rm -f docs/index.json docs/index.html docs/*.zip(N)
  cp "dist/AI_Studio-$version.zip" docs/
  $BLENDER --factory-startup --command extension server-generate --repo-dir docs 2>&1 | grep -vE "^Blender|^$" || true
  python3 tools/repo_page.py docs
  ls docs
fi

#!/bin/zsh
# Builds the extension zip into dist/ (needs Blender 5.2 in /Applications).
set -e
cd "$(dirname "$0")"
mkdir -p dist
/Applications/Blender.app/Contents/MacOS/Blender --factory-startup --command extension build \
  --source-dir src --output-dir dist 2>&1 | grep -E "created|ERROR|FATAL"

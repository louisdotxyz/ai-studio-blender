#!/bin/bash
# reel.sh <reel.json> : Frames in Blender-Chunks rendern (35-s-Kill), dann HUD + MP4
CFG="$1"
OUT=$(python -c "import json;print(json.load(open(\"$CFG\"))[\"out\"])")
B=/Applications/Blender.app/Contents/MacOS/Blender
for i in $(seq 1 200); do
  [ -f "$OUT/.done" ] && break
  "$B" -b --factory-startup -P scripts/render_modes.py -- "$CFG" > /dev/null 2>&1
done
[ -f "$OUT/.done" ] || { echo "FAIL frames"; exit 1; }
rm -f "$OUT"/tmp_*.png
python scripts/hud.py "$CFG"

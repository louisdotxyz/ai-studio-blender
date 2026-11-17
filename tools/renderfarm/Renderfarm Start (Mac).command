#!/bin/bash
# BLENDER RENDER FARM starten (Mac Studio): Doppelklick, oder im Terminal: bash + diese Datei hineinziehen.
# Der eigentliche Start liegt in _worker/start_mac.sh und läuft als lokale Kopie.
SUB="BDE_VID/BA/02_Studierende/Louis Widmer/Flamenco"
DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd)"
for f in "$DIR/_worker/start_mac.sh" /Volumes/DDE*/"$SUB"/_worker/start_mac.sh; do
  [ -f "$f" ] && { mkdir -p "$HOME/.renderfarm"; cp -f "$f" "$HOME/.renderfarm/start.sh"; exec bash "$HOME/.renderfarm/start.sh" "$@"; }
done
echo "Server nicht gefunden: WLAN/VPN prüfen und nochmals öffnen."
